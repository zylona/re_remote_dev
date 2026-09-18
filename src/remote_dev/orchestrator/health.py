"""Failure classification and bounded retry policy for endpoint forwarders."""

from __future__ import annotations

import re


RETRY_DELAYS = (5, 10, 20, 40, 80, 160, 300)


def retry_delay(failures: int) -> float:
    """Return a bounded exponential delay; ``failures`` is one-based."""
    return float(RETRY_DELAYS[min(max(failures, 1) - 1, len(RETRY_DELAYS) - 1)])


def classify_failure(message: str) -> str:
    """Map SSH/systemd diagnostics to a stable, secret-free category."""
    text = message.lower()
    if re.search(r"permission denied|publickey|authentication", text):
        return "SSH_AUTH_FAILED"
    if re.search(r"administratively prohibited|forwarding.*denied|open failed", text):
        return "FORWARDING_DENIED"
    if re.search(r"address already in use|cannot listen|port.*busy", text):
        return "REMOTE_PORT_BUSY"
    if re.search(r"timed out|connection refused|network is unreachable|no route|unreachable", text):
        return "REMOTE_UNREACHABLE"
    if re.search(r"proxy.*(unavailable|refused)|4227", text):
        return "LOCAL_PROXY_UNAVAILABLE"
    return "FORWARDER_FAILED"
