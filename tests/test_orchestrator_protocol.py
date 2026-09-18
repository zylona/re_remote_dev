import json
import os
import socket
import threading
import time

import pytest

from remote_dev.orchestrator import (
    EndpointKey,
    Event,
    MasterState,
    ProtocolError,
    TargetKey,
    decode_event,
    encode_event,
)
from remote_dev.orchestrator.server import OrchestratorServer


def test_target_digest_is_stable_and_identity_aware():
    first = TargetKey("example.test", 22, "developer", "key-a")
    same = TargetKey("example.test", 22, "developer", "key-a")
    other = TargetKey("example.test", 22, "developer", "key-b")
    assert first.digest == same.digest
    assert first.digest != other.digest
    assert len(first.digest) == 32
    assert first.canonical not in first.digest


def test_event_round_trip_and_unknown_fields():
    event = Event("CODEX_START", "target-hash", "nonce", pid=123, args_mode="normal")
    encoded = encode_event(event)
    decoded = decode_event(encoded[:-1] + b'\n')
    assert decoded == event
    payload = json.loads(encoded)
    payload["future"] = "ignored"
    assert decode_event(json.dumps(payload)).target == "target-hash"


@pytest.mark.parametrize(
    "payload",
    [
        b"{}",
        b'{"v":2,"event":"HEALTH","target":"t","nonce":"n"}',
        b'{"v":1,"event":"NOPE","target":"t","nonce":"n"}',
        b'{"v":1,"event":"HEALTH","target":"t","nonce":""}',
        b"[1, 2, 3]",
    ],
)
def test_invalid_event_is_rejected(payload):
    with pytest.raises(ProtocolError):
        decode_event(payload)


def test_protocol_message_size_is_limited():
    event = Event("HEALTH", "target", "n")
    payload = json.loads(encode_event(event))
    payload["padding"] = "x" * 9000
    with pytest.raises(ProtocolError, match="8 KiB"):
        decode_event(json.dumps(payload))


def test_model_enums_are_stable():
    assert MasterState.READY.value == "READY"
    with pytest.raises(ValueError):
        TargetKey("", 22, "user")
    with pytest.raises(ValueError):
        TargetKey("host", 0, "user")
    assert EndpointKey("host", 22).digest == EndpointKey("host", 22).digest
    assert EndpointKey("host", 22).digest != EndpointKey("other", 22).digest


def test_endpoint_acquire_is_idempotent_and_release_keeps_other_session():
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server = OrchestratorServer(listener, None)
    endpoint = {"hostname": "host", "port": 22}
    first = server.handle({"op": "acquire", "endpoint": endpoint, "candidate": {"user": "a", "identity_fingerprint": "key-a"}, "session_id": "s1"})
    again = server.handle({"op": "acquire", "endpoint": endpoint, "candidate": {"user": "a", "identity_fingerprint": "key-a"}, "session_id": "s1"})
    second = server.handle({"op": "acquire", "endpoint": endpoint, "candidate": {"user": "b", "identity_fingerprint": "key-b"}, "session_id": "s2"})
    assert first["ok"] and first["reused"] is False
    assert again["reused"] is True and again["session_count"] == 1
    assert second["reused"] is True and second["owner"] == "a"
    released = server.handle({"op": "release", "endpoint": endpoint, "session_id": "s1"})
    assert released["session_count"] == 1
    assert server.handle({"op": "release", "endpoint": endpoint, "session_id": "s2"})["released"] is True
    assert server.handle({"op": "status"})["endpoints"] == []
    listener.close()


def test_endpoint_owner_forwarder_is_started_once_and_reassigned(monkeypatch, tmp_path):
    class FakeForwarder:
        def __init__(self):
            self.started = []
            self.stopped = []
        def start(self, endpoint, user, identity):
            self.started.append((endpoint, user, identity))
        def stop(self, endpoint, user=None):
            self.stopped.append((endpoint, user))

    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    forwarder = FakeForwarder()
    server = OrchestratorServer(listener, None, endpoint_forwarder=forwarder)
    endpoint = {"hostname": "host", "port": 22}
    server.handle({"op": "acquire", "endpoint": endpoint, "candidate": {"user": "b", "identity_file": str(tmp_path / "b")}, "session_id": "s1"})
    server.handle({"op": "acquire", "endpoint": endpoint, "candidate": {"user": "a", "identity_file": str(tmp_path / "a")}, "session_id": "s2"})
    assert len(forwarder.started) == 1
    server.handle({"op": "release", "endpoint": endpoint, "session_id": "s1"})
    assert len(forwarder.started) == 2
    assert forwarder.started[-1][1] == "a"
    server.handle({"op": "release", "endpoint": endpoint, "session_id": "s2"})
    assert forwarder.stopped
    listener.close()


def test_degraded_endpoint_retries_with_bounded_backoff(tmp_path):
    class FlakyForwarder:
        def __init__(self):
            self.calls = 0
        def start(self, endpoint, user, identity):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("connection timed out")
        def stop(self, endpoint, user=None):
            pass

    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    forwarder = FlakyForwarder()
    server = OrchestratorServer(listener, None, endpoint_forwarder=forwarder)
    endpoint = {"hostname": "host", "port": 22}
    first = server.handle({"op": "acquire", "endpoint": endpoint, "candidate": {"user": "u", "identity_file": str(tmp_path / "id")}, "session_id": "s1"})
    assert first["state"] == "DEGRADED"
    record = server.endpoints[first["endpoint_digest"]]
    assert record["last_error"] == "REMOTE_UNREACHABLE"
    record["next_retry"] = 0
    server.handle({"op": "status"})
    assert server.endpoints[first["endpoint_digest"]]["state"] == "READY"
    assert server.endpoints[first["endpoint_digest"]]["failure_count"] == 0
    listener.close()


def test_endpoint_session_cannot_move_between_endpoints():
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server = OrchestratorServer(listener, None)
    server.handle({"op": "acquire", "endpoint": {"hostname": "a"}, "candidate": {"user": "u"}, "session_id": "s"})
    result = server.handle({"op": "acquire", "endpoint": {"hostname": "b"}, "candidate": {"user": "u"}, "session_id": "s"})
    assert result == {"ok": False, "error": "SESSION_CONFLICT"}
    listener.close()


def test_heartbeat_refreshes_lease_and_ttl_releases_orphan():
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server = OrchestratorServer(listener, None)
    server.lease_ttl = 0.01
    endpoint = {"hostname": "host", "port": 22}
    acquired = server.handle({"op": "acquire", "endpoint": endpoint, "candidate": {"user": "u"}, "session_id": "live"})
    assert acquired["ok"]
    time.sleep(0.005)
    heartbeat = server.handle({"op": "heartbeat", "endpoint": endpoint, "session_id": "live"})
    assert heartbeat == {"ok": True, "endpoint_digest": acquired["endpoint_digest"], "session_count": 1}
    time.sleep(0.02)
    assert server.handle({"op": "status"})["endpoints"] == []
    listener.close()


def test_heartbeat_registration_preserves_live_state():
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server = OrchestratorServer(listener, None)
    target = {"hostname": "host", "port": 22, "user": "u"}
    server.handle({"op": "register", "target": target, "control_path": "/tmp/cm", "proxy_available": True})
    key = TargetKey("host", 22, "u")
    server.handle({"v": 1, "event": "CODEX_START", "target": key.digest, "nonce": "n", "pid": 7})
    server.handle({"op": "register", "target": target, "control_path": "/tmp/cm", "proxy_available": True})
    status = server.handle({"op": "status"})["targets"][0]
    assert status["master"] == "CODEX_RUNNING"
    assert status["session_count"] == 1
    listener.close()


def test_server_register_status_and_shutdown():
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    path = "/tmp/remote-dev-test-orchestrator.sock"
    try:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        listener.bind(path)
        listener.listen(4)
        server = OrchestratorServer(listener, None)
        thread = threading.Thread(target=server.serve, daemon=True)
        thread.start()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(path)
            client.sendall(b'{"op":"register","target":{"hostname":"host","port":22,"user":"u"}}\n')
            response = json.loads(client.recv(4096))
        assert response["ok"] is True
        assert response["digest"]
        assert server.handle({"op": "status"})["targets"][0]["master"] == "ABSENT"
        assert server.handle({"op": "shutdown"}) == {"ok": True}
        thread.join(timeout=2)
    finally:
        try:
            listener.close()
        except OSError:
            pass
        try:
            os.unlink(path)
        except (FileNotFoundError, OSError):
            pass


def test_server_accepts_codex_lifecycle_events():
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    path = "/tmp/remote-dev-test-events.sock"
    try:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        listener.bind(path)
        listener.listen(4)
        server = OrchestratorServer(listener, None)
        thread = threading.Thread(target=server.serve, daemon=True)
        thread.start()
        target = TargetKey("host", 22, "u")
        assert server.handle({"op": "register", "target": {"hostname": "host", "port": 22, "user": "u"}})["ok"]
        assert server.handle({"v": 1, "event": "CODEX_START", "target": target.digest, "nonce": "n", "pid": 7})["ok"]
        status = server.handle({"op": "status"})["targets"][0]
        assert status["master"] == "CODEX_RUNNING"
        assert status["session_count"] == 1
        assert server.handle({"v": 1, "event": "CODEX_EXIT", "target": target.digest, "nonce": "n", "pid": 7, "exit_code": 0})["ok"]
        assert server.handle({"op": "status"})["targets"][0]["session_count"] == 0
        server.handle({"op": "shutdown"})
        thread.join(timeout=2)
    finally:
        try:
            listener.close()
        except OSError:
            pass
        try:
            os.unlink(path)
        except (FileNotFoundError, OSError):
            pass


def test_codex_start_automatically_requests_callback_forward(monkeypatch):
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server = OrchestratorServer(listener, None)
    target = TargetKey("host", 22, "u")
    calls = []
    monkeypatch.setattr(server.oauth, "start", lambda target, **kwargs: calls.append((target, kwargs)))
    server.handle({"op": "register", "target": {"hostname": "host", "port": 22, "user": "u"}, "control_path": "/tmp/cm"})
    result = server.handle({"v": 1, "event": "CODEX_START", "target": target.digest, "nonce": "n", "pid": 9})
    assert result["ok"] is True
    assert calls == [(target, {"control_path": "/tmp/cm"})]
    listener.close()


def test_oauth_url_event_opens_local_browser(monkeypatch):
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server = OrchestratorServer(listener, None)
    target = TargetKey("host", 22, "u")
    opened = []
    monkeypatch.setattr(server, "_open_browser", lambda url: opened.append(url))
    server.handle({"op": "register", "target": {"hostname": "host", "port": 22, "user": "u"}, "control_path": "/tmp/cm"})
    result = server.handle({"v": 1, "event": "CODEX_OAUTH_URL", "target": target.digest, "nonce": "n", "oauth_url": "http://localhost:1455/auth/callback?x=1"})
    assert result["ok"] is True
    assert opened == ["http://localhost:1455/auth/callback?x=1"]
    listener.close()
