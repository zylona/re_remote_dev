import ast
from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_codex_role_uses_official_staged_installer() -> None:
    defaults = (ROOT / "roles/codex/defaults/main.yml").read_text(encoding="utf-8")
    tasks = (ROOT / "roles/codex/tasks/main.yml").read_text(encoding="utf-8")
    shim = (ROOT / "roles/codex/templates/codex-shim.sh.j2").read_text(encoding="utf-8")
    assert "https://chatgpt.com/codex/install.sh" in defaults
    assert "ansible.builtin.get_url" in tasks
    assert "curl | sh" not in tasks
    assert "CODEX_NON_INTERACTIVE" in tasks
    assert "codex_mise_version" in defaults
    assert "使用 mise 安装完整 Codex package" in tasks
    assert "codex-package.json" in tasks
    assert "codex_controller_path" not in defaults
    assert 'nonce="$RDO_NONCE"' in shim


def test_cc_switch_cli_is_pinned_and_user_scoped() -> None:
    defaults = (ROOT / "roles/cc_switch/defaults/main.yml").read_text(encoding="utf-8")
    tasks = (ROOT / "roles/cc_switch/tasks/main.yml").read_text(encoding="utf-8")
    docs = (ROOT / "docs/capabilities/cc-switch-cli.md").read_text(encoding="utf-8")
    assert 'cc_switch_cli_version: "5.11.2"' in defaults
    assert "checksum: \"sha256:{{ cc_switch_cli_sha256 }}\"" in tasks
    assert "cc_switch_cli_asset_name" in defaults
    assert "cc_switch_cli_release_base_url" in defaults
    assert 'url: "{{ cc_switch_cli_release_base_url }}/{{ cc_switch_cli_asset_name }}"' in tasks
    assert "cc_switch_cli_install_dir: \"{{ ansible_facts.user_dir }}/.local/bin\"" in defaults
    assert "cc_switch_cli_wrapper" not in defaults
    assert "清理旧版 CC Switch CLI 包装器" in tasks
    assert "cc-switch auth list" in docs
    assert "cc-switch auth status" in docs
    assert "cc-switch\n" in docs
    assert "启动 TUI" in docs
    assert "Set default" in docs
    assert "--shared-sessions" in docs
    assert "项目不修改其二进制" in docs
    assert "provider quota" in docs
    assert "codex logout" in docs


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
    assert 'cc-switch start codex default' not in shim
    assert 'CC Switch is intentionally not injected' in shim
    assert 'OAuth/callback entrypoint' in shim
    assert '"$real" login status' in shim
    assert 'auth.json' in shim


def test_codex_role_exposes_installed_binary_on_user_path() -> None:
    tasks = (ROOT / "roles/codex/tasks/main.yml").read_text(encoding="utf-8")
    assert "创建 Codex 官方 PATH 入口" in tasks
    assert 'dest: "{{ codex_install_dir }}/codex"' in tasks
    assert "state: link" in tasks
    assert 'src: "{{ codex_real_binary }}"' in tasks


def test_codex_lifecycle_entrypoint_is_separate_from_official_codex() -> None:
    defaults = (ROOT / "roles/codex/defaults/main.yml").read_text(encoding="utf-8")
    shell = (ROOT / "roles/user_shell/tasks/main.yml").read_text(encoding="utf-8")
    shim = (ROOT / "roles/codex/templates/codex-shim.sh.j2").read_text(encoding="utf-8")
    assert 'codex_wrapper_dir:' in defaults
    assert 'codex_shim: "{{ codex_wrapper_dir }}/tcodex"' in defaults
    assert 'remote-dev/bin:$HOME/.local/bin' in shell
    assert 'official `codex` command remains untouched' in shim
    assert 'packages/standalone/current/bin/codex' in shim
    assert 'normal Codex self-updates' in shim


def _load_oauth_parser():
    """Load only the PTY parser definitions without starting a child PTY."""
    source = (ROOT / "roles/codex/files/remote-dev-codex-pty.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {"oauth_prefix", "oauth_candidate_re", "osc_hyperlink_re", "ansi_re"}
    nodes = [
        node
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.Assign, ast.FunctionDef))
        and (
            not isinstance(node, ast.Assign)
            or any(isinstance(target, ast.Name) and target.id in names for target in node.targets)
        )
    ]
    namespace: dict[str, object] = {}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "codex-pty-parser", "exec"), namespace)
    return namespace["_complete_oauth_urls"]


def test_codex_pty_parser_accepts_encoded_raw_and_osc8_urls() -> None:
    parse_urls = _load_oauth_parser()
    query = (
        "response_type=code&client_id=app_test&"
        "redirect_uri={redirect}&scope=openid&code_challenge={challenge}&"
        "code_challenge_method=S256&state={state}"
    )
    challenge = "x" * 43
    state = "s" * 43
    encoded = "https://auth.openai.com/oauth/authorize?" + query.format(
        redirect="http%3A%2F%2Flocalhost%3A1455%2Fauth%2Fcallback", challenge=challenge, state=state
    )
    raw = encoded.replace("localhost%3A1455", "localhost:1455").replace("&", "&\n")
    osc = f"\x1b]8;;{encoded}\x1b\\".encode()
    assert parse_urls(encoded.encode()) == [encoded]
    assert parse_urls(raw.encode()) == [raw.replace("\n", "")]
    assert parse_urls(osc) == [encoded]


def test_codex_pty_parser_accepts_new_codex_loopback_redirect() -> None:
    parse_urls = _load_oauth_parser()
    url = (
        "https://auth.openai.com/oauth/authorize?response_type=code&client_id=app_test&"
        "redirect_uri=http%3A%2F%2F127.0.0.1%3A1455%2Fauth%2Fcallback&"
        "code_challenge=" + "x" * 43 + "&code_challenge_method=S256&state=" + "s" * 43 +
        "&originator=codex_cli_rs"
    )
    assert parse_urls(url.encode()) == [url]
    # Codex can redraw the next help text directly after the URL when PTY
    # line breaks are normalized; that text must not leak into the browser URL.
    assert parse_urls((url + "On a remote or headless machine").encode()) == [url]
