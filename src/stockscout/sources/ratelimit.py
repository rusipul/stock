"""레이트리밋과 재시도. 설계서 §5.3·§13."""

from __future__ import annotations

import functools
import time
from typing import Callable, TypeVar

import httpx

T = TypeVar("T")

# 재시도할 가치가 있는 예외. 네트워크 일시 장애만 해당한다.
#
# httpx.TransportError를 반드시 포함해야 한다. httpx의 연결·타임아웃 오류는
# 파이썬 내장 ConnectionError/TimeoutError를 상속하지 않으므로,
# 내장 예외만 나열하면 실제 네트워크 장애에서 재시도가 전혀 동작하지 않는다.
#
# 여기에 bare OSError를 넣지 않는다. PermissionError·FileNotFoundError 등
# "영구" 오류도 전부 OSError의 직접 하위클래스라서, OSError를 통째로 넣으면
# 인증 오류(PermissionError)까지 재시도 대상이 되어 버린다
# (재시도해도 소용없다 — 설계서 §5.3). TimeoutError·ConnectionError는
# 의미가 분명한 개별 하위클래스라서 명시적으로 나열한다.
#
# httpx.HTTPStatusError(404·401 등)도 일부러 뺐다. 재시도해도 소용없다 (설계서 §5.3).
TRANSIENT = (TimeoutError, ConnectionError, httpx.TransportError)


class DailyQuotaExceeded(Exception):
    """일일 한도 소진. 배치를 중단하고 남은 종목을 보고해야 한다."""


class TokenBucket:
    """초당 호출 수 제한. acquire()는 대기해야 할 초를 반환한다.

    시간 함수를 주입받으므로 테스트에서 sleep 없이 검증할 수 있다.
    """

    def __init__(
        self,
        rate_per_sec: float,
        capacity: float | None = None,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self.rate = rate_per_sec
        self.capacity = capacity if capacity is not None else rate_per_sec
        self._tokens = self.capacity
        self._time = time_fn
        self._last = time_fn()

    def acquire(self) -> float:
        now = self._time()
        self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
        self._last = now
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return 0.0
        wait = (1.0 - self._tokens) / self.rate
        self._tokens = 0.0
        return wait

    def wait(self) -> None:
        """실제로 대기한다. 프로덕션 경로에서 사용."""
        delay = self.acquire()
        if delay > 0:
            time.sleep(delay)


class DailyQuota:
    """DART 일 20,000건 같은 일일 한도를 프로세스 내에서 센다."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0

    def consume(self, n: int = 1) -> None:
        if self.used + n > self.limit:
            raise DailyQuotaExceeded(
                f"일일 한도 {self.limit}건 소진 (사용 {self.used}건)"
            )
        self.used += n

    @property
    def remaining(self) -> int:
        return self.limit - self.used


def retry_on_transient(
    attempts: int = 3, base_delay: float = 1.0
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """일시 장애만 지수 백오프로 재시도한다. 404·인증 오류는 즉시 던진다."""

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs) -> T:
            last: Exception | None = None
            for i in range(attempts):
                try:
                    return fn(*args, **kwargs)
                except TRANSIENT as exc:
                    last = exc
                    if i < attempts - 1 and base_delay > 0:
                        time.sleep(base_delay * (2**i))
            assert last is not None
            raise last

        return wrapper

    return decorator
