"""영탁센스: 거래량이 터지며 신고가를 돌파하고 이어가는 종목."""

from datetime import date, timedelta

import pytest

from stockscout.analysis.breakout import breakout_signal, compute_breakouts
from stockscout.config import BreakoutConfig
from stockscout.models import PriceBar

AS_OF = date(2026, 10, 28)
CFG = BreakoutConfig()   # 1년 신고가 · 거래량 2배 · 최근 10거래일 · 임박 5%


def bars(closes, volumes=None):
    """closes를 AS_OF까지 하루 간격으로 깐다."""
    volumes = volumes or [1000] * len(closes)
    start = AS_OF - timedelta(days=len(closes) - 1)
    return [PriceBar(ticker="X", date=start + timedelta(days=i), close=c, volume=v)
            for i, (c, v) in enumerate(zip(closes, volumes))]


def flat_then(tail_closes, tail_volumes, base=100.0, n=300):
    closes = [base] * n + tail_closes
    volumes = [1000] * n + tail_volumes
    return bars(closes, volumes)


def test_a_new_high_on_heavy_volume_is_a_breakout():
    """300일 동안 100 아래였다가 3배 거래량으로 110 돌파, 이후 유지."""
    b = flat_then([110.0, 112.0, 115.0], [3000, 1500, 1200])

    s = compute_breakouts(b, as_of=AS_OF, lookbacks=[252])[252]

    assert s.breakout_days_ago == 2
    assert s.breakout_volume_ratio == pytest.approx(3.0)
    assert s.breakout_level == 100.0
    assert s.distance_to_high == pytest.approx(0.0)
    assert breakout_signal(s, CFG) == "BREAKOUT"


def test_a_new_high_on_thin_volume_is_not_a_breakout():
    b = flat_then([110.0, 112.0], [1100, 1000])

    s = compute_breakouts(b, as_of=AS_OF, lookbacks=[252])[252]

    assert s.breakout_volume_ratio == pytest.approx(1.1)
    assert breakout_signal(s, CFG) is None


def test_the_heaviest_volume_breakout_in_the_window_is_kept():
    """첫 돌파는 거래량이 약했고, 이틀 뒤 다시 고점을 경신하며 4배가 터졌다."""
    b = flat_then([105.0, 104.0, 108.0, 109.0], [1200, 900, 4000, 1000])

    s = compute_breakouts(b, as_of=AS_OF, lookbacks=[252])[252]

    assert s.breakout_volume_ratio == pytest.approx(4.0, rel=0.01)
    assert s.breakout_days_ago == 1
    assert s.breakout_level == 105.0        # 그날 넘은 것은 직전 고점 105


def test_a_failed_breakout_that_fell_back_is_not_a_breakout():
    """돌파 뒤 돌파선 아래로 3% 넘게 밀리면 '이어가지' 못한 것이다."""
    b = flat_then([110.0, 100.0, 95.0], [3000, 1000, 1000])

    s = compute_breakouts(b, as_of=AS_OF, lookbacks=[252])[252]

    assert s.holding is False
    assert breakout_signal(s, CFG) is None


def test_an_old_breakout_outside_the_window_does_not_count():
    b = flat_then([110.0] + [111.0] * 15, [3000] + [1000] * 15)

    s = compute_breakouts(b, as_of=AS_OF, lookbacks=[252])[252]

    assert s.breakout_days_ago is None or s.breakout_days_ago > CFG.recent_days
    assert breakout_signal(s, CFG) != "BREAKOUT"


def test_close_to_the_high_with_rising_volume_is_near():
    """고점(120)의 3% 아래, 최근 5일 거래량이 평소의 1.5배 - 돌파 임박."""
    closes = [100.0] * 200 + [120.0] + [110.0] * 90 + [116.4] * 5
    volumes = [1000] * 291 + [1500] * 5
    s = compute_breakouts(bars(closes, volumes), as_of=AS_OF, lookbacks=[252])[252]

    assert s.distance_to_high == pytest.approx(116.4 / 120 - 1)
    assert s.recent_volume_ratio == pytest.approx(1.5)
    assert breakout_signal(s, CFG) == "NEAR"


def test_close_to_the_high_without_volume_is_not_near():
    closes = [100.0] * 200 + [120.0] + [110.0] * 90 + [116.4] * 5
    s = compute_breakouts(bars(closes), as_of=AS_OF, lookbacks=[252])[252]

    assert breakout_signal(s, CFG) is None


def test_too_little_history_for_the_lookback_is_no_entry():
    """5년 신고가를 말하려면 그만큼의 과거가 있어야 한다(90% 이상)."""
    s = compute_breakouts(flat_then([110.0], [3000], n=200), as_of=AS_OF,
                          lookbacks=[252, 1260])

    assert 252 not in s and 1260 not in s


def test_missing_volume_skips_the_volume_check():
    """거래량을 모르면 거래량 조건으로 돌파를 인정하지 않는다(비율 None)."""
    b = [x.model_copy(update={"volume": None}) for x in flat_then([110.0], [3000])]

    s = compute_breakouts(b, as_of=AS_OF, lookbacks=[252])[252]

    assert s.breakout_volume_ratio is None
    assert breakout_signal(s, CFG) is None


def test_the_config_period_and_multiple_are_used():
    b = flat_then([110.0, 111.0], [1800, 1000])
    s = compute_breakouts(b, as_of=AS_OF, lookbacks=[252])[252]

    assert breakout_signal(s, CFG) is None
    assert breakout_signal(s, BreakoutConfig(volume_multiple=1.5)) == "BREAKOUT"
