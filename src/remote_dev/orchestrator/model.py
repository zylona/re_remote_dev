"""编排层的稳定领域模型。

这些类型只描述事实和身份，不负责启动进程、修改 SSH 配置或执行远端命令。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256


class MasterState(StrEnum):
    ABSENT = "ABSENT"
    MASTER_STARTING = "MASTER_STARTING"
    READY = "READY"
    CODEX_RUNNING = "CODEX_RUNNING"
    OAUTH_PENDING = "OAUTH_PENDING"
    AUTHENTICATED = "AUTHENTICATED"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"


class OAuthState(StrEnum):
    NONE = "NONE"
    PENDING = "PENDING"
    AUTHENTICATED = "AUTHENTICATED"
    FAILED = "FAILED"


class ForwardState(StrEnum):
    """Lifecycle state for an isolated SSH forwarding process."""

    ABSENT = "ABSENT"
    STARTING = "STARTING"
    READY = "READY"
    DEGRADED = "DEGRADED"
    STOPPING = "STOPPING"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class ForwardStatus:
    """Observable, secret-free state of one dedicated forwarder."""

    kind: str
    state: ForwardState = ForwardState.ABSENT
    pid: int | None = None
    local_port: int | None = None
    remote_port: int | None = None
    last_error: str | None = None
    last_success: float | None = None


@dataclass(frozen=True, slots=True)
class TargetKey:
    """SSH 目标的规范身份；别名不能单独决定连接隔离。"""

    hostname: str
    port: int
    user: str
    identity_fingerprint: str = ""

    def __post_init__(self) -> None:
        if not self.hostname.strip():
            raise ValueError("hostname 不能为空")
        if not 1 <= self.port <= 65535:
            raise ValueError("port 必须位于 1..65535")
        if not self.user.strip():
            raise ValueError("user 不能为空")

    @property
    def canonical(self) -> str:
        return f"{self.user}@{self.hostname}:{self.port}|{self.identity_fingerprint}"

    @property
    def digest(self) -> str:
        """用于 ControlPath/日志关联的不可逆短标识，不暴露完整身份。"""

        return sha256(self.canonical.encode("utf-8")).hexdigest()[:32]

    @property
    def endpoint_digest(self) -> str:
        """Stable identity for one remote network namespace, independent of user."""
        return sha256(f"{self.hostname}:{self.port}|".encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True, slots=True)
class EndpointKey:
    """Identity of one remote network namespace, independent of SSH user."""

    hostname: str
    port: int = 22

    def __post_init__(self) -> None:
        if not self.hostname.strip():
            raise ValueError("hostname 不能为空")
        if not 1 <= self.port <= 65535:
            raise ValueError("port 必须位于 1..65535")

    @property
    def canonical(self) -> str:
        return f"{self.hostname}:{self.port}|"

    @property
    def digest(self) -> str:
        return sha256(self.canonical.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True, slots=True)
class TargetStatus:
    target: TargetKey
    master: MasterState = MasterState.ABSENT
    oauth: OAuthState = OAuthState.NONE
    control_path: str | None = None
    proxy_available: bool = False
    session_count: int = 0
    proxy_forward: ForwardStatus = field(default_factory=lambda: ForwardStatus("proxy"))
    event_forward: ForwardStatus = field(default_factory=lambda: ForwardStatus("event"))
    oauth_forward: ForwardStatus = field(default_factory=lambda: ForwardStatus("oauth"))
