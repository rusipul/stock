"""데이터 소스 교체 지점. 모든 소스는 이 두 프로토콜만 구현한다.

여기를 지키면 유료 API로 옮기거나 소스가 깨졌을 때
파일 하나를 추가하고 설정에서 이름만 바꾸면 된다.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable

from stockscout.models import PriceBar, RawStatement


class SourceError(Exception):
    """소스 호출 실패. 종목 단위로 격리되어 배치를 멈추지 않는다."""


class PermanentSourceError(SourceError):
    """재시도해도 소용없는 실패 (404, 인증 오류, 데이터 없음)."""


@runtime_checkable
class PriceSource(Protocol):
    name: str

    def fetch_daily(self, ticker: str, start: date, end: date) -> list[PriceBar]:
        """수정종가 일봉을 날짜 오름차순으로 반환한다."""
        ...


@runtime_checkable
class FundamentalSource(Protocol):
    name: str

    def fetch_annual(self, ticker: str, years: list[int]) -> list[RawStatement]:
        """요청한 회계연도의 연간 재무제표 원본을 반환한다.

        일부 연도가 없으면 그 연도만 빠진 목록을 반환한다(예외를 던지지 않는다).
        """
        ...
