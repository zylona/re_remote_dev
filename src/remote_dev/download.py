"""Asynchronous SFTP download queue used by the preview bridge (P3)."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .file_transfer import (
    CacheLayout,
    EndpointIdentity,
    canonical_remote_path,
    metadata_matches,
    read_sidecar,
    sidecar_path,
    sidecar_payload,
)
from .preview_bridge import PreviewRequest
from .preview_open import PreviewOpenError, open_local


@dataclass(frozen=True, slots=True)
class EndpointConnection:
    hostname: str
    port: int
    user: str
    identity_file: str | None = None
    control_path: str | None = None

    @property
    def identity(self) -> EndpointIdentity:
        return EndpointIdentity(self.hostname, self.port, self.user)

    @property
    def destination(self) -> str:
        return f"{self.user}@{self.hostname}"


@dataclass(slots=True)
class DownloadJob:
    request_id: str
    remote_path: str
    local_path: Path
    operation: str = "get"
    state: str = "QUEUED"
    error: str | None = None
    future: Future[None] | None = None


class DownloadManager:
    """Deduplicated, bounded local transfer queue.

    The worker never writes the final file directly.  A completed transfer is
    hashed locally and atomically renamed before its sidecar is published.
    """

    def __init__(self, connection: EndpointConnection, layout: CacheLayout | None = None, *, workers: int = 2) -> None:
        self.connection = connection
        self.layout = layout or CacheLayout.defaults()
        self.endpoint = connection.identity
        self._executor = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="tssh-rdo")
        self._lock = threading.Lock()
        self._jobs: dict[tuple[str, str], DownloadJob] = {}

    def submit(self, request: PreviewRequest) -> DownloadJob:
        remote_path = canonical_remote_path(request.remote_path)
        key = (self.endpoint.digest(), remote_path)
        # Keep the completed artifact in the user-visible, endpoint-isolated
        # download tree; the cache root remains available for future preview
        # metadata or transient work.
        target = self.layout.download_path(self.endpoint, remote_path)
        with self._lock:
            existing = self._jobs.get(key)
            if existing is not None and existing.state in {"QUEUED", "RUNNING"}:
                return existing
            job = DownloadJob(request.request_id, remote_path, target, request.operation)
            self._jobs[key] = job
            job.future = self._executor.submit(self._run, key, job)
            return job

    def _run(self, key: tuple[str, str], job: DownloadJob) -> None:
        try:
            job.state = "RUNNING"
            size, mtime_ns = self._remote_stat(job.remote_path)
            sidecar = read_sidecar(sidecar_path(job.local_path))
            if metadata_matches(job.local_path, sidecar, self.endpoint, job.remote_path, size, mtime_ns):
                job.state = "CACHED"
                self._open_if_requested(job)
                return
            job.local_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary = job.local_path.with_name(f".{job.local_path.name}.{job.request_id}.part")
            try:
                self._sftp_get(job.remote_path, temporary)
                if temporary.stat().st_size != size:
                    raise RuntimeError(f"下载大小不一致：期望 {size}，实际 {temporary.stat().st_size}")
                local_sha256 = _sha256(temporary)
                os.replace(temporary, job.local_path)
                metadata = sidecar_payload(self.endpoint, job.remote_path, size, mtime_ns, local_sha256)
                sidecar = sidecar_path(job.local_path)
                sidecar_tmp = sidecar.with_name(f".{sidecar.name}.{job.request_id}.part")
                sidecar_tmp.write_text(json.dumps(metadata, ensure_ascii=False, sort_keys=True), encoding="utf-8")
                os.replace(sidecar_tmp, sidecar)
                job.state = "READY"
                self._open_if_requested(job)
            finally:
                temporary.unlink(missing_ok=True)
        except Exception as exc:  # worker errors are reported through status, never the SSH PTY
            job.state = "FAILED"
            job.error = str(exc)
        finally:
            with self._lock:
                self._jobs[key] = job

    @staticmethod
    def _open_if_requested(job: DownloadJob) -> None:
        if job.operation != "open":
            return
        try:
            open_local(job.local_path)
        except PreviewOpenError as exc:
            job.error = str(exc)
            job.state = "READY_WITH_WARNING"
            print(f"rdo: {exc}", file=sys.stderr, flush=True)

    def _base_ssh(self, executable: str) -> list[str]:
        command = [executable, "-F", "/dev/null", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8"]
        if self.connection.identity_file:
            command.extend(["-i", self.connection.identity_file])
        if self.connection.control_path:
            command.extend([
                "-o", f"ControlPath={self.connection.control_path}",
                "-o", "ControlMaster=auto", "-o", "ControlPersist=60",
            ])
        if self.connection.port != 22:
            command.extend(["-p" if executable == "ssh" else "-P", str(self.connection.port)])
        return command

    def _remote_stat(self, remote_path: str) -> tuple[int, int]:
        command = self._base_ssh("ssh") + [self.connection.destination, "stat", "-c", shlex.quote("%s %Y"), "--", shlex.quote(remote_path)]
        result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=15)
        if result.returncode:
            raise RuntimeError((result.stderr or "远程文件 stat 失败").strip())
        parts = result.stdout.strip().split()
        if len(parts) != 2:
            raise RuntimeError("远程 stat 响应格式无效")
        return int(parts[0]), int(parts[1]) * 1_000_000_000

    def _sftp_get(self, remote_path: str, local_path: Path) -> None:
        command = self._base_ssh("sftp") + ["-b", "-", self.connection.destination]
        batch = f"get -p {shlex.quote(remote_path)} {shlex.quote(str(local_path))}\n"
        result = subprocess.run(command, input=batch, check=False, capture_output=True, text=True, timeout=300)
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout or "SFTP 下载失败").strip())

    def status(self, request_id: str) -> dict[str, Any] | None:
        with self._lock:
            for job in self._jobs.values():
                if job.request_id == request_id:
                    return {"request_id": job.request_id, "state": job.state, "remote_path": job.remote_path, "local_path": str(job.local_path), "error": job.error}
        return None

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
