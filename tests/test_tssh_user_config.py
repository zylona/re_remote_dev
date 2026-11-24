from pathlib import Path

import pytest

from remote_dev import lease_config, tssh


def test_proxy_config_defaults_without_network_probe(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert lease_config.load_proxy() == lease_config.ProxyEndpoint("127.0.0.1", 4227)


def test_proxy_config_write_is_user_private_and_preserves_lease_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config = tmp_path / "tssh" / "config"
    config.parent.mkdir()
    config.write_text("[lease]\nheartbeat_interval = 1\nlease_ttl = 3\n", encoding="utf-8")

    saved = lease_config.save_proxy(lease_config.ProxyEndpoint("localhost", 7890))

    assert lease_config.load_proxy() == lease_config.ProxyEndpoint("localhost", 7890)
    assert lease_config.load().heartbeat_interval == 1
    assert saved.stat().st_mode & 0o777 == 0o600
    assert "[lease]" in saved.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("host", "port"),
    [("", 4227), ("http://localhost", 4227), ("local host", 4227), ("localhost", 0), ("localhost", 65536)],
)
def test_proxy_config_rejects_invalid_values(host, port):
    with pytest.raises(ValueError):
        lease_config.save_proxy(lease_config.ProxyEndpoint(host, port))


def test_proxy_command_does_not_probe_endpoint(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(tssh, "local_proxy_available", lambda *_args, **_kwargs: pytest.fail("must not probe"), raising=False)

    assert tssh.main(["--proxy", "set", "127.0.0.1", "9999"]) == 0
    assert tssh.main(["--proxy", "show"]) == 0
    output = capsys.readouterr().out
    assert "127.0.0.1:9999" in output
    assert "不进行连通性检查" in output


def test_ansible_controller_downloads_follow_configured_proxy_source():
    defaults = {
        "roles/user_shell/defaults/main.yml": "user_shell_controller_proxy_url",
        "roles/tools/defaults/main.yml": "tools_controller_proxy_url",
        "roles/codex/defaults/main.yml": "codex_controller_proxy_url",
        "roles/nvim/defaults/main.yml": "nvim_controller_proxy_url",
    }
    for path, variable in defaults.items():
        text = Path(path).read_text(encoding="utf-8")
        line = next(line for line in text.splitlines() if line.startswith(f"{variable}:"))
        assert "remote_dev.proxy.host" in line
        assert "remote_dev.proxy.port" in line
        assert "http://127.0.0.1:4227" not in line
