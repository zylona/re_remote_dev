from pathlib import Path

from remote_dev.ssh_integration import BEGIN, VSCODE_BEGIN, VSCODE_END, install, install_legacy_hook, uninstall


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
    assert "ControlMaster no" not in second
    assert "PermitLocalCommand yes" not in second
    assert "remote-dev-ssh-hook" not in second
    assert second.count(BEGIN) == 0
    assert not (tmp_path / ".ssh/config-vscode").exists()


def test_legacy_hook_requires_explicit_opt_in(tmp_path: Path) -> None:
    ssh = tmp_path / ".ssh"
    ssh.mkdir()
    config = ssh / "config"
    config.write_text("Host example\n  User demo\n", encoding="utf-8")
    install(home=tmp_path)
    install_legacy_hook(home=tmp_path)
    updated = config.read_text(encoding="utf-8")
    assert updated.count(BEGIN) == 1
    assert "LocalCommand ~/.local/bin/remote-dev-ssh-hook" in updated


def test_unmarked_vscode_config_is_not_touched(tmp_path: Path) -> None:
    ssh = tmp_path / ".ssh"
    ssh.mkdir()
    (ssh / "config").write_text("Host example\n  User demo\n", encoding="utf-8")
    vscode = ssh / "config-vscode"
    vscode.write_text("Host personal\n  User me\n", encoding="utf-8")
    install(home=tmp_path)
    assert vscode.read_text(encoding="utf-8") == "Host personal\n  User me\n"


def test_uninstall_removes_managed_vscode_snapshot(tmp_path: Path) -> None:
    ssh = tmp_path / ".ssh"
    ssh.mkdir()
    (ssh / "config").write_text("Host example\n  User demo\n", encoding="utf-8")
    (ssh / "config-vscode").write_text(f"{VSCODE_BEGIN}\nHost example\n{VSCODE_END}\n", encoding="utf-8")
    install(home=tmp_path)
    uninstall(home=tmp_path)
    assert not (ssh / "config-vscode").exists()


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


def test_duplicate_managed_blocks_are_collapsed_to_one(tmp_path: Path) -> None:
    ssh = tmp_path / ".ssh"
    ssh.mkdir()
    config = ssh / "config"
    block = (
        f"{BEGIN}\nHost * !github.com\n"
        "  ControlMaster no\n  PermitLocalCommand yes\n"
        "# <<< remote-dev ssh integration <<<\n"
    )
    config.write_text(block + "Host * !github.com\n  User demo\n" + block, encoding="utf-8")

    install(home=tmp_path)
    updated = config.read_text(encoding="utf-8")
    assert updated.count(BEGIN) == 0
    assert "LocalCommand ~/.local/bin/remote-dev-ssh-hook %h %p %r" not in updated
    assert "ControlMaster no" not in updated
    assert "User demo" in updated


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
