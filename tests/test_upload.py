from remote_dev import upload
from remote_dev.upload import UPLOAD_ROOT, UploadConnection, _sftp_quote


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
