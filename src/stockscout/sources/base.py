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


class FatalSourceError(SourceError):
    """이 종목만이 아니라 남은 모든 종목이 똑같이 실패할 실패. 배치를 멈춰야 한다.

    SourceError는 "이 종목만" 실패했다는 뜻이고, PermanentSourceError는
    "이 종목은 재시도해도 소용없다"는 뜻이지만, 둘 다 "다음 종목은 계속
    시도할 가치가 있다"는 전제를 깔고 있다. FatalSourceError는 그 전제가
    깨졌을 때 쓴다 — 원인이 이 종목이 아니라 요청 자체(예: 등록되지 않은
    API 키, 잘못된 요청 파라미터, 소진된 일일 한도)에 있어서, 남은 42개
    종목도 정확히 같은 이유로 하나씩 실패하고 만다.

    그래서 PermanentSourceError의 하위클래스로 두지 않는다. 둘을 부모-자식
    관계로 두면 `except PermanentSourceError`로 종목 단위 격리를 하는
    코드가 FatalSourceError까지 무심코 같이 잡아, 배치 전체가 실패할
    상황에서도 종목마다 계속 재시도하며 동일한 오류를 N번 기록하게 된다
    (예: 잘못된 API 키 하나가 42개의 "DART 오류 010" 행을 남기고, 정작
    운영자는 원인이 데이터가 아니라 키라는 걸 알 방법이 없다). 이 클래스는
    호출부(pipeline/collect.py)가 DailyQuotaExceeded와 동일하게
    `summary.aborted = True`로 배치를 멈추고 원인 하나만 보고하도록
    SourceError의 형제 하위클래스로 둔다.
    """


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
