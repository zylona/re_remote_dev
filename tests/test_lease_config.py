from remote_dev import lease_config


def test_default_lease_settings_are_aggressive_but_safe(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    settings = lease_config.load()
    assert settings.heartbeat_interval == 1
    assert settings.lease_ttl == 3


def test_user_can_override_lease_settings(monkeypatch, tmp_path):
    config_dir = tmp_path / "tssh"
    config_dir.mkdir()
    (config_dir / "config").write_text("[lease]\nheartbeat_interval = 2\nlease_ttl = 7\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    settings = lease_config.load()
    assert settings.heartbeat_interval == 2
    assert settings.lease_ttl == 7


def test_unsafe_ttl_is_raised_to_three_heartbeats(monkeypatch, tmp_path):
    config_dir = tmp_path / "tssh"
    config_dir.mkdir()
    (config_dir / "config").write_text("[lease]\nheartbeat_interval = 2\nlease_ttl = 2\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    settings = lease_config.load()
    assert settings.lease_ttl == 6
