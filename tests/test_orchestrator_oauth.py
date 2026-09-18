from pathlib import Path

import pytest

from remote_dev.orchestrator.model import TargetKey
from remote_dev.orchestrator.oauth import OAuthError, OAuthManager


class LiveProcess:
    _pid = 100
    def __init__(self):
        LiveProcess._pid += 1
        self.pid = LiveProcess._pid
    def wait(self, timeout=None):
        raise __import__("subprocess").TimeoutExpired("ssh", timeout)
    def poll(self): return None
    def send_signal(self, signal): pass
    def kill(self): pass


class FreePortProbe:
    def connect_ex(self, address):
        return 111
    def close(self):
        pass


def test_oauth_uses_standalone_forwarders_and_is_single_active(monkeypatch, tmp_path: Path):
    calls = []
    monkeypatch.setattr("remote_dev.orchestrator.oauth.subprocess.Popen", lambda args, **kwargs: calls.append(args) or LiveProcess())
    monkeypatch.setattr("remote_dev.orchestrator.oauth.socket.socket", lambda *args, **kwargs: FreePortProbe())
    manager = OAuthManager()
    first = TargetKey("one", 22, "u")
    second = TargetKey("two", 22, "u")
    assert manager.start(first, identity_file=tmp_path / "key") == manager.start(first, identity_file=tmp_path / "key")
    with pytest.raises(OAuthError, match="已有另一个目标"):
        manager.start(second, identity_file=tmp_path / "key")
    assert len(calls) == 2
    assert all("-O" not in call for call in calls)
    assert any("127.0.0.1:1455:127.0.0.1:1455" in call for call in calls)


def test_oauth_finish_stops_forwarders_and_is_idempotent(monkeypatch, tmp_path: Path):
    processes = []
    monkeypatch.setattr("remote_dev.orchestrator.oauth.subprocess.Popen", lambda *args, **kwargs: processes.append(LiveProcess()) or processes[-1])
    monkeypatch.setattr("remote_dev.orchestrator.oauth.socket.socket", lambda *args, **kwargs: FreePortProbe())
    manager = OAuthManager()
    target = TargetKey("host", 22, "u")
    manager.start(target, identity_file=tmp_path / "key")
    assert manager.finish(target) is True
    assert manager.finish(target) is False


def test_oauth_requires_identity_file():
    with pytest.raises(OAuthError) as exc:
        OAuthManager().start(TargetKey("host", 22, "u"))
    assert exc.value.code == "MASTER_UNAVAILABLE"
