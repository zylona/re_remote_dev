"""Manage the small, opt-in local SSH integration used by remote-dev."""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path


BEGIN = "# >>> remote-dev ssh integration >>>"
END = "# <<< remote-dev ssh integration <<<"
INCLUDE = "Include ~/.config/remote-dev/ssh_config"  # legacy marker, removed on migration


def _managed_block(proxy_port: int = 4227, control_port: int = 4228, local_control_port: int = 4230) -> str:
    return "\n".join(
        [
            BEGIN,
            "Host * !github.com",
            "  ControlMaster auto",
            "  ControlPersist 600",
            "  ControlPath ~/.ssh/remote-dev/cm/%C",
            "  ExitOnForwardFailure yes",
            "  ServerAliveInterval 30",
            "  ServerAliveCountMax 3",
            END,
            "",
        ]
    )


def _target_block(host: str, control_path: Path, identity_file: Path, *, proxy_port: int = 4227, control_port: int = 4228, local_control_port: int = 4230) -> str:
    return "\n".join([
        f"# >>> remote-dev target {host} >>>",
        f"Host {host}",
        # Keep user shells on independent TCP connections.  The persistent
        # master carries proxy/Codex traffic; sharing it causes head-of-line
        # blocking and makes every interactive window lag under load.
        "  ControlMaster no",
        "  ControlPath none",
        f"  IdentityFile {identity_file.expanduser()}",
        "  ExitOnForwardFailure yes",
        "  ServerAliveInterval 30",
        "  ServerAliveCountMax 3",
        f"# <<< remote-dev target {host} <<<",
        "",
    ])


def _atomic_write(path: Path, content: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _without_managed_block(content: str) -> str:
    pattern = rf"(?:^|\n){re.escape(BEGIN)}\n.*?{re.escape(END)}\n?(?:\n)?"
    return re.sub(pattern, "\n", content, flags=re.DOTALL)


def install(*, home: Path | None = None, proxy_port: int = 4227) -> Path:
    """Install one idempotent shared SSH master/forwarding block."""
    root = home or Path.home()
    ssh_dir = root / ".ssh"
    config = ssh_dir / "config"
    ssh_dir.mkdir(parents=True, exist_ok=True)
    ssh_dir.chmod(0o700)
    original = config.read_text(encoding="utf-8") if config.exists() else ""
    cleaned = _without_managed_block(original)
    # Migrate the old external-include implementation if present.
    cleaned = re.sub(rf"^\s*{re.escape(INCLUDE)}\s*$\n?", "", cleaned, flags=re.MULTILINE)
    # Remove exact options from the previous manual-forwarding implementation;
    # user-specific values remain untouched unless they are these old project
    # defaults, which would otherwise win due to ssh_config first-value rules.
    cleaned = re.sub(r"^\s*ControlMaster\s+no\s*$\n?", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"^\s*ControlPath\s+none\s*$\n?", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"^\s*RemoteForward\s+127\.0\.0\.1:4227\s+127\.0\.0\.1:4227\s*$\n?", "", cleaned, flags=re.MULTILINE)
    cleaned = cleaned.rstrip("\n") + "\n" + _managed_block(proxy_port=proxy_port)
    if cleaned != original:
        _atomic_write(config, cleaned, 0o600)
    return config


def install_target(*, host: str, control_path: Path, identity_file: Path, home: Path | None = None, proxy_port: int = 4227) -> Path:
    """Insert a target-specific block before the generic block so %C cannot win."""
    root = home or Path.home()
    config = root / ".ssh" / "config"
    original = config.read_text(encoding="utf-8") if config.exists() else ""
    begin = f"# >>> remote-dev target {host} >>>"
    end = f"# <<< remote-dev target {host} <<<"
    cleaned = re.sub(rf"(?:^|\n){re.escape(begin)}\n.*?{re.escape(end)}\n?(?:\n)?", "\n", original, flags=re.DOTALL)
    block = _target_block(host, control_path, identity_file, proxy_port=proxy_port)
    marker = cleaned.find(BEGIN)
    content = cleaned[:marker] + block + cleaned[marker:] if marker >= 0 else cleaned.rstrip("\n") + "\n" + block
    if content != original:
        _atomic_write(config, content, 0o600)
    return config


def uninstall(*, home: Path | None = None) -> Path:
    """Remove the project's marked block and legacy fragment, never user rules."""
    root = home or Path.home()
    config = root / ".ssh" / "config"
    if config.exists():
        cleaned = _without_managed_block(config.read_text(encoding="utf-8"))
        cleaned = cleaned.strip("\n")
        _atomic_write(config, (cleaned + "\n") if cleaned else "", 0o600)
    fragment = root / ".config" / "remote-dev" / "ssh_config"
    if fragment.exists():
        fragment.unlink()
    return config
