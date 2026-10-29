"""최근 실적 변화: 가장 최근 분기·반기 누적을 작년 같은 기간과 비교한다.

5년 평균 점수가 최근의 급격한 악화를 가릴 수 있다. 등급 조건에는 넣지
않고 경고로 보인다 - 일시적 충격인지 사업 훼손인지는 숫자만으로 가를 수 없다.
"""

from datetime import date

import pytest

from stockscout.models import InterimResults, RawStatement
from stockscout.normalize.kr_mapping import normalize_dart_interim
from stockscout.normalize.us_mapping import normalize_edgar_interim
from stockscout.sources.edgar import interim_facts


def dart_item(sj, account_id, name, add, prior_add):
    return {"sj_div": sj, "account_id": account_id, "account_nm": name,
            "thstrm_amount": "1", "thstrm_add_amount": add,
            "frmtrm_q_amount": "1", "frmtrm_add_amount": prior_add}


def test_kr_half_year_cumulative_is_compared_with_last_year():
    """실측 모양: 삼성전자 2026 반기보고서(11012)."""
    stmt = RawStatement(ticker="005930.KS", fiscal_year=2026, source="dart_interim", payload={
        "reprt_code": "11012", "fs_div": "CFS", "list": [
            dart_item("IS", "ifrs-full_Revenue", "매출액", "305372914000000", "153706820000000"),
            dart_item("IS", "dart_OperatingIncomeLoss", "영업이익", "146725209000000", "11361329000000"),
        ]})

    r = normalize_dart_interim(stmt)

    assert r.months == 6
    assert r.period_end == date(2026, 6, 30)
    assert r.revenue_growth == pytest.approx(305372914 / 153706820 - 1)
    assert r.operating_income_growth > 10
    assert r.deteriorated is False


def test_kr_third_quarter_report_is_nine_months():
    stmt = RawStatement(ticker="X.KS", fiscal_year=2026, source="dart_interim", payload={
        "reprt_code": "11014", "list": [
            dart_item("IS", "ifrs-full_Revenue", "매출액", "900", "1000"),
            dart_item("IS", "dart_OperatingIncomeLoss", "영업이익", "50", "100"),
        ]})

    r = normalize_dart_interim(stmt)

    assert (r.months, r.period_end) == (9, date(2026, 9, 30))
    assert r.operating_income_growth == pytest.approx(-0.5)
    assert r.deteriorated is True               # -30% 이하


def test_turning_to_a_loss_is_deterioration():
    r = InterimResults(period_end=date(2026, 6, 30), months=6, revenue=100.0,
                       revenue_prior=100.0, operating_income=-5.0,
                       operating_income_prior=10.0)

    assert r.deteriorated is True
    assert r.operating_income_growth is None    # 부호가 바뀌면 성장률이 무의미하다


def test_operating_margin_now_and_a_year_ago():
    r = InterimResults(period_end=date(2026, 6, 30), months=6, revenue=200.0,
                       revenue_prior=100.0, operating_income=20.0,
                       operating_income_prior=20.0)

    assert (r.operating_margin, r.operating_margin_prior) == (0.1, 0.2)


def test_missing_operating_income_is_no_result():
    stmt = RawStatement(ticker="X.KS", fiscal_year=2026, source="dart_interim", payload={
        "reprt_code": "11012", "list": [
            dart_item("IS", "ifrs-full_Revenue", "매출액", "900", "1000")]})

    assert normalize_dart_interim(stmt) is None


# --- 미국: companyfacts의 10-Q 누적 ------------------------------------------


def entry(val, start, end, form="10-Q", filed="2026-08-01"):
    return {"val": val, "start": start, "end": end, "form": form, "filed": filed}


def facts(**tags):
    return {"facts": {"us-gaap": {t: {"units": {"USD": e}} for t, e in tags.items()}}}


def test_us_year_to_date_is_compared_with_the_same_period_last_year():
    cf = facts(
        Revenues=[
            entry(300, "2026-01-01", "2026-06-30"),             # 6개월 누적
            entry(150, "2026-04-01", "2026-06-30"),             # 2분기만
            entry(250, "2025-01-01", "2025-06-30"),             # 전년 동기 누적
            entry(1000, "2025-01-01", "2025-12-31", form="10-K"),
        ],
        OperatingIncomeLoss=[
            entry(30, "2026-01-01", "2026-06-30"),
            entry(50, "2025-01-01", "2025-06-30"),
        ],
    )

    p = interim_facts(cf)

    assert p["period_end"] == "2026-06-30" and p["months"] == 6
    assert p["current"] == {"Revenues": 300, "OperatingIncomeLoss": 30}
    assert p["prior"] == {"Revenues": 250, "OperatingIncomeLoss": 50}


def test_us_interim_older_than_the_latest_annual_is_ignored():
    """10-K가 더 최근이면 중간보고서는 이미 연간에 들어갔다."""
    cf = facts(Revenues=[
        entry(300, "2025-01-01", "2025-06-30"),
        entry(1000, "2025-01-01", "2025-12-31", form="10-K"),
    ])

    assert interim_facts(cf) is None


def test_us_interim_normalizes_like_kr():
    stmt = RawStatement(ticker="AAPL", fiscal_year=2026, source="edgar_interim", payload={
        "period_end": "2026-06-27", "months": 9,
        "current": {"RevenueFromContractWithCustomerExcludingAssessedTax": 330.0,
                    "OperatingIncomeLoss": 100.0},
        "prior": {"Revenues": 300.0, "OperatingIncomeLoss": 110.0}})

    r = normalize_edgar_interim(stmt)

    assert r.period_end == date(2026, 6, 27) and r.months == 9
    assert r.revenue_growth == pytest.approx(0.1)
    assert r.operating_income_growth == pytest.approx(100 / 110 - 1)
