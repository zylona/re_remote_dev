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
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .model import EndpointKey, ForwardState, ForwardStatus, MasterState, OAuthState, TargetKey, TargetStatus
from .protocol import ProtocolError, decode_event
from .oauth import OAuthError, OAuthManager
from .endpoint_forward import EndpointForwardManager
from .proxy_persistent import cleanup_orphans
from .health import classify_failure, retry_delay
from ..lease_config import load as load_lease_settings

MAX_REQUEST_BYTES = 8 * 1024


class OrchestratorServer:
    def __init__(self, listener: socket.socket, path: Path | None = None, *, extra_listeners: list[socket.socket] | None = None, endpoint_forwarder: EndpointForwardManager | None = None) -> None:
        self.listener = listener
        self.listeners = [listener, *(extra_listeners or [])]
        self.path = path
        self.targets: dict[str, TargetStatus] = {}
        # P2 endpoint leases are intentionally in-memory.  P3 adds the
        # forwarder/owner lifecycle; P2 only guarantees registration and
        # idempotent acquire/release semantics.
        self.endpoints: dict[str, dict[str, Any]] = {}
        self.sessions: dict[str, dict[str, Any]] = {}
        # A normal tssh exit sends ``release`` immediately.  This short grace
        # period is only for a killed terminal or a broken network path; it
        # keeps the proxy responsive without leaving 4227/4228 behind for
        # minutes.
        self.lease_ttl = load_lease_settings().lease_ttl
        self.endpoint_forwarder = endpoint_forwarder or EndpointForwardManager()
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
        self._expire_sessions()
        self._reconcile_endpoints()
        if "event" in payload:
            return self.handle_event(payload)
        op = payload.get("op")
        if op == "health":
            return {"ok": True, "pid": os.getpid(), "version": 1}
        if op == "status":
            endpoints = []
            now = time.monotonic()
            for record in self.endpoints.values():
                summary = {key: value for key, value in record.items() if key != "next_retry"}
                if "next_retry" in record:
                    summary["retry_in"] = round(max(0.0, float(record["next_retry"]) - now), 1)
                digest = record.get("endpoint_digest")
                summary["mode"] = "PERSISTENT" if any(
                    item.get("endpoint_digest") == digest and item.get("persistent")
                    for item in self.sessions.values()
                ) else "SESSION"
                endpoints.append(summary)
            return {
                "ok": True,
                "targets": [self._status_dict(item) for item in self.targets.values()],
                "endpoints": endpoints,
            }
        if op == "shutdown":
            self.running = False
            return {"ok": True}
        if op == "cleanup":
            return self._force_cleanup()
        if op in {"acquire", "release", "heartbeat"}:
            return self._handle_lease(op, payload)
        if op == "preview_register":
            return self._register_preview(payload)
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

    def _register_preview(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Attach a P1 loopback preview context to an existing lease."""
        session_id = payload.get("session_id")
        preview = payload.get("preview")
        if not isinstance(session_id, str) or not session_id or len(session_id) > 128:
            return {"ok": False, "error": "session_id 无效"}
        if not isinstance(preview, dict):
            return {"ok": False, "error": "preview 必须是 object"}
        session = self.sessions.get(session_id)
        if session is None:
            return {"ok": False, "error": "LEASE_CLEARED"}
        required = ("endpoint_digest", "session_id", "nonce", "local_port")
        if any(key not in preview for key in required) or preview.get("session_id") != session_id:
            return {"ok": False, "error": "preview context 无效"}
        try:
            local_port = int(preview["local_port"])
            remote_port = int(preview.get("remote_port", local_port))
        except (TypeError, ValueError):
            return {"ok": False, "error": "preview.local_port 无效"}
        if not 1 <= local_port <= 65535 or not 1 <= remote_port <= 65535:
            return {"ok": False, "error": "preview.local_port 无效"}
        for key, limit in (("endpoint_digest", 128), ("nonce", 256)):
            value = preview.get(key)
            if not isinstance(value, str) or not value or len(value) > limit:
                return {"ok": False, "error": f"preview.{key} 无效"}
        session["preview"] = {
            "endpoint_digest": preview["endpoint_digest"],
            "session_id": session_id,
            "nonce": preview["nonce"],
            "local_port": local_port,
            "remote_port": remote_port,
        }
        return {"ok": True, "session_id": session_id, "preview": session["preview"]}

    @staticmethod
    def _parse_endpoint(payload: dict[str, Any]) -> EndpointKey:
        value = payload.get("endpoint")
        if not isinstance(value, dict):
            raise ValueError("endpoint 必须是 object")
        hostname = value.get("hostname", value.get("host"))
        if not isinstance(hostname, str) or not hostname or len(hostname) > 253:
            raise ValueError("endpoint.hostname 无效")
        port = value.get("port", 22)
        if isinstance(port, bool):
            raise ValueError("endpoint.port 无效")
        return EndpointKey(hostname, int(port))

    @staticmethod
    def _parse_candidate(payload: dict[str, Any]) -> tuple[str, str]:
        value = payload.get("candidate")
        if not isinstance(value, dict):
            raise ValueError("candidate 必须是 object")
        user = value.get("user")
        fingerprint = value.get("identity_fingerprint", "")
        if not isinstance(user, str) or not user or len(user) > 128:
            raise ValueError("candidate.user 无效")
        if not isinstance(fingerprint, str) or len(fingerprint) > 256:
            raise ValueError("candidate.identity_fingerprint 无效")
        return user, fingerprint

    def _handle_lease(self, op: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            endpoint = self._parse_endpoint(payload)
            session_id = payload.get("session_id")
            if not isinstance(session_id, str) or not session_id or len(session_id) > 128:
                raise ValueError("session_id 无效")
            digest = endpoint.digest
            if op == "acquire":
                user, fingerprint = self._parse_candidate(payload)
                previous = self.sessions.get(session_id)
                if previous is not None:
                    if previous["endpoint_digest"] != digest:
                        return {"ok": False, "error": "SESSION_CONFLICT"}
                    record = self.endpoints[digest]
                    previous["last_seen"] = time.monotonic()
                    if bool(payload.get("event_forward", True)) and not record.get("event_required", True):
                        identity_file = previous.get("identity_file")
                        if identity_file:
                            self.endpoint_forwarder.start(endpoint, previous["user"], Path(identity_file).expanduser(), event_forward=True)
                            record["event_required"] = True
                            record["forwards"] = self._forward_rows(record["state"], record.get("last_error"), event_forward=True)
                    return {"ok": True, "endpoint_digest": digest, "state": record["state"], "owner": record["owner"], "session_count": record["session_count"], "reused": True}
                event_required = bool(payload.get("event_forward", True))
                record = self.endpoints.setdefault(
                    digest,
                    {
                        "endpoint": {"hostname": endpoint.hostname, "port": endpoint.port},
                        "endpoint_digest": digest,
                        "state": "STARTING",
                        "owner": None,
                        "session_count": 0,
                        "event_required": event_required,
                        "forwards": self._forward_rows("STARTING", event_forward=event_required),
                    },
                )
                if record["owner"] is None:
                    record["owner"] = user
                record["session_count"] += 1
                identity_file = payload.get("candidate", {}).get("identity_file")
                session: dict[str, Any] = {
                    "endpoint_digest": digest,
                    "user": user,
                    "identity_fingerprint": fingerprint,
                    "last_seen": time.monotonic(),
                    "event_forward": event_required,
                    "persistent": bool(payload.get("persistent", False)),
                }
                if isinstance(identity_file, str) and identity_file:
                    session["identity_file"] = identity_file
                self.sessions[session_id] = session
                if isinstance(identity_file, str) and identity_file:
                    # The standalone tssh endpoint has no ControlMaster, but
                    # its event forward still needs a target registration so
                    # Codex OAuth events can start the direct 1455 callback
                    # forwarder.  The target digest intentionally matches the
                    # remote shim's user/host identity (empty fingerprint).
                    target = TargetKey(endpoint.hostname, endpoint.port, user)
                    previous_target = self.targets.get(target.digest)
                    self._identity_files[target.digest] = Path(identity_file).expanduser()
                    if previous_target is None:
                        self.targets[target.digest] = TargetStatus(
                            target=target,
                            master=MasterState.READY,
                            control_path=None,
                            proxy_available=True,
                            session_count=1,
                            proxy_forward=ForwardStatus("proxy", ForwardState.READY, remote_port=4227, local_port=4227),
                            event_forward=ForwardStatus("event", ForwardState.READY, remote_port=4228, local_port=4230),
                        )
                    else:
                        self.targets[target.digest] = TargetStatus(
                            target=previous_target.target,
                            master=previous_target.master,
                            oauth=previous_target.oauth,
                            control_path=previous_target.control_path,
                            proxy_available=True,
                            session_count=previous_target.session_count + 1,
                            proxy_forward=previous_target.proxy_forward,
                            event_forward=previous_target.event_forward,
                            oauth_forward=previous_target.oauth_forward,
                        )
                # A proxy-only persistent lease may already own this
                # endpoint.  When a new interactive session joins, upgrade
                # the shared unit once so the Codex event callback 4228→4230
                # becomes available to that session as well.
                if event_required and not record.get("event_required", True) and isinstance(identity_file, str) and identity_file:
                    try:
                        self.endpoint_forwarder.start(endpoint, user, Path(identity_file).expanduser(), event_forward=True)
                        record["event_required"] = True
                        record["forwards"] = self._forward_rows(record["state"], record.get("last_error"), event_forward=True)
                    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
                        self._mark_forward_failure(record, exc)
                if record["session_count"] == 1 and isinstance(identity_file, str) and identity_file:
                    try:
                        self.endpoint_forwarder.start(endpoint, user, Path(identity_file).expanduser(), event_forward=event_required)
                        record["state"] = "READY"
                        record["forwards"] = self._forward_rows("READY", event_forward=event_required)
                    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
                        self._mark_forward_failure(record, exc)
                return {"ok": True, "endpoint_digest": digest, "state": record["state"], "owner": record["owner"], "session_count": record["session_count"], "reused": record["session_count"] > 1}
            previous = self.sessions.get(session_id)
            if previous is None:
                if op == "heartbeat":
                    return {"ok": False, "error": "LEASE_CLEARED"}
                return {"ok": True, "released": False}
            if previous["endpoint_digest"] != endpoint.digest:
                return {"ok": False, "error": "SESSION_CONFLICT"}
            if op == "heartbeat":
                previous["last_seen"] = time.monotonic()
                record = self.endpoints.get(previous["endpoint_digest"])
                return {"ok": True, "endpoint_digest": previous["endpoint_digest"], "session_count": record["session_count"] if record else 0}
            self.sessions.pop(session_id, None)
            if previous.get("identity_file"):
                target = TargetKey(str(endpoint.hostname), int(endpoint.port), str(previous["user"]))
                target_status = self.targets.get(target.digest)
                if target_status is not None:
                    if target_status.session_count <= 1:
                        try:
                            self.oauth.finish(target)
                        except OAuthError:
                            pass
                        self.targets.pop(target.digest, None)
                        self._identity_files.pop(target.digest, None)
                    else:
                        self.targets[target.digest] = TargetStatus(
                            target=target_status.target,
                            master=target_status.master,
                            oauth=target_status.oauth,
                            control_path=target_status.control_path,
                            proxy_available=target_status.proxy_available,
                            session_count=target_status.session_count - 1,
                            proxy_forward=target_status.proxy_forward,
                            event_forward=target_status.event_forward,
                            oauth_forward=target_status.oauth_forward,
                        )
            record = self.endpoints.get(previous["endpoint_digest"])
            if record is None:
                return {"ok": True, "released": True}
            record["session_count"] = max(0, int(record["session_count"]) - 1)
            if record["session_count"] == 0:
                try:
                    self.oauth.finish(TargetKey(str(endpoint.hostname), int(endpoint.port), str(previous["user"])))
                except OAuthError:
                    pass
                try:
                    self.endpoint_forwarder.stop(endpoint, previous.get("user"))
                except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
                    pass
                self.endpoints.pop(previous["endpoint_digest"], None)
                return {"ok": True, "released": True, "endpoint_digest": previous["endpoint_digest"], "session_count": 0}
            remaining = [item for item in self.sessions.values() if item["endpoint_digest"] == previous["endpoint_digest"]]
            desired_event = any(bool(item.get("event_forward", True)) for item in remaining)
            if desired_event != bool(record.get("event_required", True)):
                replacement = next((item for item in remaining if item.get("identity_file")), None)
                if replacement:
                    try:
                        self.endpoint_forwarder.start(endpoint, replacement["user"], Path(replacement["identity_file"]).expanduser(), event_forward=desired_event)
                        record["event_required"] = desired_event
                        record["forwards"] = self._forward_rows(record["state"], record.get("last_error"), event_forward=desired_event)
                    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
                        self._mark_forward_failure(record, exc)
            if previous.get("user") == record.get("owner"):
                candidates = [item for item in self.sessions.values() if item["endpoint_digest"] == previous["endpoint_digest"]]
                candidates.sort(key=lambda item: item["user"])
                replacement = candidates[0]
                record["owner"] = replacement["user"]
                record["event_required"] = any(bool(item.get("event_forward", True)) for item in candidates)
                if replacement.get("identity_file"):
                    try:
                        self.endpoint_forwarder.start(endpoint, replacement["user"], Path(replacement["identity_file"]).expanduser(), event_forward=record["event_required"])
                        record["state"] = "READY"
                        record["forwards"] = self._forward_rows("READY", event_forward=record["event_required"])
                    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
                        self._mark_forward_failure(record, exc)
            return {"ok": True, "released": True, "endpoint_digest": previous["endpoint_digest"], "owner": record["owner"], "session_count": record["session_count"]}
        except (TypeError, ValueError, KeyError) as exc:
            return {"ok": False, "error": "PROTOCOL_INVALID", "detail": str(exc)}

    def _force_cleanup(self) -> dict[str, Any]:
        """Stop every managed endpoint tunnel and discard all in-memory leases."""
        stopped = 0
        for record in list(self.endpoints.values()):
            endpoint_data = record.get("endpoint")
            if not isinstance(endpoint_data, dict):
                continue
            try:
                endpoint_key = EndpointKey(str(endpoint_data["hostname"]), int(endpoint_data["port"]))
                owner = record.get("owner")
                if isinstance(owner, str) and owner:
                    try:
                        self.oauth.finish(TargetKey(endpoint_key.hostname, endpoint_key.port, owner))
                    except OAuthError:
                        pass
                self.endpoint_forwarder.stop(
                    endpoint_key,
                    owner,
                )
                stopped += 1
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
                continue
        self.sessions.clear()
        self.endpoints.clear()
        self.targets.clear()
        self._identity_files.clear()
        self._oauth_browser_opened.clear()
        return {"ok": True, "cleaned_endpoints": stopped}

    def _expire_sessions(self) -> None:
        """Release leases that stopped heartbeating without trusting process exit."""
        now = time.monotonic()
        for session_id, session in list(self.sessions.items()):
            if now - float(session.get("last_seen", now)) <= self.lease_ttl:
                continue
            endpoint = self.endpoints.get(session["endpoint_digest"], {}).get("endpoint")
            if isinstance(endpoint, dict):
                self._handle_lease("release", {"endpoint": endpoint, "session_id": session_id})

    @staticmethod
    def _mark_forward_failure(record: dict[str, Any], exc: Exception) -> None:
        failures = int(record.get("failure_count", 0)) + 1
        record["state"] = "DEGRADED"
        record["failure_count"] = failures
        record["last_error"] = classify_failure(str(exc))
        record["next_retry"] = time.monotonic() + retry_delay(failures)
        record["forwards"] = OrchestratorServer._forward_rows("DEGRADED", record["last_error"])

    @staticmethod
    def _forward_rows(state: str, error: str | None = None, *, event_forward: bool = True) -> list[dict[str, Any]]:
        """Describe the two endpoint forwards owned by the SSH tunnel.

        The endpoint unit uses reverse forwarding: the first port is the
        remote HTTP proxy and the second is the remote OAuth/event channel.
        Keeping this telemetry beside the lease makes ``tssh list`` useful
        without exposing SSH command lines or credentials.
        """
        rows = [{
            "kind": "proxy",
            "remote_host": "127.0.0.1",
            "remote_port": 4227,
            "local_host": "127.0.0.1",
            "local_port": 4227,
            "state": state,
            "last_error": error,
        }]
        if event_forward:
            rows.append({
                "kind": "event",
                "remote_host": "127.0.0.1",
                "remote_port": 4228,
                "local_host": "127.0.0.1",
                "local_port": 4230,
                "state": state,
                "last_error": error,
            })
        return rows

    def _reconcile_endpoints(self) -> None:
        """Retry degraded endpoint owners using bounded exponential backoff."""
        now = time.monotonic()
        for digest, record in list(self.endpoints.items()):
            if record.get("state") == "READY" or int(record.get("session_count", 0)) <= 0:
                continue
            if now < float(record.get("next_retry", 0)):
                continue
            candidates = [item for item in self.sessions.values() if item["endpoint_digest"] == digest and item.get("identity_file")]
            if not candidates:
                continue
            candidates.sort(key=lambda item: item["user"])
            owner = candidates[0]
            endpoint_data = record.get("endpoint")
            if not isinstance(endpoint_data, dict):
                continue
            try:
                self.endpoint_forwarder.start(
                    EndpointKey(str(endpoint_data["hostname"]), int(endpoint_data["port"])),
                    owner["user"],
                    Path(str(owner["identity_file"])).expanduser(),
                    event_forward=bool(record.get("event_required", True)),
                )
                record["owner"] = owner["user"]
                record["state"] = "READY"
                record["forwards"] = self._forward_rows("READY", event_forward=bool(record.get("event_required", True)))
                record["failure_count"] = 0
                record.pop("last_error", None)
                record.pop("next_retry", None)
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
                self._mark_forward_failure(record, exc)

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
            oauth_state = OAuthState.PENDING if (status.control_path or event.target in self._identity_files) else OAuthState.FAILED
            oauth_forward = status.oauth_forward
            try:
                if status.control_path or event.target in self._identity_files:
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
            # ``session_count`` includes the owning SSH lease.  A single
            # Codex process therefore observes count=2 (SSH + Codex), and
            # should release its temporary OAuth forward immediately when it
            # exits; only counts greater than two indicate another active
            # SSH/Codex participant may still need the callback channel.
            if status.session_count <= 2:
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
            # Expire abandoned leases even when no client is polling status.
            self._expire_sessions()
            self._reconcile_endpoints()
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
    # ``LISTEN_PID`` is useful as a guard for a directly launched process, but
    # it is not reliable through every shell/exec wrapper used by packaged
    # installs (and can be stale during a user-service restart).  Falling back
    # to a second self-bound listener in that case is dangerous: the old
    # process may later unlink systemd's pathname while the new process owns
    # fd 3.  systemd only sets LISTEN_FDS for an activation, so that is the
    # authoritative check here.
    if os.environ.get("LISTEN_FDS") != "1":
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
    # Lease state is intentionally in memory.  Any proxy unit left by a
    # previous process is therefore an orphan and must not keep remote 4227
    # alive until the next user login.
    cleanup_orphans()
    signal.signal(signal.SIGTERM, lambda *_: setattr(server, "running", False))
    signal.signal(signal.SIGINT, lambda *_: setattr(server, "running", False))
    server.serve()
