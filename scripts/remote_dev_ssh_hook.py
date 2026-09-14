#!/usr/bin/env python3
"""Best-effort SSH LocalCommand hook for proxy-only per-target units."""
from __future__ import annotations
import fcntl
import hashlib, os, shlex, subprocess, sys, time
from pathlib import Path

def main() -> int:
    if len(sys.argv) < 4 or os.environ.get("REMOTE_DEV_SSH_HOOK") == "1": return 0
    host, port, user = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    probe = subprocess.run(["ssh", "-G", host], capture_output=True, text=True, timeout=5, check=False)
    if probe.returncode: return 0
    values, identities = {}, []
    for line in probe.stdout.splitlines():
        key, _, value = line.partition(" ")
        if key in {"hostname", "user", "port"} and key not in values: values[key] = value.strip()
        elif key == "identityfile" and value.strip() != "none": identities.append(value.strip())
    # For literal IPs, preserve the command's destination.  A HostName alias
    # or DNS canonicalization must not redirect a forwarding unit to another
    # address than the one the user just connected to.
    hostname = host if all(ch.isdigit() or ch == "." for ch in host) else values.get("hostname", host)
    # %r is the user from the actual ssh command and must override a generic
    # User value returned by ``ssh -G <host>`` (important for shared hosts).
    resolved_user = user or values.get("user", "")
    resolved_port = int(port or values.get("port", "22"))
    identity = next((Path(x).expanduser() for x in identities if Path(x).expanduser().exists()), None)
    # The listener belongs to the device network namespace, not to an SSH
    # account.  All users on one endpoint therefore share one unit.
    digest = hashlib.sha256(f"{hostname}:{resolved_port}|".encode()).hexdigest()[:32]
    # A full remote-dev bootstrap may already own this target through its
    # managed master/session unit.  Reuse that tunnel instead of attempting a
    # second listener on the same remote 4227 port.
    user_digest = hashlib.sha256(f"{resolved_user}@{hostname}:{resolved_port}|".encode()).hexdigest()[:32]
    if subprocess.run(["systemctl", "--user", "is-active", "--quiet", f"remote-dev-master-{user_digest}.service"], check=False).returncode == 0:
        return 0
    unit = f"remote-dev-proxy-{digest}.service"
    directory = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "systemd/user"
    state_dir = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "remote-dev/endpoints"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    _cleanup_legacy_units(directory, unit, hostname, resolved_port)
    # Password-authenticated sessions deliberately stay native.  LocalCommand
    # cannot reliably tell which method authenticated the parent SSH process;
    # probe the configured key without allowing password fallback, and only
    # install a proxy sidecar when that key is actually accepted.
    if identity is None or not _key_auth_works(hostname, resolved_port, resolved_user, identity):
        return 0
    with (state_dir / f"{digest}.lock").open("w", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        # A healthy device-level tunnel is reusable across windows and users.
        if subprocess.run(["systemctl", "--user", "is-active", "--quiet", unit], check=False).returncode == 0:
            return 0
        if _password_tunnel_exists(hostname, resolved_port, resolved_user):
            return 0
        result = _ensure_key_unit(directory, unit, digest, hostname, resolved_port, resolved_user, identity)
        if subprocess.run(["systemctl", "--user", "is-active", "--quiet", unit], check=False).returncode != 0:
            return 0
        # Type=simple becomes active before ssh has completed remote-forward
        # negotiation. A short settle window prevents the first remote
        # command in a newly opened SSH session from racing the listener.
        time.sleep(0.6)
        return result

def _cleanup_legacy_units(directory: Path, current_unit: str, hostname: str, port: int) -> None:
    """Remove only older remote-dev proxy units for this endpoint."""
    for path in directory.glob("remote-dev-proxy-*.service"):
        if path.name == current_unit:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if f"-p {port} " not in content or f"@{hostname}" not in content or "remote-dev automatic proxy tunnel" not in content:
            continue
        subprocess.run(["systemctl", "--user", "disable", "--now", path.name], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def _ensure_key_unit(directory: Path, unit: str, digest: str, hostname: str, port: int, user: str, identity: Path) -> int:
    command = shlex.join(["/usr/bin/ssh", "-F", "/dev/null", "-N", "-T", "-o", "BatchMode=yes", "-o", "ControlMaster=no", "-o", "ControlPath=none", "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3", "-i", str(identity), "-p", str(port), "-R", "127.0.0.1:4227:127.0.0.1:4227", f"{user}@{hostname}"])
    text = f"""[Unit]\nDescription=remote-dev automatic proxy tunnel ({digest})\nAfter=network-online.target\nWants=network-online.target\nStartLimitIntervalSec=600\nStartLimitBurst=10\n\n[Service]\nType=simple\nExecStart={command}\nRestart=on-failure\nRestartSec=5\nNoNewPrivileges=yes\nMemoryMax=32M\nTasksMax=8\n\n[Install]\nWantedBy=default.target\n"""
    path = directory / unit
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8"); path.chmod(0o600)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    subprocess.run(["systemctl", "--user", "enable", "--now", unit], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return 0

def _key_auth_works(hostname: str, port: int, user: str, identity: Path) -> bool:
    command = [
        "/usr/bin/ssh", "-F", "/dev/null", "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=5", "-o", "PreferredAuthentications=publickey",
        "-o", "PasswordAuthentication=no", "-o", "ControlMaster=no",
        "-o", "ControlPath=none", "-i", str(identity), "-p", str(port),
        f"{user}@{hostname}", "true",
    ]
    result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    return result.returncode == 0


if __name__ == "__main__": raise SystemExit(main())
