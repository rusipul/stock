"""재무 하드필터. 설계서 §8.1."""

from stockscout.analysis.filters import (
    ADVERSE_AUDIT_OPINION,
    CAPITAL_IMPAIRED,
    CONSECUTIVE_LOSS,
    DATA_INSUFFICIENT,
    HIGH_DEBT,
    INSUFFICIENT_HISTORY,
    INTEREST_UNCOVERED,
    REVENUE_COLLAPSE,
    apply_financial_filters,
)
from stockscout.config import HardFilterConfig
from stockscout.models import Financials


def fin(year, **kwargs):
    """필수 4항목이 채워진 정상 재무 한 해. 바꿀 것만 kwargs로 준다."""
    base = dict(
        ticker="005930.KS", fiscal_year=year, currency="KRW",
        revenue=1_000.0, operating_income=100.0, net_income=80.0,
        total_assets=2_000.0, total_liabilities=500.0, total_equity=1_000.0,
        current_assets=800.0, current_liabilities=400.0,
        interest_expense=10.0, operating_cash_flow=120.0, capex=30.0,
        audit_opinion="적정의견",
    )
    base.update(kwargs)
    return Financials(**base)


def five_years(**kwargs):
    return [fin(y, **kwargs) for y in range(2021, 2026)]


CONFIG = HardFilterConfig()


def test_healthy_company_passes_everything():
    outcome = apply_financial_filters(five_years(), CONFIG)

    assert outcome.failures == []
    assert outcome.passed is True


def test_capital_impairment_is_caught():
    years = five_years()
    years[-1] = fin(2025, total_equity=-10.0)

    assert CAPITAL_IMPAIRED in apply_financial_filters(years, CONFIG).failures


def test_three_consecutive_operating_losses_are_caught():
    years = five_years()
    for i in (-3, -2, -1):
        years[i] = fin(years[i].fiscal_year, operating_income=-5.0)

    assert CONSECUTIVE_LOSS in apply_financial_filters(years, CONFIG).failures


def test_two_consecutive_losses_are_not_enough():
    """설정이 3년이다. 2년 적자로 떨어뜨리면 경기 순환 기업이 전멸한다."""
    years = five_years()
    for i in (-2, -1):
        years[i] = fin(years[i].fiscal_year, operating_income=-5.0)

    assert CONSECUTIVE_LOSS not in apply_financial_filters(years, CONFIG).failures


def test_high_debt_is_caught():
    years = five_years()
    years[-1] = fin(2025, total_liabilities=2_100.0, total_equity=1_000.0)  # 210%

    assert HIGH_DEBT in apply_financial_filters(years, CONFIG).failures


def test_debt_exactly_at_the_threshold_passes():
    """200%는 '초과'가 아니다. 경계에서 한 종목이 갈린다."""
    years = five_years()
    years[-1] = fin(2025, total_liabilities=2_000.0, total_equity=1_000.0)  # 200%

    assert HIGH_DEBT not in apply_financial_filters(years, CONFIG).failures


def test_interest_uncovered_for_two_years_is_caught():
    years = five_years()
    for i in (-2, -1):
        years[i] = fin(years[i].fiscal_year, operating_income=5.0, interest_expense=10.0)

    assert INTEREST_UNCOVERED in apply_financial_filters(years, CONFIG).failures


def test_missing_interest_expense_passes_the_coverage_filter():
    """설계서 §8.1: interest_expense가 NULL이면 통과.

    이자비용을 못 구했다는 것은 부채 부담이 크다는 뜻이 아니다.
    계정과목 매핑이 실패한 것뿐이다. 모르는 것으로 떨어뜨리지 않는다.
    """
    years = five_years()
    for i in (-2, -1):
        years[i] = fin(years[i].fiscal_year, operating_income=5.0, interest_expense=None)

    assert INTEREST_UNCOVERED not in apply_financial_filters(years, CONFIG).failures


def test_zero_interest_expense_passes_the_coverage_filter():
    """이자비용 0은 무차입이라는 뜻이다. 가장 건전한 상태다."""
    years = five_years()
    for i in (-2, -1):
        years[i] = fin(years[i].fiscal_year, operating_income=5.0, interest_expense=0.0)

    assert INTEREST_UNCOVERED not in apply_financial_filters(years, CONFIG).failures


def test_revenue_collapse_is_caught():
    years = five_years()
    years[-4] = fin(years[-4].fiscal_year, revenue=1_000.0)
    years[-1] = fin(years[-1].fiscal_year, revenue=400.0)   # -60%

    assert REVENUE_COLLAPSE in apply_financial_filters(years, CONFIG).failures


def test_revenue_halved_exactly_is_caught():
    """-50%는 임계치 '이하'다 (max_revenue_decline_3y: -0.5)."""
    years = five_years()
    years[-4] = fin(years[-4].fiscal_year, revenue=1_000.0)
    years[-1] = fin(years[-1].fiscal_year, revenue=500.0)

    assert REVENUE_COLLAPSE in apply_financial_filters(years, CONFIG).failures


def test_fewer_than_five_years_is_insufficient_history():
    assert INSUFFICIENT_HISTORY in apply_financial_filters(
        [fin(y) for y in (2023, 2024, 2025)], CONFIG
    ).failures


def test_missing_required_field_is_data_insufficient():
    years = five_years()
    years[-1] = fin(2025, revenue=None)

    assert DATA_INSUFFICIENT in apply_financial_filters(years, CONFIG).failures


def test_adverse_audit_opinion_is_caught():
    years = five_years()
    years[-1] = fin(2025, audit_opinion="의견거절")

    assert ADVERSE_AUDIT_OPINION in apply_financial_filters(years, CONFIG).failures


def test_missing_audit_opinion_passes():
    """수집이 안 된 것과 비적정은 다르다."""
    years = five_years()
    years[-1] = fin(2025, audit_opinion=None)

    assert ADVERSE_AUDIT_OPINION not in apply_financial_filters(years, CONFIG).failures


def test_empty_financials_is_insufficient_not_a_crash():
    outcome = apply_financial_filters([], CONFIG)

    assert INSUFFICIENT_HISTORY in outcome.failures
    assert DATA_INSUFFICIENT in outcome.failures


def test_all_failures_are_collected_not_just_the_first():
    """화면이 '왜 제외됐나'를 한 번에 보여줘야 한다.

    사유가 하나만 나오면 사용자가 고치고 다시 돌렸을 때 또 다른 사유로
    걸린다. FilterOutcome이 목록인 이유다.
    """
    years = five_years()
    years[-1] = fin(2025, total_equity=-10.0, total_liabilities=5_000.0,
                    audit_opinion="의견거절")

    failures = apply_financial_filters(years, CONFIG).failures

    assert CAPITAL_IMPAIRED in failures
    assert ADVERSE_AUDIT_OPINION in failures
    assert len(failures) >= 2


def test_financials_may_arrive_unsorted():
    """Repository는 정렬해 주지만, 순수 함수가 그것에 기대면 안 된다."""
    years = list(reversed(five_years()))
    years[0] = fin(2025, total_equity=-10.0)   # 뒤집힌 목록의 첫 원소가 최근 연도

    assert CAPITAL_IMPAIRED in apply_financial_filters(years, CONFIG).failures


def test_one_debt_free_year_in_the_window_skips_the_coverage_filter():
    """무차입이던 해가 창에 섞여 있으면 판정하지 않는다.

    이자보상배율은 '이자를 갚을 수 있는가'를 여러 해에 걸쳐 묻는 필터다.
    작년에 빚이 없었다면 올해 한 해의 부진만으로 떨어뜨릴 근거가 없다.
    """
    years = five_years()
    years[-2] = fin(years[-2].fiscal_year, operating_income=5.0, interest_expense=0.0)
    years[-1] = fin(years[-1].fiscal_year, operating_income=5.0, interest_expense=10.0)

    assert INTEREST_UNCOVERED not in apply_financial_filters(years, CONFIG).failures
