import json
from pathlib import Path

import pytest

from stockscout.models import RawStatement
from stockscout.normalize.kr_mapping import (
    BLANK_AMOUNTS,
    normalize_dart_statement,
    parse_share_counts,
    pick_amount,
    pick_tiered,
)

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


def test_tier_one_wins_even_when_tier_two_has_a_standard_id():
    """진짜 이자비용이 이름으로만 있어도 금융비용에 지지 않아야 한다.

    실측 13건이 이 모양이다 (LG생활건강·한국전력·삼성에스디에스).
    pick_amount은 모든 id를 이름보다 먼저 보므로, FinanceCosts를 그냥
    id 목록에 넣으면 이 회사들이 전부 틀린 값을 쓰게 된다.
    """
    items = [
        item("IS", "-표준계정코드 미사용-", "이자비용", "100"),
        item("IS", "ifrs-full_FinanceCosts", "금융비용", "900"),
    ]

    value, tier = pick_tiered(items, [
        (["ifrs-full_InterestExpense"], ["이자비용"]),
        (["ifrs-full_FinanceCosts"], ["금융비용"]),
    ])

    assert value == 100.0
    assert tier == 0


def test_tier_two_is_used_when_tier_one_is_absent():
    items = [item("IS", "ifrs-full_FinanceCosts", "금융비용", "900")]

    value, tier = pick_tiered(items, [
        (["ifrs-full_InterestExpense"], ["이자비용"]),
        (["ifrs-full_FinanceCosts"], ["금융비용"]),
    ])

    assert value == 900.0
    assert tier == 1


def test_pick_tiered_returns_none_when_no_tier_matches():
    assert pick_tiered([item("IS", "x", "엉뚱한계정", "5")], [(["a"], ["b"])]) == (None, None)


def test_normalize_records_which_fields_used_a_fallback():
    """대체값을 썼다는 사실이 남아야 판정 근거에 드러낼 수 있다."""
    stmt = RawStatement(
        ticker="051900.KS", fiscal_year=2024, source="dart",
        payload={"list": [
            item("IS", "ifrs-full_Revenue", "매출액", "300,000"),
            item("IS", "dart_OperatingIncomeLoss", "영업이익", "30,000"),
            item("BS", "ifrs-full_Liabilities", "부채총계", "60,000"),
            item("BS", "ifrs-full_Equity", "자본총계", "200,000"),
            item("IS", "ifrs-full_FinanceCosts", "금융비용", "900"),
        ]},
    )

    fin = normalize_dart_statement(stmt)

    assert fin.interest_expense == 900.0
    assert fin.proxy_fields == ["interest_expense"]


def test_normalize_leaves_proxy_fields_empty_for_exact_matches():
    stmt = RawStatement(
        ticker="005930.KS", fiscal_year=2024, source="dart",
        payload={"list": [
            item("IS", "ifrs-full_Revenue", "매출액", "300,000"),
            item("IS", "dart_OperatingIncomeLoss", "영업이익", "30,000"),
            item("BS", "ifrs-full_Liabilities", "부채총계", "60,000"),
            item("BS", "ifrs-full_Equity", "자본총계", "200,000"),
            item("IS", "ifrs-full_InterestExpense", "이자비용", "700"),
        ]},
    )

    fin = normalize_dart_statement(stmt)

    assert fin.interest_expense == 700.0
    assert fin.proxy_fields == []


# --- 주식의 총수 현황 (stockTotqySttus.json) --------------------------


def share_row(se, istc, tesstk):
    return {"se": se, "istc_totqy": istc, "tesstk_co": tesstk,
            "distb_stock_co": "0", "corp_cls": "Y"}


def test_shares_uses_common_stock_not_the_total():
    """합계를 쓰면 시가총액이 부풀려진다.

    삼성전자 2024 실측: 보통주 59.7억 주, 합계 67.9억 주. 13.8% 차이다.
    005930은 보통주 티커이고 우선주는 005935라는 별도 티커다.
    """
    payload = {"list": [
        share_row("보통주", "5,969,782,550", "29,700,000"),
        share_row("우선주", "822,886,700", "4,050,000"),
        share_row("합계", "6,792,669,250", "33,750,000"),
        share_row("비고", "-", "-"),
    ]}

    shares, treasury = parse_share_counts(payload)

    assert shares == 5_969_782_550.0
    assert treasury == 29_700_000.0


def test_shares_falls_back_to_total_when_common_row_is_absent():
    """se 표기가 회사마다 다를 수 있다. 합계라도 있으면 NULL보다 낫다."""
    payload = {"list": [share_row("합계", "1,000,000", "10,000")]}

    assert parse_share_counts(payload) == (1_000_000.0, 10_000.0)


def test_shares_ignores_the_remarks_row():
    payload = {"list": [share_row("비고", "-", "-")]}

    assert parse_share_counts(payload) == (None, None)


def test_shares_returns_none_for_empty_payload():
    assert parse_share_counts({}) == (None, None)
    assert parse_share_counts({"list": []}) == (None, None)


# --- se 표기 27종 (실측) ---------------------------------------------
#
# DART의 se(구분)는 자유 문자열이다. KOSPI 42종목 210건에서 실제로 온
# 표기 27종을 그대로 옮겼다 — 보통주 9종과 우선주·종류주 16종이 아래
# 두 목록이고, 나머지 둘('합계'·'비고')은 개별 테스트에서 쓴다.
# 줄바꿈이 문자열 한가운데 박혀 있는 것까지 실측이다.
# 정확 일치로 "보통주"만 찾으면 아래 8종을 놓쳐 합계 행으로 폴백하고,
# 합계는 우선주를 포함하므로 시가총액이 부풀려진다(기업은행 +7.1%,
# 삼성전기 +3.9%, 신한지주 +3.4%).

COMMON_LABELS = [
    "보통주",                      # 179회
    "의결권 있는 보통주",            # 8회  (신한지주·기업은행)
    "보통주식",                    # 5회  (HD한국조선해양)
    "보통주식\n(의결권 있는주식)",     # 4회  (한화오션)
    "의결권 있는 주식",              # 4회  (기업은행·셀트리온)
    "의결권이 있는\n주식(보통주)",     # 3회  (삼성전기)
    "의결권 있는\n보통주",           # 3회  (신한지주)
    "의결권이 있는주식(보통주)",       # 2회  (삼성전기)
    "의결권 있는\n주식",             # 2회  (기업은행)
]

PREFERRED_LABELS = [
    "우선주",
    "의결권 없는 우선주",
    "의결권 없는\n우선주",
    "의결권 없는 주식",
    "의결권이 없는\n주식(우선주)",
    "의결권 없는 \n주식",
    "제1종\n우선주",
    "종류주",
    "배당우선주식",
    "배당우선전환주식",
    "배당우선상환주식",
    "배당우선 전환주식(1,2종 종류주식)",
    "배당우선 상환주식(3종 종류주식)",
    "배당우선 상환주식(4종 종류주식)",
    "우선주식\n(의결권 없는주식)",
    "의결권이 없는주식(우선주)",
]


@pytest.mark.parametrize("label", COMMON_LABELS)
def test_shares_recognizes_every_real_common_stock_label(label):
    """보통주 행은 표기가 무엇이든 합계보다 먼저 잡혀야 한다."""
    payload = {"list": [
        share_row(label, "100", "1"),
        share_row("합계", "999", "9"),
    ]}

    assert parse_share_counts(payload) == (100.0, 1.0)


@pytest.mark.parametrize("label", PREFERRED_LABELS)
def test_shares_never_treats_preferred_or_class_shares_as_common(label):
    """우선주·종류주 행만 있으면 보통주가 없는 것이다 — 그 수량을
    발행주식총수로 쓰면 안 된다."""
    assert parse_share_counts({"list": [share_row(label, "100", "1")]}) == (None, None)


@pytest.mark.parametrize("label", ["우선주", "의결권 없는 주식"])
def test_shares_falls_back_to_total_rather_than_a_preferred_row(label):
    """위 테스트가 tier=None임을 이미 확정하므로 폴백 결과는 기계적으로
    따라온다 — 16종을 다시 돌리지 않고 두 종류만 대표로 확인한다.
    '우선주'는 우선 가드에, '의결권 없는 주식'은 '의결권있는'과 다르다는
    사실에 걸린다 (가드가 아니라 마커 불일치로 걸러지는 쪽이다)."""
    payload = {"list": [
        share_row(label, "100", "1"),
        share_row("합계", "999", "9"),
    ]}

    assert parse_share_counts(payload) == (999.0, 9.0)


def test_shares_reads_samsung_electro_mechanics_as_reported():
    """삼성전기 2024 실측 payload 그대로. 괄호가 섞인 표기라 정확 일치로는
    못 잡아 합계(77,600,680)로 폴백했다 — 5개 연도 모두 3.9% 과대평가였다.
    자기주식수도 합계 행(2,053,430)이 아니라 보통주 행을 써야 한다."""
    payload = {"list": [
        share_row("의결권이 있는주식(보통주)", "74,693,696", "2,000,000"),
        share_row("의결권이 없는주식(우선주)", "2,906,984", "53,430"),
        share_row("합계", "77,600,680", "2,053,430"),
        share_row("비고", "-", "-"),
    ]}

    assert parse_share_counts(payload) == (74_693_696.0, 2_000_000.0)


def test_shares_reads_ibk_which_never_says_the_word_common():
    """기업은행 2021 실측 payload 그대로. '보통주'라는 단어가 표기에 아예
    없다 — 의결권 있는 주식이 보통주다. 합계로 폴백하면 7.1% 과대평가된다."""
    payload = {"list": [
        share_row("의결권 있는\n주식", "744,300,587", "2"),
        share_row("의결권 없는 \n주식", "53,125,282", "-"),
        share_row("합계", "797,425,869", "2"),
        share_row("비고", "-", "-"),
    ]}

    assert parse_share_counts(payload) == (744_300_587.0, 2.0)


def test_shares_skips_a_voting_row_that_is_also_preferred():
    """'의결권 있는'만 보고 판단하면 안 된다. 지금 데이터에는 없지만
    의결권 있는 우선주는 실재하는 증권이다 — 보통주로 세면 안 된다."""
    payload = {"list": [
        share_row("의결권 있는 우선주", "100", "1"),
        share_row("합계", "999", "9"),
    ]}

    assert parse_share_counts(payload) == (999.0, 9.0)
