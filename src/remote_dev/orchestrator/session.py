"""受管 SSH 会话生命周期：master、目标 nonce 和编排器注册（P4/P5）。"""

from __future__ import annotations

import secrets
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .client import request
from .master import MasterManager
from .model import TargetKey
from .forwarder import EventForwarder, ForwardProcess, ProxyForwarder


@dataclass(frozen=True, slots=True)
class ManagedSession:
    target: TargetKey
    control_path: Path
    nonce: str
    identity_file: Path | None = None
    proxy_forward: ForwardProcess | None = None
    event_forward: ForwardProcess | None = None


class SessionManager:
    """Create one target-isolated ControlMaster and publish its session nonce."""

    def __init__(self, master: MasterManager | None = None, proxy: ProxyForwarder | None = None, event: EventForwarder | None = None) -> None:
        self.master = master or MasterManager()
        self.proxy = proxy or ProxyForwarder()
        self.event = event or EventForwarder()

    def _remote_nonce(self, target: TargetKey) -> str:
        # The file is deliberately fixed per target user; the value is rotated
        # for every master and is never placed in argv or logs.
        return f"/home/{target.user}/.config/remote-dev/session.nonce"

    def _write_nonce(self, session: ManagedSession) -> None:
        command = [
            "ssh", "-F", "/dev/null", "-S", str(session.control_path),
            session.target.user + "@" + session.target.hostname,
            "sh", "-c", "umask 077; mkdir -p ~/.config/remote-dev; touch ~/.config/remote-dev/session.nonce; chmod 600 ~/.config/remote-dev/session.nonce; cat > ~/.config/remote-dev/session.nonce; chmod 600 ~/.config/remote-dev/session.nonce",
        ]
        result = subprocess.run(command, input=session.nonce + "\n", text=True, check=False, capture_output=True, timeout=10)
        if result.returncode:
            raise RuntimeError("无法在目标机写入 Codex 会话 nonce，已拒绝启用受管事件")

    def start(self, target: TargetKey, identity_file: Path) -> ManagedSession:
        path = self.master.control_path(target)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.master.check(target):
            # Existing project master is reusable; registration/nonce repair is
            # still performed so a restarted orchestrator can recover state.
            session = ManagedSession(target, path, secrets.token_urlsafe(32), identity_file)
        else:
            result = subprocess.run(self.master.ensure_command(target, identity_file), check=False, capture_output=True, text=True, timeout=30)
            if result.returncode:
                raise RuntimeError(f"无法建立 SSH ControlMaster：{(result.stderr or result.stdout).strip()}")
            session = ManagedSession(target, path, secrets.token_urlsafe(32), identity_file)
        try:
            proxy_forward = self.proxy.start(target, identity_file)
        except (OSError, RuntimeError, subprocess.SubprocessError):
            subprocess.run(
                self.master.stop_command(target), check=False,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
            )
            raise RuntimeError("独立 SSH 代理 forwarder 启动失败")
        try:
            event_forward = self.event.start(target, identity_file)
        except (OSError, RuntimeError, subprocess.SubprocessError):
            ProxyForwarder.stop(proxy_forward)
            subprocess.run(self.master.stop_command(target), check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            raise RuntimeError("独立 SSH 事件 forwarder 启动失败")
        session = ManagedSession(session.target, session.control_path, session.nonce,
                                 session.identity_file, proxy_forward, event_forward)
        try:
            self._write_nonce(session)
            response = request({
                "op": "register",
                "target": {
                    "hostname": target.hostname, "port": target.port,
                    "user": target.user, "identity_fingerprint": target.identity_fingerprint,
                },
                "control_path": str(path),
                "identity_file": str(identity_file.expanduser()),
                "proxy_available": True,
                "proxy_forward": self._forward_payload(proxy_forward),
                "event_forward": self._forward_payload(event_forward),
            })
        except Exception:
            # A nonce or broker failure must not leak long-lived forwarding
            # processes (or leave a master registered as healthy).
            ProxyForwarder.stop(event_forward)
            ProxyForwarder.stop(proxy_forward)
            subprocess.run(self.master.stop_command(target), check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            raise
        if not response.get("ok"):
            self.stop(session, unregister=False)
            raise RuntimeError(f"编排器注册目标失败：{response.get('error', 'unknown')}")
        return session

    def stop(self, session: ManagedSession, *, unregister: bool = True) -> None:
        try:
            if unregister:
                request({
                    "op": "unregister",
                    "target": {
                        "hostname": session.target.hostname, "port": session.target.port,
                        "user": session.target.user, "identity_fingerprint": session.target.identity_fingerprint,
                    },
                })
        finally:
            # Cleanup must still happen when the orchestrator was restarted or
            # its Unix socket is unavailable.
            clear = [
                "ssh", "-F", "/dev/null", "-S", str(session.control_path),
                self.master.destination(session.target), "sh", "-c", "rm -f ~/.config/remote-dev/session.nonce",
            ]
            try:
                subprocess.run(clear, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            except (OSError, subprocess.SubprocessError):
                pass
            try:
                subprocess.run(self.master.stop_command(session.target), check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            except (OSError, subprocess.SubprocessError):
                pass
            if session.event_forward is not None:
                ProxyForwarder.stop(session.event_forward)
            if session.proxy_forward is not None:
                ProxyForwarder.stop(session.proxy_forward)

    @staticmethod
    def _forward_payload(forward: ForwardProcess | None) -> dict[str, object] | None:
        if forward is None:
            return None
        return {
            "kind": forward.kind,
            "state": "READY",
            "pid": forward.process.pid,
            "local_port": forward.local_port,
            "remote_port": forward.remote_port,
        }

    def serve(self, session: ManagedSession) -> None:
        """Supervise a master without turning transient probes into restarts.

        The old loop exited after three probe failures, causing systemd to
        restart the whole unit even for a temporarily blocked SSH channel.
        L0 checks are cheap and frequent; the remote forwarding probe is
        throttled.  Recovery is serialized here and uses exponential backoff.
        """
        interrupted = False
        previous = {}
        def shutdown(signum, _frame):
            nonlocal interrupted
            interrupted = True
        for sig in (signal.SIGTERM, signal.SIGINT):
            previous[sig] = signal.signal(sig, shutdown)
        consecutive_failures = 0
        backoff = 5.0
        next_heartbeat = 0.0
        try:
            while not interrupted:
                now = time.monotonic()
                master_ok: bool | None = False
                try:
                    master_ok = self.master.check(session.target, timeout=2.0)
                except subprocess.TimeoutExpired:
                    # A busy multiplexed connection can delay the control
                    # reply while Codex is streaming TUI output.  Timeout is
                    # inconclusive and must never tear down user channels.
                    master_ok = None
                except (OSError, subprocess.SubprocessError):
                    master_ok = False
                # P3 deliberately keeps forwarding outside the ControlMaster.
                # Process liveness is the only local probe; never send ``-O
                # forward`` probes over the interactive/master transport.
                forwarding_ok = all(
                    forward is not None and forward.process.poll() is None
                    for forward in (session.proxy_forward, session.event_forward)
                )
                if now >= next_heartbeat:
                    try:
                        request({
                            "op": "register",
                            "target": {"hostname": session.target.hostname, "port": session.target.port, "user": session.target.user, "identity_fingerprint": session.target.identity_fingerprint},
                            "control_path": str(session.control_path),
                            "identity_file": str(session.identity_file.expanduser()) if session.identity_file else None,
                            "proxy_available": forwarding_ok,
                            "proxy_forward": self._forward_payload(session.proxy_forward) if session.proxy_forward else None,
                            "event_forward": self._forward_payload(session.event_forward) if session.event_forward else None,
                        }, timeout=2.0)
                    except (OSError, TimeoutError, ValueError):
                        # The broker is socket-activated and may restart; a
                        # later heartbeat will re-register this live session.
                        pass
                    next_heartbeat = now + 20.0
                forward_failure = not forwarding_ok
                if master_ok is True and not forward_failure:
                    consecutive_failures = 0
                    backoff = 5.0
                elif master_ok is False or forward_failure:
                    consecutive_failures += 1
                # ``None`` means the control probe timed out.  Keep both the
                # current failure count and the live master untouched.
                if consecutive_failures >= 3:
                    # Keep the service alive while recovering.  This avoids a
                    # systemd restart storm and preserves the target status.
                    if session.identity_file is None:
                        time.sleep(min(backoff, 300.0))
                    else:
                        try:
                            ProxyForwarder.stop(session.event_forward)
                            ProxyForwarder.stop(session.proxy_forward)
                            subprocess.run(self.master.stop_command(session.target), check=False,
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
                            session = self.start(session.target, session.identity_file)
                            consecutive_failures = 0
                            backoff = 5.0
                        except (OSError, RuntimeError, subprocess.SubprocessError):
                            time.sleep(min(backoff, 300.0))
                            backoff = min(backoff * 2.0, 300.0)
                else:
                    time.sleep(5.0)
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
            self.stop(session)
