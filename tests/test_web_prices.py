from datetime import date, timedelta

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


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "t.db"
    with connect(path) as conn:
        init_db(conn)
        repo = Repository(conn)
        repo.upsert_security(Security(
            ticker="005930.KS", market="KR", name="삼성전자",
            exchange="KOSPI", currency="KRW"))
        bars, d = [], date(2021, 9, 10)
        while d <= date(2026, 9, 10):
            bars.append(PriceBar(ticker="005930.KS", date=d, close=100.0))
            d += timedelta(days=7)     # 주 1봉이면 충분하다
        repo.save_price_bars(bars, source="pykrx")
        repo.save_snapshot(
            Snapshot(run_at="2026-09-11T02:00:00+00:00", as_of="2026-09-10",
                     config_hash="h1", config_json="{}"),
            [AnalysisResult(
                ticker="005930.KS", filters=FilterOutcome(failures=[]),
                stats=PriceStats(
                    price_current=100.0,
                    avg_price={1: 110.0, 3: 120.0, 5: 130.0},
                    high_5y=200.0,
                    drop_vs_avg={1: -0.09, 3: -0.17, 5: -0.23},
                    drop_vs_high=-0.5),
                valuation=Valuation())],
        )
    return path


@pytest.fixture
def client(db):
    return TestClient(create_app(db_path=db))


def test_returns_series_ascending_by_date(client):
    body = client.get("/api/securities/005930.KS/prices").json()

    dates = [p["date"] for p in body["series"]]
    assert dates == sorted(dates)
    assert len(dates) > 200


def test_series_points_carry_date_and_close(client):
    point = client.get("/api/securities/005930.KS/prices").json()["series"][0]

    assert point["date"] == "2021-09-10"
    assert point["close"] == 100.0


def test_reference_lines_come_from_the_snapshot(client):
    """차트의 평균선은 프론트가 다시 계산하지 않는다.

    같은 평균을 두 곳에서 계산하면 반올림이나 창 정의가 갈린다.
    analyze가 계산한 값을 그대로 쓴다.
    """
    body = client.get("/api/securities/005930.KS/prices").json()

    assert body["avg_price_1y"] == 110.0
    assert body["avg_price_3y"] == 120.0
    assert body["avg_price_5y"] == 130.0
    assert body["high_5y"] == 200.0
    assert body["price_current"] == 100.0


def test_years_parameter_narrows_the_window(client):
    five = client.get("/api/securities/005930.KS/prices").json()
    one = client.get("/api/securities/005930.KS/prices",
                     params={"years": 1}).json()

    assert len(one["series"]) < len(five["series"])
    assert one["series"][0]["date"] > five["series"][0]["date"]


def test_window_is_anchored_to_snapshot_as_of_not_today(client):
    """기준일은 스냅샷의 as_of다. 오늘로 잡으면 화면과 숫자가 어긋난다."""
    body = client.get("/api/securities/005930.KS/prices").json()

    assert body["as_of"] == "2026-09-10"
    assert body["series"][-1]["date"] <= "2026-09-10"


def test_unsupported_years_is_rejected(client):
    response = client.get("/api/securities/005930.KS/prices",
                          params={"years": 2})

    assert response.status_code == 422


def test_ticker_in_master_but_absent_from_snapshot_is_404(client, db):
    """원래 이름은 test_ticker_without_prices_returns_empty_series였다.

    이름과 달리 빈 시리즈를 반환하지 않고 404를 낸다 — 그리고 이 픽스처는
    '봉이 없음' 분기가 아니라 그보다 먼저 걸리는 '스냅샷에 없음' 분기를
    탄다(EMPTY.KS를 securities에는 넣지만 스냅샷에는 넣지 않으므로).
    진짜 '봉 없음' 분기는 아래
    test_ticker_with_no_bars_in_snapshot_returns_404가 검증한다.
    """
    with connect(db) as conn:
        Repository(conn).upsert_security(Security(
            ticker="EMPTY.KS", market="KR", name="빈종목", currency="KRW"))

    response = client.get("/api/securities/EMPTY.KS/prices")

    assert response.status_code == 404


def test_ticker_with_no_bars_in_snapshot_returns_404(client, db):
    """스냅샷에는 있지만 raw_prices에는 한 건도 없는 종목은 404다.

    이것이 "봉 없음" 분기(진짜 no-bars branch)를 실제로 타는 경우다.
    """
    with connect(db) as conn:
        repo = Repository(conn)
        repo.upsert_security(Security(
            ticker="NOBAR.KS", market="KR", name="봉없음", currency="KRW"))
        repo.save_snapshot(
            Snapshot(run_at="2026-09-11T02:00:00+00:00", as_of="2026-09-10",
                     config_hash="h2", config_json="{}"),
            [
                AnalysisResult(
                    ticker="005930.KS", filters=FilterOutcome(failures=[]),
                    stats=PriceStats(
                        price_current=100.0,
                        avg_price={1: 110.0, 3: 120.0, 5: 130.0},
                        high_5y=200.0,
                        drop_vs_avg={1: -0.09, 3: -0.17, 5: -0.23},
                        drop_vs_high=-0.5),
                    valuation=Valuation()),
                AnalysisResult(
                    ticker="NOBAR.KS", filters=FilterOutcome(failures=[]),
                    stats=PriceStats(
                        price_current=50.0,
                        avg_price={1: 55.0, 3: 60.0, 5: 65.0},
                        high_5y=70.0,
                        drop_vs_avg={1: -0.09, 3: -0.17, 5: -0.23},
                        drop_vs_high=-0.29),
                    valuation=Valuation()),
            ],
        )

    response = client.get("/api/securities/NOBAR.KS/prices")

    assert response.status_code == 404
    assert "NOBAR.KS" in response.json()["detail"]


def test_unknown_ticker_is_404(client):
    assert client.get("/api/securities/NOPE.KS/prices").status_code == 404


def test_years_three_is_between_one_and_five(client):
    five = client.get("/api/securities/005930.KS/prices",
                      params={"years": 5}).json()
    three = client.get("/api/securities/005930.KS/prices",
                       params={"years": 3}).json()
    one = client.get("/api/securities/005930.KS/prices",
                     params={"years": 1}).json()

    assert len(one["series"]) < len(three["series"]) < len(five["series"])


def test_null_as_of_returns_full_series_without_crashing(client, db):
    """as_of 컬럼이 NULL인(예: 이전 버전) 스냅샷도 200과 전체 시리즈를 준다."""
    with connect(db) as conn:
        repo = Repository(conn)
        repo.save_snapshot(
            Snapshot(run_at="2026-09-11T03:00:00+00:00", as_of=None,
                     config_hash="h3", config_json="{}"),
            [AnalysisResult(
                ticker="005930.KS", filters=FilterOutcome(failures=[]),
                stats=PriceStats(
                    price_current=100.0,
                    avg_price={1: 110.0, 3: 120.0, 5: 130.0},
                    high_5y=200.0,
                    drop_vs_avg={1: -0.09, 3: -0.17, 5: -0.23},
                    drop_vs_high=-0.5),
                valuation=Valuation())],
        )

    response = client.get("/api/securities/005930.KS/prices")
    body = response.json()

    assert response.status_code == 200
    assert body["as_of"] is None
    assert len(body["series"]) > 200


def test_series_never_extends_past_as_of(client, db):
    """as_of 이후에 저장된 봉이 있어도 시리즈에는 나오면 안 된다.

    스냅샷의 숫자가 설명하지 못하는 점을 차트에 보여주면 안 되기 때문이다.
    """
    with connect(db) as conn:
        repo = Repository(conn)
        repo.save_price_bars(
            [PriceBar(ticker="005930.KS", date=date(2026, 9, 11), close=999.0)],
            source="pykrx",
        )

    body = client.get("/api/securities/005930.KS/prices").json()

    dates = [p["date"] for p in body["series"]]
    assert "2026-09-11" not in dates
    assert all(d <= "2026-09-10" for d in dates)
