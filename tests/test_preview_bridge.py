import json
import socket
import sys
import threading
import time

from remote_dev.preview_bridge import PreviewBridge, PreviewRequest, new_context
from remote_dev.tssh import _preview_shell_command


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
    bridge = PreviewBridge(context, received.append)
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


def test_preview_shell_command_exports_only_session_context():
    command = _preview_shell_command("endpoint", "session", "nonce", 45678)
    assert "RDO_ENDPOINT=endpoint" in command
    assert "RDO_SESSION=session" in command
    assert "RDO_NONCE=nonce" in command
    assert "RDO_PORT=45678" in command
    assert "exec \"${SHELL:-/bin/sh}\" -l" in command


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
                connection.sendall(b'{"ok":true,"request_id":"request"}\n')

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
    assert "已提交" in capsys.readouterr().out
