import shlex
import socket
import subprocess
from pathlib import Path
from types import SimpleNamespace

from remote_dev.download import DownloadManager, EndpointConnection
from remote_dev.file_transfer import CacheLayout
from remote_dev.preview_bridge import PreviewBridge, new_context
from remote_dev.tssh import _preview_forward, _ssh_command


def test_same_path_isolated_for_multiple_users_and_devices(tmp_path: Path):
    layout = CacheLayout(tmp_path / "cache", tmp_path / "downloads")
    first = DownloadManager(EndpointConnection("host-a", 22, "alice"), layout)
    second = DownloadManager(EndpointConnection("host-a", 22, "bob"), layout)
    third = DownloadManager(EndpointConnection("host-b", 22, "alice"), layout)
    try:
        path = "/workspace/report.pdf"
        assert first.layout.download_path(first.endpoint, path) != second.layout.download_path(second.endpoint, path)
        assert first.layout.download_path(first.endpoint, path) != third.layout.download_path(third.endpoint, path)
    finally:
        first.shutdown()
        second.shutdown()
        third.shutdown()


def test_preview_forward_failure_is_soft_and_plain_ssh_stays_native(monkeypatch):
    monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)
    assert _preview_forward("alice@example.test", None, 22, 43000) is None
    assert _ssh_command("alice@example.test", None, 22, ["echo", "ok"]) == [
        "ssh", "alice@example.test", "echo", "ok"
    ]


def test_preview_forward_uses_random_remote_loopback_port(monkeypatch):
    class Process:
        def __init__(self):
            self.returncode = None
            self.command = None

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = 0

        def wait(self, timeout=None):
            return self.returncode

    process = Process()
    captured: list[list[str]] = []
    monkeypatch.setattr(subprocess, "Popen", lambda command, **kwargs: captured.append(command) or process)
    monkeypatch.setattr("remote_dev.tssh.time.sleep", lambda _seconds: None)
    forward = _preview_forward("alice@example.test", "/tmp/key", 22, 43000)
    assert forward is not None
    assert 40000 <= forward[1] <= 59999
    assert any(item.startswith("127.0.0.1:") for item in captured[0])


def test_bridge_rejects_old_nonce_after_new_session():
    first = PreviewBridge(new_context("endpoint", "first"))
    started = first.start()
    first.stop()
    second = PreviewBridge(new_context("endpoint", "second"))
    current = second.start()
    try:
        with socket.create_connection(("127.0.0.1", current.local_port), timeout=1) as conn:
            conn.sendall((
                '{"operation":"get","remote_path":"/tmp/a","endpoint_digest":"endpoint",'
                f'"session_id":"first","nonce":"{started.nonce}"}}\n'
            ).encode())
            assert b'"ok":false' in conn.recv(4096)
    finally:
        second.stop()
