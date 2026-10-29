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



def test_rows_carry_the_drawdown_and_benchmark(tmp_path):
    from stockscout.models import DrawdownStat

    path = tmp_path / "dd.db"
    stats = PriceStats(
        price_current=60.0, avg_price={5: 100.0}, high_5y=100.0,
        drop_vs_avg={5: -0.4}, drop_vs_high=-0.4,
        drawdown={3: DrawdownStat(high=100.0, drop_from_high=-0.4, low=58.0,
                                  days_since_low=12, above_ma=True,
                                  stabilized=True, market_drop=-0.1)})
    with connect(path) as conn:
        init_db(conn)
        repo = Repository(conn)
        repo.upsert_security(Security(ticker="005930.KS", market="KR",
                                      name="삼성전자", currency="KRW"))
        repo.save_snapshot(
            Snapshot(run_at="x", config_hash="h", config_json="{}"),
            [AnalysisResult(ticker="005930.KS", filters=FilterOutcome(failures=[]),
                            stats=stats, valuation=Valuation(), benchmark="^KS11")])

    (row,) = TestClient(create_app(db_path=path)).get("/api/securities").json()

    assert row["benchmark"] == "^KS11"
    assert row["drawdown"]["3"]["drop_from_high"] == -0.4
    assert row["drawdown"]["3"]["stabilized"] is True
    assert row["drawdown"]["3"]["market_drop"] == -0.1


def test_snapshot_says_which_period_the_server_graded(client):
    """화면의 하락 기간 슬라이더 기본값이 서버 판정과 같아야 한다."""
    body = client.get("/api/snapshot").json()

    assert body["drawdown_months"] == 3
    assert body["drawdown_threshold"] == -0.25
    assert body["drawdown_choices"] == [1, 3, 6, 12]



def test_rows_carry_axis_points_and_weak_axes(client):
    (row,) = client.get("/api/securities").json()

    assert "score_stability" in row
    assert row["weak_axes"] == []


def test_detail_lists_recent_filings_with_their_category(db):
    from stockscout.models import Filing

    with connect(db) as conn:
        Repository(conn).save_filings([
            Filing(ticker="AAPL", filed=date(2026, 9, 1), title="8-K", form="8-K",
                   items="2.02", url="https://sec/1"),
            Filing(ticker="AAPL", filed=date(2026, 8, 1), title="NT 10-K", form="NT 10-K",
                   url="https://sec/2"),
        ])

    body = TestClient(create_app(db_path=db)).get("/api/securities/AAPL").json()

    assert [(f["filed"], f["category"]) for f in body["filings"]] == [
        ("2026-09-01", "EARNINGS"), ("2026-08-01", "RISK")]


def test_detail_carries_the_interim_results(db):
    import sqlite3

    conn = sqlite3.connect(db)
    conn.execute("UPDATE analysis_results SET interim = ? WHERE ticker = 'AAPL'", (
        '{"period_end": "2026-06-27", "months": 9, "revenue": 330.0, "revenue_prior": 300.0,'
        ' "operating_income": 50.0, "operating_income_prior": 110.0}',))
    conn.commit()
    conn.close()

    body = TestClient(create_app(db_path=db)).get("/api/securities/AAPL").json()

    assert body["interim"]["months"] == 9
    assert body["interim"]["deteriorated"] is True
    assert body["interim"]["revenue_growth"] == pytest.approx(0.1)



def test_detail_hides_noise_filings(db):
    from stockscout.models import Filing

    with connect(db) as conn:
        Repository(conn).save_filings([
            Filing(ticker="005930.KS", filed=date(2026, 9, 2),
                   title="임원ㆍ주요주주특정증권등소유상황보고서", url="https://dart/n"),
            Filing(ticker="005930.KS", filed=date(2026, 9, 1),
                   title="단일판매ㆍ공급계약체결", url="https://dart/c"),
        ])

    body = TestClient(create_app(db_path=db)).get("/api/securities/005930.KS").json()

    assert [f["category"] for f in body["filings"]] == ["CATALYST"]



def test_rows_carry_breakouts_and_the_snapshot_its_settings(tmp_path):
    from stockscout.models import BreakoutStat

    path = tmp_path / "b.db"
    stats = PriceStats(price_current=130.0, avg_price={5: 100.0}, high_5y=130.0,
                       drop_vs_avg={5: 0.3}, drop_vs_high=0.0)
    with connect(path) as conn:
        init_db(conn)
        repo = Repository(conn)
        repo.upsert_security(Security(ticker="005930.KS", market="KR",
                                      name="삼성전자", currency="KRW"))
        repo.save_snapshot(
            Snapshot(run_at="x", config_hash="h", config_json="{}"),
            [AnalysisResult(
                ticker="005930.KS", filters=FilterOutcome(failures=[]), stats=stats,
                valuation=Valuation(),
                breakout={252: BreakoutStat(high=130.0, distance_to_high=0.0,
                                            breakout_days_ago=0, breakout_level=100.0,
                                            breakout_volume_ratio=3.0, holding=True)},
                breakout_signal="BREAKOUT")])

    client = TestClient(create_app(db_path=path))
    (row,) = client.get("/api/securities").json()
    snap = client.get("/api/snapshot").json()

    assert row["breakout"]["252"]["breakout_volume_ratio"] == 3.0
    assert row["breakout_signal"] == "BREAKOUT"
    assert snap["breakout"]["lookback"] == 252
    assert snap["breakout"]["volume_multiple"] == 2.0
    assert "RISK_DISCLOSURE" in snap["breakout"]["blocking_failures"]


def test_prices_carry_volume_for_the_volume_chart(client):
    body = client.get("/api/securities/AAPL/prices").json()

    assert "volume" in body["series"][0]



def test_prices_carry_open_high_low(db):
    with connect(db) as conn:
        Repository(conn).save_price_bars([PriceBar(
            ticker="AAPL", date=date(2026, 9, 10), open=229.0, high=233.0, low=228.0,
            close=230.5, volume=5)], source="test")

    point = TestClient(create_app(db_path=db)).get(
        "/api/securities/AAPL/prices").json()["series"][-1]

    assert (point["open"], point["high"], point["low"]) == (229.0, 233.0, 228.0)
