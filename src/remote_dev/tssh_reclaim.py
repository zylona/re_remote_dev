"""Read-only discovery and guarded cleanup of stale tssh remote forwards."""

from __future__ import annotations

from dataclasses import dataclass
import shlex


REMOTE_RECLAIM_SCRIPT = r'''\
set -eu
operation=$1
target_user=$2

scan() {
    command -v ss >/dev/null 2>&1 || { echo 'ERROR\tss (iproute2) is required' >&2; return 20; }
    command -v ps >/dev/null 2>&1 || { echo 'ERROR\tps (procps) is required' >&2; return 20; }
    target_uid=$(id -u -- "$target_user") || { echo 'ERROR\tremote user does not exist' >&2; return 21; }
    printf 'TSSH_RECLAIM\t1\nUID\t%s\n' "$target_uid"
    ss -H -lntpe 2>/dev/null | awk '
        $1 == "LISTEN" {
            address = $4
            sub(/^.*:/, "", address)
            if (address !~ /^[0-9]+$/) next
            uid = "-"
            cgroup = "-"
            for (i = 6; i <= NF; i++) {
                if ($i ~ /^uid:/) { uid = $i; sub(/^uid:/, "", uid) }
                if ($i ~ /^cgroup:/) { cgroup = $i; sub(/^cgroup:/, "", cgroup) }
            }
            session = cgroup
            sub(/^.*session-/, "", session)
            sub(/\.scope.*$/, "", session)
            if (session !~ /^[0-9]+$/) session = "-"
            printf "L\t%s\t%s\t%s\n", address, uid, session
        }
    '
    ps -ww -eo pid=,uid=,tty=,etimes=,cgroup=,comm= 2>/dev/null | awk -v target_uid="$target_uid" '
        $2 == target_uid || $6 == "sshd" || $6 == "sshd-session" {
            session = $5
            sub(/^.*session-/, "", session)
            sub(/\.scope.*$/, "", session)
            if (session !~ /^[0-9]+$/) session = "-"
            printf "P\t%s\t%s\t%s\t%s\t%s\t%s\n", $1, $2, $3, $4, session, $6
        }
    '
}

if [ "$operation" = scan ]; then
    scan
    exit $?
fi

if [ "$operation" = terminate ]; then
    session_id=$3
    expected_pid=$4
    expected_uid=$5
    case "$session_id" in ''|*[!0-9]*) echo 'ERROR\tinvalid session id' >&2; exit 22 ;; esac
    case "$expected_pid" in ''|*[!0-9]*) echo 'ERROR\tinvalid process id' >&2; exit 22 ;; esac
    case "$expected_uid" in ''|*[!0-9]*) echo 'ERROR\tinvalid user id' >&2; exit 22 ;; esac
    actual_uid=$(id -u -- "$target_user") || exit 21
    [ "$actual_uid" = "$expected_uid" ] || { echo 'ERROR\tremote user identity changed' >&2; exit 23; }

    # Recheck the entire session immediately before signaling it. Only the
    # two sshd processes of a no-tty forwarding-only session are eligible.
    ps -ww -eo pid=,uid=,tty=,cgroup=,comm= 2>/dev/null | awk \
        -v sid="$session_id" -v pid="$expected_pid" -v uid="$expected_uid" '
        {
            cgroup = $4
            if (index(cgroup, "session-" sid ".scope") == 0) next
            count++
            if (($5 != "sshd" && $5 != "sshd-session") || $3 != "?") bad = 1
            if ($1 == pid && $2 == uid) found = 1
        }
        END { exit !(count == 2 && !bad && found) }
    ' || { echo 'ERROR\tsession changed or is not forwarding-only' >&2; exit 24; }

    ss -H -lntpe 2>/dev/null | awk \
        -v sid="$session_id" '
        $1 == "LISTEN" {
            address = $4
            sub(/^.*:/, "", address)
            cgroup = "-"
            for (i = 6; i <= NF; i++) if ($i ~ /^cgroup:/) { cgroup = $i; sub(/^cgroup:/, "", cgroup) }
            if (index(cgroup, "session-" sid ".scope") == 0) next
            count++
            if (address != "4227" && address != "4228") bad = 1
        }
        END { exit !(count > 0 && !bad) }
    ' || { echo 'ERROR\tforward ports changed or session no longer owns only 4227/4228' >&2; exit 25; }

    kill -TERM "$expected_pid" || { echo 'ERROR\tcould not signal SSH session' >&2; exit 26; }
    printf 'TERMINATED\t%s\t%s\n' "$session_id" "$expected_pid"
    exit 0
fi

echo 'ERROR\tunknown operation' >&2
exit 22
'''


@dataclass(frozen=True)
class ReclaimCandidate:
    session_id: str
    pid: int
    uid: int
    ports: tuple[int, ...]
    elapsed_seconds: int


@dataclass(frozen=True)
class ReclaimScan:
    target_uid: int | None
    occupied_ports: tuple[int, ...]
    candidates: tuple[ReclaimCandidate, ...]
    blockers: tuple[str, ...]


def remote_command(operation: str, username: str, *args: str) -> str:
    """Build the one-shot remote shell command without embedding credentials."""
    argv = ["sh", "-c", REMOTE_RECLAIM_SCRIPT, "tssh-reclaim", operation, username, *args]
    return shlex.join(argv)


def parse_scan(output: str, *, expected_uid: int | None = None) -> ReclaimScan:
    """Parse the deliberately small, tab-separated remote scan response."""
    target_uid: int | None = None
    listeners: list[tuple[int, str, str]] = []
    processes: dict[str, list[tuple[int, int, str, int, str]]] = {}
    valid_header = False
    for line in output.splitlines():
        fields = line.split("\t")
        if fields[:2] == ["TSSH_RECLAIM", "1"]:
            valid_header = True
        elif len(fields) == 2 and fields[0] == "UID":
            try:
                target_uid = int(fields[1])
            except ValueError:
                pass
        elif len(fields) == 4 and fields[0] == "L":
            try:
                listeners.append((int(fields[1]), fields[2], fields[3]))
            except ValueError:
                continue
        elif len(fields) == 7 and fields[0] == "P":
            try:
                pid, uid, tty, elapsed = int(fields[1]), int(fields[2]), fields[3], int(fields[4])
            except ValueError:
                continue
            session_id, comm = fields[5], fields[6]
            if session_id != "-":
                processes.setdefault(session_id, []).append((pid, uid, tty, elapsed, comm))

    if not valid_header:
        return ReclaimScan(None, (), (), ("远端没有返回可识别的端口扫描结果",))
    if target_uid is None or (expected_uid is not None and target_uid != expected_uid):
        return ReclaimScan(target_uid, (), (), ("无法确认远端登录用户身份",))

    relevant = [item for item in listeners if item[0] in (4227, 4228)]
    occupied_ports = tuple(sorted({item[0] for item in relevant}))
    if not relevant:
        return ReclaimScan(target_uid, (), (), ())

    session_ports: dict[str, set[int]] = {}
    blockers: list[str] = []
    unsafe_sessions: set[str] = set()
    for port, owner_uid, session_id in relevant:
        if session_id == "-":
            blockers.append(f"远端端口 {port} 的 SSH 会话归属无法确认")
            continue
        session_ports.setdefault(session_id, set()).add(port)
        if owner_uid == "-" or not owner_uid.isdigit() or int(owner_uid) != target_uid:
            blockers.append(f"远端端口 {port} 不属于登录用户的可回收会话")
            unsafe_sessions.add(session_id)

    candidates: list[ReclaimCandidate] = []
    for session_id, ports in sorted(session_ports.items(), key=lambda item: int(item[0])):
        if session_id in unsafe_sessions:
            continue
        if any(
            port not in (4227, 4228)
            for port, _, listener_session in listeners
            if listener_session == session_id
        ):
            blockers.append(f"SSH session {session_id} 还承载其他监听端口")
            continue
        members = processes.get(session_id, [])
        owned = [item for item in members if item[1] == target_uid]
        if (
            len(members) != 2
            or len(owned) != 1
            or any(item[2] != "?" or item[4] not in ("sshd", "sshd-session") for item in members)
        ):
            blockers.append(f"SSH session {session_id} 不是可确认的无终端转发专用会话")
            continue
        pid, uid, _, elapsed, _ = owned[0]
        candidates.append(ReclaimCandidate(session_id, pid, uid, tuple(sorted(ports)), elapsed))

    return ReclaimScan(target_uid, occupied_ports, tuple(candidates), tuple(dict.fromkeys(blockers)))


def format_elapsed(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    minutes, second = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m{second:02d}s"
    hours, minute = divmod(minutes, 60)
    return f"{hours}h{minute:02d}m"
