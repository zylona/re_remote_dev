"""本地用户级编排器（P1）。当前只维护内存状态，不执行 SSH。"""

from __future__ import annotations

import json
import os
import signal
import socket
import stat
import selectors
import webbrowser
import shutil
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .model import ForwardState, ForwardStatus, MasterState, OAuthState, TargetKey, TargetStatus
from .protocol import ProtocolError, decode_event
from .oauth import OAuthError, OAuthManager

MAX_REQUEST_BYTES = 8 * 1024


class OrchestratorServer:
    def __init__(self, listener: socket.socket, path: Path | None = None, *, extra_listeners: list[socket.socket] | None = None) -> None:
        self.listener = listener
        self.listeners = [listener, *(extra_listeners or [])]
        self.path = path
        self.targets: dict[str, TargetStatus] = {}
        self._identity_files: dict[str, Path] = {}
        # A TUI redraw may yield the same authorize URL through both the
        # normal and wrapped-url matchers.  Deduplicate browser launches per
        # OAuth session, not merely by URL text.
        self._oauth_browser_opened: set[str] = set()
        self.running = True
        self.oauth = OAuthManager()

    def _status_dict(self, status: TargetStatus) -> dict[str, Any]:
        data = asdict(status)
        data["target"] = asdict(status.target)
        data["master"] = status.master.value
        data["oauth"] = status.oauth.value
        data["digest"] = status.target.digest
        for key in ("proxy_forward", "event_forward", "oauth_forward"):
            forward = data[key]
            forward["state"] = forward["state"].value
        return data

    @staticmethod
    def _forward_status(value: Any, kind: str, previous: ForwardStatus | None = None) -> ForwardStatus:
        """Parse secret-free forwarder telemetry from a session heartbeat."""
        if not isinstance(value, dict):
            return previous or ForwardStatus(kind)
        try:
            state = ForwardState(str(value.get("state", "ABSENT")))
        except ValueError:
            state = ForwardState.DEGRADED
        return ForwardStatus(
            kind=kind,
            state=state,
            pid=int(value["pid"]) if value.get("pid") is not None else None,
            local_port=int(value["local_port"]) if value.get("local_port") is not None else None,
            remote_port=int(value["remote_port"]) if value.get("remote_port") is not None else None,
            last_error=str(value["last_error"]) if value.get("last_error") else None,
            last_success=float(value["last_success"]) if value.get("last_success") is not None else None,
        )

    def handle(self, payload: dict[str, Any]) -> dict[str, Any]:
        if "event" in payload:
            return self.handle_event(payload)
        op = payload.get("op")
        if op == "health":
            return {"ok": True, "pid": os.getpid(), "version": 1}
        if op == "status":
            return {"ok": True, "targets": [self._status_dict(item) for item in self.targets.values()]}
        if op == "shutdown":
            self.running = False
            return {"ok": True}
        if op in {"register", "unregister"}:
            target_data = payload.get("target")
            if not isinstance(target_data, dict):
                return {"ok": False, "error": "target 必须是 object"}
            try:
                target = TargetKey(
                    hostname=str(target_data["hostname"]),
                    port=int(target_data.get("port", 22)),
                    user=str(target_data["user"]),
                    identity_fingerprint=str(target_data.get("identity_fingerprint", "")),
                )
            except (KeyError, TypeError, ValueError) as exc:
                return {"ok": False, "error": f"target 无效：{exc}"}
            if op == "register":
                previous = self.targets.get(target.digest)
                identity_file = payload.get("identity_file")
                if isinstance(identity_file, str) and identity_file:
                    self._identity_files[target.digest] = Path(identity_file).expanduser()
                self.targets[target.digest] = TargetStatus(
                    target=target,
                    master=(previous.master if previous else (MasterState.READY if payload.get("control_path") else MasterState.ABSENT)),
                    oauth=previous.oauth if previous else OAuthState.NONE,
                    control_path=payload.get("control_path") or (previous.control_path if previous else None),
                    proxy_available=bool(payload.get("proxy_available", previous.proxy_available if previous else False)),
                    session_count=previous.session_count if previous else 0,
                    proxy_forward=self._forward_status(payload.get("proxy_forward"), "proxy", previous.proxy_forward if previous else None),
                    event_forward=self._forward_status(payload.get("event_forward"), "event", previous.event_forward if previous else None),
                    oauth_forward=previous.oauth_forward if previous else ForwardStatus("oauth"),
                )
            else:
                self.targets.pop(target.digest, None)
                self._identity_files.pop(target.digest, None)
            return {"ok": True, "digest": target.digest}
        return {"ok": False, "error": f"不支持的操作：{op}"}

    def handle_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        """接收远端 shim 的非阻塞生命周期事件。"""
        try:
            event = decode_event(json.dumps(payload, separators=(",", ":")))
        except ProtocolError as exc:
            return {"ok": False, "error": "PROTOCOL_INVALID", "detail": str(exc)}
        status = self.targets.get(event.target)
        if status is None:
            return {"ok": False, "error": "TARGET_NOT_FOUND"}
        if event.event == "CODEX_START":
            self._oauth_browser_opened.discard(event.target)
            oauth_state = OAuthState.PENDING if status.control_path else OAuthState.FAILED
            oauth_forward = status.oauth_forward
            try:
                if status.control_path:
                    oauth_kwargs = {"control_path": status.control_path}
                    if event.target in self._identity_files:
                        oauth_kwargs["identity_file"] = self._identity_files[event.target]
                    oauth_session = self.oauth.start(status.target, **oauth_kwargs)
                    if oauth_session is not None:
                        primary = oauth_session.forward
                        oauth_forward = ForwardStatus(
                            "oauth", ForwardState.READY,
                            primary.process.pid if primary else None,
                            oauth_session.local_port, oauth_session.remote_port,
                        )
            except OAuthError as exc:
                oauth_state = OAuthState.FAILED
                oauth_forward = ForwardStatus("oauth", ForwardState.FAILED, last_error=str(exc))
            self.targets[event.target] = TargetStatus(
                target=status.target, master=MasterState.CODEX_RUNNING,
                oauth=oauth_state, control_path=status.control_path,
                proxy_available=status.proxy_available,
                session_count=status.session_count + 1,
                proxy_forward=status.proxy_forward,
                event_forward=status.event_forward,
                oauth_forward=oauth_forward,
            )
        elif event.event == "CODEX_OAUTH_URL":
            if event.oauth_url and event.target not in self._oauth_browser_opened:
                self._open_browser(event.oauth_url)
                self._oauth_browser_opened.add(event.target)
            self.targets[event.target] = TargetStatus(
                target=status.target, master=MasterState.OAUTH_PENDING,
                oauth=OAuthState.PENDING, control_path=status.control_path,
                proxy_available=status.proxy_available, session_count=status.session_count,
                proxy_forward=status.proxy_forward,
                event_forward=status.event_forward,
                oauth_forward=status.oauth_forward,
            )
        elif event.event == "CODEX_EXIT":
            self._oauth_browser_opened.discard(event.target)
            if status.session_count <= 1:
                try:
                    self.oauth.finish(status.target)
                except OAuthError:
                    pass
            self.targets[event.target] = TargetStatus(
                target=status.target, master=MasterState.READY if status.session_count <= 1 else status.master,
                oauth=OAuthState.NONE, control_path=status.control_path,
                proxy_available=status.proxy_available,
                session_count=max(0, status.session_count - 1),
                proxy_forward=status.proxy_forward,
                event_forward=status.event_forward,
                oauth_forward=ForwardStatus("oauth") if status.session_count <= 1 else status.oauth_forward,
            )
        return {"ok": True, "event": event.event}

    @staticmethod
    def _open_browser(url: str) -> None:
        """Use the desktop launcher so Hyprland/Omarchy user sessions work."""
        launcher = shutil.which("omarchy-launch-browser") or shutil.which("xdg-open")
        if launcher:
            subprocess.Popen([launcher, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        else:
            webbrowser.open(url, new=2)

    def serve(self) -> None:
        selector = selectors.DefaultSelector()
        for listener in self.listeners:
            listener.setblocking(False)
            selector.register(listener, selectors.EVENT_READ)
        while self.running:
            for key, _ in selector.select(timeout=0.5):
                try:
                    conn, _ = key.fileobj.accept()
                except OSError:
                    continue
                with conn:
                    conn.settimeout(2.0)
                    data = bytearray()
                    while not data.endswith(b"\n"):
                        try:
                            chunk = conn.recv(4096)
                        except socket.timeout:
                            # A half-open probe must not terminate the shared
                            # user service.  Drop only this connection and
                            # keep the orchestrator (and SSH masters) alive.
                            data.clear()
                            break
                        if not chunk:
                            break
                        data.extend(chunk)
                        if len(data) > MAX_REQUEST_BYTES:
                            break
                    try:
                        payload = json.loads(data.decode())
                        response = self.handle(payload) if isinstance(payload, dict) else {"ok": False, "error": "请求必须是 JSON object"}
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        response = {"ok": False, "error": "请求不是有效 JSON"}
                    try:
                        conn.sendall((json.dumps(response, separators=(",", ":")) + "\n").encode())
                    except (BrokenPipeError, ConnectionResetError):
                        # A client may time out or close its socket while the
                        # broker is handling the request.  Never let one
                        # abandoned request terminate the shared service.
                        pass
        selector.close()
        for listener in self.listeners:
            listener.close()
        if self.path:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass


def _listener_from_systemd() -> socket.socket | None:
    if os.environ.get("LISTEN_PID") != str(os.getpid()) or os.environ.get("LISTEN_FDS") != "1":
        return None
    # Transfer ownership of systemd's fd 3 directly.  ``socket.fromfd``
    # duplicates the descriptor, which can leave the activation listener in a
    # state where the path exists but client connects are refused on some
    # systemd/Python combinations.
    return socket.socket(fileno=3)


def run_server(path: Path | None = None) -> None:
    listener = _listener_from_systemd()
    activated = listener is not None
    if not activated:
        if path is None:
            from .client import socket_path

            path = socket_path()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(path))
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        listener.listen(16)
    tcp_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        tcp_listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        tcp_listener.bind(("127.0.0.1", 4230))
        tcp_listener.listen(16)
    except OSError:
        tcp_listener.close()
        tcp_listener = None
    server = OrchestratorServer(listener, None if activated else path, extra_listeners=[tcp_listener] if tcp_listener else None)
    signal.signal(signal.SIGTERM, lambda *_: setattr(server, "running", False))
    signal.signal(signal.SIGINT, lambda *_: setattr(server, "running", False))
    server.serve()
