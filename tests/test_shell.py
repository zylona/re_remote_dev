from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_shell_role_has_static_bundle_and_fallback():
    text = (ROOT / "roles/user_shell/tasks/main.yml").read_text()
    assert "antidote bundle" in text
    assert ".zsh_plugins.zsh" in text
    assert "ZSH_AUTOSUGGEST_STRATEGY=(history completion)" in text
    assert "HISTSIZE=50000" in text
    assert "HIST_FIND_NO_DUPS" in text
    assert "starship init zsh" in text


def test_shell_role_provides_omarchy_nvim_shortcut():
    text = (ROOT / "roles/user_shell/tasks/main.yml").read_text()
    assert "n()" in text
    assert "command nvim ." in text
    assert 'command nvim "$@"' in text


def test_shell_role_installs_remote_preview_helpers():
    text = (ROOT / "roles/user_shell/tasks/main.yml").read_text()
    assert "remote_preview_helper.py" in text
    assert "dest: \"{{ user_shell_home }}/.local/bin/rget\"" in text
    assert "dest: \"{{ user_shell_home }}/.local/bin/rdo\"" in text


def test_nvim_role_installs_remote_preview_mapping():
    task_text = (ROOT / "roles/nvim/tasks/main.yml").read_text()
    mapping = (ROOT / "roles/nvim/files/config/lua/plugins/remote-preview.lua").read_text()
    assert "lua/plugins/remote-preview.lua" in task_text
    assert '<S-o>' in mapping
    assert '"rdo"' in mapping


def test_uninstall_playbook_restores_backup():
    text = (ROOT / "playbooks/uninstall.yml").read_text()
    assert "zshrc.original" in text
    assert "state: absent" in text


def test_cli_defines_run_scoped_logging_and_apply_tunnel():
    text = (ROOT / "src/remote_dev/cli.py").read_text()
    assert "RUN_ID" in text
    assert "ReverseProxyTunnel" in text
    assert ".local/runs" in text
