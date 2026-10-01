from remote_dev import tssh
from remote_dev.tssh import _parse_destination, _ssh_command, _version


def test_tssh_destination_parsing():
    assert _parse_destination("alice@example.test") == ("alice", "example.test")
    assert _parse_destination("example.test")[1] == "example.test"


def test_tssh_places_destination_before_remote_command():
    assert _ssh_command("alice@example.test", "/tmp/key", 22, ["echo", "ok"]) == [
        "ssh",
        "-i",
        "/tmp/key",
        "alice@example.test",
        "echo",
        "ok",
    ]


def test_tssh_version_is_available():
    assert _version()


def test_tssh_cleanup_command(monkeypatch, capsys):
    monkeypatch.setattr(tssh, "_request", lambda payload, timeout: {"ok": True, "cleaned_endpoints": 2})
    assert tssh.main(["cleanup"]) == 0
    assert "2" in capsys.readouterr().out


def test_tssh_help_and_list_commands(monkeypatch, capsys):
    assert tssh.main(["help"]) == 0
    output = capsys.readouterr().out
    assert "persist" in output
    assert "put FILE user@host" in output
    monkeypatch.setattr(tssh, "_request", lambda payload, timeout: {"ok": True, "endpoints": []})
    assert tssh.main(["list"]) == 0
    assert "no active" in capsys.readouterr().out


def test_tssh_list_shows_endpoint_ports_and_directions(monkeypatch, capsys):
    monkeypatch.setattr(
        tssh,
        "_request",
        lambda payload, timeout: {
            "ok": True,
            "endpoints": [{
                "endpoint": {"hostname": "host-a", "port": 22},
                "state": "READY",
                "owner": "alice",
                "session_count": 2,
                "endpoint_digest": "0123456789abcdef",
                "forwards": [
                    {"kind": "proxy", "remote_port": 4227, "local_port": 4227, "state": "READY"},
                    {"kind": "event", "remote_port": 4228, "local_port": 4230, "state": "READY"},
                ],
            }],
        },
    )
    assert tssh.main(["list"]) == 0
    output = capsys.readouterr().out
    assert "host-a" in output
    assert "4227" in output
    assert "4228" in output
    assert "4230" in output
    assert "remote" in output and "local" in output


def test_tssh_list_shows_proxy_type(monkeypatch, capsys):
    monkeypatch.setattr(tssh, "_request", lambda payload, timeout: {
        "ok": True,
        "endpoints": [{
            "endpoint": {"hostname": "host-a", "port": 22},
            "mode": "PERSISTENT",
            "state": "READY",
            "owner": "alice",
            "session_count": 2,
            "endpoint_digest": "digest",
            "forwards": [{"kind": "proxy", "remote_port": 4227, "local_port": 4227, "state": "READY"}],
        }],
    })
    assert tssh.main(["list"]) == 0
    assert "PERSISTENT" in capsys.readouterr().out
