"""本地 SSH/Codex 编排层的协议与状态模型。"""

from .model import MasterState, OAuthState, TargetKey, TargetStatus
from .master import MasterManager
from .oauth import OAuthError, OAuthManager, OAuthSession
from .session import ManagedSession, SessionManager
from .persistent import install as install_persistent, remove as remove_persistent, unit_name, unit_text
from .protocol import Event, ProtocolError, decode_event, encode_event

__all__ = [
    "Event",
    "MasterState",
    "MasterManager",
    "OAuthError",
    "OAuthManager",
    "OAuthSession",
    "ManagedSession",
    "SessionManager",
    "install_persistent",
    "remove_persistent",
    "unit_name",
    "unit_text",
    "OAuthState",
    "ProtocolError",
    "TargetKey",
    "TargetStatus",
    "decode_event",
    "encode_event",
]
