"""등급 판정. 우량주가 최근 몇 달 사이 크게 빠졌고, 그 하락이 멈췄는가."""

from stockscout.analysis.grading import GRADE_NONE, GRADE_STRONG, GRADE_WATCH, grade_signal
from stockscout.config import SignalConfig
from stockscout.models import DrawdownStat, PriceStats

CONFIG = SignalConfig()   # min_score=70, 최근 3개월 고점 대비 -25%


def stats(drop=-0.30, stabilized=True, months=3):
    return PriceStats(
        price_current=70.0, avg_price={5: 100.0}, high_5y=150.0,
        drop_vs_avg={5: -0.3}, drop_vs_high=-0.53,
        drawdown={months: DrawdownStat(
            high=100.0, drop_from_high=drop, low=65.0, days_since_low=12,
            above_ma=stabilized, stabilized=stabilized)},
    )


def grade(s, score=80.0, passed=True, config=CONFIG):
    return grade_signal(s, score_total=score, passed_filters=passed, config=config)


def test_a_sharp_drop_that_has_stopped_gets_two_stars():
    g, reason = grade(stats(-0.30, stabilized=True))

    assert g == GRADE_STRONG
    assert "3개월 고점 대비 -30.0%" in reason
    assert "하락 멈춤" in reason


def test_a_sharp_drop_still_falling_gets_one_star():
    """떨어지는 칼날. 후보지만 아직 멈추지 않았다."""
    g, reason = grade(stats(-0.30, stabilized=False))

    assert g == GRADE_WATCH
    assert "아직 하락 중" in reason


def test_the_threshold_is_inclusive():
    assert grade(stats(-0.25))[0] == GRADE_STRONG


def test_a_small_drop_gets_no_grade():
    g, reason = grade(stats(-0.10))

    assert g == GRADE_NONE
    assert "-10.0% > -25%" in reason


def test_quality_comes_first():
    """많이 떨어져도 우량주가 아니면 등급이 없다."""
    g, reason = grade(stats(-0.60), score=42.0)

    assert g == GRADE_NONE
    assert "42점" in reason


def test_excluded_securities_get_no_grade():
    assert grade(stats(-0.60), score=None, passed=False)[0] == GRADE_NONE


def test_missing_score_gets_no_grade():
    assert grade(stats(-0.60), score=None)[0] == GRADE_NONE


def test_the_configured_period_is_used():
    config = SignalConfig(drawdown_months=6)

    assert grade(stats(-0.30, months=6), config=config)[0] == GRADE_STRONG
    assert grade(stats(-0.30, months=3), config=config)[0] == GRADE_NONE


def test_too_little_price_history_for_the_period_gets_no_grade():
    g, reason = grade(PriceStats(price_current=1.0, avg_price={}, high_5y=1.0,
                                 drop_vs_avg={}, drop_vs_high=0.0))

    assert g == GRADE_NONE
    assert "3개월" in reason
