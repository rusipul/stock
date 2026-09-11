"""소스 예외 계층. 설계서 §13 — 실패의 "범위"를 구분한다.

SourceError: 이 종목만 실패. 배치는 계속된다.
PermanentSourceError: 이 종목은 재시도해도 소용없다. 그래도 다음 종목은 계속된다.
FatalSourceError: 남은 모든 종목이 똑같이 실패한다. 배치를 멈춰야 한다.
"""

from __future__ import annotations

from stockscout.sources.base import FatalSourceError, PermanentSourceError, SourceError


def test_fatal_source_error_is_a_source_error():
    """collect.py의 일반 `except Exception` 격리 경로가 실수로도
    FatalSourceError를 잡을 수 있어야 하므로(구분해서 먼저 잡지 않는 한)
    최소한 SourceError 계열이어야 한다."""
    assert issubclass(FatalSourceError, SourceError)


def test_fatal_source_error_is_not_a_permanent_source_error():
    """PermanentSourceError는 '이 종목만' 영구 실패라는 종목 단위 의미다.
    FatalSourceError를 그 하위로 두면 `except PermanentSourceError`로
    잡는 아무 코드나 무심코 종목 단위로 격리해버릴 수 있다 — 정확히
    이번에 고치는 버그(020을 PermanentSourceError로 잘못 분류)의 재발
    형태이므로, 형제 클래스로 분리되어 있어야 한다."""
    assert not issubclass(FatalSourceError, PermanentSourceError)


def test_fatal_source_error_is_raisable_and_carries_message():
    try:
        raise FatalSourceError("배치 중단 사유")
    except FatalSourceError as exc:
        assert str(exc) == "배치 중단 사유"
