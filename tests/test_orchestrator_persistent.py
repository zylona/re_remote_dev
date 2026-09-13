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
