from pathlib import Path

from remote_dev.orchestrator.master import MasterManager
from remote_dev.orchestrator.model import TargetKey


def test_master_commands_are_target_isolated(tmp_path: Path):
    manager = MasterManager(home=tmp_path)
    first = TargetKey("10.0.0.1", 22, "u", "key-a")
    second = TargetKey("10.0.0.2", 22, "u", "key-a")
    first_path = manager.control_path(first)
    assert first_path != manager.control_path(second)
    assert str(first_path).endswith(first.digest + "-u")
    command = manager.ensure_command(first, Path("~/.ssh/id_ed25519"))
    assert "ControlPersist=yes" in command
    forward = manager.forward_command(first)
    assert "127.0.0.1:4227:127.0.0.1:4227" in forward
    assert "127.0.0.1:4228:127.0.0.1:4230" in forward
    probe = manager.forwarding_probe_command(first)
    assert probe[-1].startswith("python3 -c '")
    assert "ports=(4227,4228)" in probe[-1]
    assert manager.check_command(first)[-1] == "u@10.0.0.1"


def test_control_path_uses_private_directory(tmp_path: Path):
    manager = MasterManager(home=tmp_path)
    target = TargetKey("host", 2222, "user", "fingerprint")
    assert manager.control_path(target).parent == tmp_path / ".ssh/remote-dev/cm"
