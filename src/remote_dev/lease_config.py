"""User-tunable tssh lease timing without storing credentials."""

from __future__ import annotations

import configparser
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class LeaseSettings:
    heartbeat_interval: float = 1.0
    lease_ttl: float = 3.0


@dataclass(frozen=True, slots=True)
class ProxyEndpoint:
    host: str = "127.0.0.1"
    port: int = 4227


def config_path() -> Path:
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "tssh" / "config"


def load_proxy() -> ProxyEndpoint:
    """Read the user-selected local HTTP proxy endpoint without probing it."""
    parser = configparser.ConfigParser()
    try:
        parser.read(config_path(), encoding="utf-8")
        section = parser["proxy"] if parser.has_section("proxy") else {}
        host = str(section.get("host", "127.0.0.1")).strip()
        raw_port = section.get("port", "4227")
        port = int(raw_port)
    except (OSError, configparser.Error, TypeError, ValueError) as exc:
        raise ValueError(f"tssh 代理配置无效：{exc}") from exc
    endpoint = ProxyEndpoint(host, port)
    validate_proxy(endpoint)
    return endpoint


def validate_proxy(endpoint: ProxyEndpoint) -> None:
    if (
        not endpoint.host
        or any(char.isspace() for char in endpoint.host)
        or "://" in endpoint.host
        or "/" in endpoint.host
        or not 1 <= endpoint.port <= 65535
    ):
        raise ValueError("代理地址无效；请提供主机名/IP 和 1-65535 范围内的端口")


def save_proxy(endpoint: ProxyEndpoint) -> Path:
    """Atomically update only the [proxy] values in the shared tssh config."""
    validate_proxy(endpoint)
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    parser = configparser.ConfigParser()
    try:
        parser.read(path, encoding="utf-8")
    except (OSError, configparser.Error) as exc:
        raise ValueError(f"无法读取 tssh 配置：{exc}") from exc
    if not parser.has_section("proxy"):
        parser.add_section("proxy")
    parser.set("proxy", "host", endpoint.host)
    parser.set("proxy", "port", str(endpoint.port))
    fd, temporary = tempfile.mkstemp(prefix=".config.", dir=path.parent, text=True)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            parser.write(stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return path


def load() -> LeaseSettings:
    """Load safe, non-secret timing settings shared by client and server.

    Environment variables are useful for tests and one-off invocations; the
    user config is the persistent setting used by both the CLI and systemd
    user service. Invalid values fall back to defaults rather than making the
    SSH orchestrator unavailable.
    """
    defaults = LeaseSettings()
    parser = configparser.ConfigParser()
    try:
        parser.read(config_path(), encoding="utf-8")
    except (OSError, configparser.Error):
        parser = configparser.ConfigParser()
    section = parser["lease"] if parser.has_section("lease") else {}

    def number(name: str, fallback: float) -> float:
        raw = os.environ.get(f"TSSH_{name.upper()}", section.get(name, str(fallback)))
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return fallback
        return value if value > 0 else fallback

    heartbeat = number("heartbeat_interval", defaults.heartbeat_interval)
    ttl = number("lease_ttl", defaults.lease_ttl)
    # A lease must tolerate at least two missed heartbeats. If a user enters
    # an unsafe combination, preserve availability with a sane relationship.
    if ttl <= heartbeat * 2:
        ttl = heartbeat * 3
    return LeaseSettings(heartbeat_interval=heartbeat, lease_ttl=ttl)
