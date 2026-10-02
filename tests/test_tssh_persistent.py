from pathlib import Path

from remote_dev import tssh


def test_persistent_command_uses_package_launcher_not_module_file(monkeypatch):
    monkeypatch.setattr(tssh.shutil, "which", lambda _name: None)
    command = tssh._persistent_command("user@example", str(Path("/tmp/id")), 22)

    # A systemd unit must start the package, not execute tssh.py directly:
    # direct execution makes its relative imports fail.
    assert command[:3] == ["/usr/bin/env", f"PYTHONPATH={Path(tssh.__file__).resolve().parents[1]}", tssh.sys.executable]
    assert command[3:6] == ["-m", "remote_dev.tssh", "--persistent-run"]
    assert command[5:8] == ["--persistent-run", "user@example", "--identity"]
    assert command[8] == "/tmp/id"


def test_persistent_command_prefers_release_wrapper(monkeypatch, tmp_path):
    wrapper = tmp_path / "tssh"
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
    wrapper.chmod(0o755)
    monkeypatch.setattr(tssh.shutil, "which", lambda _name: str(wrapper))
    command = tssh._persistent_command("user@example", None, 2200)

    # The release wrapper sets PYTHONPATH itself and is safe for systemd.
    assert command[0] == str(wrapper)
    assert command[1:3] == ["--persistent-run", "user@example"]
    assert command[-2:] == ["--port", "2200"]


def test_persistent_unit_is_boot_scoped_not_enabled(monkeypatch):
    command = ["/home/user/.local/bin/tssh", "--persistent-run", "u@h"]
    text = tssh._persistent_unit_text("h", command)
    assert "Before=shutdown.target" in text
    assert "Conflicts=shutdown.target" in text
    assert "KillMode=control-group" in text
    assert "TimeoutStopSec=8" in text
    assert "WantedBy=default.target" not in text


def test_release_installer_removes_legacy_persistent_enablement():
    text = (Path(__file__).parents[1] / "release/install").read_text(encoding="utf-8")
    assert 'default.target.wants' in text
    assert 'tssh-session-*.service' in text
    assert 'systemctl --user disable "$session_unit"' in text
