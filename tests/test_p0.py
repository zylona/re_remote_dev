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


def test_debian_bootstrap_repairs_empty_or_deb822_apt_sources() -> None:
    tasks = (ROOT / "roles/bootstrap_runtime/tasks/main.yml").read_text()
    assert "没有有效网络源" in tasks
    assert "Types:" in tasks
    assert "URIs:" in tasks
    assert "apt-get -o Acquire::http::Timeout=20" in tasks


def test_tools_downloads_use_tls_compatible_curl_home() -> None:
    defaults = (ROOT / "roles/tools/defaults/main.yml").read_text()
    tasks = (ROOT / "roles/tools/tasks/main.yml").read_text()
    assert "tools_curl_bin" in defaults
    assert "urllib.request" in tasks
    assert "curl compatibility wrapper" in tasks
    assert tasks.count("PATH: \"{{ tools_curl_bin }}:") >= 2
    assert tasks.count("ansible.builtin.get_url:") >= 4


def test_tools_include_optional_btop_and_gpustat() -> None:
    defaults = (ROOT / "roles/tools/defaults/main.yml").read_text(encoding="utf-8")
    tasks = (ROOT / "roles/tools/tasks/main.yml").read_text(encoding="utf-8")
    assert "tools_optional_system_packages:" in defaults
    assert "- btop" in defaults
    assert "- eza" in defaults
    assert "tools_gpu_python_tool: gpustat" in defaults
    assert "安装可选系统工具" in tasks
    assert "使用 mise 安装 GPU 工具所需的 uv" in tasks
    assert "安装 gpustat 到用户工具目录" in tasks


def test_user_shell_provides_optional_eza_aliases() -> None:
    tasks = (ROOT / "roles/user_shell/tasks/main.yml").read_text(encoding="utf-8")
    assert "command -v eza" in tasks
    assert "alias ls='eza -lh --group-directories-first --icons=auto'" in tasks
    assert "alias lsa='ls -a'" in tasks
    assert "alias lt='eza --tree --level=2 --long --icons --git'" in tasks


def test_antidote_bootstrap_avoids_git_tls_path() -> None:
    tasks = (ROOT / "roles/user_shell/tasks/main.yml").read_text()
    assert "下载 Antidote release（控制端 Python HTTPS 客户端）" in tasks
    assert "delegate_to: localhost" in tasks
    assert "ansible.builtin.git:" not in tasks
    assert "extra_opts: [--strip-components=1]" in tasks


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
