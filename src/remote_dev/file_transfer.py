"""P0 contract primitives for remote-file download and preview.

This module deliberately contains no SSH execution or GUI launching.  It
defines the stable identity, path and cache rules used by the later bridge and
the remote ``rget``/``rdo`` helpers.
"""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


SIDECAR_VERSION = 1


class FileTransferError(ValueError):
    """A user-correctable remote-file contract error."""


@dataclass(frozen=True, slots=True)
class EndpointIdentity:
    hostname: str
    port: int = 22
    user: str = ""
    identity_fingerprint: str = ""

    def digest(self) -> str:
        """Return a stable, non-secret endpoint identifier."""
        payload = {
            "hostname": self.hostname,
            "port": self.port,
            "user": self.user,
            "identity_fingerprint": self.identity_fingerprint,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()[:32]


def canonical_remote_path(value: str, cwd: str = "/") -> str:
    """Resolve a remote POSIX path without touching the local filesystem."""
    if not isinstance(value, str) or not value or "\x00" in value:
        raise FileTransferError("远程路径不能为空或包含 NUL 字符")
    if not isinstance(cwd, str) or not cwd.startswith("/"):
        raise FileTransferError("远程当前目录必须是绝对路径")
    if value.startswith("~"):
        raise FileTransferError("请使用远程绝对路径或相对路径，不支持未展开的 ~")
    joined = value if value.startswith("/") else posixpath.join(cwd, value)
    normalized = posixpath.normpath(joined)
    if not normalized.startswith("/"):
        raise FileTransferError("远程路径解析后不是绝对路径")
    return normalized


def path_digest(remote_path: str) -> str:
    return hashlib.sha256(remote_path.encode("utf-8")).hexdigest()[:32]


def safe_basename(remote_path: str) -> str:
    """Return a display-safe basename while retaining its extension."""
    name = posixpath.basename(remote_path) or "root"
    name = re.sub(r"[\x00-\x1f\x7f/\\]", "_", name).strip() or "remote-file"
    return name[:240]


def _safe_component(value: str) -> str:
    """Keep a remote path component inside the local cache namespace."""
    component = re.sub(r"[\x00-\x1f\x7f/\\]", "_", value)
    if component in {"", ".", ".."}:
        return "_"
    return component[:240]


@dataclass(frozen=True, slots=True)
class CacheLayout:
    cache_root: Path
    download_root: Path

    @classmethod
    def defaults(cls, home: Path | None = None) -> "CacheLayout":
        base = home or Path.home()
        cache = Path(os.environ.get("XDG_CACHE_HOME", base / ".cache")) / "tssh" / "rdo"
        downloads = base / "Downloads" / "remote-dev"
        return cls(cache_root=cache, download_root=downloads)

    def preview_path(self, endpoint: EndpointIdentity, remote_path: str) -> Path:
        return self.cache_root / endpoint.digest() / path_digest(remote_path) / safe_basename(remote_path)

    def download_path(self, endpoint: EndpointIdentity, remote_path: str) -> Path:
        """Map an absolute remote path below an endpoint-isolated directory."""
        relative = remote_path.lstrip("/") or "root"
        parts = [_safe_component(part) for part in relative.split("/")]
        return self.download_root / endpoint.digest() / Path(*parts)


def sidecar_path(local_path: Path) -> Path:
    return local_path.with_name(local_path.name + ".rdo.json")


def read_sidecar(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def sidecar_payload(
    endpoint: EndpointIdentity,
    remote_path: str,
    remote_size: int,
    remote_mtime_ns: int,
    local_sha256: str,
    remote_sha256: str | None = None,
) -> dict[str, Any]:
    """Build the versioned metadata written next to a completed download."""
    payload: dict[str, Any] = {
        "schema_version": SIDECAR_VERSION,
        "endpoint_digest": endpoint.digest(),
        "remote_path": remote_path,
        "remote_size": remote_size,
        "remote_mtime_ns": remote_mtime_ns,
        "local_sha256": local_sha256,
    }
    if remote_sha256:
        payload["remote_sha256"] = remote_sha256
    return payload


def metadata_matches(
    local_path: Path,
    sidecar: Mapping[str, Any] | None,
    endpoint: EndpointIdentity,
    remote_path: str,
    remote_size: int,
    remote_mtime_ns: int,
) -> bool:
    """Fast, non-cryptographic cache hit check used before a download."""
    if not local_path.is_file() or not sidecar:
        return False
    expected = {
        "endpoint_digest": endpoint.digest(),
        "remote_path": remote_path,
        "remote_size": remote_size,
        "remote_mtime_ns": remote_mtime_ns,
    }
    if any(sidecar.get(key) != value for key, value in expected.items()):
        return False
    return bool(local_path.stat().st_size == remote_size and sidecar.get("local_sha256"))
