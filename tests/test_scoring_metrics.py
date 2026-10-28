"""지표 12개. 설계서 §8.2."""

import math

from stockscout.analysis.scoring import (
    metric_current_ratio,
    metric_debt_ratio,
    metric_fcf_positive_years,
    metric_growth_consistency,
    metric_interest_coverage,
    metric_ocf_positive_years,
    metric_ocf_to_op_income,
    metric_op_income_cagr3,
    metric_op_margin_avg3,
    metric_op_margin_trend,
    metric_revenue_cagr3,
    metric_roe_avg3,
)
from stockscout.config import MetricConfig
from stockscout.models import (
    Financials,
    STATUS_BASE_NEGATIVE,
    STATUS_DENOMINATOR_NONPOSITIVE,
    STATUS_END_NONPOSITIVE,
    STATUS_MISSING,
    STATUS_OK,
)


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


TEN = MetricConfig(points=10, bands=[(0.15, 10), (0.10, 7), (0.05, 4), (0.0, 1)])
FIVE = MetricConfig(points=5)


# --- 수익성 ---------------------------------------------------------

def test_roe_avg3_averages_the_last_three_years():
    years = five_years()
    for i, ni in zip((-3, -2, -1), (100.0, 200.0, 300.0)):
        years[i] = fin(years[i].fiscal_year, net_income=ni, total_equity=1_000.0)

    score = metric_roe_avg3(years, TEN)

    assert math.isclose(score.value, 0.20)     # (0.1 + 0.2 + 0.3) / 3
    assert score.points == 10
    assert score.status == STATUS_OK


def test_roe_avg3_needs_at_least_two_years():
    years = [fin(2025, net_income=None)] * 1

    assert metric_roe_avg3(years, TEN).status == STATUS_MISSING


def test_roe_avg3_ignores_years_whose_roe_is_none():
    years = five_years()
    years[-1] = fin(2025, net_income=None)

    assert metric_roe_avg3(years, TEN).status == STATUS_OK


def test_op_margin_avg3_averages_the_last_three_years():
    """2021·2022는 창 밖이다. 거기 마진을 바꿔도 평균이 움직이지 않아야 한다."""
    years = five_years()
    years[0] = fin(2021, operating_income=900.0)
    for i, op in zip((-3, -2, -1), (60.0, 120.0, 180.0)):
        years[i] = fin(years[i].fiscal_year, revenue=1_000.0, operating_income=op)

    score = metric_op_margin_avg3(years, TEN)

    assert math.isclose(score.value, 0.12)     # (0.06 + 0.12 + 0.18) / 3
    assert score.points == 7


def test_op_margin_trend_rewards_a_rising_margin():
    """기울기 단위는 연당 %p다. 0.5%p/년 초과면 만점."""
    years = five_years()
    for i, rev, op in zip((-3, -2, -1), (1000.0, 1000.0, 1000.0), (50.0, 100.0, 150.0)):
        years[i] = fin(years[i].fiscal_year, revenue=rev, operating_income=op)

    score = metric_op_margin_trend(years, FIVE)

    assert score.value > 0.5      # 5%p/년
    assert score.points == 5


def test_op_margin_trend_penalises_a_falling_margin():
    years = five_years()
    for i, op in zip((-3, -2, -1), (150.0, 100.0, 50.0)):
        years[i] = fin(years[i].fiscal_year, revenue=1000.0, operating_income=op)

    score = metric_op_margin_trend(years, FIVE)

    assert score.value < -0.5
    assert score.points == 0


def test_op_margin_trend_gives_partial_credit_for_flat():
    years = five_years()   # 전 연도 동일 마진

    score = metric_op_margin_trend(years, FIVE)

    assert math.isclose(score.value, 0.0, abs_tol=1e-9)
    assert score.points == 2


def test_op_margin_trend_needs_three_years():
    assert metric_op_margin_trend([fin(2025)], FIVE).status == STATUS_MISSING


# --- 성장성 ---------------------------------------------------------

def test_revenue_cagr3_uses_the_year_three_back():
    """기저 바로 다음 해를 다른 값으로 둔다. 같은 값이면 한 해 밀려 재도
    결과가 같아 '어느 해를 기저로 쓰는가'를 검증하지 못한다."""
    years = five_years()
    years[-4] = fin(years[-4].fiscal_year, revenue=1_000.0)
    years[-3] = fin(years[-3].fiscal_year, revenue=1_100.0)
    years[-1] = fin(years[-1].fiscal_year, revenue=1_331.0)   # 10%/년 x 3

    score = metric_revenue_cagr3(years, TEN)

    assert math.isclose(score.value, 0.10, abs_tol=1e-9)


def test_revenue_cagr3_needs_four_years():
    assert metric_revenue_cagr3([fin(y) for y in (2023, 2024, 2025)],
                                TEN).status == STATUS_MISSING


def test_op_income_cagr3_records_base_negative_and_scores_zero():
    """설계서 §8.2: 기저가 음수면 CAGR이 정의되지 않는다.

    0점 처리하되 유효만점에는 남긴다 - 3년 전 적자는 데이터 공백이
    아니라 사업 사실이다.
    """
    years = five_years()
    years[-4] = fin(years[-4].fiscal_year, operating_income=-50.0)

    score = metric_op_income_cagr3(years, TEN)

    assert score.status == STATUS_BASE_NEGATIVE
    assert score.points == 0
    assert score.max_points == 10


def test_cagr_base_of_exactly_zero_is_base_negative():
    """0으로 나누면 터진다. 음수만 막으면 이 줄에서 ZeroDivisionError가 난다."""
    years = five_years()
    years[-4] = fin(years[-4].fiscal_year, revenue=0.0)

    assert metric_revenue_cagr3(years, TEN).status == STATUS_BASE_NEGATIVE


def test_cagr_with_a_loss_now_scores_zero_but_keeps_its_points():
    """실측: 삼성SDI 영업이익 2022 1.81조 -> 2025 -1.72조.

    음수의 세제곱근을 ** (1/3)으로 구하면 파이썬은 복소수를 돌려주고,
    구간 비교에서 TypeError로 종목 전체의 분석이 죽는다. 흑자에서
    적자로 돌아선 것은 사업 사실이므로 BASE_NEGATIVE처럼 0점이되 만점에는
    남긴다.
    """
    years = five_years()
    years[-1] = fin(years[-1].fiscal_year, operating_income=-50.0)

    score = metric_op_income_cagr3(years, TEN)

    assert score.status == STATUS_END_NONPOSITIVE
    assert score.value is None
    assert score.points == 0
    assert score.max_points == 10


def test_growth_consistency_counts_rising_years():
    years = [fin(2021, revenue=100.0), fin(2022, revenue=110.0),
             fin(2023, revenue=120.0), fin(2024, revenue=115.0),
             fin(2025, revenue=130.0)]

    score = metric_growth_consistency(years, FIVE)

    assert math.isclose(score.value, 3 / 4)
    assert math.isclose(score.points, 3.75)


def test_growth_consistency_needs_three_comparisons():
    years = [fin(2024, revenue=100.0), fin(2025, revenue=110.0)]

    assert metric_growth_consistency(years, FIVE).status == STATUS_MISSING


# --- 안정성 ---------------------------------------------------------

def test_debt_ratio_uses_the_latest_year():
    years = five_years()
    years[-1] = fin(2025, total_liabilities=400.0, total_equity=1_000.0)   # 40%
    cfg = MetricConfig(points=10, bands=[(50, 10), (100, 7)], lower_is_better=True)

    score = metric_debt_ratio(years, cfg)

    assert math.isclose(score.value, 40.0)
    assert score.points == 10


def test_current_ratio_is_a_ratio_not_a_percentage():
    """설정의 구간이 [2.0, 1.5, 1.0]이다. 200이 아니라 2.0이다."""
    years = five_years()
    years[-1] = fin(2025, current_assets=900.0, current_liabilities=400.0)
    cfg = MetricConfig(points=5, bands=[(2.0, 5), (1.5, 3.5), (1.0, 2)])

    score = metric_current_ratio(years, cfg)

    assert math.isclose(score.value, 2.25)
    assert score.points == 5


def test_interest_coverage_scores_full_when_there_is_no_debt():
    years = five_years()
    years[-1] = fin(2025, interest_expense=0.0)
    cfg = MetricConfig(points=10, bands=[(10, 10), (5, 7), (3, 4), (1, 2)])

    score = metric_interest_coverage(years, cfg)

    assert score.points == 10
    assert score.status == STATUS_OK


def test_interest_coverage_is_missing_when_the_expense_is_unknown():
    years = five_years()
    years[-1] = fin(2025, interest_expense=None)
    cfg = MetricConfig(points=10, bands=[(10, 10)])

    assert metric_interest_coverage(years, cfg).status == STATUS_MISSING


# --- 현금흐름 -------------------------------------------------------

def test_ocf_positive_years_counts_consecutively_from_the_latest():
    years = five_years()
    years[-3] = fin(years[-3].fiscal_year, operating_cash_flow=-10.0)
    cfg = MetricConfig(points=10, bands=[(5, 10), (4, 8), (3, 5), (2, 3)])

    score = metric_ocf_positive_years(years, cfg)

    assert score.value == 2       # 최근 2년만 연속 흑자
    assert score.points == 3


def test_ocf_positive_years_is_missing_when_no_year_has_data():
    years = five_years(operating_cash_flow=None)

    assert metric_ocf_positive_years(
        years, MetricConfig(points=10, bands=[(5, 10)])
    ).status == STATUS_MISSING


def test_ocf_to_op_income_uses_three_year_totals():
    years = five_years()
    for i in (-3, -2, -1):
        years[i] = fin(years[i].fiscal_year, operating_cash_flow=120.0,
                       operating_income=100.0)
    cfg = MetricConfig(points=10, bands=[(1.2, 10), (1.0, 8), (0.8, 5), (0.5, 2)])

    score = metric_ocf_to_op_income(years, cfg)

    assert math.isclose(score.value, 1.2)
    assert score.points == 10


def test_ocf_to_op_income_records_a_nonpositive_denominator():
    """설계서 §8.2: 3년 누계 영업이익이 0 이하면 비율이 무의미하다."""
    years = five_years()
    for i in (-3, -2, -1):
        years[i] = fin(years[i].fiscal_year, operating_income=-100.0)
    cfg = MetricConfig(points=10, bands=[(1.2, 10)])

    score = metric_ocf_to_op_income(years, cfg)

    assert score.status == STATUS_DENOMINATOR_NONPOSITIVE
    assert score.points == 0
    assert score.max_points == 10


def test_fcf_positive_years_subtracts_capex():
    years = five_years()
    for i in (-2, -1):
        years[i] = fin(years[i].fiscal_year, operating_cash_flow=100.0, capex=150.0)
    cfg = MetricConfig(points=5, bands=[(3, 5), (2, 3), (1, 1)])

    score = metric_fcf_positive_years(years, cfg)

    assert score.value == 0
    assert score.points == 0


def test_fcf_positive_years_is_missing_without_capex():
    years = five_years(capex=None)

    assert metric_fcf_positive_years(
        years, MetricConfig(points=5, bands=[(3, 5)])
    ).status == STATUS_MISSING
