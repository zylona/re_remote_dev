"""User-tunable tssh lease timing without storing credentials."""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class LeaseSettings:
    heartbeat_interval: float = 1.0
    lease_ttl: float = 3.0


def config_path() -> Path:
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "tssh" / "config"


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
