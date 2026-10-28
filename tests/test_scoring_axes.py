"""축 조립과 §8.3 환산."""

import math

import pytest

from stockscout.analysis.scoring import score_security
from stockscout.config import MetricConfig, load_config
from stockscout.models import (
    AXIS_UNMEASURABLE,
    Financials,
    STATUS_MISSING,
    STATUS_OK,
)

CONFIG = load_config().scoring   # 실제 config/settings.yaml을 쓴다


def fin(year, **kwargs):
    base = dict(
        ticker="005930.KS", fiscal_year=year, currency="KRW",
        revenue=1_000.0, operating_income=100.0, net_income=80.0,
        total_assets=2_000.0, total_liabilities=500.0, total_equity=1_000.0,
        current_assets=800.0, current_liabilities=400.0,
        interest_expense=10.0, operating_cash_flow=120.0, capex=30.0,
    )
    base.update(kwargs)
    return Financials(**base)


def five_years(**kwargs):
    return [fin(y, **kwargs) for y in range(2021, 2026)]


def axis(card, name):
    return next(a for a in card.axes if a.axis == name)


def test_a_complete_company_scores_out_of_a_hundred():
    card = score_security(five_years(), CONFIG)

    assert len(card.axes) == 4
    assert all(a.status == STATUS_OK for a in card.axes)
    assert math.isclose(sum(a.denominator for a in card.axes), 100.0)
    assert 0 <= card.total <= 100


def test_one_missing_metric_shrinks_the_denominator():
    """설계서 §8.3: capex 결측이면 FCF 지표(5점)가 유효만점에서 빠진다."""
    card = score_security(five_years(capex=None), CONFIG)

    cashflow = axis(card, "cashflow")

    assert cashflow.denominator == 20.0          # 25 - 5
    assert cashflow.status == STATUS_OK
    fcf = next(m for m in cashflow.metrics if m.metric == "fcf_positive_years")
    assert fcf.status == STATUS_MISSING
    assert fcf.max_points == 5.0                 # 화면은 "5점 중 측정 불가"를 보여야 한다


def test_an_axis_that_loses_most_of_its_points_is_zeroed_not_prorated():
    """§8.3의 축 하한. OCF가 없으면 현금흐름 25점 중 20점이 사라진다.

    남는 것은 FCF 5점뿐이라 유효만점이 배점의 절반 미만이다. 환산하면
    80점 만점으로 후하게 채점되고, "현금흐름을 볼 수 없다"가 감점이
    아니라 면제가 된다.
    """
    card = score_security(five_years(operating_cash_flow=None), CONFIG)

    cashflow = axis(card, "cashflow")

    assert cashflow.status == AXIS_UNMEASURABLE
    assert cashflow.earned == 0.0
    assert cashflow.points == 0.0


def test_a_zeroed_axis_keeps_its_full_weight_in_the_denominator():
    """하한이 실제로 무는 곳이다.

    분모에서도 25점을 빼면 0점 처리가 아무 효과가 없다 - 총점이
    그대로다. 이 테스트가 그것을 막는다.
    """
    complete = score_security(five_years(), CONFIG)
    no_ocf = score_security(five_years(operating_cash_flow=None), CONFIG)

    assert axis(no_ocf, "cashflow").denominator == 25.0
    assert no_ocf.total < complete.total


def test_the_floor_bites_below_half_not_at_half():
    """절반은 살아남는다. '미만'이지 '이하'가 아니다.

    실제 설정(10·10·5점)으로는 유효만점이 정확히 12.5가 되는 조합이
    없어 경계를 볼 수 없다. 10점짜리 두 지표로 된 축을 따로 만들어
    하나를 잃게 한다 - 20점 중 10점, 정확히 절반이다.
    """
    two_metrics = {"stability": {
        "debt_ratio": CONFIG["stability"]["debt_ratio"],
        "interest_coverage": MetricConfig(points=10, bands=[(10, 10)]),
    }}

    card = score_security(five_years(interest_expense=None), two_metrics)

    stability = axis(card, "stability")
    assert stability.status == STATUS_OK
    assert stability.denominator == 10.0


def test_base_negative_keeps_its_points_in_the_denominator():
    """0점 처리와 측정 불가는 다르다.

    3년 전 영업적자는 데이터 공백이 아니라 사업 사실이다. 유효만점에서
    빼주면 적자 기업이 오히려 후하게 채점된다.
    """
    years = five_years()
    years[-4] = fin(years[-4].fiscal_year, operating_income=-50.0)

    growth = axis(score_security(years, CONFIG), "growth")

    assert growth.denominator == 25.0
    assert growth.status == STATUS_OK


def test_total_is_zero_when_there_are_no_financials():
    card = score_security([], CONFIG)

    assert card.total == 0.0
    assert all(a.status == AXIS_UNMEASURABLE for a in card.axes)


def test_unknown_metric_in_config_is_an_error_not_a_silent_zero():
    """설정 오타로 지표가 조용히 사라지면 아무도 모른다."""
    broken = {"profitability": {"roe_avg9": CONFIG["profitability"]["roe_avg3"]}}

    with pytest.raises(ValueError, match="roe_avg9"):
        score_security(five_years(), broken)


def test_unknown_axis_in_config_is_an_error():
    broken = {"vibes": {"roe_avg3": CONFIG["profitability"]["roe_avg3"]}}

    with pytest.raises(ValueError, match="vibes"):
        score_security(five_years(), broken)
