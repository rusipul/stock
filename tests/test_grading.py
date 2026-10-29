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


# --- 영역별 최소점 --------------------------------------------------------

from stockscout.analysis.grading import weak_axes  # noqa: E402
from stockscout.models import AXIS_UNMEASURABLE, STATUS_OK, AxisScore, ScoreCard  # noqa: E402


def card(**points):
    """축 이름 -> 25점 만점 점수. None이면 측정 불가."""
    axes = []
    for axis in ("profitability", "growth", "stability", "cashflow"):
        p = points.get(axis, 20.0)
        if p is None:
            axes.append(AxisScore(axis=axis, metrics=[], earned=0.0,
                                  denominator=25.0, status=AXIS_UNMEASURABLE))
        else:
            axes.append(AxisScore(axis=axis, metrics=[], earned=p,
                                  denominator=25.0, status=STATUS_OK))
    return ScoreCard(axes=axes)


def test_a_weak_axis_blocks_the_grade_even_with_a_high_total():
    """실측: Oracle은 74점이지만 안정성이 6점이다. 자금 사정이 약한 우량주는 없다."""
    c = card(profitability=25.0, growth=23.0, stability=6.0, cashflow=20.0)

    g, reason = grade_signal(stats(-0.30), score_total=74.0, passed_filters=True,
                             config=CONFIG, card=c)

    assert g == GRADE_NONE
    assert "안정성 6.0점" in reason and "10점" in reason


def test_every_axis_at_the_minimum_passes():
    c = card(profitability=10.0, growth=10.0, stability=10.0, cashflow=25.0)

    assert grade_signal(stats(-0.30), score_total=80.0, passed_filters=True,
                        config=CONFIG, card=c)[0] == GRADE_STRONG


def test_an_unmeasurable_axis_blocks_the_grade():
    """볼 수 없었던 영역은 품질을 확인하지 못한 것이다."""
    assert weak_axes(card(cashflow=None), CONFIG.min_axis_ratio) == ["cashflow"]


def test_weak_axes_lists_every_axis_below_the_minimum():
    c = card(growth=4.5, stability=9.9)

    assert weak_axes(c, 0.4) == ["growth", "stability"]


def test_the_minimum_scales_with_the_axis_points():
    """배점을 설정에서 바꿔도 비율로 잰다 (10점 만점 축이면 4점)."""
    axis = AxisScore(axis="growth", metrics=[], earned=4.0, denominator=10.0,
                     status=STATUS_OK, max_points=10.0)

    assert weak_axes(ScoreCard(axes=[axis]), 0.4) == []
