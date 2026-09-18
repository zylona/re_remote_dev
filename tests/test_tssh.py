from remote_dev.tssh import _parse_destination, _ssh_command


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
