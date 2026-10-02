"""Endpoint-scoped 4227 forwarder lifecycle for the P3 lease protocol."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from .model import EndpointKey, TargetKey
from .proxy_persistent import ensure, probe_remote_proxy, remove, unit_path


class EndpointForwardManager:
    """Start at most one systemd proxy unit for an endpoint owner."""

    def __init__(self) -> None:
        # A healthy 4227 may belong to another controller. Never remove such
        # a tunnel when our own lease is released.
        self._external: set[str] = set()
        self._event_only: set[str] = set()

    def start(self, endpoint: EndpointKey, user: str, identity_file: Path, *, event_forward: bool = True) -> None:
        target = TargetKey(endpoint.hostname, endpoint.port, user)
        path = unit_path(target)
        local_active = subprocess.run(
            ["systemctl", "--user", "is-active", "--quiet", path.name], check=False
        ).returncode == 0
        # Reconcile the fixed remote port before creating a new SSH forward.
        # A healthy proxy may belong to another controller: preserve it and,
        # for an interactive session, create only the separate 4228 event leg.
        proxy_forward = True
        if local_active:
            try:
                proxy_forward = "127.0.0.1:4227:127.0.0.1:4227" in path.read_text(encoding="utf-8")
            except OSError:
                proxy_forward = True
            if not proxy_forward and not event_forward:
                self._external.add(endpoint.digest)
                return
            if not proxy_forward:
                self._external.add(endpoint.digest)
                self._event_only.add(endpoint.digest)
        if not local_active:
            probe = probe_remote_proxy(target, identity_file)
            if probe.state == "healthy":
                self._external.add(endpoint.digest)
                proxy_forward = False
                if not event_forward:
                    return
                self._event_only.add(endpoint.digest)
            if probe.state == "occupied":
                raise RuntimeError("远端 4227 已被占用且代理请求无响应；疑似异常旧 SSH 转发，请先运行 tssh doctor/reclaim")
        try:
            path = ensure(target, identity_file, event_forward=event_forward, proxy_forward=proxy_forward)
        except Exception:
            # This start was not active before the attempt, so removing the
            # just-created unit cannot affect another lease.  It prevents a
            # remote bind collision from becoming a local restart loop.
            remove(target)
            raise
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
            remove(target)
            raise RuntimeError(f"SSH 反向转发未保持运行（{detail or 'unit inactive'}）")

    def stop(self, endpoint: EndpointKey, user: str | None = None) -> None:
        # ``remove`` derives the endpoint-scoped unit name, so the user is not
        # part of ownership and is only retained for API/readability.
        if endpoint.digest in self._external:
            self._external.discard(endpoint.digest)
            # A healthy external proxy is never removed, but an event-only
            # unit created for this session still belongs to us.
            if endpoint.digest in self._event_only:
                self._event_only.discard(endpoint.digest)
                remove(TargetKey(endpoint.hostname, endpoint.port, user or "owner"))
            return
        remove(TargetKey(endpoint.hostname, endpoint.port, user or "owner"))
