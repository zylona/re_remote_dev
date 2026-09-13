from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_p0_files_exist() -> None:
    expected = [
        "ansible.cfg",
        "requirements.yml",
        "pyproject.toml",
        "mise.toml",
        "install-local-service",
        "inventory/example.yml",
        "playbooks/site.yml",
        "playbooks/verify.yml",
    ]
    assert all((ROOT / path).is_file() for path in expected)


def test_local_service_installer_uses_unified_entrypoint() -> None:
    installer = ROOT / "install-local-service"
    assert installer.stat().st_mode & 0o111
    assert 'exec "$SCRIPT_DIR/re-remote" local-service-install "$@"' in installer.read_text()


def test_secret_paths_are_ignored() -> None:
    gitignore = (ROOT / ".gitignore").read_text()
    assert ".local/" in gitignore
    assert "inventory/production.yml" in gitignore


def test_plays_do_not_override_detected_become_method() -> None:
    """The single prompted password must remain valid for every play."""
    for play in (ROOT / "playbooks/plays").glob("*.yml"):
        assert "become_method:" not in play.read_text(), play


def test_docker_proxy_is_systemd_managed_and_idempotent() -> None:
    tasks = (ROOT / "roles/docker_proxy/tasks/main.yml").read_text()
    handlers = (ROOT / "roles/docker_proxy/handlers/main.yml").read_text()
    site = (ROOT / "playbooks/site.yml").read_text()

    assert "HTTP_PROXY={{ docker_proxy_url }}" in tasks
    assert "HTTPS_PROXY={{ docker_proxy_url }}" in tasks
    assert "ansible.builtin.copy:" in tasks
    assert "state: restarted" in handlers
    assert "when: docker_proxy_service_active.stdout" in handlers
    assert "plays/15_docker_proxy.yml" in site
