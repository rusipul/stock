"""하드필터. 설계서 §8.1.

걸리면 점수를 계산하지 않고 제외한다. 이 계획은 재무제표 없이 판정
가능한 두 가지만 구현한다. 재무 기반 필터(자본잠식·연속적자·과다부채·
이자미충당·매출급감·필수항목결측)는 계획 C가 더한다.

순수 함수다. 네트워크도 DB도 모른다.
"""

from __future__ import annotations

from datetime import date

from stockscout.analysis.valuation import window_start
from stockscout.models import FilterOutcome, Security

FINANCIAL_SECTOR = "FINANCIAL_SECTOR"
INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"


def apply_price_filters(
    security: Security,
    first_bar_date: date | None,
    as_of: date,
    min_history_years: int,
) -> FilterOutcome:
    """가격·업종 기반 하드필터를 적용한다.

    첫 실패에서 멈추지 않고 모든 사유를 모은다. 화면에서 "왜 제외됐나"를
    보여줄 때 사유가 하나만 있으면 고치고 다시 돌렸을 때 또 다른 사유로
    걸려, 사용자가 같은 과정을 반복하게 된다.

    first_bar_date는 그 종목의 "진짜" 첫 저장 봉 날짜여야 한다 —
    Repository.first_price_date()처럼 조회 구간과 무관하게 구한 값.
    봉 목록(list[PriceBar])을 받아 여기서 min()을 구하던 옛 설계로
    되돌리면 안 된다. 호출자(analyze.py)는 평균 계산을 위해 주가를
    5년 창으로 잘라 읽는데, 그 잘린 목록을 그대로 넘기면
    min_history_years가 그 창의 길이와 같을 때(기본값 5년) 목록의
    최솟값이 구성상 항상 창의 시작일 이상이 된다. 그러면 아래의
    "first_bar_date > required_start"가 사실상 "창 시작일에 정확히
    거래일이 있는가"만 묻는 것으로 퇴화하고, 5년을 뒤로 미는 시프트가
    요일을 바꾸므로 이는 동전 던지기가 된다 — 실측: as_of=2026-09-11이면
    required_start(2021-09-11)가 토요일이 되어 42종목 전부가
    INSUFFICIENT_HISTORY로 탈락했다. date 하나만 받게 한 것은 그 실수를
    애초에 불가능하게 만들기 위해서다: 호출자가 잘린 목록을 실수로
    넘길 수 없다. "단순화"해 list를 다시 받도록 되돌리고 싶어질 수
    있는데, 그게 바로 이 버그였다.
    """
    failures: list[str] = []

    # 은행·보험·증권은 부채비율·유동비율이 구조적으로 달라 제조업과
    # 같은 잣대로 재면 무의미하다. 전용 필터셋은 설계서 §16의 과제다.
    if security.is_financial:
        failures.append(FINANCIAL_SECTOR)

    # 달력 기준으로 잰다. 연분수(days / 365.25)로 재면 정확히 5달력년인
    # 구간이 4.9993년이 되어 전 종목이 탈락한다.
    required_start = window_start(as_of, min_history_years)
    if first_bar_date is None or first_bar_date > required_start:
        failures.append(INSUFFICIENT_HISTORY)

    return FilterOutcome(failures=failures)
