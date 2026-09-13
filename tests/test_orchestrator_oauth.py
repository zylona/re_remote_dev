from pathlib import Path

import pytest

from remote_dev.orchestrator.master import MasterManager
from remote_dev.orchestrator.model import TargetKey
from remote_dev.orchestrator.oauth import OAuthError, OAuthManager


def test_oauth_start_is_single_active_and_idempotent(monkeypatch, tmp_path: Path):
    calls = []

    class Result:
        returncode = 0
        stderr = ""
        stdout = ""

    monkeypatch.setattr("remote_dev.orchestrator.oauth.subprocess.run", lambda args, **kwargs: calls.append(args) or Result())
    manager = OAuthManager(MasterManager(home=tmp_path))
    first = TargetKey("one", 22, "u")
    second = TargetKey("two", 22, "u")
    assert manager.start(first) == manager.start(first)
    with pytest.raises(OAuthError, match="已有另一个目标"):
        manager.start(second)
    assert calls[0][calls[0].index("-O") + 1] == "forward"
    assert "127.0.0.1:1455:127.0.0.1:1455" in calls[0]


def test_oauth_finish_cleans_forward_and_is_idempotent(monkeypatch, tmp_path: Path):
    calls = []

    class Result:
        returncode = 0
        stderr = ""
        stdout = ""

    monkeypatch.setattr("remote_dev.orchestrator.oauth.subprocess.run", lambda args, **kwargs: calls.append(args) or Result())
    manager = OAuthManager(MasterManager(home=tmp_path))
    target = TargetKey("host", 22, "u")
    manager.start(target)
    assert manager.finish(target) is True
    assert manager.finish(target) is False
    assert calls[1][calls[1].index("-O") + 1] == "cancel"


def test_oauth_forward_failure_is_actionable(monkeypatch, tmp_path: Path):
    class Result:
        returncode = 255
        stderr = "Control socket unavailable"
        stdout = ""

    monkeypatch.setattr("remote_dev.orchestrator.oauth.subprocess.run", lambda *args, **kwargs: Result())
    with pytest.raises(OAuthError, match="无法建立") as exc:
        OAuthManager(MasterManager(home=tmp_path)).start(TargetKey("host", 22, "u"))
    assert exc.value.code == "PORT_CONFLICT"
