import json
from pathlib import Path

import pytest

from stockscout.models import RawStatement
from stockscout.normalize.kr_mapping import BLANK_AMOUNTS, normalize_dart_statement, pick_amount

FIXTURE = Path(__file__).parent / "fixtures" / "dart_statement_005930_2024.json"


def item(sj_div, account_id, account_nm, amount):
    return {
        "sj_div": sj_div, "account_id": account_id,
        "account_nm": account_nm, "thstrm_amount": amount,
    }


def test_pick_amount_matches_by_account_id_first():
    items = [
        item("IS", "ifrs-full_Revenue", "엉뚱한이름", "1,000"),
        item("IS", "other", "매출액", "9,999"),
    ]

    assert pick_amount(items, ids=["ifrs-full_Revenue"], names=["매출액"]) == 1000.0


def test_pick_amount_falls_back_to_account_name():
    items = [item("IS", "unmapped_id", "영업수익", "2,500")]

    assert pick_amount(items, ids=["ifrs-full_Revenue"], names=["매출액", "영업수익"]) == 2500.0


def test_pick_amount_handles_negative_parentheses():
    items = [item("CF", "x", "유형자산의 취득", "(1,234)")]

    assert pick_amount(items, ids=[], names=["유형자산의 취득"]) == -1234.0


def test_pick_amount_returns_none_when_absent():
    assert pick_amount([], ids=["a"], names=["b"]) is None


def test_pick_amount_returns_none_for_blank_amount():
    items = [item("IS", "ifrs-full_Revenue", "매출액", "-")]

    assert pick_amount(items, ids=["ifrs-full_Revenue"], names=[]) is None


def test_normalize_produces_required_fields():
    stmt = RawStatement(
        ticker="005930.KS", fiscal_year=2024, source="dart",
        payload={"list": [
            item("IS", "ifrs-full_Revenue", "매출액", "300,000"),
            item("IS", "dart_OperatingIncomeLoss", "영업이익", "30,000"),
            item("BS", "ifrs-full_Liabilities", "부채총계", "60,000"),
            item("BS", "ifrs-full_Equity", "자본총계", "200,000"),
        ]},
    )

    fin = normalize_dart_statement(stmt)

    assert fin.has_required
    assert fin.revenue == 300000.0
    assert fin.operating_income == 30000.0
    assert fin.currency == "KRW"
    assert fin.fiscal_year == 2024


def test_normalize_stores_capex_as_positive_magnitude():
    stmt = RawStatement(
        ticker="A", fiscal_year=2024, source="dart",
        payload={"list": [item("CF", "x", "유형자산의 취득", "(5,000)")]},
    )

    fin = normalize_dart_statement(stmt)

    assert fin.capex == 5000.0


def test_normalize_reports_missing_when_nothing_matches():
    stmt = RawStatement(
        ticker="A", fiscal_year=2024, source="dart", payload={"list": []}
    )

    fin = normalize_dart_statement(stmt)

    assert fin.has_required is False
    assert "revenue" in fin.missing_fields


@pytest.mark.skipif(not FIXTURE.exists(), reason="Task 8에서 픽스처를 만들어야 한다")
def test_real_fixture_maps_all_required_fields():
    """실제 삼성전자 응답에서 필수 4개 항목이 전부 잡혀야 한다.

    실패하면 매핑표에 실제 account_id를 추가한다. 실패 메시지가 무엇이 빠졌는지 알려준다.
    """
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    stmt = RawStatement(
        ticker="005930.KS", fiscal_year=2024, source="dart", payload=payload
    )

    fin = normalize_dart_statement(stmt)

    assert fin.has_required, (
        f"필수 항목 매핑 실패: {fin.missing_fields}. "
        "scripts/spike_dart.py statement 로 실제 account_id를 확인해 "
        "kr_mapping.py의 ACCOUNT_MAP에 추가하세요."
    )


@pytest.mark.skipif(not FIXTURE.exists(), reason="Task 8에서 픽스처를 만들어야 한다")
def test_real_fixture_maps_most_optional_fields():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    stmt = RawStatement(
        ticker="005930.KS", fiscal_year=2024, source="dart", payload=payload
    )

    fin = normalize_dart_statement(stmt)

    for field in ("net_income", "total_assets", "current_assets",
                  "current_liabilities", "operating_cash_flow"):
        assert getattr(fin, field) is not None, (
            f"{field} 매핑 실패. spike_dart.py 출력에서 실제 account_id를 찾아 추가하세요."
        )


def test_pick_amount_skips_blank_before_matching_duplicate_id():
    """DART는 연결/별도 재무제표에서 같은 account_id를 반복한다.

    첫 항목의 금액이 공백이면 인덱싱 전에 걸러지므로, 같은 id를 가진
    두 번째(실제 값이 있는) 항목이 가려지지 않고 선택되어야 한다.
    """
    items = [
        item("IS", "ifrs-full_Revenue", "매출액", "-"),
        item("IS", "ifrs-full_Revenue", "매출액", "5,000"),
    ]

    assert pick_amount(items, ids=["ifrs-full_Revenue"], names=[]) == 5000.0


def test_blank_amounts_has_four_distinct_markers():
    """'-'(ASCII)와 '－'(전각)는 서로 다른 코드포인트다.

    편집기가 이를 정규화하면 집합이 조용히 3개로 줄어들 수 있으므로 회귀 방지용으로 개수를 고정한다.
    """
    assert len(BLANK_AMOUNTS) == 4
    assert "-" in BLANK_AMOUNTS
    assert "－" in BLANK_AMOUNTS  # U+FF0D FULLWIDTH HYPHEN-MINUS ('－')
    assert "-" != "－"
