import json
import socket
import sys
import threading
import time
from pathlib import Path

from remote_dev.preview_bridge import PreviewBridge, PreviewRequest, new_context
from remote_dev.tssh import _preview_shell_command, _proxy_shell_command


def _request(port: int, payload: dict[str, object]) -> dict[str, object]:
    with socket.create_connection(("127.0.0.1", port), timeout=2) as conn:
        conn.sendall((json.dumps(payload) + "\n").encode())
        data = bytearray()
        while not data.endswith(b"\n"):
            data.extend(conn.recv(4096))
        return json.loads(data.decode())


def test_preview_request_decode_assigns_id_and_rejects_bad_operation():
    request = PreviewRequest.decode(
        json.dumps(
            {
                "operation": "get",
                "remote_path": "/workspace/report.pdf",
                "endpoint_digest": "endpoint",
                "session_id": "session",
                "nonce": "nonce",
            }
        )
    )
    assert request.request_id
    assert request.remote_path.endswith("report.pdf")

    try:
        PreviewRequest.decode(json.dumps({"operation": "delete", "remote_path": "/tmp/x"}))
    except ValueError as exc:
        assert "operation" in str(exc)
    else:
        raise AssertionError("invalid operation was accepted")


def test_preview_bridge_validates_session_nonce_and_stops_cleanly():
    context = new_context("endpoint", "session")
    received: list[PreviewRequest] = []

    class Job:
        local_path = Path("/tmp/download/report.pdf")
        state = "QUEUED"

    def receive(request: PreviewRequest) -> Job:
        received.append(request)
        return Job()

    bridge = PreviewBridge(context, receive)
    started = bridge.start()
    try:
        response = _request(
            started.local_port,
            {
                "operation": "get",
                "remote_path": "/workspace/report.pdf",
                "endpoint_digest": "endpoint",
                "session_id": "session",
                "nonce": started.nonce,
            },
        )
        assert response["ok"] is True
        assert response["local_path"] == "/tmp/download/report.pdf"
        assert response["state"] == "QUEUED"
        deadline = time.monotonic() + 1
        while not received and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(received) == 1

        rejected = _request(
            started.local_port,
            {
                "operation": "get",
                "remote_path": "/workspace/report.pdf",
                "endpoint_digest": "endpoint",
                "session_id": "other-session",
                "nonce": started.nonce,
            },
        )
        assert rejected["ok"] is False
    finally:
        bridge.stop()

    assert bridge._listener is None


def test_preview_bridge_streams_progress_until_job_completes():
    context = new_context("endpoint", "session")
    completed = threading.Event()

    class Future:
        def done(self) -> bool:
            return completed.is_set()

    class Job:
        local_path = Path("/tmp/download/report.pdf")
        state = "RUNNING"
        future = Future()
        bytes_done = 4
        bytes_total = 8
        speed_bps = 4.0
        eta_seconds = 1.0

    bridge = PreviewBridge(context, lambda _request: Job())
    started = bridge.start()
    try:
        with socket.create_connection(("127.0.0.1", started.local_port), timeout=2) as conn:
            stream = conn.makefile("rb")
            conn.sendall((json.dumps({
                "operation": "get",
                "remote_path": "/workspace/report.pdf",
                "endpoint_digest": "endpoint",
                "session_id": "session",
                "nonce": started.nonce,
            }) + "\n").encode())
            initial = json.loads(stream.readline().decode())
            assert initial["state"] == "RUNNING"
            progress = json.loads(stream.readline().decode())
            assert progress["event"] == "progress"
            assert progress["bytes_total"] == 8
            completed.set()
            final = json.loads(stream.readline().decode())
            while final.get("event") != "complete":
                final = json.loads(stream.readline().decode())
            assert final["event"] == "complete"
    finally:
        bridge.stop()


def test_preview_shell_command_exports_only_session_context():
    command = _preview_shell_command("endpoint", "session", "nonce", 45678)
    assert "RDO_ENDPOINT=endpoint" in command
    assert "RDO_SESSION=session" in command
    assert "RDO_NONCE=nonce" in command
    assert "RDO_PORT=45678" in command
    assert "REMOTE_DEV_TSSH_PROXY_PORT=4227" in command
    assert "exec \"${SHELL:-/bin/sh}\" -l" in command


def test_proxy_shell_command_exports_only_proxy_marker():
    command = _proxy_shell_command()
    assert "REMOTE_DEV_TSSH_PROXY_PORT=4227" in command
    assert "http_proxy=http://127.0.0.1:4227" in command
    assert "HTTPS_PROXY=http://127.0.0.1:4227" in command


def test_remote_helper_submits_relative_path(monkeypatch, capsys):
    received: dict[str, object] = {}
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def serve() -> None:
        with listener:
            connection, _ = listener.accept()
            with connection:
                received.update(json.loads(connection.recv(8192).decode()))
                connection.sendall(b'{"ok":true,"request_id":"request","local_path":"/home/test/Downloads/remote-dev/hash/aabbccddeeff0011-report.pdf","state":"CACHED"}\n')

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    monkeypatch.setenv("RDO_ENDPOINT", "endpoint")
    monkeypatch.setenv("RDO_SESSION", "session")
    monkeypatch.setenv("RDO_NONCE", "nonce")
    monkeypatch.setenv("RDO_PORT", str(port))
    monkeypatch.chdir("/tmp")
    from scripts import remote_preview_helper

    monkeypatch.setattr(sys, "argv", ["rget", "docs/report.pdf"])
    assert remote_preview_helper.main() == 0
    thread.join(timeout=1)
    assert received["operation"] == "get"
    assert received["remote_path"] == "/tmp/docs/report.pdf"
    output = capsys.readouterr().out
    assert "已提交" in output
    assert "存储路径：/home/test/Downloads/remote-dev/hash/aabbccddeeff0011-report.pdf" in output
    assert "状态：缓存命中" in output
