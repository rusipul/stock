"""구간 평가와 점수 모델. 설계서 §8.2·§8.3."""

from pathlib import Path

from stockscout.analysis.scoring import band_score
from stockscout.config import MetricConfig, load_config
from stockscout.models import (
    AXIS_MAX,
    AXIS_UNMEASURABLE,
    STATUS_BASE_NEGATIVE,
    STATUS_DENOMINATOR_NONPOSITIVE,
    STATUS_MISSING,
    STATUS_OK,
    AxisScore,
    MetricScore,
    ScoreCard,
)

REAL_SETTINGS = Path(__file__).parent.parent / "config" / "settings.yaml"

ROE = MetricConfig(points=10, bands=[(0.15, 10), (0.10, 7), (0.05, 4), (0.0, 1)])
DEBT = MetricConfig(points=10, bands=[(50, 10), (100, 7), (150, 4), (200, 1)],
                    lower_is_better=True)


def test_higher_is_better_takes_the_first_band_the_value_clears():
    assert band_score(0.20, ROE) == 10
    assert band_score(0.15, ROE) == 10      # 경계는 포함이다
    assert band_score(0.12, ROE) == 7
    assert band_score(0.0, ROE) == 1


def test_higher_is_better_scores_zero_below_every_band():
    assert band_score(-0.05, ROE) == 0


def test_lower_is_better_flips_the_comparison():
    assert band_score(30, DEBT) == 10
    assert band_score(50, DEBT) == 10       # 경계는 포함이다
    assert band_score(80, DEBT) == 7
    assert band_score(200, DEBT) == 1


def test_lower_is_better_scores_zero_above_every_band():
    assert band_score(250, DEBT) == 0


def test_infinity_takes_the_top_band():
    """이자비용 0이면 interest_coverage가 inf다. 무차입은 가장 건전하다."""
    coverage = MetricConfig(points=10, bands=[(10, 10), (5, 7), (3, 4), (1, 2)])

    assert band_score(float("inf"), coverage) == 10


def test_metric_with_no_bands_scores_zero():
    """bands가 빈 지표(op_margin_trend 등)는 전용 계산을 쓴다.

    band_score에 실수로 넘기면 0점이 나온다. 조용히 만점을 주는 것보다
    낫다 - 0점은 눈에 띄고 만점은 안 띈다.
    """
    assert band_score(5.0, MetricConfig(points=5)) == 0


def test_band_score_returns_the_configured_points_not_a_fixed_ladder():
    """10/7/4/1은 YAML의 값이지 코드의 값이 아니다.

    설계서 §1: 기준은 설정에서 온다. 구간 점수를 코드에 박으면 사용자가
    YAML을 고쳐도 점수가 그대로다.
    """
    odd = MetricConfig(points=3, bands=[(1.0, 3.0), (0.5, 1.5), (0.1, 0.25)])

    assert band_score(2.0, odd) == 3.0
    assert band_score(0.5, odd) == 1.5
    assert band_score(0.1, odd) == 0.25


def test_the_first_matching_band_wins_even_if_a_later_one_pays_more():
    """구간은 위에서부터 평가한다. 정렬을 바꾸거나 max()로 고르면 여기서 걸린다."""
    weird = MetricConfig(points=10, bands=[(0.10, 4.0), (0.05, 9.0)])

    assert band_score(0.20, weird) == 4.0


def test_real_settings_bands_score_through_band_score():
    """실제 config/settings.yaml의 구간표가 이 함수로 그대로 돌아간다."""
    cfg = load_config(REAL_SETTINGS)
    debt = cfg.scoring["stability"]["debt_ratio"]
    coverage = cfg.scoring["stability"]["interest_coverage"]

    assert band_score(60.0, debt) == 7        # 100% 이하 구간
    assert band_score(250.0, debt) == 0       # 200%를 넘으면 구간 밖이다
    assert band_score(float("inf"), coverage) == 10   # 무차입


def test_axis_points_scale_earned_over_denominator_to_25():
    axis = AxisScore(
        axis="profitability", metrics=[], earned=15.0, denominator=20.0,
        status=STATUS_OK,
    )

    assert axis.points == 18.75


def test_axis_points_are_zero_when_the_denominator_is_zero():
    axis = AxisScore(axis="cashflow", metrics=[], earned=0.0, denominator=0.0,
                     status=STATUS_OK)

    assert axis.points == 0.0


def test_axis_points_use_the_configured_max_not_a_fixed_25():
    """축 배점은 설정에서 온다. points가 25를 박아 쓰면 YAML을 고쳐도 안 바뀐다."""
    axis = AxisScore(axis="growth", metrics=[], earned=5.0, denominator=10.0,
                     status=STATUS_OK, max_points=30.0)

    assert axis.points == 15.0


def test_axis_max_defaults_to_the_25_point_axis():
    """4축 x 25 = 100점이 기본 배치다 (설계서 §8.2)."""
    axis = AxisScore(axis="stability", metrics=[], earned=10.0, denominator=10.0,
                     status=STATUS_OK)

    assert AXIS_MAX == 25.0
    assert axis.max_points == AXIS_MAX


def test_scorecard_total_is_global_not_an_average_of_axes():
    """설계서 §8.3: (획득 합계 / 유효만점 합계) x 100.

    축 점수를 평균내면 안 된다. 유효만점이 작은 축이 과대 대표된다.
    """
    card = ScoreCard(axes=[
        AxisScore(axis="a", metrics=[], earned=20.0, denominator=25.0, status=STATUS_OK),
        AxisScore(axis="b", metrics=[], earned=5.0, denominator=10.0, status=STATUS_OK),
    ])

    assert card.total == 25.0 / 35.0 * 100


def test_scorecard_total_is_100_when_every_point_is_earned():
    card = ScoreCard(axes=[
        AxisScore(axis="a", metrics=[], earned=25.0, denominator=25.0, status=STATUS_OK),
        AxisScore(axis="b", metrics=[], earned=20.0, denominator=20.0, status=STATUS_OK),
    ])

    assert card.total == 100.0


def test_scorecard_total_is_zero_when_nothing_is_measurable():
    assert ScoreCard(axes=[]).total == 0.0


def test_a_zeroed_axis_drags_the_total_down_only_if_it_keeps_its_denominator():
    """설계서 §8.3의 축 하한: "볼 수 없다"가 면제가 되면 안 된다.

    축을 0점 처리하면서 분모까지 빼면 그 축이 총점에서 통째로 사라져
    오히려 후한 점수가 나온다. 분모를 남겨야 감점이 된다. 축을 실제로
    0점 처리하는 것은 Task 7의 일이고, 여기서는 모델이 그 표현을 담을
    수 있는지만 고정한다.
    """
    zeroed = ScoreCard(axes=[
        AxisScore(axis="profitability", metrics=[], earned=20.0, denominator=25.0,
                  status=STATUS_OK),
        AxisScore(axis="cashflow", metrics=[], earned=0.0, denominator=25.0,
                  status=AXIS_UNMEASURABLE),
    ])
    dropped = ScoreCard(axes=[
        AxisScore(axis="profitability", metrics=[], earned=20.0, denominator=25.0,
                  status=STATUS_OK),
        AxisScore(axis="cashflow", metrics=[], earned=0.0, denominator=0.0,
                  status=AXIS_UNMEASURABLE),
    ])

    assert zeroed.total == 40.0
    assert dropped.total == 80.0


def test_metric_score_carries_its_raw_value_for_the_screen():
    m = MetricScore(metric="roe_avg3", value=0.123, points=7.0, max_points=10.0,
                    status=STATUS_OK)

    assert m.value == 0.123
    assert m.status == STATUS_OK


def test_missing_metric_keeps_its_max_points_for_the_screen():
    """유효만점 계산에서는 빠지지만, 화면은 '10점 중 측정 불가'를 보여야 한다."""
    m = MetricScore(metric="fcf_positive_years", value=None, points=0.0,
                    max_points=5.0, status=STATUS_MISSING)

    assert m.max_points == 5.0


def test_status_constants_are_distinct():
    """상태가 겹치면 화면이 "왜 이 점수인가"를 설명할 수 없다."""
    statuses = [STATUS_OK, STATUS_MISSING, STATUS_BASE_NEGATIVE,
                STATUS_DENOMINATOR_NONPOSITIVE, AXIS_UNMEASURABLE]

    assert len(set(statuses)) == len(statuses)
