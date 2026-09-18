"""Per-target systemd user units for the proxy-only SSH reverse tunnel."""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from .model import TargetKey


def unit_name(target: TargetKey) -> str:
    return f"remote-dev-proxy-{target.endpoint_digest}.service"


def unit_text(target: TargetKey, identity: Path) -> str:
    command = shlex.join([
        "/usr/bin/ssh", "-F", "/dev/null", "-N", "-T",
        "-o", "BatchMode=yes", "-o", "ControlMaster=no", "-o", "ControlPath=none",
        "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=15",
        "-o", "ServerAliveCountMax=3", "-i", str(identity.expanduser()),
        "-R", "127.0.0.1:4227:127.0.0.1:4227", "-p", str(target.port),
        f"{target.user}@{target.hostname}",
    ])
    return f"""[Unit]
Description=remote-dev proxy tunnel ({target.endpoint_digest})
After=network-online.target
Wants=network-online.target
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

[Install]
WantedBy=default.target
"""


def ensure(target: TargetKey, identity: Path, *, systemd_dir: Path | None = None) -> Path:
    directory = systemd_dir or (Path.home() / ".config/systemd/user")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / unit_name(target)
    active = subprocess.run(["systemctl", "--user", "is-active", "--quiet", path.name], check=False).returncode == 0
    if active:
        return path
    content = unit_text(target, identity)
    changed = not path.exists() or path.read_text(encoding="utf-8") != content
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", path.name], check=True)
    if changed:
        subprocess.run(["systemctl", "--user", "restart", path.name], check=True)
    return path


def remove(target: TargetKey, *, systemd_dir: Path | None = None) -> Path:
    """Stop and remove the endpoint unit created by :func:`ensure`."""
    directory = systemd_dir or (Path.home() / ".config/systemd/user")
    path = directory / unit_name(target)
    subprocess.run(["systemctl", "--user", "disable", "--now", path.name], check=False)
    if path.exists():
        path.unlink()
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    return path
