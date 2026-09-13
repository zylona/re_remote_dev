from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_codex_role_uses_official_staged_installer() -> None:
    defaults = (ROOT / "roles/codex/defaults/main.yml").read_text(encoding="utf-8")
    tasks = (ROOT / "roles/codex/tasks/main.yml").read_text(encoding="utf-8")
    assert "https://chatgpt.com/codex/install.sh" in defaults
    assert "ansible.builtin.get_url" in tasks
    assert "curl | sh" not in tasks
    assert "CODEX_NON_INTERACTIVE" in tasks


def test_login_command_uses_official_device_auth_and_forwarding() -> None:
    cli = (ROOT / "src/remote_dev/cli.py").read_text(encoding="utf-8")
    assert 'def codex_login(' in cli
    assert 'exec codex login --device-auth' in cli
    assert 'export http_proxy=http://127.0.0.1:' in cli
    assert '"RemoteForward=127.0.0.1:4227:127.0.0.1:4227"' in cli


def test_codex_role_deploys_non_blocking_lifecycle_shim() -> None:
    tasks = (ROOT / "roles/codex/tasks/main.yml").read_text(encoding="utf-8")
    shim = (ROOT / "roles/codex/templates/codex-shim.sh.j2").read_text(encoding="utf-8")
    sender = (ROOT / "roles/codex/files/remote-dev-codex-event.py").read_text(encoding="utf-8")
    assert "codex-shim.sh.j2" in tasks
    assert "remote-dev-codex-event.py" in tasks
    assert 'CODEX_START' in shim and 'CODEX_EXIT' in shim
    assert '\nexec "$real"' not in shim  # shim must report exit and preserve status
    assert 'timeout=0.15' in sender
    assert 'except (OSError, ValueError)' in sender
