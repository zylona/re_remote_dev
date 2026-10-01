"""Endpoint-scoped 4227 forwarder lifecycle for the P3 lease protocol."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from .model import EndpointKey, TargetKey
from .proxy_persistent import ensure, remove


class EndpointForwardManager:
    """Start at most one systemd proxy unit for an endpoint owner."""

    def start(self, endpoint: EndpointKey, user: str, identity_file: Path, *, event_forward: bool = True) -> None:
        target = TargetKey(endpoint.hostname, endpoint.port, user)
        path = ensure(target, identity_file, event_forward=event_forward)
        # ``systemctl start`` can succeed even when the short-lived ssh
        # process exits immediately (for example, because remote 4227 is
        # already occupied).  Confirm the unit is actually active before the
        # orchestrator advertises READY; callers can then mark the endpoint
        # degraded while leaving the native SSH session usable.
        active = None
        for _ in range(10):
            active = subprocess.run(
                ["systemctl", "--user", "is-active", "--quiet", path.name],
                check=False,
            )
            if active.returncode == 0:
                # ssh(1) reports a remote bind collision just after it starts;
                # hold the success decision briefly so that failure is not
                # mistaken for a healthy long-lived tunnel.
                time.sleep(0.5)
                active = subprocess.run(
                    ["systemctl", "--user", "is-active", "--quiet", path.name],
                    check=False,
                )
                if active.returncode == 0:
                    break
            time.sleep(0.1)
        if active is None or active.returncode != 0:
            detail = subprocess.run(
                ["systemctl", "--user", "show", "--property=ExecMainStatus,Result", "--value", path.name],
                check=False,
                capture_output=True,
                text=True,
            ).stdout.strip()
            raise RuntimeError(f"SSH 反向转发未保持运行（{detail or 'unit inactive'}）")

    def stop(self, endpoint: EndpointKey, user: str | None = None) -> None:
        # ``remove`` derives the endpoint-scoped unit name, so the user is not
        # part of ownership and is only retained for API/readability.
        remove(TargetKey(endpoint.hostname, endpoint.port, user or "owner"))
