from remote_dev import upload
from remote_dev.upload import UPLOAD_ROOT, UploadConnection, UploadError, _sftp_quote, expand_upload_sources


def test_upload_contract_uses_fixed_remote_root():
    connection = UploadConnection("host.example", 22, "alice", "/tmp/key")
    assert connection.destination == "alice@host.example"
    assert UPLOAD_ROOT == "Uploads/remote-dev"


def test_sftp_quote_protects_filename():
    quoted = _sftp_quote('report "final" $1.pdf')
    assert quoted.startswith('"') and quoted.endswith('"')
    assert '\\"' in quoted
    assert "\\$" in quoted


def test_upload_uses_append_only_for_existing_partial_files():
    source = (upload.Path(upload.__file__).read_text(encoding="utf-8"))
    assert 'put_flags = "-ap" if resume_from > 0 else "-p"' in source


def test_expand_upload_sources_supports_multiple_files_and_globs(tmp_path):
    first = tmp_path / "one.txt"
    second = tmp_path / "two.txt"
    first.write_text("one")
    second.write_text("two")

    items = expand_upload_sources([str(tmp_path / "*.txt")], recursive=False)

    assert [relative for _, relative in items] == ["one.txt", "two.txt"]


def test_expand_upload_sources_preserves_recursive_directory(tmp_path):
    tree = tmp_path / "docs"
    (tree / "nested").mkdir(parents=True)
    (tree / "README.md").write_text("readme")
    (tree / "nested" / "note.md").write_text("note")

    items = expand_upload_sources([str(tree)], recursive=True)

    assert [relative for _, relative in items] == ["docs/README.md", "docs/nested/note.md"]


def test_expand_upload_sources_requires_recursive_for_directories(tmp_path):
    directory = tmp_path / "docs"
    directory.mkdir()
    (directory / "README.md").write_text("readme")

    try:
        expand_upload_sources([str(directory)], recursive=False)
    except UploadError as exc:
        assert "-r" in str(exc)
    else:
        raise AssertionError("directory upload unexpectedly succeeded without -r")
