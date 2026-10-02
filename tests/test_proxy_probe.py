from pathlib import Path

from remote_dev.orchestrator.model import TargetKey
from remote_dev.orchestrator import proxy_persistent
from remote_dev.orchestrator.endpoint_forward import EndpointForwardManager
from remote_dev.orchestrator.model import EndpointKey


def test_probe_classifies_healthy(monkeypatch):
    class Result:
        returncode = 0
        stdout = "HEALTHY\n"
        stderr = ""

    monkeypatch.setattr(proxy_persistent.subprocess, "run", lambda *args, **kwargs: Result())
    result = proxy_persistent.probe_remote_proxy(TargetKey("host", 22, "user"), Path("/tmp/id"))
    assert result.state == "healthy"


def test_probe_classifies_stale_listener(monkeypatch):
    class Result:
        returncode = 0
        stdout = "OCCUPIED\n"
        stderr = ""

    monkeypatch.setattr(proxy_persistent.subprocess, "run", lambda *args, **kwargs: Result())
    result = proxy_persistent.probe_remote_proxy(TargetKey("host", 22, "user"), Path("/tmp/id"))
    assert result.state == "occupied"
    assert "timed out" in result.detail


def test_probe_does_not_auto_kill_unknown_listener(monkeypatch):
    calls = []

    class Result:
        returncode = 0
        stdout = "OCCUPIED\n"
        stderr = ""

    monkeypatch.setattr(proxy_persistent.subprocess, "run", lambda *args, **kwargs: (calls.append(args[0]) or Result()))
    proxy_persistent.probe_remote_proxy(TargetKey("host", 22, "user"), Path("/tmp/id"))
    assert not any("kill" in str(call) or "pkill" in str(call) for call in calls)


def test_endpoint_manager_reuses_healthy_external_proxy(monkeypatch):
    endpoint = EndpointKey("host", 22)
    manager = EndpointForwardManager()
    calls = []

    class Result:
        def __init__(self, returncode):
            self.returncode = returncode

    checks = iter([Result(1), Result(0), Result(0)])
    monkeypatch.setattr("remote_dev.orchestrator.endpoint_forward.subprocess.run", lambda *args, **kwargs: next(checks))
    monkeypatch.setattr(
        "remote_dev.orchestrator.endpoint_forward.probe_remote_proxy",
        lambda target, identity: proxy_persistent.RemoteProxyProbe("healthy"),
    )
    monkeypatch.setattr(
        "remote_dev.orchestrator.endpoint_forward.ensure",
        lambda *args, **kwargs: calls.append("ensure"),
    )
    monkeypatch.setattr(
        "remote_dev.orchestrator.endpoint_forward.remove",
        lambda *args, **kwargs: calls.append("remove"),
    )

    manager.start(endpoint, "user", Path("/tmp/id"), event_forward=False)
    manager.stop(endpoint, "user")
    assert calls == []


def test_endpoint_manager_uses_event_only_leg_for_healthy_proxy(monkeypatch):
    endpoint = EndpointKey("host", 22)
    manager = EndpointForwardManager()
    calls = []

    class Result:
        def __init__(self, returncode):
            self.returncode = returncode

    checks = iter([Result(1), Result(0), Result(0)])
    monkeypatch.setattr("remote_dev.orchestrator.endpoint_forward.subprocess.run", lambda *args, **kwargs: next(checks))
    monkeypatch.setattr(
        "remote_dev.orchestrator.endpoint_forward.probe_remote_proxy",
        lambda target, identity: proxy_persistent.RemoteProxyProbe("healthy"),
    )
    monkeypatch.setattr(
        "remote_dev.orchestrator.endpoint_forward.ensure",
        lambda *args, **kwargs: calls.append(kwargs) or Path("/tmp/event-only.service"),
    )
    monkeypatch.setattr("remote_dev.orchestrator.endpoint_forward.remove", lambda *args, **kwargs: calls.append("remove"))
    manager.start(endpoint, "user", Path("/tmp/id"), event_forward=True)
    assert calls[0] == {"event_forward": True, "proxy_forward": False}


def test_endpoint_manager_rejects_unresponsive_occupied_proxy(monkeypatch):
    endpoint = EndpointKey("host", 22)

    class Result:
        returncode = 1

    monkeypatch.setattr("remote_dev.orchestrator.endpoint_forward.subprocess.run", lambda *args, **kwargs: Result())
    monkeypatch.setattr(
        "remote_dev.orchestrator.endpoint_forward.probe_remote_proxy",
        lambda target, identity: proxy_persistent.RemoteProxyProbe("occupied"),
    )
    monkeypatch.setattr(
        "remote_dev.orchestrator.endpoint_forward.ensure",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not replace unknown listener")),
    )

    try:
        EndpointForwardManager().start(endpoint, "user", Path("/tmp/id"), event_forward=False)
    except RuntimeError as exc:
        assert "REMOTE" not in str(exc)
        assert "4227" in str(exc)
    else:
        raise AssertionError("occupied proxy must be reported")
