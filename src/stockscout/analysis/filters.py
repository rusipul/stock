"""하드필터. 설계서 §8.1.

걸리면 점수를 계산하지 않고 제외한다. 이 계획은 재무제표 없이 판정
가능한 두 가지만 구현한다. 재무 기반 필터(자본잠식·연속적자·과다부채·
이자미충당·매출급감·필수항목결측)는 계획 C가 더한다.

순수 함수다. 네트워크도 DB도 모른다.
"""

from __future__ import annotations

from datetime import date

from stockscout.analysis.valuation import window_start
from stockscout.models import FilterOutcome, PriceBar, Security

FINANCIAL_SECTOR = "FINANCIAL_SECTOR"
INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"


def apply_price_filters(
    security: Security,
    bars: list[PriceBar],
    as_of: date,
    min_history_years: int,
) -> FilterOutcome:
    """가격·업종 기반 하드필터를 적용한다.

    첫 실패에서 멈추지 않고 모든 사유를 모은다. 화면에서 "왜 제외됐나"를
    보여줄 때 사유가 하나만 있으면 고치고 다시 돌렸을 때 또 다른 사유로
    걸려, 사용자가 같은 과정을 반복하게 된다.
    """
    failures: list[str] = []

    # 은행·보험·증권은 부채비율·유동비율이 구조적으로 달라 제조업과
    # 같은 잣대로 재면 무의미하다. 전용 필터셋은 설계서 §16의 과제다.
    if security.is_financial:
        failures.append(FINANCIAL_SECTOR)

    # 달력 기준으로 잰다. 연분수(days / 365.25)로 재면 정확히 5달력년인
    # 구간이 4.9993년이 되어 전 종목이 탈락한다.
    required_start = window_start(as_of, min_history_years)
    first_bar = min((b.date for b in bars), default=None)
    if first_bar is None or first_bar > required_start:
        failures.append(INSUFFICIENT_HISTORY)

    return FilterOutcome(failures=failures)
