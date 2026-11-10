import ast
import shlex
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
    assert 'nonce="${RDO_NONCE:-}"' in shim
    assert 'export CODEX_HOME="${CODEX_HOME:-$codex_home}"' in shim
    assert 'auth_file="$CODEX_HOME/auth.json"' in shim
    assert 'if [ "$CODEX_HOME" = "$codex_home" ]; then' in shim
    assert '临时 CODEX_HOME 登录完成；跳过 daemon 重启。' in shim


def test_codex_skills_are_restored_via_mise_and_verified_read_only() -> None:
    defaults = (ROOT / "roles/codex_skills/defaults/main.yml").read_text(encoding="utf-8")
    tasks = (ROOT / "roles/codex_skills/tasks/main.yml").read_text(encoding="utf-8")
    verify = (ROOT / "roles/codex_skills/tasks/verify.yml").read_text(encoding="utf-8")
    docs = (ROOT / "docs/capabilities/codex-skills.md").read_text(encoding="utf-8")
    play = (ROOT / "playbooks/plays/40_codex.yml").read_text(encoding="utf-8")

    assert "Yuan1z0825/nature-skills" in defaults
    assert "https://github.com/xingtaxueshu/literature-review-skills" in defaults
    assert defaults.count("  - nature-") == 11
    assert "literature-review-skills" in defaults
    assert "npx" in tasks and "node@{{ codex_skills_node_version }}" in tasks
    assert "--global" in tasks and "--agent" in tasks and "--copy" in tasks
    assert "codex_skills_missing | intersect" in tasks
    assert "codex_skills_root" in verify and "SKILL.md" in verify
    assert "ansible.builtin.stat" in verify
    assert "ansible.builtin.command" not in verify
    assert "codex_skills" in play
    assert "npx --yes skills list --global --agent codex" in docs


def test_codexswitch_is_pinned_after_codex_and_node() -> None:
    defaults = (ROOT / "roles/codexswitch/defaults/main.yml").read_text(encoding="utf-8")
    tasks = (ROOT / "roles/codexswitch/tasks/main.yml").read_text(encoding="utf-8")
    verify = (ROOT / "roles/codexswitch/tasks/verify.yml").read_text(encoding="utf-8")
    play = (ROOT / "playbooks/plays/40_codex.yml").read_text(encoding="utf-8")
    standalone_verify = (ROOT / "playbooks/plays/90_verify.yml").read_text(encoding="utf-8")
    docs = (ROOT / "docs/capabilities/codexswitch.md").read_text(encoding="utf-8")
    shim = (ROOT / "roles/codex/templates/codex-shim.sh.j2").read_text(encoding="utf-8")

    assert 'codexswitch_version: "0.7.0"' in defaults
    assert 'codexswitch_package: "@carjms/codexswitch"' in defaults
    assert "node@{{ codexswitch_node_version }}" in tasks
    assert "codexswitch_codex_path" in tasks
    assert "npm" in tasks and "--ignore-scripts" in tasks
    assert "tools_offline" in tasks
    assert "codexswitch" in play and play.index("- codex") < play.index("- codexswitch")
    assert "codexswitch" in standalone_verify and "tasks_from: verify" in standalone_verify
    assert "ansible.builtin.command" in verify and "changed_when: false" in verify
    assert "tcodex login" in docs and "tcodex use" in docs and "cxs run" in docs
    assert 'help|-h|--help)' in shim
    assert 'login)' in shim and 'list|import|remove)' in shim and 'use)' in shim
    assert 'show_tcodex_help' in shim and 'tcodex remove <name>' in shim
    assert 'export CODEX_SWITCH_CODEX_BIN="$tcodex_bin"' in shim
    assert 'exec "$codexswitch_bin" login "$@"' not in shim
    assert 'REMOTE_DEV_CODEXSWITCH_CODEX_CHILD=1' in shim
    assert 'app-server daemon restart' in shim
    assert "CODEX_SWITCH_CODEX_BIN" not in (ROOT / "roles/user_shell/tasks/main.yml").read_text(encoding="utf-8")


def test_cc_switch_cli_is_not_in_recovery_flow() -> None:
    play = (ROOT / "playbooks/plays/40_codex.yml").read_text(encoding="utf-8")
    verify = (ROOT / "playbooks/plays/90_verify.yml").read_text(encoding="utf-8")
    assert "cc_switch" not in play
    assert "cc_switch" not in verify
    assert not (ROOT / "roles/cc_switch").exists()
    assert not (ROOT / "docs/capabilities/cc-switch-cli.md").exists()


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
    assert 'CodexSwitch is intentionally not injected' in shim
    assert 'OAuth/callback entrypoint' in shim
    assert '"$real" login status' in shim
    assert 'auth.json' in shim
    assert '"$@" --no-daemon' in shim
    assert 'if [ "${1-}" != "login" ]; then' in shim
    assert 'OAuth 回调未完成' in shim
    assert '"$real" login status' in shim
    assert 'app-server daemon restart' in shim


def test_tcodex_login_and_rejects_removed_cxs_alias(tmp_path: Path) -> None:
    template = (ROOT / "roles/codex/templates/codex-shim.sh.j2").read_text(encoding="utf-8")
    home = tmp_path / "home"
    codex_home = home / ".codex"
    libexec = home / ".local/libexec"
    local_bin = home / ".local/bin"
    nonce_file = home / ".config/remote-dev/session.nonce"
    codex_home.mkdir(parents=True)
    libexec.mkdir(parents=True)
    local_bin.mkdir(parents=True)
    fake_codex = tmp_path / "codex"
    fake_codex.write_text(
        "#!/bin/sh\n"
        "case \"$1\" in\n"
        "  --version) echo 'codex fake'; exit 0 ;;\n"
        "  login) mkdir -p \"$CODEX_HOME\"; printf '{}' > \"$CODEX_HOME/auth.json\"; exit 0 ;;\n"
        "  app-server) echo daemon-restarted >> \"$HOME/codex.log\"; exit 0 ;;\n"
        "  esac\n"
        "  if [ \"$1\" = status ]; then test -s \"$CODEX_HOME/auth.json\"; exit; fi\n"
        "  exit 0\n",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    shim = tmp_path / "tcodex"
    (local_bin / "mise").write_text("#!/bin/sh\nshift; shift; exec \"$@\"\n", encoding="utf-8")
    (local_bin / "mise").chmod(0o755)
    fake_codexswitch = local_bin / "codexswitch"
    fake_codexswitch.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$CODEX_SWITCH_CODEX_BIN|$*\" > \"$HOME/codexswitch.log\"\n",
        encoding="utf-8",
    )
    fake_codexswitch.chmod(0o755)
    replacements = {
        "{{ codex_real_binary | quote }}": shlex.quote(str(fake_codex)),
        "{{ codex_home | quote }}": shlex.quote(str(codex_home)),
        "{{ codex_shim | quote }}": shlex.quote(str(shim)),
        "{{ codex_libexec_dir | quote }}": shlex.quote(str(libexec)),
        "{{ codex_target_digest | default('unknown') | quote }}": "'test-target'",
        "{{ codex_nonce_file | default(ansible_facts.user_dir ~ '/.config/remote-dev/session.nonce') | quote }}": shlex.quote(str(nonce_file)),
    }
    for source, replacement in replacements.items():
        template = template.replace(source, replacement)
    shim.write_text(template, encoding="utf-8")
    shim.chmod(0o755)

    import os
    import subprocess

    env = {**os.environ, "HOME": str(home), "CODEX_HOME": str(tmp_path / "codexswitch-login-tmp")}
    help_result = subprocess.run([str(shim), "help"], env=env, text=True, capture_output=True, check=False)
    login = subprocess.run(
        [str(shim), "login"],
        env={**env, "REMOTE_DEV_CODEXSWITCH_CODEX_CHILD": "1"},
        text=True,
        capture_output=True,
        check=False,
    )
    removed_alias = subprocess.run(
        [str(shim), "cxs", "login", "work"], env=env, text=True, capture_output=True, check=False
    )

    assert help_result.returncode == 0 and "tcodex login <name>" in help_result.stdout
    assert login.returncode == 0
    assert (tmp_path / "codexswitch-login-tmp/auth.json").is_file()
    assert "临时 CODEX_HOME 登录完成；跳过 daemon 重启。" in login.stderr
    assert not (home / "codex.log").exists()
    assert removed_alias.returncode == 2
    assert "未知命令或参数：cxs" in removed_alias.stderr
    assert "tcodex help" in removed_alias.stderr
    assert not (home / "codexswitch.log").exists()


def test_tcodex_account_commands_restart_daemon_only_after_successful_use(tmp_path: Path) -> None:
    template = (ROOT / "roles/codex/templates/codex-shim.sh.j2").read_text(encoding="utf-8")
    home = tmp_path / "home"
    codex_home = home / ".codex"
    libexec = home / ".local/libexec"
    local_bin = home / ".local/bin"
    mise_bin = local_bin / "mise"
    codex_home.mkdir(parents=True)
    libexec.mkdir(parents=True)
    local_bin.mkdir(parents=True)
    (home / "calls.log").touch()
    fake_codex = local_bin / "codex"
    fake_codex.write_text(
        "#!/bin/sh\n"
        "printf 'codex:%s\\n' \"$*\" >> \"$HOME/calls.log\"\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    mise_bin.write_text(
        "#!/bin/sh\n"
        "shift\n"
        "shift\n"
        "exec \"$@\"\n",
        encoding="utf-8",
    )
    mise_bin.chmod(0o755)
    fake_cxs = local_bin / "codexswitch"
    fake_cxs.write_text(
        "#!/bin/sh\n"
        "printf 'cxs:%s\\n' \"$*\" >> \"$HOME/calls.log\"\n"
        "[ \"${CXS_FAIL:-0}\" = 0 ]\n",
        encoding="utf-8",
    )
    fake_cxs.chmod(0o755)
    shim = home / ".local/share/remote-dev/bin/tcodex"
    shim.parent.mkdir(parents=True)
    replacements = {
        "{{ codex_real_binary | quote }}": shlex.quote(str(fake_codex)),
        "{{ codex_home | quote }}": shlex.quote(str(codex_home)),
        "{{ codex_shim | quote }}": shlex.quote(str(shim)),
        "{{ codex_libexec_dir | quote }}": shlex.quote(str(libexec)),
        "{{ codex_target_digest | default('unknown') | quote }}": "'test-target'",
        "{{ codex_nonce_file | default(ansible_facts.user_dir ~ '/.config/remote-dev/session.nonce') | quote }}": shlex.quote(str(home / "nonce")),
    }
    for source, replacement in replacements.items():
        template = template.replace(source, replacement)
    shim.write_text(template, encoding="utf-8")
    shim.chmod(0o755)

    import os
    import subprocess

    env = {**os.environ, "HOME": str(home), "CODEX_HOME": str(codex_home)}
    use = subprocess.run([str(shim), "use", "work"], env=env, text=True, capture_output=True, check=False)
    failed_use = subprocess.run(
        [str(shim), "use", "missing"], env={**env, "CXS_FAIL": "1"}, text=True, capture_output=True, check=False
    )
    listing = subprocess.run([str(shim), "list"], env=env, text=True, capture_output=True, check=False)
    importing = subprocess.run([str(shim), "import", "work"], env=env, text=True, capture_output=True, check=False)
    removing = subprocess.run([str(shim), "remove", "old"], env=env, text=True, capture_output=True, check=False)
    help_result = subprocess.run([str(shim), "help"], env=env, text=True, capture_output=True, check=False)

    assert use.returncode == 0
    assert "daemon restart" in (home / "calls.log").read_text(encoding="utf-8")
    calls = (home / "calls.log").read_text(encoding="utf-8").splitlines()
    assert calls[:2] == ["cxs:use work", "codex:app-server daemon restart"]
    assert failed_use.returncode != 0
    assert calls.count("codex:app-server daemon restart") == 1
    assert listing.returncode == 0 and "cxs:list" in calls
    assert importing.returncode == 0 and "cxs:import work" in calls
    assert removing.returncode == 0 and calls[-1] == "cxs:remove old"
    assert calls.count("codex:app-server daemon restart") == 1
    assert help_result.returncode == 0
    assert "tcodex login <name>" in help_result.stdout
    assert "tcodex remove <name>" in help_result.stdout
    assert "daemon" in help_result.stdout
    assert "cxs:help" not in (home / "calls.log").read_text(encoding="utf-8")


def test_tcodex_login_routes_nested_codex_binary_without_recursing(tmp_path: Path) -> None:
    template = (ROOT / "roles/codex/templates/codex-shim.sh.j2").read_text(encoding="utf-8")
    home = tmp_path / "home"
    local_bin = home / ".local/bin"
    libexec = home / ".local/libexec"
    codex_home = home / ".codex"
    local_bin.mkdir(parents=True)
    libexec.mkdir(parents=True)
    codex_home.mkdir(parents=True)
    fake_codex = local_bin / "codex"
    fake_codex.write_text(
        "#!/bin/sh\n"
        "printf 'official:%s:%s\\n' \"$*\" \"${REMOTE_DEV_CODEXSWITCH_CODEX_CHILD:-}\" >> \"$HOME/calls.log\"\n"
        "if [ \"$1\" = login ]; then mkdir -p \"$CODEX_HOME\"; printf '{}' > \"$CODEX_HOME/auth.json\"; fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    fake_cxs = local_bin / "codexswitch"
    fake_cxs.write_text(
        "#!/bin/sh\n"
        "printf 'manager:%s:%s\\n' \"$*\" \"$CODEX_SWITCH_CODEX_BIN\" >> \"$HOME/calls.log\"\n"
        "CODEX_HOME=\"$HOME/.codex-switch-login\" \"$CODEX_SWITCH_CODEX_BIN\" login\n",
        encoding="utf-8",
    )
    fake_cxs.chmod(0o755)
    (local_bin / "mise").write_text("#!/bin/sh\nshift; shift; exec \"$@\"\n", encoding="utf-8")
    (local_bin / "mise").chmod(0o755)
    shim = home / ".local/share/remote-dev/bin/tcodex"
    shim.parent.mkdir(parents=True)
    replacements = {
        "{{ codex_real_binary | quote }}": shlex.quote(str(fake_codex)),
        "{{ codex_home | quote }}": shlex.quote(str(codex_home)),
        "{{ codex_shim | quote }}": shlex.quote(str(shim)),
        "{{ codex_libexec_dir | quote }}": shlex.quote(str(libexec)),
        "{{ codex_target_digest | default('unknown') | quote }}": "'test-target'",
        "{{ codex_nonce_file | default(ansible_facts.user_dir ~ '/.config/remote-dev/session.nonce') | quote }}": shlex.quote(str(home / "nonce")),
    }
    for source, replacement in replacements.items():
        template = template.replace(source, replacement)
    shim.write_text(template, encoding="utf-8")
    shim.chmod(0o755)

    import os
    import subprocess

    result = subprocess.run([str(shim), "login", "personal"], env={**os.environ, "HOME": str(home)}, text=True, capture_output=True, check=False)
    calls = (home / "calls.log").read_text(encoding="utf-8").splitlines()
    assert result.returncode == 0
    assert calls[0].startswith("manager:login personal:")
    assert calls[1] == "official:login:1"
    assert (home / ".codex-switch-login/auth.json").is_file()


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
