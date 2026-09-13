from pathlib import Path

from remote_dev.ssh_integration import BEGIN, install, uninstall


def test_install_is_idempotent_and_preserves_config(tmp_path: Path) -> None:
    ssh = tmp_path / ".ssh"
    ssh.mkdir()
    config = ssh / "config"
    config.write_text("Host github.com\n  User git\n", encoding="utf-8")

    install(home=tmp_path)
    first = config.read_text(encoding="utf-8")
    install(home=tmp_path)
    second = config.read_text(encoding="utf-8")

    assert first == second
    assert "Host github.com" in second
    assert "RemoteForward 127.0.0.1:4227 127.0.0.1:4227" not in second
    assert "ControlMaster auto" in second
    assert second.count(BEGIN) == 1


def test_uninstall_removes_only_managed_files(tmp_path: Path) -> None:
    ssh = tmp_path / ".ssh"
    ssh.mkdir()
    config = ssh / "config"
    config.write_text("Host example\n  User me\n", encoding="utf-8")
    install(home=tmp_path)
    uninstall(home=tmp_path)
    assert config.read_text(encoding="utf-8") == "Host example\n  User me\n"
    assert not (tmp_path / ".config/remote-dev/ssh_config").exists()


def test_existing_inline_rule_is_preserved_without_external_include(tmp_path: Path) -> None:
    ssh = tmp_path / ".ssh"
    ssh.mkdir()
    config = ssh / "config"
    config.write_text(
        "Host * !github.com\n  RemoteForward 127.0.0.1:4227 127.0.0.1:4227\n",
        encoding="utf-8",
    )
    install(home=tmp_path)
    updated = config.read_text(encoding="utf-8")
    assert "RemoteForward 127.0.0.1:4227 127.0.0.1:4227" not in updated
    assert "LocalForward 127.0.0.1:1455 127.0.0.1:1455" not in updated


def test_old_managed_oauth_forward_is_migrated_to_manual_help_flow(tmp_path: Path) -> None:
    ssh = tmp_path / ".ssh"
    ssh.mkdir()
    config = ssh / "config"
    config.write_text(
        "# >>> remote-dev ssh integration >>>\n"
        "Host * !github.com\n"
        "  RemoteForward 127.0.0.1:4227 127.0.0.1:4227\n"
        "  LocalForward 127.0.0.1:1455 127.0.0.1:1455\n"
        "# <<< remote-dev ssh integration <<<\n",
        encoding="utf-8",
    )
    install(home=tmp_path)
    updated = config.read_text(encoding="utf-8")
    assert "LocalForward 127.0.0.1:1455 127.0.0.1:1455" not in updated
    assert "RemoteForward 127.0.0.1:4227 127.0.0.1:4227" not in updated


def test_remote_help_is_manual_and_generates_session_specific_oauth_command() -> None:
    script = Path("roles/user_shell/files/rd-help").read_text(encoding="utf-8")
    assert "rd-help codex" in script
    assert "SSH_CONNECTION" in script
    assert "-L 1455:127.0.0.1:1455" in script
    assert "ssh -fNT" in script
