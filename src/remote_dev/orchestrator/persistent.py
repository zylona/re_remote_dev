"""Optional systemd --user integration for a single managed SSH session (P6)."""

from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path

from .model import TargetKey


def unit_name(target: TargetKey) -> str:
    return f"remote-dev-master-{target.digest}.service"


def unit_text(target: TargetKey, identity: Path) -> str:
    executable = shutil.which("remote-dev") or "remote-dev"
    command = shlex.join([executable, "orchestrator", "connect", target.hostname, target.user, "--identity", str(identity.expanduser()), "--port", str(target.port), "--persistent"])
    return """[Unit]\nDescription=remote-dev managed SSH session (%s)\nAfter=network-online.target tssh.service\nPartOf=tssh.service\nStartLimitIntervalSec=600\nStartLimitBurst=10\n\n[Service]\nType=simple\nExecStart=%s\nRestart=on-failure\nRestartSec=30\n\n[Install]\nWantedBy=default.target\n""" % (target.digest, command)


def install(target: TargetKey, identity: Path, *, systemd_dir: Path | None = None) -> Path:
    directory = systemd_dir or (Path.home() / ".config/systemd/user")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / unit_name(target)
    content = unit_text(target, identity)
    changed = not path.exists() or path.read_text(encoding="utf-8") != content
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    # A target outage can exhaust systemd's start-rate limit while the
    # supervisor is unable to establish the initial SSH session.  A later
    # bootstrap is an explicit recovery attempt, so clear that historical
    # failure before asking systemd to start the unit again.
    subprocess.run(["systemctl", "--user", "reset-failed", path.name], check=False)
    subprocess.run(["systemctl", "--user", "enable", "--now", path.name], check=True)
    if changed:
        subprocess.run(["systemctl", "--user", "restart", path.name], check=True)
    return path


def remove(target: TargetKey, *, systemd_dir: Path | None = None) -> Path:
    directory = systemd_dir or (Path.home() / ".config/systemd/user")
    path = directory / unit_name(target)
    subprocess.run(["systemctl", "--user", "disable", "--now", path.name], check=False)
    if path.exists():
        path.unlink()
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    return path
