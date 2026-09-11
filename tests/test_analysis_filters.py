from datetime import date

import pytest

from stockscout.analysis.filters import (
    FINANCIAL_SECTOR,
    INSUFFICIENT_HISTORY,
    apply_price_filters,
)
from stockscout.models import Security


def sec(ticker="005930.KS", is_financial=False):
    return Security(ticker=ticker, market="KR", name="테스트",
                    exchange="KOSPI", currency="KRW", is_financial=is_financial)


def test_financial_sector_is_excluded():
    outcome = apply_price_filters(
        sec(is_financial=True), date(2021, 1, 1),
        as_of=date(2026, 9, 10), min_history_years=5,
    )

    assert outcome.passed is False
    assert FINANCIAL_SECTOR in outcome.failures


def test_non_financial_with_full_history_passes():
    outcome = apply_price_filters(
        sec(), date(2021, 9, 10),
        as_of=date(2026, 9, 10), min_history_years=5,
    )

    assert outcome.passed is True
    assert outcome.failures == []


def test_exactly_five_calendar_years_passes():
    """2021-09-10 ~ 2026-09-10은 정확히 5달력년이다.

    (MAX-MIN).days / 365.25 로 재면 1826/365.25 = 4.9993 이 되어
    이 종목이 탈락한다. 실제로 그 방식을 쓰면 42종목 전부가 탈락했다.
    """
    outcome = apply_price_filters(
        sec(), date(2021, 9, 10),
        as_of=date(2026, 9, 10), min_history_years=5,
    )

    assert INSUFFICIENT_HISTORY not in outcome.failures


def test_one_day_short_of_five_years_fails():
    outcome = apply_price_filters(
        sec(), date(2021, 9, 11),
        as_of=date(2026, 9, 10), min_history_years=5,
    )

    assert INSUFFICIENT_HISTORY in outcome.failures


def test_recent_listing_fails_history():
    """LG에너지솔루션은 2022-01-27 상장이다."""
    outcome = apply_price_filters(
        sec("373220.KS"), date(2022, 1, 27),
        as_of=date(2026, 9, 10), min_history_years=5,
    )

    assert INSUFFICIENT_HISTORY in outcome.failures


def test_no_bars_fails_history():
    outcome = apply_price_filters(
        sec(), None, as_of=date(2026, 9, 10), min_history_years=5,
    )

    assert INSUFFICIENT_HISTORY in outcome.failures


def test_all_failures_are_collected_not_short_circuited():
    """사유가 하나만 나오면 고쳐도 또 다른 사유로 걸린다."""
    outcome = apply_price_filters(
        sec(is_financial=True), date(2025, 1, 1),
        as_of=date(2026, 9, 10), min_history_years=5,
    )

    assert set(outcome.failures) == {FINANCIAL_SECTOR, INSUFFICIENT_HISTORY}


def test_failures_are_stable_in_order():
    """화면과 스냅샷 비교를 위해 순서가 흔들리면 안 된다."""
    a = apply_price_filters(sec(is_financial=True), None,
                            as_of=date(2026, 9, 10), min_history_years=5)
    b = apply_price_filters(sec(is_financial=True), None,
                            as_of=date(2026, 9, 10), min_history_years=5)

    assert a.failures == b.failures


def test_min_history_years_is_honored():
    """5년 기준으로는 탈락하는 이력이 3년 기준으로는 통과해야 한다.

    구현이 min_history_years를 무시하고 5를 하드코딩하면 이 테스트가
    이를 잡아낸다.
    """
    first_bar_date = date(2023, 1, 1)

    outcome_5y = apply_price_filters(
        sec(), first_bar_date, as_of=date(2026, 9, 10), min_history_years=5,
    )
    assert INSUFFICIENT_HISTORY in outcome_5y.failures

    outcome_3y = apply_price_filters(
        sec(), first_bar_date, as_of=date(2026, 9, 10), min_history_years=3,
    )
    assert INSUFFICIENT_HISTORY not in outcome_3y.failures


def test_single_bar_old_enough_passes_history():
    """봉이 하나뿐이어도 그 봉이 충분히 오래됐으면 이력 필터를 통과한다."""
    outcome = apply_price_filters(
        sec(), date(2020, 1, 1), as_of=date(2026, 9, 10), min_history_years=5,
    )

    assert INSUFFICIENT_HISTORY not in outcome.failures


def test_the_earliest_bar_lookup_moved_to_the_repository():
    """이력 판정 책임이 저장 계층으로 옮겨갔다 — apply_price_filters가
    직접 받는 이유.

    예전엔 이 함수가 봉 목록을 받아 min()으로 가장 이른 날짜를 스스로
    찾았다. 하지만 호출자(analyze.py)는 평균 계산을 위해 주가를 5년
    창으로 잘라 읽으므로, 그 잘린 목록을 그대로 넘기면
    min_history_years가 그 창의 길이와 같을 때(기본값 5년) 목록의
    최솟값이 구성상 항상 창의 시작일 이상이 된다. 그러면
    "first_bar_date > required_start"가 사실상 "창 시작일에 정확히
    거래일이 있는가"만 묻는 셈이 되고, 5년을 뒤로 미는 시프트가 요일을
    바꾸므로 이는 동전 던지기가 된다 — 실측: as_of=2026-09-11이면
    required_start가 토요일이 되어 42종목 전부가 탈락했다.

    지금은 date 하나만 받는다. 그 값은 Repository.first_price_date()처럼
    조회 구간과 무관하게 구해야 하며, get_price_bars(start=..., end=...)로
    잘린 목록에서 다시 min()을 구해 넘기면 안 된다 — 이 함수는 그
    실수를 검증 없이 그대로 믿는다. list[PriceBar]를 받는 옛 시그니처로
    "단순화"해 되돌리고 싶어질 수 있는데, 그게 바로 이 버그였다.
    """
    outcome = apply_price_filters(
        sec(), date(2021, 9, 10), as_of=date(2026, 9, 10), min_history_years=5,
    )

    assert INSUFFICIENT_HISTORY not in outcome.failures
