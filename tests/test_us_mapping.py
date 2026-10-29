"""US-GAAP 태그 -> Financials. 설계서 §18.4."""

import json
from pathlib import Path

from stockscout.models import RawStatement
from stockscout.normalize.us_mapping import normalize_edgar_statement

FIXTURE = Path(__file__).parent / "fixtures" / "edgar_aapl_2025.json"


def stmt(facts, year=2025):
    return RawStatement(ticker="AAPL", fiscal_year=year, source="edgar",
                        payload={"period_end": f"{year}-09-27", "facts": facts})


def test_real_aapl_2025_maps_the_required_fields():
    """실제 SEC 응답. 값은 AAPL 10-K(2025-09-27)와 대조했다."""
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    fin = normalize_edgar_statement(
        RawStatement(ticker="AAPL", fiscal_year=2025, source="edgar", payload=payload))

    assert fin.currency == "USD"
    assert fin.revenue == 416_161_000_000
    assert fin.operating_income == 133_050_000_000
    assert fin.total_liabilities == 285_508_000_000
    assert fin.capex == 12_715_000_000
    assert fin.has_required


def test_real_aapl_2025_has_no_interest_expense():
    """AAPL은 2024년부터 이자비용을 따로 보고하지 않는다. 대체값을 두지 않는다."""
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    fin = normalize_edgar_statement(
        RawStatement(ticker="AAPL", fiscal_year=2025, source="edgar", payload=payload))

    assert fin.interest_expense is None
    assert fin.proxy_fields == []


def test_revenue_tag_priority_is_applied_per_year():
    """AAPL은 Revenues에서 RevenueFromContract...로 옮겼다. 해마다 있는 쪽을 쓴다."""
    old = normalize_edgar_statement(stmt({"Revenues": 260.0}, year=2019))
    new = normalize_edgar_statement(stmt(
        {"RevenueFromContractWithCustomerExcludingAssessedTax": 416.0}))

    assert old.revenue == 260.0
    assert new.revenue == 416.0


def test_the_first_tag_in_priority_wins():
    fin = normalize_edgar_statement(stmt({
        "RevenueFromContractWithCustomerExcludingAssessedTax": 416.0,
        "Revenues": 999.0,
    }))

    assert fin.revenue == 416.0


def test_equity_includes_noncontrolling_interest_like_kr():
    """한국은 자본총계(비지배지분 포함)를 쓴다. 같은 개념을 먼저 본다."""
    fin = normalize_edgar_statement(stmt({
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest": 110.0,
        "StockholdersEquity": 100.0,
    }))

    assert fin.total_equity == 110.0


def test_liabilities_are_derived_when_not_reported():
    """총부채를 따로 내지 않는 회사가 있다. 부채와자본총계 - 자본총계는 항등식이다."""
    fin = normalize_edgar_statement(stmt({
        "LiabilitiesAndStockholdersEquity": 1000.0,
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest": 300.0,
    }))

    assert fin.total_liabilities == 700.0
    assert fin.proxy_fields == []    # 항등식이라 대체값이 아니다


def test_reported_liabilities_win_over_the_derivation():
    fin = normalize_edgar_statement(stmt({
        "Liabilities": 650.0,
        "LiabilitiesAndStockholdersEquity": 1000.0,
        "StockholdersEquity": 300.0,
    }))

    assert fin.total_liabilities == 650.0


def test_capex_and_interest_are_stored_as_positive_sizes():
    fin = normalize_edgar_statement(stmt({
        "PaymentsToAcquirePropertyPlantAndEquipment": -30.0,
        "InterestExpense": -5.0,
    }))

    assert fin.capex == 30.0
    assert fin.interest_expense == 5.0


def test_shares_fall_back_to_the_cover_page():
    fin = normalize_edgar_statement(stmt({"EntityCommonStockSharesOutstanding": 15.0}))

    assert fin.shares_outstanding == 15.0
    assert fin.shares_treasury is None      # 미국 유통주식수는 이미 자기주식을 뺐다
    assert fin.audit_opinion is None        # XBRL에 없다


def test_an_empty_payload_is_all_missing_not_an_error():
    fin = normalize_edgar_statement(stmt({}))

    assert not fin.has_required


# --- 실측 40종목에서 더한 태그 (설계서 §18.4) ----------------------------


def test_interest_and_debt_expense_is_interest_expense():
    """BA·HON은 InterestAndDebtExpense로 보고한다. 이자비용과 같은 개념이다."""
    fin = normalize_edgar_statement(stmt({"InterestAndDebtExpense": 2725.0}))

    assert fin.interest_expense == 2725.0
    assert fin.proxy_fields == []


def test_interest_paid_is_not_interest_expense():
    """CAT·GE에는 InterestPaidNet(현금 지급액)뿐이다. 뜻이 달라 쓰지 않는다."""
    assert normalize_edgar_statement(
        stmt({"InterestPaidNet": 1738.0})).interest_expense is None


def test_other_productive_assets_is_capex():
    """VZ의 설비투자는 PaymentsToAcquireOtherProductiveAssets로 온다."""
    assert normalize_edgar_statement(
        stmt({"PaymentsToAcquireOtherProductiveAssets": 17090.0})).capex == 17090.0


def test_weighted_average_shares_are_a_marked_proxy():
    """META·NKE·EL·UPS는 주식 클래스가 여럿이라 회사 전체 기말 주식수가 없다.

    기본 EPS의 분모인 가중평균 주식수로 대신하고, 대체값으로 표시한다.
    """
    fin = normalize_edgar_statement(stmt({
        "WeightedAverageNumberOfSharesOutstandingBasic": 2521.0}))

    assert fin.shares_outstanding == 2521.0
    assert fin.proxy_fields == ["shares_outstanding"]


def test_reported_shares_win_over_the_weighted_average():
    fin = normalize_edgar_statement(stmt({
        "CommonStockSharesOutstanding": 100.0,
        "WeightedAverageNumberOfSharesOutstandingBasic": 105.0}))

    assert fin.shares_outstanding == 100.0
    assert fin.proxy_fields == []


# --- 영업이익 대체값 (계획 E 후속) ---------------------------------------


def test_pretax_income_is_a_marked_proxy_for_operating_income():
    """JNJ·MRK·PFE·GE·NKE는 손익계산서에 영업이익 줄이 없다."""
    fin = normalize_edgar_statement(stmt({
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest": 32.0}))

    assert fin.operating_income == 32.0
    assert fin.proxy_fields == ["operating_income"]


def test_cvx_style_pretax_tag_is_also_used():
    fin = normalize_edgar_statement(stmt({
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments": 19.7}))

    assert fin.operating_income == 19.7
    assert fin.proxy_fields == ["operating_income"]


def test_reported_operating_income_wins_over_pretax():
    fin = normalize_edgar_statement(stmt({
        "OperatingIncomeLoss": 133.0,
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest": 132.0}))

    assert fin.operating_income == 133.0
    assert fin.proxy_fields == []


def test_domestic_pretax_alone_is_not_used():
    """국내분만으로는 회사 전체 이익이 아니다."""
    fin = normalize_edgar_statement(stmt({
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesDomestic": 15.0}))

    assert fin.operating_income is None



# --- 현금·단기차입금 (12개월 자금 여력) ------------------------------------


def test_us_short_term_debt_sums_borrowings_and_current_long_term_debt():
    fin = normalize_edgar_statement(stmt({
        "CashAndCashEquivalentsAtCarryingValue": 500.0,
        "ShortTermBorrowings": 100.0,
        "CommercialPaper": 100.0,           # 대개 ShortTermBorrowings에 포함된다
        "LongTermDebtCurrent": 40.0,
    }))

    assert fin.cash == 500.0
    assert fin.short_term_debt == 140.0


def test_us_debt_current_is_already_the_total():
    fin = normalize_edgar_statement(stmt({"DebtCurrent": 90.0, "LongTermDebtCurrent": 40.0}))

    assert fin.short_term_debt == 90.0


def test_real_aapl_2025_has_cash_and_short_term_debt():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    fin = normalize_edgar_statement(
        RawStatement(ticker="AAPL", fiscal_year=2025, source="edgar", payload=payload))

    assert fin.cash is not None and fin.cash > 0
    assert fin.short_term_debt is not None and fin.short_term_debt > 0


def test_cash_falls_back_to_the_cash_flow_ending_balance():
    """실측: CVX는 재무상태표 현금 태그 없이 현금흐름표 기말 현금만 낸다."""
    fin = normalize_edgar_statement(stmt({
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents": 7285.0}))

    assert fin.cash == 7285.0
