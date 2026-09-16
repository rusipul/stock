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


def five_years():
    """정상 재무 다섯 해(2021~2025). 바꿀 해는 호출한 쪽에서 교체한다.

    일괄 수정용 **kwargs를 두지 않는 것은 의도다 — 매출 급감 필터의 기준
    연도까지 같은 값으로 덮어 '어느 해를 기준으로 재는가'를 검증할 수 없게
    만든다.
    """
    return [fin(y) for y in range(2021, 2026)]


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

    # 정확한 집합으로 단언한다. `len(failures) >= 2`로 두면 사유가 하나
    # 더 붙어도(예: 자본잠식 기업에 HIGH_DEBT가 겹쳐 붙어도) 통과한다 —
    # 모으는 것은 확인하면서 *무엇을* 모았는지는 놓치는 단언이었다.
    assert set(failures) == {CAPITAL_IMPAIRED, ADVERSE_AUDIT_OPINION}


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


def test_proxy_interest_expense_skips_the_coverage_filter():
    """설계서 §8.1.1: 금융원가로 대신 채운 해는 이자보상배율 판정에서 뺀다.

    금융원가는 이자비용의 상위 개념이라 배율이 3~10배 낮게 나온다.
    실측에서 SK이노베이션의 '이자비용'이 매출의 8%(6.36조)로 잡혔다.
    그 값으로 내리는 이진 판정은 보수적인 게 아니라 틀린 것이다.
    """
    years = five_years()
    for i in (-2, -1):
        years[i] = fin(
            years[i].fiscal_year,
            operating_income=5.0,
            interest_expense=10.0,          # 배율 0.5 — 대체값이 아니었다면 걸린다
            proxy_fields=["interest_expense"],
        )

    assert INTEREST_UNCOVERED not in apply_financial_filters(years, CONFIG).failures


def test_other_proxy_fields_do_not_disable_the_coverage_filter():
    """대체값인 항목이 interest_expense일 때만 건너뛴다.

    proxy_fields가 비어 있지 않다는 이유만으로 필터를 끄면, 매출을
    대체 계정으로 채운 회사가 이자 미충당 판정까지 통째로 면제받는다.
    """
    years = five_years()
    for i in (-2, -1):
        years[i] = fin(
            years[i].fiscal_year,
            operating_income=5.0,
            interest_expense=10.0,
            proxy_fields=["revenue"],       # 이자비용은 실측값이다
        )

    assert INTEREST_UNCOVERED in apply_financial_filters(years, CONFIG).failures


def test_one_proxy_year_in_the_window_skips_the_filter():
    """창에 대체값 해와 실측 해가 섞이면 판정하지 않는다.

    §8.1은 '최근 2년 *모두* 배율 < 1'을 요구한다. 두 해 중 하나를
    잴 수 없으면 '모두'를 확인할 방법이 없다. NULL·0인 해가 섞였을 때와
    같은 처리이며, '모르는 것으로 떨어뜨리지 않는다'의 직접적 귀결이다.
    실측 한 해만으로 떨어뜨리면 그건 2년 필터가 아니라 1년 필터다.
    """
    years = five_years()
    years[-2] = fin(
        years[-2].fiscal_year, operating_income=5.0, interest_expense=10.0,
        proxy_fields=["interest_expense"],
    )
    years[-1] = fin(
        years[-1].fiscal_year, operating_income=5.0, interest_expense=10.0,
    )

    assert INTEREST_UNCOVERED not in apply_financial_filters(years, CONFIG).failures


# --- 경계·설정 준수 (명세 준수 검토에서 드러난 빈 구멍) -----------------


def test_equity_of_exactly_zero_is_capital_impairment():
    """자본이 정확히 0인 회사. `<= 0`의 등호가 살아 있는지 본다.

    등호를 잃으면 이 회사는 하드필터를 *전부* 통과한다. debt_ratio도
    자본이 0이면 None이라 HIGH_DEBT가 대신 잡아 주지 못하기 때문이다.
    자본잠식 경계선에 선 회사가 그대로 점수판에 올라온다.
    """
    years = five_years()
    years[-1] = fin(2025, total_equity=0.0)

    failures = apply_financial_filters(years, CONFIG).failures

    # 사유가 이것 하나뿐이라는 것이 요점이다 — 받쳐 주는 그물이 없다.
    assert failures == [CAPITAL_IMPAIRED]


def test_one_bad_year_does_not_trigger_interest_uncovered():
    """설계서 §8.1은 '최근 2년 *모두* 배율 < 1'을 요구한다.

    all을 any로 바꾸면 한 해 삐끗한 경기순환 기업이 통째로 탈락한다.
    """
    years = five_years()
    years[-2] = fin(years[-2].fiscal_year, operating_income=100.0, interest_expense=10.0)  # 10배
    years[-1] = fin(years[-1].fiscal_year, operating_income=5.0, interest_expense=10.0)    # 0.5배

    assert INTEREST_UNCOVERED not in apply_financial_filters(years, CONFIG).failures


def test_capital_impairment_does_not_also_report_high_debt():
    """자본잠식 기업에 HIGH_DEBT를 겹쳐 붙이지 않는다.

    부채비율은 자본이 0 이하면 정의되지 않는다(debt_ratio가 None). 그 상태를
    '부채비율이 높다'로 보고하면 화면의 제외 사유가 틀린다 — 부채가 많아서가
    아니라 자본이 없어서인데 사용자는 문제가 둘이라고 읽는다.
    """
    years = five_years()
    years[-1] = fin(2025, total_equity=-10.0, total_liabilities=5_000.0)

    failures = apply_financial_filters(years, CONFIG).failures

    assert CAPITAL_IMPAIRED in failures
    assert HIGH_DEBT not in failures


def _years_with_revenue(revenues):
    """2021~2025 다섯 해에 연도별로 다른 매출을 심는다.

    매출이 모든 해 같은 픽스처로는 '어느 해를 기준으로 재는가'를 검증할 수
    없다 — 기준 연도가 바뀌어도 같은 값이 나오기 때문이다.
    """
    return [fin(2021 + i, revenue=r) for i, r in enumerate(revenues)]


def test_revenue_collapse_is_measured_against_three_years_back():
    """기준은 3년 전(2022)이지 2년 전(2023)이 아니다."""
    #                              2021  2022   2023  2024  2025
    years = _years_with_revenue([2_000, 1_000, 600, 550, 500])

    # 2022년 1,000 대비 2025년 500 → -50%. 임계치 이하다.
    # 2023년 600을 기준으로 재면 -16.7%에 그쳐 걸리지 않는다.
    assert REVENUE_COLLAPSE in apply_financial_filters(years, CONFIG).failures


def test_recent_two_year_drop_alone_is_not_revenue_collapse():
    """3년 전 대비로는 오히려 늘었다면 급감이 아니다.

    앞 테스트의 반대 방향이다. 둘이 함께 기준 연도를 양쪽에서 고정한다.
    """
    #                              2021   2022  2023   2024   2025
    years = _years_with_revenue([1_000, 400, 1_000, 1_000, 500])

    # 2022년 400 대비 2025년 500 → +25%. 2년 전(2023) 1,000 대비로만 -50%다.
    assert REVENUE_COLLAPSE not in apply_financial_filters(years, CONFIG).failures


# --- 설계서 §1: "기준은 코드가 아니라 설정이다" -------------------------
#
# 임계치를 코드에 하드코딩하는 변이가 전부 살아남는 것을 명세 준수 검토가
# 잡아냈다. 모든 테스트가 HardFilterConfig() 기본값만 썼기 때문이다. 원칙을
# 지키는 테스트가 하나도 없으면 그것은 원칙이 아니라 희망이다. 아래는
# HardFilterConfig의 여섯 필드가 *전부* 판정에 실제로 도달하는지 본다.
# 각 테스트는 같은 재무제표를 기본 설정과 바꾼 설정에 각각 통과시켜,
# 판정이 재무가 아니라 설정을 따라 갈리는 것을 보인다.


def test_max_debt_ratio_comes_from_config():
    years = five_years()
    years[-1] = fin(2025, total_liabilities=1_500.0, total_equity=1_000.0)  # 150%

    assert HIGH_DEBT not in apply_financial_filters(years, CONFIG).failures
    assert HIGH_DEBT in apply_financial_filters(
        years, HardFilterConfig(max_debt_ratio=100.0)
    ).failures


def test_consecutive_operating_loss_years_comes_from_config():
    years = five_years()
    for i in (-2, -1):
        years[i] = fin(years[i].fiscal_year, operating_income=-5.0)   # 2년 연속 적자

    assert CONSECUTIVE_LOSS not in apply_financial_filters(years, CONFIG).failures
    assert CONSECUTIVE_LOSS in apply_financial_filters(
        years, HardFilterConfig(consecutive_operating_loss_years=2)
    ).failures


def test_min_history_years_comes_from_config():
    years = [fin(y) for y in (2022, 2023, 2024, 2025)]   # 4개년

    assert INSUFFICIENT_HISTORY in apply_financial_filters(years, CONFIG).failures
    assert INSUFFICIENT_HISTORY not in apply_financial_filters(
        years, HardFilterConfig(min_history_years=3)
    ).failures


def test_min_interest_coverage_comes_from_config():
    years = five_years()
    for i in (-2, -1):
        years[i] = fin(years[i].fiscal_year, operating_income=20.0, interest_expense=10.0)  # 2배

    assert INTEREST_UNCOVERED not in apply_financial_filters(years, CONFIG).failures
    assert INTEREST_UNCOVERED in apply_financial_filters(
        years, HardFilterConfig(min_interest_coverage=3.0)
    ).failures


def test_interest_coverage_years_comes_from_config():
    years = five_years()
    years[-2] = fin(years[-2].fiscal_year, operating_income=100.0, interest_expense=10.0)  # 10배
    years[-1] = fin(years[-1].fiscal_year, operating_income=5.0, interest_expense=10.0)    # 0.5배

    assert INTEREST_UNCOVERED not in apply_financial_filters(years, CONFIG).failures
    assert INTEREST_UNCOVERED in apply_financial_filters(
        years, HardFilterConfig(interest_coverage_years=1)
    ).failures


def test_max_revenue_decline_3y_comes_from_config():
    #                              2021   2022   2023  2024  2025
    years = _years_with_revenue([1_000, 1_000, 900, 800, 700])   # 3년 전 대비 -30%

    assert REVENUE_COLLAPSE not in apply_financial_filters(years, CONFIG).failures
    assert REVENUE_COLLAPSE in apply_financial_filters(
        years, HardFilterConfig(max_revenue_decline_3y=-0.2)
    ).failures
