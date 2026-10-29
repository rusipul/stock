"""단기 하락과 하락 멈춤. 우량주가 최근 몇 달 사이 크게 빠졌는가."""

from datetime import date, timedelta

import pytest

from stockscout.analysis.valuation import compute_drawdowns, months_before
from stockscout.models import PriceBar

AS_OF = date(2026, 10, 28)


def series(closes, end=AS_OF):
    """closes를 end까지 하루 간격 봉으로 깐다 (마지막 값이 end)."""
    start = end - timedelta(days=len(closes) - 1)
    return [PriceBar(ticker="X", date=start + timedelta(days=i), close=c)
            for i, c in enumerate(closes)]


def test_months_before_uses_calendar_months():
    assert months_before(date(2026, 10, 28), 3) == date(2026, 7, 28)
    assert months_before(date(2026, 3, 31), 1) == date(2026, 2, 28)   # 말일 보정
    assert months_before(date(2026, 1, 15), 12) == date(2025, 1, 15)


def test_drop_is_measured_from_the_high_inside_the_window():
    """3개월 전보다 오래된 고점은 3개월 하락에 들어가지 않는다."""
    bars = (series([300.0], end=AS_OF - timedelta(days=150))      # 5개월 전 고점
            + series([200.0] * 60 + [150.0], end=AS_OF))           # 최근 2달

    dd = compute_drawdowns(bars, as_of=AS_OF, months=[3, 6])

    assert dd[3].high == 200.0
    assert dd[3].drop_from_high == pytest.approx(150 / 200 - 1)
    assert dd[6].high == 300.0
    assert dd[6].drop_from_high == pytest.approx(150 / 300 - 1)


def test_still_falling_is_not_stabilized():
    """오늘이 저점이고 20일선 아래 - 떨어지는 칼날이다."""
    closes = [100.0 - i for i in range(60)]   # 매일 하락
    dd = compute_drawdowns(series(closes), as_of=AS_OF, months=[3])

    assert dd[3].days_since_low == 0
    assert dd[3].above_ma is False
    assert dd[3].stabilized is False


def test_no_new_low_for_ten_trading_days_is_stabilized():
    closes = [100.0] * 30 + [60.0] + [61.0] * 10   # 저점 이후 10거래일 횡보
    dd = compute_drawdowns(series(closes), as_of=AS_OF, months=[3])

    assert dd[3].low == 60.0
    assert dd[3].days_since_low == 10
    assert dd[3].stabilized is True


def test_nine_days_after_the_low_and_below_the_average_is_not_yet():
    closes = [100.0] * 30 + [60.0] + [61.0] * 9
    dd = compute_drawdowns(series(closes), as_of=AS_OF, months=[3])

    assert dd[3].days_since_low == 9
    assert dd[3].stabilized is False


def test_back_above_the_twenty_day_average_is_stabilized():
    """저점이 바로 어제여도 20일선을 회복했으면 멈춘 것으로 본다."""
    closes = [100.0] * 40 + [50.0] * 19 + [49.0, 95.0]
    dd = compute_drawdowns(series(closes), as_of=AS_OF, months=[3])

    assert dd[3].days_since_low == 1
    assert dd[3].above_ma is True
    assert dd[3].stabilized is True


def test_the_low_is_taken_after_the_high():
    """고점 이전의 더 낮은 값은 이번 하락의 저점이 아니다."""
    closes = [40.0] + [100.0] + [70.0] * 20
    dd = compute_drawdowns(series(closes), as_of=AS_OF, months=[3])

    assert dd[3].high == 100.0
    assert dd[3].low == 70.0


def test_bars_after_as_of_are_ignored():
    bars = series([100.0, 50.0], end=AS_OF) + [
        PriceBar(ticker="X", date=AS_OF + timedelta(days=1), close=500.0)]

    dd = compute_drawdowns(bars, as_of=AS_OF, months=[1])

    assert dd[1].high == 100.0


def test_no_bars_in_the_window_means_no_entry():
    old = series([100.0], end=AS_OF - timedelta(days=400))

    assert compute_drawdowns(old, as_of=AS_OF, months=[3]) == {}


def test_touching_the_same_low_again_is_not_a_new_low():
    closes = [100.0] * 30 + [60.0] + [65.0] * 5 + [60.0] + [65.0] * 4
    dd = compute_drawdowns(series(closes), as_of=AS_OF, months=[3])

    assert dd[3].days_since_low == 10



def test_the_period_return_is_measured_from_the_first_bar_of_the_window():
    """고점 대비 낙폭과 별개로, 기간 첫날 대비 단순 수익률도 남긴다."""
    closes = [80.0] + [100.0] * 30 + [60.0]
    dd = compute_drawdowns(series(closes), as_of=AS_OF, months=[3])

    assert dd[3].period_return == pytest.approx(60 / 80 - 1)
    assert dd[3].drop_from_high == pytest.approx(60 / 100 - 1)
