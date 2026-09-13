"""OpenSSH ControlMaster command construction and lifecycle helpers (P2)."""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from .model import TargetKey


class MasterManager:
    def __init__(self, *, home: Path | None = None, proxy_port: int = 4227, control_port: int = 4228, local_control_port: int = 4230) -> None:
        self.home = home or Path.home()
        self.proxy_port = proxy_port
        self.control_port = control_port
        self.local_control_port = local_control_port

    def control_path(self, target: TargetKey) -> Path:
        # Include the remote login user in the socket name.  A host may be
        # reached as both the bootstrap/login user and the final target user;
        # OpenSSH otherwise reuses the first master and silently changes the
        # effective identity of an explicit ``ssh other-user@host`` command.
        return self.home / ".ssh" / "remote-dev" / "cm" / f"{target.digest}-{target.user}"

    def destination(self, target: TargetKey) -> str:
        return f"{target.user}@{target.hostname}"

    def check_command(self, target: TargetKey) -> list[str]:
        return ["ssh", "-F", "/dev/null", "-S", str(self.control_path(target)), "-O", "check", self.destination(target)]

    def ensure_command(self, target: TargetKey, identity_file: Path) -> list[str]:
        path = self.control_path(target)
        return [
            "ssh", "-F", "/dev/null", "-i", str(identity_file.expanduser()),
            "-M", "-S", str(path), "-o", "ControlPersist=yes",
            "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=3",
            "-N", "-f", self.destination(target),
        ]

    def forwarding_probe_command(self, target: TargetKey) -> list[str]:
        script = (
            "import socket,sys; "
            f"ports=({self.proxy_port},{self.control_port}); "
            "sys.exit(0 if all(socket.socket().connect_ex(('127.0.0.1', p)) == 0 for p in ports) else 1)"
        )
        return [
            "ssh", "-F", "/dev/null", "-S", str(self.control_path(target)),
            self.destination(target), f"python3 -c {shlex.quote(script)}",
        ]

    def forward_command(self, target: TargetKey) -> list[str]:
        return [
            "ssh", "-F", "/dev/null", "-S", str(self.control_path(target)),
            "-O", "forward",
            "-R", f"127.0.0.1:{self.proxy_port}:127.0.0.1:{self.proxy_port}",
            "-R", f"127.0.0.1:{self.control_port}:127.0.0.1:{self.local_control_port}",
            self.destination(target),
        ]

    def forwarding_ready(self, target: TargetKey, *, timeout: float = 8.0) -> bool:
        result = subprocess.run(
            self.forwarding_probe_command(target), check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout,
        )
        return result.returncode == 0

    def ensure_forwarding(self, target: TargetKey) -> bool:
        # Ports are host-local and may already be provided by another managed
        # login user for the same host. In that case all users can reuse them.
        if self.forwarding_ready(target):
            return True
        result = subprocess.run(
            self.forward_command(target), check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
        )
        return result.returncode == 0 and self.forwarding_ready(target)

    def stop_command(self, target: TargetKey) -> list[str]:
        return ["ssh", "-F", "/dev/null", "-S", str(self.control_path(target)), "-O", "exit", self.destination(target)]

    def check(self, target: TargetKey, *, timeout: float = 5.0) -> bool:
        result = subprocess.run(self.check_command(target), check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout)
        return result.returncode == 0
