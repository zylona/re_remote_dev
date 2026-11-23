from __future__ import annotations

import subprocess

from remote_dev import tssh
from remote_dev.tssh_reclaim import parse_scan, remote_command


def snapshot(*, listener_uid: int = 1000, owner_session: str = "12", extra_listener: str = "") -> str:
    rows = [
        "TSSH_RECLAIM\t1",
        "UID\t1000",
        f"L\t4227\t{listener_uid}\t{owner_session}",
        f"L\t4228\t{listener_uid}\t{owner_session}",
        "P\t24060\t0\t?\t7800\t12\tsshd-session",
        "P\t24067\t1000\t?\t7800\t12\tsshd-session",
    ]
    if extra_listener:
        rows.append(extra_listener)
    return "\n".join(rows)


def test_reclaim_remote_command_quotes_username_and_uses_one_sh_shell():
    command = remote_command("scan", "user name")
    assert command.startswith("sh -c ")
    assert "tssh-reclaim scan 'user name'" in command
    assert "ss -H -lntpe" in command


def test_parse_scan_recognizes_only_target_users_forwarding_sessions():
    result = parse_scan(snapshot())
    assert result.target_uid == 1000
    assert result.occupied_ports == (4227, 4228)
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert (candidate.session_id, candidate.pid, candidate.uid, candidate.ports) == (
        "12", 24067, 1000, (4227, 4228)
    )
    assert candidate.elapsed_seconds == 7800
    assert not result.blockers


def test_parse_scan_refuses_other_user_or_non_forwarding_session():
    other_user = parse_scan(snapshot(listener_uid=1001))
    assert not other_user.candidates
    assert other_user.blockers

    extra_port = parse_scan(snapshot(extra_listener="L\t9000\t1000\t12"))
    assert not extra_port.candidates
    assert "其他监听端口" in extra_port.blockers[0]

    with_tty = parse_scan(snapshot().replace("P\t24067\t1000\t?", "P\t24067\t1000\tpts/0"))
    assert not with_tty.candidates
    assert with_tty.blockers

    with_child = parse_scan(snapshot() + "\nP\t24100\t1000\t?\t7799\t12\tzsh")
    assert not with_child.candidates
    assert with_child.blockers


def test_tssh_reclaim_dry_run_never_terminates(monkeypatch, capsys):
    monkeypatch.setattr(
        tssh,
        "_resolve_ssh_target",
        lambda *_: tssh._ResolvedSSHTarget("host", "alice", 22, None),
    )
    calls: list[list[str]] = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=snapshot())

    monkeypatch.setattr(tssh.subprocess, "run", run)
    assert tssh.main(["reclaim", "alice@host", "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "session 12" in output
    assert "预览结束" in output
    assert len(calls) == 1


def test_tssh_reclaim_yes_rechecks_and_terminates_candidate(monkeypatch, capsys):
    monkeypatch.setattr(
        tssh,
        "_resolve_ssh_target",
        lambda *_: tssh._ResolvedSSHTarget("host", "alice", 22, None),
    )
    calls: list[list[str]] = []

    def run(command, **kwargs):
        calls.append(command)
        if len(calls) == 1:
            return subprocess.CompletedProcess(command, 0, stdout=snapshot())
        return subprocess.CompletedProcess(command, 0, stdout="TERMINATED\t12\t24067\n")

    monkeypatch.setattr(tssh.subprocess, "run", run)
    assert tssh.main(["reclaim", "alice@host", "--yes"]) == 0
    assert len(calls) == 2
    assert "terminate" in calls[1][-1]
    assert "已发送 TERM" in capsys.readouterr().out


def test_tssh_reclaim_refuses_to_act_when_any_owner_is_ambiguous(monkeypatch, capsys):
    monkeypatch.setattr(
        tssh,
        "_resolve_ssh_target",
        lambda *_: tssh._ResolvedSSHTarget("host", "alice", 22, None),
    )
    calls: list[list[str]] = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=snapshot(listener_uid=1001))

    monkeypatch.setattr(tssh.subprocess, "run", run)
    assert tssh.main(["reclaim", "alice@host", "--yes"]) == 1
    assert len(calls) == 1
    assert "没有结束任何进程" in capsys.readouterr().err
