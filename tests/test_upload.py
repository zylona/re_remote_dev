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
