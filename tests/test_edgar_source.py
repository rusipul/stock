"""SEC EDGAR HTTP. 네트워크 없이 MockTransport로 검증한다."""

import httpx
import pytest

from stockscout.sources.base import FatalSourceError, PermanentSourceError
from stockscout.sources.edgar import EdgarSource, parse_company_tickers

UA = "stockscout test@example.com"

TICKERS = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 1067983, "ticker": "BRK-B", "title": "BERKSHIRE HATHAWAY INC"},
}

FACTS = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
    {"val": 400, "start": "2024-09-29", "end": "2025-09-27", "form": "10-K",
     "filed": "2025-10-31", "fy": 2025},
    {"val": 350, "start": "2023-10-01", "end": "2024-09-28", "form": "10-K",
     "filed": "2025-10-31", "fy": 2025},
]}}}}}


def make_source(handler):
    transport = httpx.MockTransport(handler)
    return EdgarSource(UA, rate_per_sec=1000.0,
                       client=httpx.Client(transport=transport))


def routes(facts_status=200, facts_body=FACTS, seen=None):
    def handler(request):
        if seen is not None:
            seen.append(request)
        if request.url.path.endswith("company_tickers.json"):
            return httpx.Response(200, json=TICKERS)
        if "companyfacts" in request.url.path:
            return httpx.Response(facts_status, json=facts_body)
        return httpx.Response(404)
    return handler


def test_parse_company_tickers_maps_ticker_to_cik():
    assert parse_company_tickers(TICKERS) == {"AAPL": 320193, "BRK-B": 1067983}


def test_fetch_annual_returns_only_the_requested_years():
    src = make_source(routes())

    stmts = src.fetch_annual("AAPL", [2025, 2023])

    assert [s.fiscal_year for s in stmts] == [2025]
    assert stmts[0].source == "edgar"
    assert stmts[0].payload["facts"]["Revenues"] == 400


def test_the_cik_is_zero_padded_in_the_url():
    seen = []
    make_source(routes(seen=seen)).fetch_annual("AAPL", [2025])

    assert any("CIK0000320193.json" in str(r.url) for r in seen)


def test_every_request_carries_the_contact_user_agent():
    """SEC는 연락처 없는 요청을 403으로 막는다 (설계서 §18.2)."""
    seen = []
    make_source(routes(seen=seen)).fetch_annual("AAPL", [2025])

    assert seen and all(r.headers["User-Agent"] == UA for r in seen)


def test_the_ticker_list_is_fetched_once_per_run():
    seen = []
    src = make_source(routes(seen=seen))

    src.fetch_annual("AAPL", [2025])
    src.fetch_annual("BRK-B", [2025])

    assert sum("company_tickers" in str(r.url) for r in seen) == 1


def test_unknown_ticker_is_a_permanent_failure():
    with pytest.raises(PermanentSourceError, match="ZZZZ"):
        make_source(routes()).fetch_annual("ZZZZ", [2025])


def test_missing_companyfacts_is_a_permanent_failure():
    with pytest.raises(PermanentSourceError):
        make_source(routes(facts_status=404)).fetch_annual("AAPL", [2025])


def test_403_stops_the_batch():
    """User-Agent가 막힌 것이다. 남은 종목도 전부 403이다."""
    with pytest.raises(FatalSourceError, match="SEC_USER_AGENT"):
        make_source(routes(facts_status=403)).fetch_annual("AAPL", [2025])


def test_429_is_retried_then_raised():
    calls = []

    def handler(request):
        if request.url.path.endswith("company_tickers.json"):
            return httpx.Response(200, json=TICKERS)
        calls.append(request)
        return httpx.Response(429)

    src = make_source(handler)
    src._retry_delay = 0   # 테스트에서 백오프를 기다리지 않는다

    with pytest.raises(ConnectionError):
        src.fetch_annual("AAPL", [2025])
    assert len(calls) == 3
