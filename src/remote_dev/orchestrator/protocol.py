"""远端 shim 与本地编排器之间的 v1 单行 JSON 协议。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 8 * 1024
_ALLOWED_EVENTS = {"HELLO", "CODEX_START", "CODEX_EXIT", "CODEX_OAUTH_URL", "HEALTH"}


class ProtocolError(ValueError):
    """协议消息不可信、过大或版本不兼容。"""


@dataclass(frozen=True, slots=True)
class Event:
    event: str
    target: str
    nonce: str
    pid: int | None = None
    exit_code: int | None = None
    args_mode: str | None = None
    oauth_url: str | None = None
    version: int = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        if self.version != PROTOCOL_VERSION:
            raise ProtocolError(f"不支持的协议版本: {self.version}")
        if self.event not in _ALLOWED_EVENTS:
            raise ProtocolError(f"不支持的事件: {self.event}")
        if not isinstance(self.target, str) or not self.target or len(self.target) > 128:
            raise ProtocolError("target 无效")
        if not isinstance(self.nonce, str) or not self.nonce or len(self.nonce) > 256:
            raise ProtocolError("nonce 无效")
        if self.pid is not None and (not isinstance(self.pid, int) or self.pid < 1):
            raise ProtocolError("pid 无效")

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "v": self.version,
            "event": self.event,
            "target": self.target,
            "nonce": self.nonce,
        }
        for key, value in (
            ("pid", self.pid),
            ("exit_code", self.exit_code),
            ("args_mode", self.args_mode),
            ("oauth_url", self.oauth_url),
        ):
            if value is not None:
                data[key] = value
        return data


def encode_event(event: Event) -> bytes:
    """编码为带换行的 UTF-8 单行消息，并限制大小。"""

    raw = (json.dumps(event.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )
    if len(raw) > MAX_MESSAGE_BYTES:
        raise ProtocolError("协议消息超过 8 KiB 限制")
    return raw


def decode_event(payload: bytes | str) -> Event:
    """严格解析一条消息；未知字段允许保留以支持向后兼容。"""

    raw = payload.encode("utf-8") if isinstance(payload, str) else payload
    if len(raw) > MAX_MESSAGE_BYTES:
        raise ProtocolError("协议消息超过 8 KiB 限制")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("协议消息不是有效 JSON") from exc
    if not isinstance(data, dict):
        raise ProtocolError("协议消息必须是 JSON object")
    try:
        version = data["v"]
        event = data["event"]
        target = data["target"]
        nonce = data["nonce"]
    except KeyError as exc:
        raise ProtocolError(f"缺少协议字段: {exc.args[0]}") from exc
    if not isinstance(version, int) or not isinstance(event, str):
        raise ProtocolError("协议字段类型无效")
    return Event(
        version=version,
        event=event,
        target=target,
        nonce=nonce,
        pid=data.get("pid"),
        exit_code=data.get("exit_code"),
        args_mode=data.get("args_mode"),
        oauth_url=data.get("oauth_url"),
    )
