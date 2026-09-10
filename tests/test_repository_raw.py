from datetime import date

import pytest

from stockscout.models import PriceBar, RawStatement
from stockscout.store.db import connect, init_db
from stockscout.store.repository import Repository


@pytest.fixture
def repo(tmp_path):
    with connect(tmp_path / "t.db") as conn:
        init_db(conn)
        yield Repository(conn)


def test_save_and_read_raw_statement(repo):
    stmt = RawStatement(
        ticker="005930.KS", fiscal_year=2025, source="dart",
        payload={"list": [{"account_id": "ifrs-full_Revenue", "thstrm_amount": "300"}]},
    )

    repo.save_raw_statements([stmt])
    got = repo.get_raw_statements("005930.KS")

    assert len(got) == 1
    assert got[0].payload["list"][0]["account_id"] == "ifrs-full_Revenue"


def test_existing_statement_years_supports_incremental_collect(repo):
    repo.save_raw_statements([
        RawStatement(ticker="005930.KS", fiscal_year=2024, source="dart", payload={}),
        RawStatement(ticker="005930.KS", fiscal_year=2025, source="dart", payload={}),
    ])

    years = repo.existing_statement_years("005930.KS", source="dart")

    assert years == {2024, 2025}


def test_save_raw_statements_overwrites_same_key(repo):
    repo.save_raw_statements([
        RawStatement(ticker="005930.KS", fiscal_year=2025, source="dart", payload={"v": 1})
    ])
    repo.save_raw_statements([
        RawStatement(ticker="005930.KS", fiscal_year=2025, source="dart", payload={"v": 2})
    ])

    got = repo.get_raw_statements("005930.KS")

    assert len(got) == 1
    assert got[0].payload["v"] == 2


def test_save_and_read_price_bars(repo):
    bars = [
        PriceBar(ticker="005930.KS", date=date(2026, 1, 5), close=71000.0, volume=1000),
        PriceBar(ticker="005930.KS", date=date(2026, 1, 6), close=72000.0, volume=2000),
    ]

    repo.save_price_bars(bars, source="pykrx")
    got = repo.get_price_bars("005930.KS")

    assert [b.close for b in got] == [71000.0, 72000.0]
    assert got[0].date == date(2026, 1, 5)


def test_latest_price_date_is_none_when_empty(repo):
    assert repo.latest_price_date("005930.KS") is None


def test_latest_price_date_returns_max(repo):
    repo.save_price_bars([
        PriceBar(ticker="005930.KS", date=date(2026, 1, 5), close=1.0),
        PriceBar(ticker="005930.KS", date=date(2026, 3, 2), close=2.0),
    ], source="pykrx")

    assert repo.latest_price_date("005930.KS") == date(2026, 3, 2)


def test_get_price_bars_respects_date_range(repo):
    repo.save_price_bars([
        PriceBar(ticker="A", date=date(2026, 1, 1), close=1.0),
        PriceBar(ticker="A", date=date(2026, 6, 1), close=2.0),
    ], source="pykrx")

    got = repo.get_price_bars("A", start=date(2026, 5, 1))

    assert [b.close for b in got] == [2.0]


def test_record_error_and_summarize(repo):
    repo.record_error("005930.KS", "dart", "timeout")
    repo.record_error("000660.KS", "dart", "404")

    errors = repo.recent_errors(limit=10)

    assert len(errors) == 2
    assert {e["ticker"] for e in errors} == {"005930.KS", "000660.KS"}
