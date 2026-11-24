from pathlib import Path
import json
from types import SimpleNamespace

from click import unstyle
from typer.testing import CliRunner

from remote_dev.cli import _local_service_matches, _run, app
from remote_dev.ssh_integration import install, install_target


def _plain_output(result) -> str:
    """Normalize Rich ANSI styling before asserting user-visible text."""
    return unstyle(result.output)


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
    output = _plain_output(result)
    assert "阶段 1/2：本地编排器服务安装" in output
    assert "远程恢复流程也未执行" in output
    assert "目标 IP 或主机名" not in output


def test_local_service_install_decline_is_standalone(monkeypatch) -> None:
    monkeypatch.setattr("remote_dev.cli._local_service_matches", lambda *_args, **_kwargs: False)
    result = CliRunner().invoke(app, ["local-service-install"], input="n\n")

    assert result.exit_code == 0
    output = _plain_output(result)
    assert "本地编排器服务安装" in output
    assert "已取消：本地服务未安装。" in output
    assert "远程恢复" not in output


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


def test_release_builder_bundles_every_shell_helper_installed_by_the_role():
    builder = Path("scripts/build_orchestrator_release.sh").read_text(encoding="utf-8")
    shell_tasks = Path("roles/user_shell/tasks/main.yml").read_text(encoding="utf-8")

    assert "remote_preview_helper.py" in shell_tasks
    assert '"$root_dir/scripts/remote_preview_helper.py"' in builder


def test_setup_uses_one_existing_sudo_user_and_never_provisions_accounts(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("remote_dev.cli._install_local_orchestrator", lambda **_kwargs: None)

    class FakeProxy:
        process = None
        proxy_url = "http://127.0.0.1:45000"

        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

    class FakeSettings:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr("remote_dev.proxy.ProxySettings", FakeSettings)
    monkeypatch.setattr("remote_dev.lease_config.load_proxy", lambda: SimpleNamespace(host="127.0.0.1", port=4227))
    monkeypatch.setattr("remote_dev.proxy.ReverseProxyTunnel", FakeProxy)
    runs = []

    def fake_run(playbook, inventory, **kwargs):
        content = inventory.read_text(encoding="utf-8")
        runs.append((playbook, content, kwargs))

    monkeypatch.setattr("remote_dev.cli._run", fake_run)
    result = CliRunner().invoke(
        app,
        ["setup"],
        input="host.example\n30112\nzylona\ny\nkey\n/home/test/.ssh/id_test\n",
    )

    assert result.exit_code == 0, result.output
    assert [run[0] for run in runs] == ["playbooks/site.yml"]
    playbook, inventory, kwargs = runs[0]
    assert "ansible_user: zylona" in inventory
    assert "target_user: zylona" in inventory
    assert "initial_user" not in inventory
    assert "manage_target_user_sudo" not in inventory
    assert "target_authorized_keys" not in inventory
    assert kwargs["secret_vars"] == {}
    assert kwargs["ask_become"] is True
    assert kwargs["extra_vars"]["ansible_become_method"] == "sudo"
    assert "不会创建或修改目标账号" in _plain_output(result)


def test_setup_password_auth_keeps_credentials_out_of_inventory(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("remote_dev.cli._install_local_orchestrator", lambda **_kwargs: None)
    monkeypatch.setattr("remote_dev.cli.shutil.which", lambda name: "/usr/bin/sshpass" if name == "sshpass" else None)

    class FakeProxy:
        process = None
        proxy_url = "http://127.0.0.1:45000"

        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def __enter__(self):
            assert self.kwargs["auth_password"] == "ssh-secret"
            return self

        def __exit__(self, *_args):
            pass

    monkeypatch.setattr("remote_dev.proxy.ProxySettings", lambda **_kwargs: object())
    monkeypatch.setattr("remote_dev.lease_config.load_proxy", lambda: SimpleNamespace(host="127.0.0.1", port=4227))
    monkeypatch.setattr("remote_dev.proxy.ReverseProxyTunnel", FakeProxy)
    runs = []
    monkeypatch.setattr("remote_dev.cli._run", lambda playbook, inventory, **kwargs: runs.append((playbook, inventory.read_text(), kwargs)))

    result = CliRunner().invoke(
        app,
        ["setup"],
        input="host.example\n22\nzylona\ny\npassword\nssh-secret\n",
    )

    assert result.exit_code == 0, result.output
    assert len(runs) == 1
    assert "ssh-secret" not in runs[0][1]
    assert runs[0][2]["secret_vars"] == {"ansible_password": "ssh-secret"}
    assert runs[0][2]["ask_become"] is True
    assert runs[0][2]["env"]["ANSIBLE_SSH_PASSWORD_MECHANISM"] == "sshpass"


def test_setup_key_mode_can_reuse_global_ssh_configuration(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("remote_dev.cli._install_local_orchestrator", lambda **_kwargs: None)

    class FakeProxy:
        process = None
        proxy_url = "http://127.0.0.1:45000"

        def __init__(self, **kwargs):
            assert kwargs["identity_file"] is None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

    monkeypatch.setattr("remote_dev.proxy.ProxySettings", lambda **_kwargs: object())
    monkeypatch.setattr("remote_dev.lease_config.load_proxy", lambda: SimpleNamespace(host="127.0.0.1", port=4227))
    monkeypatch.setattr("remote_dev.proxy.ReverseProxyTunnel", FakeProxy)
    runs = []
    monkeypatch.setattr("remote_dev.cli._run", lambda playbook, inventory, **kwargs: runs.append(inventory.read_text()))

    result = CliRunner().invoke(
        app,
        ["setup"],
        input="host.example\n22\nzylona\ny\nkey\n\n",
    )

    assert result.exit_code == 0, result.output
    assert len(runs) == 1
    assert "ansible_ssh_common_args" not in runs[0]
    assert "ansible_ssh_private_key_file" not in runs[0]


def test_setup_rejects_root_as_the_target_user(monkeypatch):
    monkeypatch.setattr("remote_dev.cli._install_local_orchestrator", lambda **_kwargs: None)
    result = CliRunner().invoke(app, ["setup"], input="host.example\n22\nroot\n")

    assert result.exit_code != 0
    assert "普通用户" in _plain_output(result)


def test_top_level_help_contains_manual_user_preparation_guide():
    # CI may force Rich colors, which inserts ANSI boundaries inside styled
    # command lines (for example between ``useradd`` and ``-m``).
    result = CliRunner().invoke(app, ["--help"], color=True)
    output = _plain_output(result)

    assert result.exit_code == 0
    assert "Debian / Ubuntu" in output
    assert "useradd -m" in output
    assert "目标用户准备" in output
    assert "user-guide" not in output


def test_main_playbook_does_not_create_or_modify_target_accounts():
    site = Path("playbooks/site.yml").read_text(encoding="utf-8")
    assert "target_user.yml" not in site
    assert not Path("roles/target_user/tasks/main.yml").exists()
    assert not Path("playbooks/setup-bootstrap.yml").exists()


def test_run_passes_secret_vars_through_private_ephemeral_file(monkeypatch, tmp_path: Path):
    captured = {}
    monkeypatch.setattr("remote_dev.cli._log_event", lambda *_args, **_kwargs: None)

    def fake_subprocess_run(command, **kwargs):
        captured["command"] = command
        secret_arg = command[command.index("--extra-vars") + 1]
        secret_path = Path(secret_arg.removeprefix("@"))
        captured["secrets"] = json.loads(secret_path.read_text())
        captured["secret_mode"] = secret_path.stat().st_mode & 0o777
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("remote_dev.cli.subprocess.run", fake_subprocess_run)
    _run("playbook.yml", tmp_path / "inventory.yml", secret_vars={"ansible_password": "hidden"})

    assert "hidden" not in " ".join(captured["command"])
    assert captured["secrets"] == {"ansible_password": "hidden"}
    assert captured["secret_mode"] == 0o600
    assert not list(tmp_path.glob("ansible-secrets-*.json"))


def test_runtime_prerequisites_keep_acl_and_do_not_bootstrap_sudo_privileges():
    runtime_defaults = Path("roles/bootstrap_runtime/defaults/main.yml").read_text(encoding="utf-8")
    runtime_tasks = Path("roles/bootstrap_runtime/tasks/main.yml").read_text(encoding="utf-8")
    assert "  - acl" in runtime_defaults
    assert "  - sudo" not in runtime_defaults
    assert "acl) c=\"setfacl\"" in runtime_tasks


def test_zoxide_install_is_skipped_when_target_binary_exists():
    tasks = Path("roles/tools/tasks/main.yml").read_text(encoding="utf-8")
    assert "检查目标端现有 zoxide 二进制" in tasks
    assert "register: tools_target_zoxide" in tasks
    assert tasks.count("not (tools_target_zoxide.stat.exists | default(false))") >= 3


def test_tssh_release_launcher_uses_its_bundled_python_runtime():
    builder = Path("scripts/build_orchestrator_release.sh").read_text(encoding="utf-8")
    installer = Path("release/install").read_text(encoding="utf-8")
    tssh = Path("src/remote_dev/tssh.py").read_text(encoding="utf-8")

    assert 'find "$base_dir/python" -type f -path \'*/bin/python3.14\'' in builder
    assert 'exec "$python_bin" "$@"' in builder
    assert 'exec "$base_dir/bin/tssh-python" -m remote_dev.tssh "$@"' in builder
    assert 'ln -sfn "$base/current/bin/tssh" "$bin_dir/tssh"' in installer
    assert 'command = [sys.executable, "-m", "remote_dev", "setup"]' in tssh


def test_codex_mise_install_has_bounded_retry_and_network_diagnostics():
    tasks = Path("roles/codex/tasks/main.yml").read_text(encoding="utf-8")
    assert 'argv: [timeout, "900", "{{ codex_mise_path }}", install, --verbose' in tasks
    assert 'MISE_HTTP_RETRIES: "8"' in tasks
    assert 'MISE_HTTP_DOWNLOAD_TIMEOUT: 900s' in tasks
    assert "codex_mise_install.stderr" in tasks
    assert "codex_legacy_before.stat.islnk" in tasks


def test_nvim_first_sync_retries_until_required_plugins_are_present():
    tasks = Path("roles/nvim/tasks/main.yml").read_text(encoding="utf-8")
    assert 'attempt=1' in tasks
    assert 'while [ "$attempt" -le 3 ]' in tasks
    assert 'nvim-treesitter/lua/nvim-treesitter/init.lua' in tasks
    assert 'markdown-preview.nvim/app/bin/markdown-preview-linux' in tasks
    assert '"+Lazy! restore"' in tasks
    assert "set -o pipefail" not in tasks
