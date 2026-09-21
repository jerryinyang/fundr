import httpx
import pytest

from fundr.retry import with_retries


def _status_error(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.hyperliquid.xyz/info")
    response = httpx.Response(code, request=request)
    return httpx.HTTPStatusError(str(code), request=request, response=response)


def test_returns_immediately_on_success():
    calls = {"n": 0}

    def ok():
        calls["n"] += 1
        return "fine"

    assert with_retries(ok, sleep=lambda s: None) == "fine"
    assert calls["n"] == 1


def test_retries_then_succeeds():
    calls = {"n": 0}
    slept = []

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _status_error(429)
        return "ok"

    assert with_retries(flaky, sleep=slept.append) == "ok"
    assert calls["n"] == 3
    assert len(slept) == 2 and slept[1] > slept[0]   # backoff grows


def test_reraises_after_attempts():
    calls = {"n": 0}

    def always():
        calls["n"] += 1
        raise _status_error(429)

    with pytest.raises(httpx.HTTPStatusError):
        with_retries(always, attempts=3, sleep=lambda s: None)
    assert calls["n"] == 3


def test_does_not_retry_unlisted_errors():
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise ValueError("not retryable")

    with pytest.raises(ValueError):
        with_retries(boom, sleep=lambda s: None)
    assert calls["n"] == 1


def test_does_not_retry_permanent_4xx():
    # Lighter answers an unsupported resolution with HTTP 400 {"code":20001,"message":
    # "invalid param "} (measured 2026-09-21). No number of retries makes that succeed.
    calls = {"n": 0}

    def bad_request():
        calls["n"] += 1
        raise _status_error(400)

    with pytest.raises(httpx.HTTPStatusError):
        with_retries(bad_request, sleep=lambda s: None)
    assert calls["n"] == 1


def test_retries_server_errors():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise _status_error(503)
        return "ok"

    assert with_retries(flaky, sleep=lambda s: None) == "ok"
    assert calls["n"] == 2


def test_retries_transport_errors():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise httpx.ConnectError("dns")
        return "ok"

    assert with_retries(flaky, sleep=lambda s: None) == "ok"
    assert calls["n"] == 2
