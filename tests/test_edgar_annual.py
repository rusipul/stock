"""companyfacts에서 연간 값을 고른다. 설계서 §18.2·§18.3."""

from stockscout.sources.edgar import annual_facts


def fact(val, end, start=None, form="10-K", filed="2025-11-01", fy=2025):
    entry = {"val": val, "end": end, "form": form, "filed": filed, "fy": fy,
             "fp": "FY"}
    if start:
        entry["start"] = start
    return entry


def facts(us_gaap=None, dei=None):
    def wrap(tags, unit):
        return {tag: {"units": {unit(tag): entries}} for tag, entries in tags.items()}

    unit = lambda tag: "shares" if "Shares" in tag else "USD"
    return {"facts": {"us-gaap": wrap(us_gaap or {}, unit),
                      "dei": wrap(dei or {}, unit)}}


def test_an_annual_flow_lands_in_the_year_of_its_end_date():
    cf = facts({"Revenues": [fact(100, "2025-09-27", "2024-09-29")]})

    years = annual_facts(cf)

    assert years[2025]["facts"]["Revenues"] == 100
    assert years[2025]["period_end"] == "2025-09-27"


def test_a_quarter_inside_a_10k_is_not_annual():
    """실측: AAPL의 Revenues에 form=10-K이면서 한 분기짜리 항목이 있다.

    분기 항목을 앞에 둔다. 같은 공시라 filed가 같으므로, 기간 검사가
    없으면 먼저 본 항목이 남는다 - 뒤에 두면 검사가 빠져도 통과해 버린다.
    """
    cf = facts({"Revenues": [
        fact(100, "2025-09-27", "2025-06-29"),   # 4분기만
        fact(400, "2025-09-27", "2024-09-29"),
    ]})

    assert annual_facts(cf)[2025]["facts"]["Revenues"] == 400


def test_a_10q_is_never_used():
    cf = facts({"Revenues": [
        fact(400, "2025-09-27", "2024-09-29"),
        fact(999, "2025-09-27", "2024-09-29", form="10-Q"),
    ]})

    assert annual_facts(cf)[2025]["facts"]["Revenues"] == 400


def test_comparatives_are_assigned_by_period_not_by_fy():
    """2025년 10-K에 든 2024년 비교 수치는 fy=2025지만 2024년 값이다."""
    cf = facts({"Revenues": [
        fact(400, "2025-09-27", "2024-09-29", fy=2025),
        fact(350, "2024-09-28", "2023-10-01", fy=2025),
    ]})

    years = annual_facts(cf)

    assert years[2025]["facts"]["Revenues"] == 400
    assert years[2024]["facts"]["Revenues"] == 350


def test_a_restatement_wins_over_the_original():
    cf = facts({"Revenues": [
        fact(350, "2024-09-28", "2023-10-01", filed="2024-11-01"),
        fact(355, "2024-09-28", "2023-10-01", filed="2025-11-01"),   # 정정
    ]})

    assert annual_facts(cf)[2024]["facts"]["Revenues"] == 355


def test_a_balance_sheet_value_is_taken_at_the_fiscal_year_end():
    cf = facts({
        "Revenues": [fact(400, "2025-09-27", "2024-09-29")],
        "Assets": [fact(900, "2025-09-27"), fact(800, "2025-06-28")],
    })

    assert annual_facts(cf)[2025]["facts"]["Assets"] == 900


def test_a_cover_page_share_count_dated_after_year_end_is_kept():
    """dei:EntityCommonStockSharesOutstanding은 표지 날짜(결산 후 몇 주)로 온다."""
    cf = facts(
        {"Revenues": [fact(400, "2025-09-27", "2024-09-29")]},
        dei={"EntityCommonStockSharesOutstanding": [
            fact(15, "2025-10-17"),
            fact(99, "2026-04-17", form="10-Q"),
        ]},
    )

    assert annual_facts(cf)[2025]["facts"]["EntityCommonStockSharesOutstanding"] == 15


def test_a_january_year_end_belongs_to_the_year_it_ends_in():
    """월마트는 2025-01-31에 끝나는 해를 FY2025라 부른다. end의 연도와 같다."""
    cf = facts({"Revenues": [fact(680, "2025-01-31", "2024-02-01")]})

    assert set(annual_facts(cf)) == {2025}


def test_no_annual_flow_means_no_years():
    """연간 흐름 항목이 하나도 없으면 회계연도 말을 알 수 없다."""
    cf = facts({"Assets": [fact(900, "2025-09-27")]})

    assert annual_facts(cf) == {}


def test_other_units_are_ignored():
    cf = {"facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [fact(400, "2025-09-27", "2024-09-29")]}},
        "EarningsPerShareBasic": {"units": {"USD/shares": [
            fact(6.1, "2025-09-27", "2024-09-29")]}},
    }}}

    assert "EarningsPerShareBasic" not in annual_facts(cf)[2025]["facts"]


def test_a_stray_annual_footnote_does_not_move_the_fiscal_year_end():
    """실측: PG(6월 결산)의 10-K 주석에 2023-10-01 ~ 2024-09-30 구조조정 비용이 있다.

    기간이 12개월이라 연간으로 보이지만 회계연도가 아니다. 그 해 결산일을
    "늦은 쪽"으로 고르면 2024년의 결산일이 9월 30일이 되어 매출·자산이
    전부 사라진다. 그날짜로 끝나는 연간 항목이 가장 많은 날이 결산일이다.
    """
    cf = facts({
        "Revenues": [fact(84, "2024-06-30", "2023-07-01")],
        "NetIncomeLoss": [fact(14, "2024-06-30", "2023-07-01")],
        "RestructuringAndRelatedCostIncurredCost": [
            fact(1.2, "2024-09-30", "2023-10-01")],
        "Assets": [fact(122, "2024-06-30")],
    })

    year = annual_facts(cf)[2024]

    assert year["period_end"] == "2024-06-30"
    assert year["facts"]["Revenues"] == 84
    assert year["facts"]["Assets"] == 122
