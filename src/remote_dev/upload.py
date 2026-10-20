"""Local-to-remote uploads for the standalone ``tssh put`` command."""

from __future__ import annotations

import glob
import os
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable

UPLOAD_ROOT = "Uploads/remote-dev"


@dataclass(frozen=True, slots=True)
class UploadConnection:
    hostname: str
    port: int
    user: str
    identity_file: str | None = None
    control_path: str | None = None

    @property
    def destination(self) -> str:
        return f"{self.user}@{self.hostname}"


class UploadError(RuntimeError):
    """A user-correctable upload failure."""


UploadItem = tuple[Path, str]


def _sftp_quote(value: str) -> str:
    """Quote one path for OpenSSH sftp batch syntax."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`") + '"'


def _ssh_base(connection: UploadConnection, executable: str) -> list[str]:
    command = [executable, "-o", "BatchMode=yes", "-o", "ConnectTimeout=8"]
    if connection.identity_file:
        command.extend(["-i", connection.identity_file])
    if connection.control_path:
        command.extend(["-o", f"ControlPath={connection.control_path}", "-o", "ControlMaster=auto", "-o", "ControlPersist=60"])
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
    command = _ssh_base(connection, "ssh") + [connection.destination, "stat -c %s -- " + shlex.quote(path)]
    result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=15)
    if result.returncode:
        return 0
    try:
        return int(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return 0


def _ensure_remote_parent(connection: UploadConnection, parent: str) -> None:
    if parent == UPLOAD_ROOT:
        return
    command = _ssh_base(connection, "ssh") + [connection.destination, "mkdir -p -- " + shlex.quote(parent)]
    result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise UploadError((result.stderr or "无法创建远程上传子目录").strip())


def _start_master(connection: UploadConnection) -> tuple[UploadConnection, bool]:
    if not connection.control_path:
        return connection, False
    master = _ssh_base(connection, "ssh") + ["-M", "-S", connection.control_path, "-fnNT", connection.destination]
    result = subprocess.run(master, check=False, capture_output=True, text=True, timeout=20)
    if result.returncode == 0:
        return connection, True
    return UploadConnection(connection.hostname, connection.port, connection.user, connection.identity_file), False


def _stop_master(connection: UploadConnection, started: bool) -> None:
    if started and connection.control_path:
        subprocess.run(["ssh", "-S", connection.control_path, "-O", "exit", connection.destination], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _validate_remote_relative(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or not value or any(part in {"", ".", ".."} for part in path.parts):
        raise UploadError(f"无效的远程相对路径：{value}")
    return path.as_posix()


def _put_file_active(connection: UploadConnection, root: str, local_path: Path, remote_relative: str, *, progress: bool) -> str:
    if not local_path.is_file():
        raise UploadError(f"本地文件不存在或不是普通文件：{local_path}")
    local_path = local_path.resolve()
    remote_relative = _validate_remote_relative(remote_relative)
    remote_sftp = f"{UPLOAD_ROOT}/{remote_relative}"
    remote_path = f"{root}/{remote_relative}"
    remote_parent = str(PurePosixPath(remote_sftp).parent)
    _ensure_remote_parent(connection, remote_parent)
    filename = PurePosixPath(remote_relative).name
    remote_partial_sftp = f"{remote_parent}/.{filename}.tssh-part"
    remote_partial = f"{root}/{remote_partial_sftp[len(UPLOAD_ROOT) + 1:]}"
    total = local_path.stat().st_size
    started = time.monotonic()
    resume_from = _remote_size(connection, remote_partial)
    if resume_from > total:
        resume_from = 0
    put_flags = "-ap" if resume_from > 0 else "-p"
    command = _ssh_base(connection, "sftp") + ["-b", "-", connection.destination]
    batch = f"put {put_flags} {_sftp_quote(str(local_path))} {_sftp_quote(remote_partial_sftp)}\nrename {_sftp_quote(remote_partial_sftp)} {_sftp_quote(remote_sftp)}\n"
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
            eta_text = f" ETA {eta:.1f}s" if eta is not None else ""
            print(f"\rput: [{ratio * 100:5.1f}%] {done}/{total} bytes {speed / 1024 / 1024:.1f} MiB/s{eta_text}\x1b[K", end="", flush=True)
            last_print = now
        time.sleep(0.2)
    stdout, stderr = process.communicate(timeout=5)
    if process.returncode:
        raise UploadError((stderr or stdout or "SFTP 上传失败").strip())
    if progress:
        print("\rput: 上传完成\x1b[K")
    return remote_path


def put_file(connection: UploadConnection, local_path: Path, *, remote_relative: str | None = None, progress: bool = True) -> str:
    """Upload one regular file and return its absolute remote path."""
    local_path = Path(local_path)
    active, master_started = _start_master(connection)
    try:
        return _put_file_active(active, _remote_root(active), local_path, remote_relative or local_path.name, progress=progress)
    finally:
        _stop_master(active, master_started)


def put_files(connection: UploadConnection, items: Iterable[UploadItem], *, progress: bool = True) -> list[str]:
    """Upload a batch while sharing one temporary SSH ControlMaster."""
    materialized = list(items)
    if not materialized:
        raise UploadError("没有可上传的文件")
    active, master_started = _start_master(connection)
    try:
        root = _remote_root(active)
        result: list[str] = []
        for index, (local_path, remote_relative) in enumerate(materialized, 1):
            if len(materialized) > 1:
                print(f"put: [{index}/{len(materialized)}] {local_path} → {remote_relative}")
            result.append(_put_file_active(active, root, local_path, remote_relative, progress=progress))
        return result
    finally:
        _stop_master(active, master_started)


def expand_upload_sources(raw_sources: Iterable[str], *, recursive: bool) -> list[UploadItem]:
    """Expand local paths/globs into files and deterministic remote paths."""
    items: list[UploadItem] = []
    seen: set[Path] = set()
    remote_seen: set[str] = set()
    for raw in raw_sources:
        pattern = os.path.expanduser(raw)
        matches = sorted(glob.glob(pattern, recursive=True)) if glob.has_magic(pattern) else [pattern]
        if not matches:
            raise UploadError(f"本地路径或通配符没有匹配项：{raw}")
        for match in matches:
            source = Path(match)
            if source.is_dir():
                if not recursive:
                    raise UploadError(f"{source} 是目录；上传目录请使用 -r/--recursive")
                base = source.resolve()
                for candidate in sorted(base.rglob("*")):
                    if not candidate.is_file():
                        continue
                    resolved = candidate.resolve()
                    if resolved in seen:
                        continue
                    relative = PurePosixPath(base.name, *candidate.relative_to(base).parts).as_posix()
                    if relative in remote_seen:
                        raise UploadError(f"多个源文件映射到同一个远程路径：{relative}")
                    seen.add(resolved)
                    remote_seen.add(relative)
                    items.append((resolved, relative))
            elif source.is_file():
                resolved = source.resolve()
                if resolved in seen:
                    continue
                relative = resolved.name
                if relative in remote_seen:
                    raise UploadError(f"多个源文件映射到同一个远程路径：{relative}")
                seen.add(resolved)
                remote_seen.add(relative)
                items.append((resolved, relative))
            else:
                raise UploadError(f"本地路径不存在或不是普通文件/目录：{source}")
    if not items:
        raise UploadError("目录中没有可上传的普通文件")
    return items
