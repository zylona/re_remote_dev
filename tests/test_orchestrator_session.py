import subprocess
from pathlib import Path

from remote_dev.orchestrator.master import MasterManager
from remote_dev.orchestrator.model import TargetKey
from remote_dev.orchestrator.session import SessionManager


def test_session_start_writes_nonce_and_registers(monkeypatch, tmp_path: Path):
    commands = []

    class Result:
        returncode = 0
        stderr = ""
        stdout = ""

    monkeypatch.setattr("remote_dev.orchestrator.session.subprocess.run", lambda args, **kwargs: commands.append(args) or Result())
    monkeypatch.setattr("remote_dev.orchestrator.session.request", lambda payload: {"ok": True, "digest": "d"})
    monkeypatch.setattr(manager_check := MasterManager(home=tmp_path), "check", lambda target: False)
    monkeypatch.setattr(manager_check, "ensure_forwarding", lambda target: True)
    class Proxy:
        def start(self, target, identity):
            return None
    class Event(Proxy):
        pass
    manager = SessionManager(manager_check, Proxy(), Event())
    session = manager.start(TargetKey("host", 22, "u"), Path("~/.ssh/id_ed25519"))
    assert len(session.nonce) > 20
    assert any("session.nonce" in part for command in commands for part in command)
    assert any("-M" in command for command in commands)


def test_session_registers_verified_proxy(monkeypatch, tmp_path: Path):
    payloads = []

    class Result:
        returncode = 0
        stderr = ""
        stdout = ""

    manager = MasterManager(home=tmp_path)
    monkeypatch.setattr(manager, "check", lambda target: True)
    monkeypatch.setattr(manager, "ensure_forwarding", lambda target: True)
    monkeypatch.setattr("remote_dev.orchestrator.session.subprocess.run", lambda *args, **kwargs: Result())
    monkeypatch.setattr("remote_dev.orchestrator.session.request", lambda payload: payloads.append(payload) or {"ok": True})

    class Proxy:
        def start(self, target, identity):
            return None
    SessionManager(manager, Proxy(), Proxy()).start(TargetKey("host", 22, "u"), tmp_path / "key")

    assert payloads[-1]["proxy_available"] is True


def test_session_stop_unregisters_cleans_and_exits(monkeypatch, tmp_path: Path):
    commands = []

    class Result:
        returncode = 0
        stderr = ""
        stdout = ""

    monkeypatch.setattr("remote_dev.orchestrator.session.subprocess.run", lambda args, **kwargs: commands.append(args) or Result())
    monkeypatch.setattr("remote_dev.orchestrator.session.request", lambda payload: {"ok": True})
    manager = SessionManager(MasterManager(home=tmp_path))
    target = TargetKey("host", 22, "u")
    from remote_dev.orchestrator.session import ManagedSession
    session = ManagedSession(target, manager.master.control_path(target), "nonce")
    manager.stop(session)
    assert any("session.nonce" in part for command in commands for part in command)
    assert any("-O" in command and "exit" in command for command in commands)


def test_session_stop_tolerates_cleanup_timeout(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("remote_dev.orchestrator.session.request", lambda payload: {"ok": True})
    monkeypatch.setattr(
        "remote_dev.orchestrator.session.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired(args[0], 10)),
    )
    manager = SessionManager(MasterManager(home=tmp_path))
    from remote_dev.orchestrator.session import ManagedSession

    manager.stop(ManagedSession(TargetKey("host", 22, "u"), tmp_path / "cm", "nonce"))
