"""Asynchronous SFTP download queue used by the preview bridge (P3)."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
import threading
import time
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
    bytes_total: int = 0
    bytes_done: int = 0
    speed_bps: float = 0.0
    eta_seconds: float | None = None


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
            self._write_status(job)
            size, mtime_ns = self._remote_stat(job.remote_path)
            job.bytes_total = size
            self._write_status(job)
            sidecar = read_sidecar(sidecar_path(job.local_path))
            if metadata_matches(job.local_path, sidecar, self.endpoint, job.remote_path, size, mtime_ns):
                job.state = "CACHED"
                job.bytes_done = size
                self._write_status(job)
                self._open_if_requested(job)
                return
            self._migrate_legacy_cache(job, size, mtime_ns)
            sidecar = read_sidecar(sidecar_path(job.local_path))
            if metadata_matches(job.local_path, sidecar, self.endpoint, job.remote_path, size, mtime_ns):
                job.state = "CACHED"
                job.bytes_done = size
                self._write_status(job)
                self._open_if_requested(job)
                return
            job.local_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary = job.local_path.with_name(f".{job.local_path.name}.part")
            partial_metadata = temporary.with_name(temporary.name + ".rdo.json")
            expected_partial = {
                "endpoint_digest": self.endpoint.digest(),
                "remote_path": job.remote_path,
                "remote_size": size,
                "remote_mtime_ns": mtime_ns,
            }
            old_partial = read_sidecar(partial_metadata)
            if temporary.exists() and (
                not old_partial
                or any(old_partial.get(name) != value for name, value in expected_partial.items())
                or temporary.stat().st_size > size
            ):
                temporary.unlink(missing_ok=True)
                partial_metadata.unlink(missing_ok=True)
            partial_metadata.write_text(json.dumps(expected_partial, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            job.bytes_done = temporary.stat().st_size if temporary.exists() else 0
            self._write_status(job)
            try:
                self._sftp_get(job.remote_path, temporary, job)
                if temporary.stat().st_size != size:
                    raise RuntimeError(f"下载大小不一致：期望 {size}，实际 {temporary.stat().st_size}")
                local_sha256 = _sha256(temporary)
                os.replace(temporary, job.local_path)
                partial_metadata.unlink(missing_ok=True)
                metadata = sidecar_payload(self.endpoint, job.remote_path, size, mtime_ns, local_sha256)
                sidecar = sidecar_path(job.local_path)
                sidecar_tmp = sidecar.with_name(f".{sidecar.name}.{job.request_id}.part")
                sidecar_tmp.write_text(json.dumps(metadata, ensure_ascii=False, sort_keys=True), encoding="utf-8")
                os.replace(sidecar_tmp, sidecar)
                job.state = "READY"
                job.bytes_done = size
                self._write_status(job)
                self._open_if_requested(job)
            finally:
                # Keep a valid partial file after a transport failure so the
                # next request can resume it. Successful transfers already
                # atomically moved it above.
                if job.state in {"READY", "CACHED", "READY_WITH_WARNING"}:
                    temporary.unlink(missing_ok=True)
                    partial_metadata.unlink(missing_ok=True)
        except Exception as exc:  # worker errors are reported through status, never the SSH PTY
            job.state = "FAILED"
            job.error = str(exc)
            self._write_status(job)
        finally:
            with self._lock:
                self._jobs[key] = job

    def _migrate_legacy_cache(self, job: DownloadJob, size: int, mtime_ns: int) -> None:
        """Move a valid old long-path artifact to the short visible path."""
        if job.local_path.exists():
            return
        legacy = self.layout.legacy_download_path(self.endpoint, job.remote_path)
        legacy_sidecar = sidecar_path(legacy)
        legacy_metadata = read_sidecar(legacy_sidecar)
        if not metadata_matches(legacy, legacy_metadata, self.endpoint, job.remote_path, size, mtime_ns):
            return
        job.local_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.replace(legacy, job.local_path)
        if legacy_sidecar.exists():
            os.replace(legacy_sidecar, sidecar_path(job.local_path))

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

    def _sftp_get(self, remote_path: str, local_path: Path, job: DownloadJob) -> None:
        command = self._base_ssh("sftp") + ["-b", "-", self.connection.destination]
        # ``get -a`` resumes an existing local partial file and starts a new
        # transfer when the file does not exist.
        batch = f"get -ap {shlex.quote(remote_path)} {shlex.quote(str(local_path))}\n"
        process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
        )
        if process.stdin is None:
            raise RuntimeError("无法向 SFTP 发送下载请求")
        process.stdin.write(batch)
        process.stdin.close()
        process.stdin = None
        started = time.monotonic()
        last_write = started
        deadline = started + 300
        while process.poll() is None:
            now = time.monotonic()
            if now > deadline:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                raise TimeoutError("SFTP 下载超时（超过 300 秒）")
            try:
                job.bytes_done = local_path.stat().st_size
            except FileNotFoundError:
                job.bytes_done = 0
            elapsed = max(now - started, 0.001)
            job.speed_bps = max(0.0, job.bytes_done / elapsed)
            if job.bytes_total and job.speed_bps > 0:
                job.eta_seconds = max(0.0, (job.bytes_total - job.bytes_done) / job.speed_bps)
            if now - last_write >= 0.5:
                self._write_status(job)
                last_write = now
            time.sleep(0.1)
        stdout, stderr = process.communicate(timeout=3)
        if process.returncode:
            raise RuntimeError((stderr or stdout or "SFTP 下载失败").strip())
        job.bytes_done = local_path.stat().st_size if local_path.exists() else 0
        self._write_status(job)

    def _write_status(self, job: DownloadJob) -> None:
        try:
            status_root = self.layout.cache_root / self.endpoint.digest() / "jobs"
            status_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            payload = {
                "request_id": job.request_id,
                "endpoint": {"hostname": self.endpoint.hostname, "port": self.endpoint.port, "user": self.endpoint.user},
                "state": job.state,
                "remote_path": job.remote_path,
                "local_path": str(job.local_path),
                "bytes_done": job.bytes_done,
                "bytes_total": job.bytes_total,
                "speed_bps": round(job.speed_bps, 2),
                "eta_seconds": round(job.eta_seconds, 1) if job.eta_seconds is not None else None,
                "error": job.error,
                "updated_at": time.time(),
            }
            target = status_root / f"{job.request_id}.json"
            temporary = target.with_name(f".{target.name}.part")
            temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            os.replace(temporary, target)
        except OSError:
            # Progress telemetry is best effort and must never turn a valid
            # download into a failed request (for example on a read-only home).
            return

    def status(self, request_id: str) -> dict[str, Any] | None:
        with self._lock:
            for job in self._jobs.values():
                if job.request_id == request_id:
                    return {
                        "request_id": job.request_id, "state": job.state,
                        "remote_path": job.remote_path, "local_path": str(job.local_path),
                        "bytes_done": job.bytes_done, "bytes_total": job.bytes_total,
                        "speed_bps": job.speed_bps, "eta_seconds": job.eta_seconds,
                        "error": job.error,
                    }
        return None

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
