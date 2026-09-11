import pytest

from stockscout.sources.ratelimit import (
    DailyQuotaExceeded,
    DailyQuota,
    TokenBucket,
    retry_on_transient,
)


def test_token_bucket_allows_burst_up_to_capacity():
    clock = {"t": 0.0}
    bucket = TokenBucket(rate_per_sec=10, capacity=10, time_fn=lambda: clock["t"])

    waits = [bucket.acquire() for _ in range(10)]

    assert waits == [0.0] * 10


def test_token_bucket_reports_wait_when_drained():
    clock = {"t": 0.0}
    bucket = TokenBucket(rate_per_sec=10, capacity=10, time_fn=lambda: clock["t"])
    for _ in range(10):
        bucket.acquire()

    wait = bucket.acquire()

    assert wait == pytest.approx(0.1, abs=1e-6)


def test_token_bucket_refills_over_time():
    clock = {"t": 0.0}
    bucket = TokenBucket(rate_per_sec=10, capacity=10, time_fn=lambda: clock["t"])
    for _ in range(10):
        bucket.acquire()

    clock["t"] = 1.0  # 1초 뒤 10개 충전
    waits = [bucket.acquire() for _ in range(10)]

    assert waits == [0.0] * 10


def test_daily_quota_raises_when_exhausted():
    quota = DailyQuota(limit=2)
    quota.consume()
    quota.consume()

    with pytest.raises(DailyQuotaExceeded):
        quota.consume()


def test_retry_retries_transient_and_succeeds():
    calls = {"n": 0}

    @retry_on_transient(attempts=3, base_delay=0.0)
    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise TimeoutError("boom")
        return "ok"

    assert flaky() == "ok"
    assert calls["n"] == 3


def test_retry_gives_up_after_attempts():
    calls = {"n": 0}

    @retry_on_transient(attempts=2, base_delay=0.0)
    def always_fails():
        calls["n"] += 1
        raise TimeoutError("boom")

    with pytest.raises(TimeoutError):
        always_fails()
    assert calls["n"] == 2


def test_retry_does_not_retry_permanent_errors():
    calls = {"n": 0}

    @retry_on_transient(attempts=3, base_delay=0.0)
    def permanent():
        calls["n"] += 1
        raise PermissionError("bad key")

    with pytest.raises(PermissionError):
        permanent()
    assert calls["n"] == 1  # 재시도하지 않는다


def test_retry_covers_httpx_transport_errors():
    """httpx의 연결 오류는 내장 ConnectionError를 상속하지 않는다.

    내장 예외만 나열하면 실제 네트워크 장애에서 재시도가 죽는다.
    """
    import httpx

    calls = {"n": 0}

    @retry_on_transient(attempts=3, base_delay=0.0)
    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("connection refused")
        return "ok"

    assert flaky() == "ok"
    assert calls["n"] == 3


def test_retry_does_not_retry_http_status_errors():
    """404·401은 재시도해도 소용없다 (설계서 §5.3)."""
    import httpx

    calls = {"n": 0}

    @retry_on_transient(attempts=3, base_delay=0.0)
    def not_found():
        calls["n"] += 1
        raise httpx.HTTPStatusError(
            "404", request=httpx.Request("GET", "http://x"),
            response=httpx.Response(404),
        )

    with pytest.raises(httpx.HTTPStatusError):
        not_found()
    assert calls["n"] == 1
