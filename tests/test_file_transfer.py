import json
from pathlib import Path

import pytest

from remote_dev.file_transfer import (
    CacheLayout,
    EndpointIdentity,
    FileTransferError,
    canonical_remote_path,
    metadata_matches,
    path_digest,
    read_sidecar,
    safe_basename,
    sidecar_path,
    sidecar_payload,
)


def test_endpoint_identity_is_stable_and_isolates_users_and_hosts():
    first = EndpointIdentity("host-a", user="alice")
    assert first.digest() == EndpointIdentity("host-a", user="alice").digest()
    assert first.digest() != EndpointIdentity("host-a", user="bob").digest()
    assert first.digest() != EndpointIdentity("host-b", user="alice").digest()


def test_remote_path_is_canonicalized_without_local_filesystem_access():
    assert canonical_remote_path("notes/../report.md", "/srv/work") == "/srv/work/report.md"
    assert canonical_remote_path("/tmp/../var/log/app.log") == "/var/log/app.log"

    with pytest.raises(FileTransferError):
        canonical_remote_path("~/report.md", "/srv/work")
    with pytest.raises(FileTransferError):
        canonical_remote_path("bad\x00path", "/srv/work")
    with pytest.raises(FileTransferError):
        canonical_remote_path("notes.md", "relative-cwd")


def test_cache_layout_isolated_by_endpoint_and_safe_from_path_traversal(tmp_path: Path):
    endpoint = EndpointIdentity("host-a", user="alice")
    other = EndpointIdentity("host-a", user="bob")
    layout = CacheLayout(tmp_path / "cache", tmp_path / "downloads")
    remote_path = canonical_remote_path("../../report.md", "/srv/work")

    first = layout.download_path(endpoint, remote_path)
    second = layout.download_path(other, remote_path)
    assert first != second
    assert first.is_relative_to(tmp_path / "downloads")
    assert ".." not in first.parts
    assert first.parent == tmp_path / "downloads" / endpoint.digest()
    assert first.name.startswith(f"{path_digest(remote_path)[:16]}-")
    assert "srv" not in first.parts
    assert path_digest(remote_path) == path_digest(remote_path)
    assert safe_basename(remote_path) == "report.md"


def test_sidecar_and_fast_metadata_match(tmp_path: Path):
    endpoint = EndpointIdentity("host-a", user="alice")
    local = tmp_path / "report.md"
    local.write_text("hello", encoding="utf-8")
    metadata = sidecar_payload(endpoint, "/srv/report.md", 5, 123, "hash")
    sidecar = sidecar_path(local)
    sidecar.write_text(json.dumps(metadata), encoding="utf-8")

    assert read_sidecar(sidecar) == metadata
    assert metadata_matches(local, metadata, endpoint, "/srv/report.md", 5, 123)
    assert not metadata_matches(local, metadata, endpoint, "/srv/report.md", 6, 123)
    assert not metadata_matches(local, metadata, EndpointIdentity("other"), "/srv/report.md", 5, 123)
