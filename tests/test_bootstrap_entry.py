from pathlib import Path

from typer.testing import CliRunner

from remote_dev.cli import _local_service_matches, app
from remote_dev.ssh_integration import install, install_target


def test_target_install_migrates_legacy_block_without_adding_device_config(tmp_path: Path):
    (tmp_path / ".ssh").mkdir()
    (tmp_path / ".ssh/config").write_text("Host example\n  User demo\n", encoding="utf-8")
    install(home=tmp_path)
    install_target(host="vm.example", control_path=tmp_path / ".ssh/remote-dev/cm/digest", identity_file=Path("~/.ssh/id_test"), home=tmp_path)
    config = (tmp_path / ".ssh/config").read_text(encoding="utf-8")
    assert "Host vm.example" not in config
    assert "LocalCommand ~/.local/bin/remote-dev-ssh-hook" not in config


def test_setup_decline_cancels_before_target_prompts(monkeypatch) -> None:
    monkeypatch.setattr("remote_dev.cli._local_service_matches", lambda *_args, **_kwargs: False)
    result = CliRunner().invoke(app, ["setup"], input="n\n")

    assert result.exit_code == 0
    assert "阶段 1/2：本地编排器服务安装" in result.output
    assert "远程恢复流程也未执行" in result.output
    assert "目标 IP 或主机名" not in result.output


def test_local_service_install_decline_is_standalone(monkeypatch) -> None:
    monkeypatch.setattr("remote_dev.cli._local_service_matches", lambda *_args, **_kwargs: False)
    result = CliRunner().invoke(app, ["local-service-install"], input="n\n")

    assert result.exit_code == 0
    assert "本地编排器服务安装" in result.output
    assert "已取消：本地服务未安装。" in result.output
    assert "远程恢复" not in result.output


def test_local_service_current_check_is_content_based(tmp_path: Path) -> None:
    project_root = Path(__file__).parents[1]
    service_dir = tmp_path / ".config/systemd/user"
    home = tmp_path
    assert not _local_service_matches(project_root, service_dir, home)
    service_dir.mkdir(parents=True)
    (home / ".local/bin").mkdir(parents=True)
    service_text = (project_root / "systemd/tssh.service").read_text(encoding="utf-8").replace(
        "ExecStart=tssh-server", f"ExecStart={project_root / 're-remote'} orchestrator-server"
    )
    (service_dir / "tssh.service").write_text(service_text, encoding="utf-8")
    (service_dir / "tssh.socket").write_text((project_root / "systemd/tssh.socket").read_text(encoding="utf-8"), encoding="utf-8")
    (home / ".local/bin/remote-dev-ssh-hook").write_text((project_root / "scripts/remote_dev_ssh_hook.py").read_text(encoding="utf-8"), encoding="utf-8")
    (home / ".local/bin/tdl").write_text((project_root / "scripts/tdl").read_text(encoding="utf-8"), encoding="utf-8")
    assert _local_service_matches(project_root, service_dir, home)


def test_runtime_bootstrap_preserves_global_ssh_config_for_keyless_key_mode():
    playbook = Path("playbooks/plays/05_bootstrap_runtime.yml").read_text(encoding="utf-8")
    assert "default('', true)" in playbook
    assert "-F/dev/null" in playbook
    assert "remote_dev.ssh_private_key_file" in playbook
