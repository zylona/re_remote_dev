from pathlib import Path

from remote_dev.orchestrator.forwarder import EventForwarder, ProxyForwarder
from remote_dev.orchestrator.model import TargetKey


def test_proxy_forwarder_isolated_command(tmp_path: Path):
    target = TargetKey("host", 22, "user")
    command = ProxyForwarder().command(target, tmp_path / "key")
    assert "ControlMaster=no" in command
    assert "ControlPath=none" in command
    assert "127.0.0.1:4227:127.0.0.1:4227" in command
    assert "-N" in command


def test_event_forwarder_uses_independent_event_channel(tmp_path: Path):
    target = TargetKey("host", 22, "user")
    command = EventForwarder().command(target, tmp_path / "key")
    assert "127.0.0.1:4228:127.0.0.1:4230" in command
    assert "127.0.0.1:4227" not in " ".join(command)


def test_forwarder_stop_is_graceful(monkeypatch):
    class Process:
        pid = 42
        def poll(self): return None
        def send_signal(self, signal): self.signal = signal
        def wait(self, timeout=None): return 0
    from remote_dev.orchestrator.forwarder import ForwardProcess
    forward = ForwardProcess(TargetKey("host", 22, "user"), "proxy", Process(), 4227, 4227)
    ProxyForwarder.stop(forward)
    assert forward.process.signal is not None
