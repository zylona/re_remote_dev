from remote_dev.orchestrator.health import classify_failure, retry_delay


def test_failure_categories_are_stable_and_secret_free():
    assert classify_failure("Permission denied (publickey)") == "SSH_AUTH_FAILED"
    assert classify_failure("channel open administratively prohibited") == "FORWARDING_DENIED"
    assert classify_failure("bind: Address already in use") == "REMOTE_PORT_BUSY"
    assert classify_failure("Connection timed out") == "REMOTE_UNREACHABLE"


def test_retry_delay_is_exponential_and_bounded():
    assert [retry_delay(i) for i in range(1, 7)] == [5, 10, 20, 40, 80, 160]
    assert retry_delay(100) == 300
