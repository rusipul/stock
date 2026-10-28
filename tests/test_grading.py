"""등급 판정. 설계서 §9.3."""

from stockscout.analysis.grading import GRADE_NONE, GRADE_STRONG, GRADE_WATCH, grade_signal
from stockscout.config import SignalConfig
from stockscout.models import PriceStats

CONFIG = SignalConfig()   # min_score=70, 두 임계치 모두 -0.40


def stats(drop_avg, drop_high):
    return PriceStats(
        price_current=100.0,
        avg_price={1: 150.0, 3: 160.0, 5: 170.0},
        high_5y=200.0,
        drop_vs_avg={1: -0.33, 3: -0.375, 5: drop_avg},
        drop_vs_high=drop_high,
    )


def test_both_thresholds_and_a_good_score_gives_two_stars():
    grade, reason = grade_signal(
        stats(-0.45, -0.50), score_total=80.0, passed_filters=True,
        config=CONFIG, primary_window=5,
    )

    assert grade == GRADE_STRONG
    assert "평균" in reason and "고점" in reason


def test_only_one_threshold_gives_one_star():
    grade, _ = grade_signal(
        stats(-0.30, -0.50), score_total=80.0, passed_filters=True,
        config=CONFIG, primary_window=5,
    )

    assert grade == GRADE_WATCH


def test_a_falling_stock_that_is_not_quality_gets_no_grade():
    """이 계획의 시금석이다.

    LG화학은 5년 평균 대비 -41.3%로 현재 1위지만 영업이익률이 11.8%에서
    2.6%로 무너졌고 작년 ROE는 -2.1%다. 41% 하락에는 이유가 있었다.
    많이 떨어졌다는 것만으로 등급이 나오면 이 제품은 위험하다.
    """
    grade, reason = grade_signal(
        stats(-0.45, -0.50), score_total=42.0, passed_filters=True,
        config=CONFIG, primary_window=5,
    )

    assert grade == GRADE_NONE
    assert "70" in reason        # 왜 안 되는지 숫자로 말해야 한다


def test_exactly_the_min_score_is_quality():
    """설계서 §9.3은 'score_total >= min_score'다. 70점은 통과한다."""
    grade, _ = grade_signal(
        stats(-0.45, -0.50), score_total=70.0, passed_filters=True,
        config=CONFIG, primary_window=5,
    )

    assert grade == GRADE_STRONG


def test_an_excluded_stock_gets_no_grade_even_with_a_high_score():
    grade, _ = grade_signal(
        stats(-0.45, -0.50), score_total=95.0, passed_filters=False,
        config=CONFIG, primary_window=5,
    )

    assert grade == GRADE_NONE


def test_no_score_yet_gives_no_grade():
    """점수가 없는 동안 등급을 표시하면 품질을 확인한 것처럼 보인다."""
    grade, reason = grade_signal(
        stats(-0.45, -0.50), score_total=None, passed_filters=True,
        config=CONFIG, primary_window=5,
    )

    assert grade == GRADE_NONE
    assert "점수" in reason


def test_a_quality_stock_that_has_not_fallen_gets_no_grade():
    grade, _ = grade_signal(
        stats(0.10, -0.05), score_total=90.0, passed_filters=True,
        config=CONFIG, primary_window=5,
    )

    assert grade == GRADE_NONE


def test_the_primary_window_selects_which_drop_is_used():
    """설정이 primary_window를 1로 바꾸면 1년 평균 대비를 본다."""
    s = PriceStats(
        price_current=100.0, avg_price={1: 200.0, 5: 105.0}, high_5y=300.0,
        drop_vs_avg={1: -0.50, 5: -0.05}, drop_vs_high=-0.10,
    )

    one_year, _ = grade_signal(s, 80.0, True, CONFIG, primary_window=1)
    five_year, _ = grade_signal(s, 80.0, True, CONFIG, primary_window=5)

    assert one_year == GRADE_WATCH     # 1년 평균 대비 -50%가 임계치를 넘는다
    assert five_year == GRADE_NONE     # 5년 평균 대비는 -5%뿐이다


def test_a_null_drop_does_not_count_as_a_hit():
    """커버리지 미달 창은 None이다 (설계서 §9.1). 등급 판정에서 제외한다."""
    s = PriceStats(
        price_current=100.0, avg_price={5: None}, high_5y=200.0,
        drop_vs_avg={5: None}, drop_vs_high=-0.50,
    )

    grade, _ = grade_signal(s, 80.0, True, CONFIG, primary_window=5)

    assert grade == GRADE_WATCH        # 고점 대비만으로 별 하나


def test_exactly_at_the_threshold_is_a_hit():
    """-40%는 '이하'다. 경계에서 종목이 갈린다."""
    grade, _ = grade_signal(
        stats(-0.40, -0.40), 80.0, True, CONFIG, primary_window=5,
    )

    assert grade == GRADE_STRONG


def test_reason_contains_the_actual_numbers():
    """화면이 계산식을 그대로 보여준다 (설계서 §11.2)."""
    _, reason = grade_signal(
        stats(-0.45, -0.50), 80.0, True, CONFIG, primary_window=5,
    )

    assert "-45.0%" in reason
    assert "-50.0%" in reason
