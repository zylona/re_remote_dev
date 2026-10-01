"""Local-to-remote uploads for the standalone ``tssh put`` command."""

from __future__ import annotations

import os
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


UPLOAD_ROOT = "Uploads/remote-dev"


@dataclass(frozen=True, slots=True)
class UploadConnection:
    hostname: str
    port: int
    user: str
    identity_file: str | None = None

    @property
    def destination(self) -> str:
        return f"{self.user}@{self.hostname}"


class UploadError(RuntimeError):
    """A user-correctable upload failure."""


def _sftp_quote(value: str) -> str:
    """Quote one path for OpenSSH sftp batch syntax."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`") + '"'


def _ssh_base(connection: UploadConnection, executable: str) -> list[str]:
    command = [executable, "-o", "BatchMode=yes", "-o", "ConnectTimeout=8"]
    if connection.identity_file:
        command.extend(["-i", connection.identity_file])
    if connection.port != 22:
        command.extend(["-p" if executable == "ssh" else "-P", str(connection.port)])
    return command


def _remote_root(connection: UploadConnection) -> str:
    command = _ssh_base(connection, "ssh") + [
        connection.destination,
        "sh -c 'mkdir -p -- \"$HOME/Uploads/remote-dev\" && printf %s \"$HOME/Uploads/remote-dev\"'",
    ]
    result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise UploadError((result.stderr or "无法创建远程上传目录").strip())
    root = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    if not root.startswith("/") or not root.endswith("/Uploads/remote-dev"):
        raise UploadError("远程上传目录响应无效")
    return root


def _remote_size(connection: UploadConnection, path: str) -> int:
    command = _ssh_base(connection, "ssh") + [
        connection.destination,
        "stat -c %s -- " + shlex.quote(path),
    ]
    result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=15)
    if result.returncode:
        return 0
    try:
        return int(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return 0


def put_file(connection: UploadConnection, local_path: Path, *, progress: bool = True) -> str:
    """Upload a regular local file and return its absolute remote path."""
    if not local_path.is_file():
        raise UploadError(f"本地文件不存在或不是普通文件：{local_path}")
    local_path = local_path.resolve()
    root = _remote_root(connection)
    remote_path = f"{root}/{local_path.name}"
    remote_partial = f"{root}/.{local_path.name}.tssh-part"
    total = local_path.stat().st_size
    started = time.monotonic()
    command = _ssh_base(connection, "sftp") + ["-b", "-", connection.destination]
    batch = (
        f"put -ap {_sftp_quote(str(local_path))} {_sftp_quote(f'{UPLOAD_ROOT}/.{local_path.name}.tssh-part')}\n"
        f"rename {_sftp_quote(f'{UPLOAD_ROOT}/.{local_path.name}.tssh-part')} {_sftp_quote(f'{UPLOAD_ROOT}/{local_path.name}')}\n"
    )
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if process.stdin is None:
        raise UploadError("无法向 SFTP 发送上传请求")
    process.stdin.write(batch)
    process.stdin.close()
    process.stdin = None
    last_print = 0.0
    deadline = started + 3600
    while process.poll() is None:
        now = time.monotonic()
        if now > deadline:
            process.terminate()
            raise UploadError("SFTP 上传超时（超过 1 小时）")
        done = _remote_size(connection, remote_partial)
        if progress and now - last_print >= 0.5:
            elapsed = max(now - started, 0.001)
            speed = done / elapsed
            ratio = min(1.0, done / total) if total else 1.0
            eta = (total - done) / speed if speed > 0 and total else None
            percent = f"{ratio * 100:5.1f}%"
            eta_text = f" ETA {eta:.1f}s" if eta is not None else ""
            print(f"\rput: [{percent}] {done}/{total} bytes {speed / 1024 / 1024:.1f} MiB/s{eta_text}\x1b[K", end="", flush=True)
            last_print = now
        time.sleep(0.2)
    stdout, stderr = process.communicate(timeout=5)
    if process.returncode:
        raise UploadError((stderr or stdout or "SFTP 上传失败").strip())
    if progress:
        print("\rput: 上传完成\x1b[K")
    return remote_path
