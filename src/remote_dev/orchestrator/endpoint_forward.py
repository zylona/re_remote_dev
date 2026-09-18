"""Endpoint-scoped 4227 forwarder lifecycle for the P3 lease protocol."""

from __future__ import annotations

from pathlib import Path

from .model import EndpointKey, TargetKey
from .proxy_persistent import ensure, remove


class EndpointForwardManager:
    """Start at most one systemd proxy unit for an endpoint owner."""

    def start(self, endpoint: EndpointKey, user: str, identity_file: Path) -> None:
        ensure(TargetKey(endpoint.hostname, endpoint.port, user), identity_file)

    def stop(self, endpoint: EndpointKey, user: str | None = None) -> None:
        # ``remove`` derives the endpoint-scoped unit name, so the user is not
        # part of ownership and is only retained for API/readability.
        remove(TargetKey(endpoint.hostname, endpoint.port, user or "owner"))
