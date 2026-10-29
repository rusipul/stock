"""시장별 스냅샷을 API가 구분하는지. 설계서 §18.5."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from stockscout.models import (
    AnalysisResult,
    FilterOutcome,
    PriceBar,
    PriceStats,
    Security,
    Snapshot,
    Valuation,
)
from stockscout.store.db import connect, init_db
from stockscout.store.repository import Repository
from stockscout.web.api import create_app


def result(ticker, price):
    return AnalysisResult(
        ticker=ticker, filters=FilterOutcome(failures=[]),
        stats=PriceStats(price_current=price,
                         avg_price={1: price, 3: price, 5: price},
                         high_5y=price, drop_vs_avg={1: 0.0, 3: 0.0, 5: 0.0},
                         drop_vs_high=0.0),
        valuation=Valuation(),
    )


@pytest.fixture
def db(tmp_path):
    """한국을 먼저, 미국을 나중에 분석한 DB. 최신 스냅샷은 미국이다."""
    path = tmp_path / "t.db"
    with connect(path) as conn:
        init_db(conn)
        repo = Repository(conn)
        repo.upsert_security(Security(ticker="005930.KS", market="KR",
                                      name="삼성전자", currency="KRW"))
        repo.upsert_security(Security(ticker="AAPL", market="US",
                                      name="Apple", currency="USD"))
        repo.save_price_bars(
            [PriceBar(ticker="AAPL", date=date(2026, 9, 10), close=230.5)],
            source="test")
        repo.save_snapshot(
            Snapshot(run_at="2026-09-11T00:00:00+00:00", as_of="2026-09-10",
                     config_hash="h", config_json="{}", market="KR"),
            [result("005930.KS", 60000.0)])
        repo.save_snapshot(
            Snapshot(run_at="2026-09-12T00:00:00+00:00", as_of="2026-09-10",
                     config_hash="h", config_json="{}", market="US"),
            [result("AAPL", 230.5)])
    return path


@pytest.fixture
def client(db):
    return TestClient(create_app(db_path=db))


def test_snapshot_defaults_to_kr_even_when_us_is_newer(client):
    body = client.get("/api/snapshot").json()

    assert body["market"] == "KR"
    assert body["total"] == 1


def test_snapshot_can_be_asked_for_us(client):
    body = client.get("/api/snapshot", params={"market": "US"}).json()

    assert body["market"] == "US"


def test_securities_list_only_that_market(client):
    kr = client.get("/api/securities").json()
    us = client.get("/api/securities", params={"market": "US"}).json()

    assert [r["ticker"] for r in kr] == ["005930.KS"]
    assert [r["ticker"] for r in us] == ["AAPL"]


def test_detail_finds_a_us_ticker_in_the_us_snapshot(client):
    """상세는 티커로 찾는다. 그 티커의 시장에서 최신 스냅샷을 골라야 한다."""
    body = client.get("/api/securities/AAPL").json()

    assert body["price_current"] == 230.5
    assert body["currency"] == "USD"


def test_detail_still_finds_a_kr_ticker_after_us_was_analyzed(client):
    response = client.get("/api/securities/005930.KS")

    assert response.status_code == 200


def test_prices_find_a_us_ticker(client):
    response = client.get("/api/securities/AAPL/prices")

    assert response.status_code == 200
    assert response.json()["price_current"] == 230.5


def test_missing_market_snapshot_says_which_command_to_run(tmp_path):
    path = tmp_path / "kr-only.db"
    with connect(path) as conn:
        init_db(conn)
        Repository(conn).save_snapshot(
            Snapshot(run_at="x", config_hash="h", config_json="{}"), [])

    response = TestClient(create_app(db_path=path)).get(
        "/api/snapshot", params={"market": "US"})

    assert response.status_code == 404
    assert "analyze --market US" in response.json()["detail"]


def test_unknown_market_is_rejected(client):
    assert client.get("/api/snapshot", params={"market": "JP"}).status_code == 422


def test_us_rationale_shows_cents(client):
    """원화처럼 정수로 찍으면 $249.76 / $268.83이 250 / 269가 되어 계산을 검증할 수 없다."""
    lines = client.get("/api/securities/AAPL").json()["rationale"]

    assert any("230.50 / 230.50" in line for line in lines), lines


def test_kr_rationale_stays_whole_won(client):
    lines = client.get("/api/securities/005930.KS").json()["rationale"]

    assert any("60,000 / 60,000" in line for line in lines), lines


# --- 기간별 통계 (비교 기간 슬라이더) ------------------------------------


def test_rows_carry_stats_for_each_window(client):
    (row,) = client.get("/api/securities").json()

    assert set(row["window_stats"]) == {"1", "3", "5"}
    assert row["window_stats"]["5"]["avg_price"] == 60000.0


def test_detail_carries_window_stats_too(client):
    body = client.get("/api/securities/AAPL").json()

    assert body["window_stats"]["1"]["drop_vs_avg"] == 0.0


def test_rows_saved_before_window_stats_fall_back_to_the_columns(db):
    """옛 스냅샷의 window_stats는 NULL이다. 1·3·5년 컬럼과 5년 고점으로 메운다."""
    import sqlite3

    conn = sqlite3.connect(db)
    conn.execute("UPDATE analysis_results SET window_stats = NULL")
    conn.commit()
    conn.close()

    (row,) = TestClient(create_app(db_path=db)).get("/api/securities").json()

    assert set(row["window_stats"]) == {"1", "3", "5"}
    assert row["window_stats"]["5"]["high"] == 60000.0
    assert row["window_stats"]["1"]["high"] is None
