"""Session-scoped local bridge for the remote file preview protocol.

The bridge owns a loopback listener, a short-lived nonce and request
validation.  SFTP execution is intentionally deferred to P3.  Keeping this
piece independent means a failed preview can never write to or block the SSH
PTY.
"""

from __future__ import annotations

import json
import secrets
import socket
import threading
import uuid
from dataclasses import dataclass
from typing import Any, Callable

MAX_REQUEST_BYTES = 8 * 1024
MAX_PATH_BYTES = 4096


class PreviewBridgeError(ValueError):
    """A malformed or unauthorized preview bridge request."""


@dataclass(frozen=True, slots=True)
class PreviewContext:
    endpoint_digest: str
    session_id: str
    nonce: str
    local_port: int
    remote_port: int = 0


@dataclass(frozen=True, slots=True)
class PreviewRequest:
    operation: str
    remote_path: str
    endpoint_digest: str
    session_id: str
    nonce: str
    request_id: str

    @classmethod
    def decode(cls, payload: bytes | str) -> "PreviewRequest":
        raw = payload.encode("utf-8") if isinstance(payload, str) else payload
        if len(raw) > MAX_REQUEST_BYTES:
            raise PreviewBridgeError("preview request exceeds 8 KiB")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PreviewBridgeError("preview request is not valid JSON") from exc
        if not isinstance(value, dict):
            raise PreviewBridgeError("preview request must be an object")
        operation = value.get("operation")
        remote_path = value.get("remote_path")
        endpoint_digest = value.get("endpoint_digest")
        session_id = value.get("session_id")
        nonce = value.get("nonce")
        request_id = value.get("request_id") or uuid.uuid4().hex
        if operation not in {"get", "open"}:
            raise PreviewBridgeError("unsupported preview operation")
        if not isinstance(remote_path, str) or not remote_path or len(remote_path.encode()) > MAX_PATH_BYTES:
            raise PreviewBridgeError("remote_path is invalid")
        for name, item, limit in (
            ("endpoint_digest", endpoint_digest, 128),
            ("session_id", session_id, 128),
            ("nonce", nonce, 256),
            ("request_id", request_id, 128),
        ):
            if not isinstance(item, str) or not item or len(item) > limit:
                raise PreviewBridgeError(f"{name} is invalid")
        return cls(operation, remote_path, endpoint_digest, session_id, nonce, request_id)


class PreviewBridge:
    """A loopback-only, one-session request listener.

    The listener returns an acknowledgement immediately.  The callback is
    responsible for queueing the actual download, so a remote request cannot
    hold open the interactive SSH session.
    """

    def __init__(self, context: PreviewContext, on_request: Callable[[PreviewRequest], None] | None = None) -> None:
        self.context = context
        self.on_request = on_request
        self._listener: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def local_port(self) -> int:
        if self._listener is None:
            return self.context.local_port
        address = self._listener.getsockname()
        return int(address[1])

    def start(self) -> PreviewContext:
        if self._listener is not None:
            return PreviewContext(self.context.endpoint_digest, self.context.session_id, self.context.nonce, self.local_port, self.context.remote_port)
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", self.context.local_port))
        listener.listen(8)
        listener.settimeout(0.2)
        self._listener = listener
        self._thread = threading.Thread(target=self._serve, name="tssh-preview-bridge", daemon=True)
        self._thread.start()
        return PreviewContext(self.context.endpoint_digest, self.context.session_id, self.context.nonce, self.local_port, self.context.remote_port)

    def stop(self) -> None:
        self._stop.set()
        listener, self._listener = self._listener, None
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=1)
            self._thread = None

    def _serve(self) -> None:
        while not self._stop.is_set():
            listener = self._listener
            if listener is None:
                return
            try:
                conn, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), name="tssh-preview-request", daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        with conn:
            conn.settimeout(2.0)
            data = bytearray()
            try:
                while len(data) <= MAX_REQUEST_BYTES:
                    chunk = conn.recv(min(4096, MAX_REQUEST_BYTES + 1 - len(data)))
                    if not chunk:
                        break
                    data.extend(chunk)
                    if b"\n" in chunk:
                        break
                request = PreviewRequest.decode(bytes(data).split(b"\n", 1)[0])
                if (
                    request.endpoint_digest != self.context.endpoint_digest
                    or request.session_id != self.context.session_id
                    or not secrets.compare_digest(request.nonce, self.context.nonce)
                ):
                    raise PreviewBridgeError("preview request context mismatch")
                if self.on_request is not None:
                    self.on_request(request)
                response: dict[str, Any] = {"ok": True, "request_id": request.request_id, "queued": True}
            except (PreviewBridgeError, ValueError, OSError, socket.timeout) as exc:
                response = {"ok": False, "error": str(exc)}
            try:
                conn.sendall((json.dumps(response, separators=(",", ":")) + "\n").encode("utf-8"))
            except OSError:
                pass


def new_context(endpoint_digest: str, session_id: str, *, local_port: int = 0, remote_port: int = 0) -> PreviewContext:
    """Create a non-secret-in-logs session context with an ephemeral port."""
    if not endpoint_digest or not session_id:
        raise PreviewBridgeError("endpoint_digest and session_id are required")
    return PreviewContext(endpoint_digest, session_id, secrets.token_urlsafe(32), local_port, remote_port)
