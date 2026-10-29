"""최근 공시: 수집·분류·위험 공시 필터·화면. 하락 원인과 회복 계기를 사람이 판단하도록 돕는다."""

from datetime import date

import httpx
import pytest

from stockscout.models import Filing
from stockscout.normalize.filings import (
    CATEGORY_DILUTION,
    CATEGORY_EARNINGS,
    CATEGORY_LEGAL,
    CATEGORY_RISK,
    classify_filing,
)
from stockscout.sources.dart import DartSource
from stockscout.sources.edgar import EdgarSource


def kr(title):
    return classify_filing(Filing(ticker="X.KS", filed=date(2026, 9, 1), title=title,
                                  url="u"))


def us(form, items=""):
    return classify_filing(Filing(ticker="X", filed=date(2026, 9, 1), title=form,
                                  form=form, items=items, url="u"))


# --- 분류 ------------------------------------------------------------------


@pytest.mark.parametrize("title", [
    "관리종목지정(감사의견거절)",
    "상장적격성 실질심사 대상 결정",
    "불성실공시법인지정",
    "회생절차개시신청",
    "횡령ㆍ배임혐의발생",
    "[기재정정]감사의견거절",
])
def test_kr_risk_titles(title):
    assert kr(title) == CATEGORY_RISK


def test_a_trading_halt_for_a_stock_split_is_not_a_risk():
    """액면분할·병합 때도 매매거래가 정지된다. 위험이 아니다."""
    assert kr("주권매매거래정지(주식의 분할)") != CATEGORY_RISK
    assert kr("주권매매거래정지(조회공시 미답변)") == CATEGORY_RISK


@pytest.mark.parametrize("title,category", [
    ("주요사항보고서(유상증자결정)", CATEGORY_DILUTION),
    ("전환사채권발행결정", CATEGORY_DILUTION),
    ("연결재무제표기준영업(잠정)실적(공정공시)", CATEGORY_EARNINGS),
    ("매출액또는손익구조30%(대규모법인은15%)이상변경", CATEGORY_EARNINGS),
    ("소송등의제기ㆍ신청(일정금액 이상의 청구)", CATEGORY_LEGAL),
    ("임원ㆍ주요주주특정증권등소유상황보고서", None),
])
def test_kr_other_categories(title, category):
    assert kr(title) == category


@pytest.mark.parametrize("form,items,category", [
    ("NT 10-K", "", CATEGORY_RISK),          # 공시 지연
    ("8-K", "4.02,9.01", CATEGORY_RISK),     # 과거 재무제표 신뢰 불가(정정)
    ("8-K", "1.03", CATEGORY_RISK),          # 파산
    ("8-K", "2.04", CATEGORY_RISK),          # 채무 가속
    ("8-K", "2.02,9.01", CATEGORY_EARNINGS), # 실적 발표
    ("S-3", "", CATEGORY_DILUTION),
    ("424B5", "", CATEGORY_DILUTION),
    ("10-Q", "", CATEGORY_EARNINGS),
    ("4", "", None),
])
def test_us_categories(form, items, category):
    assert us(form, items) == category


# --- 수집: DART 공시검색 ---------------------------------------------------


def test_dart_fetch_filings_reads_the_disclosure_list():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"status": "000", "list": [
            {"rcept_no": "20260901000123", "rcept_dt": "20260901",
             "report_nm": "주요사항보고서(유상증자결정)"},
        ]})

    src = DartSource("k", rate_per_sec=1000.0,
                     client=httpx.Client(transport=httpx.MockTransport(handler)))
    src.corp_codes = {"005930": "00126380"}

    (f,) = src.fetch_filings("005930.KS", date(2025, 10, 1), date(2026, 9, 30))

    assert f.filed == date(2026, 9, 1)
    assert f.title == "주요사항보고서(유상증자결정)"
    assert f.url.endswith("rcpNo=20260901000123")
    params = seen[0].url.params
    assert params["bgn_de"] == "20251001" and params["end_de"] == "20260930"
    assert "bsns_year" not in params          # 공시검색에는 사업연도가 없다


def test_dart_no_filings_is_an_empty_list():
    def handler(request):
        return httpx.Response(200, json={"status": "013", "message": "no data"})

    src = DartSource("k", rate_per_sec=1000.0,
                     client=httpx.Client(transport=httpx.MockTransport(handler)))
    src.corp_codes = {"005930": "00126380"}

    assert src.fetch_filings("005930.KS", date(2025, 10, 1), date(2026, 9, 30)) == []


# --- 수집: SEC submissions -------------------------------------------------


SUBMISSIONS = {"filings": {"recent": {
    "form": ["8-K", "10-Q", "8-K"],
    "filingDate": ["2026-09-10", "2026-08-01", "2025-01-05"],
    "items": ["2.02,9.01", "", "4.02"],
    "accessionNumber": ["0000320193-26-000101", "0000320193-26-000090",
                        "0000320193-25-000001"],
    "primaryDocument": ["a8k.htm", "q.htm", "old.htm"],
    "primaryDocDescription": ["8-K", "10-Q", "8-K"],
}}}


def test_edgar_fetch_filings_keeps_only_the_requested_period():
    def handler(request):
        if request.url.path.endswith("company_tickers.json"):
            return httpx.Response(200, json={"0": {"cik_str": 320193, "ticker": "AAPL",
                                                   "title": "Apple"}})
        assert "submissions/CIK0000320193.json" in str(request.url)
        return httpx.Response(200, json=SUBMISSIONS)

    src = EdgarSource("ua me@example.com", rate_per_sec=1000.0,
                      client=httpx.Client(transport=httpx.MockTransport(handler)))

    filings = src.fetch_filings("AAPL", date(2025, 10, 1), date(2026, 9, 30))

    assert [(f.form, f.items) for f in filings] == [("8-K", "2.02,9.01"), ("10-Q", "")]
    assert filings[0].url == ("https://www.sec.gov/Archives/edgar/data/320193/"
                              "000032019326000101/a8k.htm")
