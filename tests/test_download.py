import shlex
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

from remote_dev.download import DownloadManager, EndpointConnection
from remote_dev.file_transfer import CacheLayout
from remote_dev.preview_bridge import PreviewRequest


def _request(request_id: str = "request") -> PreviewRequest:
    return PreviewRequest("get", "/workspace/report.md", "endpoint", "session", "nonce", request_id)


class _FakeStdin:
    def __init__(self, owner):
        self.owner = owner

    def write(self, value: str) -> None:
        self.owner.batch = value

    def close(self) -> None:
        local = Path(shlex.split(self.owner.batch)[-1])
        local.write_bytes(b"hello")


class _FakePopen:
    def __init__(self, command, **kwargs):
        self.command = command
        self.returncode = None
        self.batch = ""
        self.stdin = _FakeStdin(self)
        self.stdout = None
        self.stderr = None

    def poll(self):
        self.returncode = 0
        return self.returncode

    def wait(self, **kwargs):
        self.returncode = 0
        return self.returncode

    def communicate(self, **kwargs):
        return "", ""

    def terminate(self):
        self.returncode = 1

    def kill(self):
        self.returncode = 1


def test_download_manager_downloads_atomically_and_reuses_metadata(monkeypatch, tmp_path: Path):
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[0] == "ssh":
            return SimpleNamespace(returncode=0, stdout="5 123\n", stderr="")
        batch = kwargs["input"]
        local = Path(shlex.split(batch)[-1])
        local.write_bytes(b"hello")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    manager = DownloadManager(
        EndpointConnection("host", 22, "alice", "/tmp/key"),
        CacheLayout(tmp_path / "cache", tmp_path / "downloads"),
    )
    try:
        first = manager.submit(_request("first"))
        first.future.result(timeout=2)
        assert first.state == "READY"
        assert first.local_path.read_bytes() == b"hello"
        assert first.local_path.with_name(first.local_path.name + ".rdo.json").is_file()

        before = len(calls)
        second = manager.submit(_request("second"))
        second.future.result(timeout=2)
        assert second.state == "CACHED"
        assert len(calls) == before + 1  # stat only; no second SFTP transfer
    finally:
        manager.shutdown()


def test_download_manager_merges_inflight_requests(monkeypatch, tmp_path: Path):
    started = threading.Event()
    release = threading.Event()

    def fake_run(command, **kwargs):
        if command[0] == "ssh":
            started.set()
            release.wait(timeout=2)
            return SimpleNamespace(returncode=0, stdout="5 123\n", stderr="")
        local = Path(shlex.split(kwargs["input"])[-1])
        local.write_bytes(b"hello")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    manager = DownloadManager(
        EndpointConnection("host", 22, "alice", "/tmp/key"),
        CacheLayout(tmp_path / "cache", tmp_path / "downloads"),
    )
    try:
        first = manager.submit(_request("first"))
        assert started.wait(timeout=1)
        second = manager.submit(_request("second"))
        assert first is second
        release.set()
        first.future.result(timeout=2)
    finally:
        manager.shutdown()
