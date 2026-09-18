from remote_dev.tssh import _parse_destination


def test_tssh_destination_parsing():
    assert _parse_destination("alice@example.test") == ("alice", "example.test")
    assert _parse_destination("example.test")[1] == "example.test"
