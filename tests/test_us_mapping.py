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
