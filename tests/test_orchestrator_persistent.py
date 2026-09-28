from pathlib import Path

from remote_dev.orchestrator.model import TargetKey
from remote_dev.orchestrator.persistent import unit_name, unit_text


def test_persistent_unit_is_target_isolated_and_uses_same_lifecycle(tmp_path: Path):
    target = TargetKey("host", 2222, "user")
    name = unit_name(target)
    text = unit_text(target, tmp_path / "id_ed25519")
    assert target.digest in name
    assert "remote-dev orchestrator connect" in text
    assert "--persistent" in text
    assert "Restart=on-failure" in text
    assert "RestartSec=30" in text
    assert "StartLimitBurst=10" in text
    assert "id_ed25519" in text


def test_endpoint_proxy_unit_is_not_enabled_by_default(tmp_path: Path):
    from remote_dev.orchestrator.proxy_persistent import unit_text

    target = TargetKey("host", 22, "user")
    text = unit_text(target, tmp_path / "id_ed25519")
    assert "CollectMode=inactive-or-failed" in text
    assert "WantedBy=default.target" not in text


def test_endpoint_proxy_only_unit_omits_codex_event_forward(tmp_path: Path):
    from remote_dev.orchestrator.proxy_persistent import unit_text

    target = TargetKey("host", 22, "user")
    text = unit_text(target, tmp_path / "id_ed25519", event_forward=False)
    assert "127.0.0.1:4227:127.0.0.1:4227" in text
    assert "127.0.0.1:4228:127.0.0.1:4230" not in text
