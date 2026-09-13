"""对用户可读且可测试的编排层错误码。"""

from enum import StrEnum


class OrchestratorErrorCode(StrEnum):
    PROTOCOL_INVALID = "PROTOCOL_INVALID"
    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    MASTER_UNAVAILABLE = "MASTER_UNAVAILABLE"
    FORWARDING_DISABLED = "FORWARDING_DISABLED"
    PORT_CONFLICT = "PORT_CONFLICT"
    OAUTH_BUSY = "OAUTH_BUSY"
    OAUTH_TIMEOUT = "OAUTH_TIMEOUT"
    CLEANUP_FAILED = "CLEANUP_FAILED"


class OrchestratorError(RuntimeError):
    def __init__(self, code: OrchestratorErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
