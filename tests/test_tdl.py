from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def test_tdl_is_a_tmux_codex_launcher():
    script = ROOT / "scripts" / "tdl"
    text = script.read_text(encoding="utf-8")
    assert "tmux new-session" in text
    assert "tmux new-window" in text
    assert text.count("tmux split-window") >= 2
    assert "-h -p 60" in text
    assert "-v -p 25" in text
    assert "select-layout" in text
    assert "pane_id" in text
    assert "#{session_name}" in text
    assert '请先进入 tmux 窗口' in text
    assert 'TMUX:-' in text
    assert "tcodex" in text
    assert "TDL_TMUX_SESSION" in text
    subprocess.run(["bash", "-n", str(script)], check=True)


def test_remote_restore_deploys_tdl():
    tasks = (ROOT / "roles/user_shell/tasks/main.yml").read_text(encoding="utf-8")
    assert "部署 tmux Codex 快速启动入口" in tasks
    assert "scripts/tdl" in tasks

