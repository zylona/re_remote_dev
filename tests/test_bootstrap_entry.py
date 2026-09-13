from pathlib import Path

from typer.testing import CliRunner

from remote_dev.cli import app
from remote_dev.ssh_integration import install, install_target


def test_target_ssh_block_precedes_generic_block(tmp_path: Path):
    install(home=tmp_path)
    install_target(host="vm.example", control_path=tmp_path / ".ssh/remote-dev/cm/digest", identity_file=Path("~/.ssh/id_test"), home=tmp_path)
    config = (tmp_path / ".ssh/config").read_text(encoding="utf-8")
    assert config.index("Host vm.example") < config.index("Host * !github.com")
    assert "ControlPath " + str(tmp_path / ".ssh/remote-dev/cm/digest") in config


def test_setup_decline_cancels_before_target_prompts() -> None:
    result = CliRunner().invoke(app, ["setup"], input="n\n")

    assert result.exit_code == 0
    assert "阶段 1/2：本地编排器服务安装" in result.output
    assert "远程恢复流程也未执行" in result.output
    assert "目标 IP 或主机名" not in result.output


def test_local_service_install_decline_is_standalone() -> None:
    result = CliRunner().invoke(app, ["local-service-install"], input="n\n")

    assert result.exit_code == 0
    assert "本地编排器服务安装" in result.output
    assert "已取消：本地服务未安装。" in result.output
    assert "远程恢复" not in result.output
