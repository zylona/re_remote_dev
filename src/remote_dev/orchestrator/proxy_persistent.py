"""Ephemeral systemd user units for endpoint-scoped SSH reverse tunnels."""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .model import TargetKey


@dataclass(frozen=True)
class RemoteProxyProbe:
    """Read-only result for the fixed remote 4227 listener."""

    state: str  # free, healthy, occupied, unavailable, unreachable
    detail: str = ""


def probe_remote_proxy(target: TargetKey, identity: Path, *, timeout: float = 8.0) -> RemoteProxyProbe:
    """Classify an existing remote 4227 listener without changing remote state.

    A healthy listener is safe to reuse.  A listener which accepts TCP but does
    not complete an HTTP proxy request is classified as occupied rather than
    blindly replaced; the owner may be another controller/user.  The probe is
    deliberately best-effort so a target without curl does not block normal
    SSH forwarding startup.
    """
    remote_probe = (
        "if ! command -v curl >/dev/null 2>&1; then printf 'UNAVAILABLE\\n'; exit 0; fi; "
        "curl -sS -o /dev/null -I --proxy http://127.0.0.1:4227 "
        "--connect-timeout 2 --max-time 4 http://example.com >/dev/null 2>&1; "
        "rc=$?; case $rc in 0|3|22) printf 'HEALTHY\\n' ;; "
        "7) printf 'FREE\\n' ;; *) printf 'OCCUPIED\\n' ;; esac"
    )
    command = [
        "/usr/bin/ssh", "-F", "/dev/null", "-T",
        "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
        "-o", "ConnectionAttempts=1", "-o", "ServerAliveInterval=3",
        "-o", "ServerAliveCountMax=1", "-i", str(identity.expanduser()),
        "-p", str(target.port), f"{target.user}@{target.hostname}",
        "sh", "-c", remote_probe,
    ]
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return RemoteProxyProbe("unreachable", type(exc).__name__)
    if result.returncode != 0:
        detail = (result.stderr or "").lower()
        if "permission denied" in detail or "publickey" in detail:
            return RemoteProxyProbe("unreachable", "SSH_AUTH_FAILED")
        return RemoteProxyProbe("unreachable", "SSH_UNREACHABLE")
    marker = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "UNAVAILABLE"
    return {
        "HEALTHY": RemoteProxyProbe("healthy"),
        "FREE": RemoteProxyProbe("free"),
        "OCCUPIED": RemoteProxyProbe("occupied", "remote 4227 accepts connections but proxy request timed out"),
    }.get(marker, RemoteProxyProbe("unavailable", marker))


def unit_path(target: TargetKey, *, systemd_dir: Path | None = None) -> Path:
    directory = systemd_dir or (Path.home() / ".config/systemd/user")
    return directory / unit_name(target)


def unit_name(target: TargetKey) -> str:
    return f"remote-dev-proxy-{target.endpoint_digest}.service"


def unit_text(
    target: TargetKey,
    identity: Path,
    *,
    event_forward: bool = True,
    proxy_forward: bool = True,
) -> str:
    forwards: list[str] = []
    if proxy_forward:
        forwards.extend(["-R", "127.0.0.1:4227:127.0.0.1:4227"])
    if event_forward:
        forwards.extend(["-R", "127.0.0.1:4228:127.0.0.1:4230"])
    command = shlex.join([
        "/usr/bin/ssh", "-F", "/dev/null", "-N", "-T",
        "-o", "BatchMode=yes", "-o", "ControlMaster=no", "-o", "ControlPath=none",
        "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=15",
        "-o", "ServerAliveCountMax=3", "-i", str(identity.expanduser()),
        *forwards, "-p", str(target.port),
        f"{target.user}@{target.hostname}",
    ])
    return f"""[Unit]
Description=remote-dev proxy tunnel ({target.endpoint_digest})
After=network-online.target
Wants=network-online.target
CollectMode=inactive-or-failed
StartLimitIntervalSec=600
StartLimitBurst=10

[Service]
Type=simple
ExecStart={command}
Restart=on-failure
RestartSec=5
NoNewPrivileges=yes
MemoryMax=32M
TasksMax=8
"""


def ensure(
    target: TargetKey,
    identity: Path,
    *,
    event_forward: bool = True,
    proxy_forward: bool = True,
    systemd_dir: Path | None = None,
) -> Path:
    directory = systemd_dir or (Path.home() / ".config/systemd/user")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = unit_path(target, systemd_dir=directory)
    content = unit_text(target, identity, event_forward=event_forward, proxy_forward=proxy_forward)
    changed = not path.exists() or path.read_text(encoding="utf-8") != content
    active = subprocess.run(["systemctl", "--user", "is-active", "--quiet", path.name], check=False).returncode == 0
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    # Never enable endpoint units: their lifetime follows active tssh leases.
    # This also removes the default.target symlink created by older releases.
    subprocess.run(["systemctl", "--user", "disable", path.name], check=False)
    subprocess.run(["systemctl", "--user", "reset-failed", path.name], check=False)
    if changed and active:
        subprocess.run(["systemctl", "--user", "restart", path.name], check=True)
    elif not active:
        subprocess.run(["systemctl", "--user", "start", path.name], check=True)
    return path


def remove(target: TargetKey, *, systemd_dir: Path | None = None) -> Path:
    """Stop and remove the endpoint unit created by :func:`ensure`."""
    directory = systemd_dir or (Path.home() / ".config/systemd/user")
    path = directory / unit_name(target)
    # Do not make the interactive tssh exit wait for ssh(1)'s graceful
    # shutdown. systemd owns the stop job and will finish it in the
    # background; the in-memory lease is removed immediately by the caller.
    subprocess.run(["systemctl", "--user", "disable", path.name], check=False)
    subprocess.run(["systemctl", "--user", "stop", "--no-block", path.name], check=False)
    if path.exists():
        path.unlink()
    # Unit removal is not on the interactive session's critical path.  A
    # later ensure() performs a synchronous reload before reusing the unit.
    subprocess.Popen(
        ["systemctl", "--user", "daemon-reload"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return path


def cleanup_orphans(*, systemd_dir: Path | None = None) -> None:
    """Remove endpoint proxy units left after an orchestrator restart."""
    directory = systemd_dir or (Path.home() / ".config/systemd/user")
    names: set[str] = set()
    if directory.exists():
        names.update(path.name for path in directory.glob("remote-dev-proxy-*.service"))
    listed = subprocess.run(
        ["systemctl", "--user", "list-units", "--all", "--no-legend", "--no-pager"],
        check=False, capture_output=True, text=True,
    )
    for line in listed.stdout.splitlines():
        fields = line.split()
        if fields and fields[0].startswith("remote-dev-proxy-") and fields[0].endswith(".service"):
            names.add(fields[0])
    for name in sorted(names):
        subprocess.run(["systemctl", "--user", "disable", "--now", name], check=False)
        try:
            (directory / name).unlink()
        except FileNotFoundError:
            pass
    if names:
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
