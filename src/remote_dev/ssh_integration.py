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
            # Keep ordinary interactive shells on independent TCP sessions.
            # The proxy hook owns a separate per-target systemd tunnel; sharing
            # a ControlMaster would bypass LocalCommand and can add HOL delay.
            "  ControlMaster no",
            "  ControlPath none",
            "  ExitOnForwardFailure yes",
            "  ServerAliveInterval 30",
            "  ServerAliveCountMax 3",
            "  PermitLocalCommand yes",
            "  LocalCommand ~/.local/bin/remote-dev-ssh-hook %h %p %r",
            END,
            "",
        ]
    )


_MANAGED_OPTION_RE = re.compile(
    r"^[ \t]+(?:ControlMaster\s+no|ControlPath\s+none|ExitOnForwardFailure\s+yes|"
    r"ServerAliveInterval\s+30|ServerAliveCountMax\s+3|PermitLocalCommand\s+yes|"
    r"LocalCommand\s+~/.local/bin/remote-dev-ssh-hook\s+%h\s+%p\s+%r)\s*$\n?",
    re.MULTILINE,
)


def _merge_global_block(content: str, *, proxy_port: int = 4227) -> str:
    """Embed the managed forwarding options into the existing global block."""
    match = re.search(r"(?m)^Host[ \t]+\*[ \t]+!github\.com[ \t]*\n", content)
    if match:
        next_block = re.search(r"(?m)^(?:Host|Match)[ \t]+", content[match.end():])
        end = match.end() + (next_block.start() if next_block else len(content[match.end():]))
        body = _MANAGED_OPTION_RE.sub("", content[match.end():end]).strip("\n")
        managed = _managed_block(proxy_port=proxy_port)
        replacement = content[match.start():match.end()] + ((body + "\n") if body else "") + managed
        return content[:match.start()] + replacement + content[end:]
    suffix = content.rstrip("\n")
    if suffix:
        suffix += "\n\n"
    return suffix + "Host * !github.com\n" + _managed_block(proxy_port=proxy_port)


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
    cleaned = re.sub(pattern, "\n", content, flags=re.DOTALL)
    # If the installer created the global block solely for its managed rules,
    # remove that now-empty Host stanza during uninstall.
    return re.sub(r"(?m)^Host[ \t]+\*[ \t]+!github\.com[ \t]*\n(?:[ \t]*\n)*(?=(?:Host|Match|\Z))", "", cleaned)


def _without_target_blocks(content: str) -> str:
    """Remove legacy per-target blocks created by older releases."""
    return re.sub(
        r"(?:^|\n)# >>> remote-dev target [^\n]+ >>>\n.*?# <<< remote-dev target [^\n]+ <<<\n?(?:\n)?",
        "\n", content, flags=re.DOTALL,
    )


def install(*, home: Path | None = None, proxy_port: int = 4227) -> Path:
    """Install one idempotent shared SSH master/forwarding block."""
    root = home or Path.home()
    ssh_dir = root / ".ssh"
    config = ssh_dir / "config"
    ssh_dir.mkdir(parents=True, exist_ok=True)
    ssh_dir.chmod(0o700)
    original = config.read_text(encoding="utf-8") if config.exists() else ""
    cleaned = _without_managed_block(original)
    cleaned = _without_target_blocks(cleaned)
    # Migrate the old external-include implementation if present.
    cleaned = re.sub(rf"^\s*{re.escape(INCLUDE)}\s*$\n?", "", cleaned, flags=re.MULTILINE)
    # Migrate only the exact legacy remote-dev forwarding directives; leave
    # unrelated user forwarding rules untouched.
    cleaned = re.sub(r"^\s*(?:RemoteForward\s+127\.0\.0\.1:4227\s+127\.0\.0\.1:4227|LocalForward\s+127\.0\.0\.1:1455\s+127\.0\.0\.1:1455)\s*$\n?", "", cleaned, flags=re.MULTILINE)
    cleaned = _merge_global_block(cleaned, proxy_port=proxy_port)
    if cleaned != original:
        _atomic_write(config, cleaned, 0o600)
    return config


def install_target(*, host: str, control_path: Path, identity_file: Path, home: Path | None = None, proxy_port: int = 4227) -> Path:
    """Migrate legacy target blocks without adding per-device SSH config."""
    root = home or Path.home()
    config = root / ".ssh" / "config"
    original = config.read_text(encoding="utf-8") if config.exists() else ""
    content = _without_target_blocks(original)
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
