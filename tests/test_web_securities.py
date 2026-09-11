import pytest
from fastapi.testclient import TestClient

from stockscout.models import (
    AnalysisResult,
    FilterOutcome,
    PriceStats,
    Security,
    Snapshot,
    Valuation,
)
from stockscout.store.db import connect, init_db
from stockscout.store.repository import Repository
from stockscout.web.api import create_app


def result(ticker, current, avg5, failures=None):
    return AnalysisResult(
        ticker=ticker,
        filters=FilterOutcome(failures=failures or []),
        stats=PriceStats(
            price_current=current,
            avg_price={1: avg5, 3: avg5, 5: avg5},
            high_5y=avg5 * 2,
            drop_vs_avg={1: current / avg5 - 1, 3: current / avg5 - 1,
                         5: current / avg5 - 1},
            drop_vs_high=current / (avg5 * 2) - 1,
        ),
        valuation=Valuation(),
    )


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "t.db"
    with connect(path) as conn:
        init_db(conn)
        repo = Repository(conn)
        for ticker, name, fin in [
            ("005930.KS", "삼성전자", False),
            ("035720.KS", "카카오", False),
            ("105560.KS", "KB금융", True),
        ]:
            repo.upsert_security(Security(
                ticker=ticker, market="KR", name=name, exchange="KOSPI",
                currency="KRW", is_financial=fin))
        repo.save_snapshot(
            Snapshot(run_at="2026-09-11T02:00:00+00:00", as_of="2026-09-10",
                     config_hash="h1", config_json="{}"),
            [
                result("005930.KS", 100.0, 100.0),    # 평균대비 0%
                result("035720.KS", 60.0, 100.0),     # 평균대비 -40%
                result("105560.KS", 80.0, 100.0, failures=["FINANCIAL_SECTOR"]),
            ],
        )
    return path


@pytest.fixture
def client(db):
    return TestClient(create_app(db_path=db))


def test_returns_every_security_in_the_snapshot(client):
    rows = client.get("/api/securities").json()

    assert {r["ticker"] for r in rows} == {"005930.KS", "035720.KS", "105560.KS"}


def test_row_carries_name_and_exchange_from_master(client):
    rows = {r["ticker"]: r for r in client.get("/api/securities").json()}

    assert rows["005930.KS"]["name"] == "삼성전자"
    assert rows["005930.KS"]["exchange"] == "KOSPI"
    assert rows["105560.KS"]["is_financial"] is True


def test_row_carries_every_metric_the_slider_needs(client):
    """프론트가 서버 왕복 없이 재판정하려면 지표가 전부 있어야 한다."""
    row = next(r for r in client.get("/api/securities").json()
               if r["ticker"] == "035720.KS")

    assert row["price_current"] == 60.0
    assert row["avg_price_5y"] == 100.0
    assert row["drop_vs_avg_5y"] == pytest.approx(-0.4)
    assert row["drop_vs_high"] == pytest.approx(-0.7)
    assert row["high_5y"] == 200.0


def test_filter_failures_are_parsed_into_a_list(client):
    row = next(r for r in client.get("/api/securities").json()
               if r["ticker"] == "105560.KS")

    assert row["passed_filters"] is False
    assert row["filter_failures"] == ["FINANCIAL_SECTOR"]


def test_empty_score_columns_are_null_not_zero(client):
    """0점과 '점수 없음'은 다르다. 프론트가 구분해야 한다."""
    row = client.get("/api/securities").json()[0]

    assert row["score_total"] is None
    assert row["per"] is None
    assert row["market_cap"] is None


def test_search_matches_name_and_ticker(client):
    by_name = client.get("/api/securities", params={"q": "카카오"}).json()
    by_ticker = client.get("/api/securities", params={"q": "035720"}).json()

    assert [r["ticker"] for r in by_name] == ["035720.KS"]
    assert [r["ticker"] for r in by_ticker] == ["035720.KS"]


def test_search_is_case_insensitive_for_ascii(client):
    rows = client.get("/api/securities", params={"q": "ks"}).json()

    assert len(rows) == 3


def test_sort_by_drop_puts_most_fallen_first(client):
    rows = client.get("/api/securities", params={"sort": "drop"}).json()

    assert rows[0]["ticker"] == "035720.KS"   # -40%


def test_sort_by_name_is_alphabetical(client):
    rows = client.get("/api/securities", params={"sort": "name"}).json()

    assert [r["name"] for r in rows] == ["KB금융", "삼성전자", "카카오"]


def test_unknown_sort_is_rejected(client):
    response = client.get("/api/securities", params={"sort": "bogus"})

    assert response.status_code == 422


def test_watchlist_membership_is_reported(client, db):
    with connect(db) as conn:
        conn.execute("INSERT INTO watchlist (ticker, added_at) "
                     "VALUES ('005930.KS', '2026-09-11')")
        conn.commit()

    rows = {r["ticker"]: r for r in client.get("/api/securities").json()}

    assert rows["005930.KS"]["in_watchlist"] is True
    assert rows["035720.KS"]["in_watchlist"] is False


def test_404_when_no_snapshot(tmp_path):
    path = tmp_path / "empty.db"
    with connect(path) as conn:
        init_db(conn)

    response = TestClient(create_app(db_path=path)).get("/api/securities")

    assert response.status_code == 404


# --- 추가 테스트 -------------------------------------------------------
#
# 위 12개는 플랜 D의 지정 테스트다. 아래는 "신중히 확인할 것" 항목들을
# 실제로 테스트로 굳힌 것 — 구현이 조금만 틀어져도 여기서 잡혀야 한다.


def test_security_without_master_row_falls_back_to_ticker(db):
    """스냅샷에는 있지만 securities 마스터에는 없는 종목.

    상장폐지된 종목이 대표적이다 — 스냅샷이 존재를 결정하지, 마스터
    테이블이 필터링해서는 안 된다(설계서 §6.1 관련: 목록은 스냅샷 기준).
    """
    with connect(db) as conn:
        repo = Repository(conn)
        repo.save_snapshot(
            Snapshot(run_at="2026-09-12T02:00:00+00:00", as_of="2026-09-11",
                     config_hash="h2", config_json="{}"),
            [result("999999.KS", 50.0, 100.0)],
        )
    rows = {r["ticker"]: r
            for r in TestClient(create_app(db_path=db))
            .get("/api/securities").json()}

    assert "999999.KS" in rows
    row = rows["999999.KS"]
    assert row["name"] == "999999.KS"
    assert row["exchange"] is None
    assert row["is_financial"] is False


def test_row_with_no_drop_average_sorts_last_under_drop_sort(db):
    """avg_price가 없어 drop_vs_avg_5y가 None인 종목(커버리지 미달).

    None을 0.0으로 취급하면 "데이터 없음"이 마치 하락률 0%인 것처럼
    끼어든다. +20%짜리 종목을 함께 넣어 그 차이를 드러낸다 — None을
    0.0으로 흉내 내면 0.0으로 동률(005930.KS)인 종목과 뒤섞여 우연히
    맨 끝에 남을 수도 있지만, 정렬 대상에 진짜 양(+)의 하락률이 있으면
    그 종목이 이 종목보다 뒤로 밀려나는 일은 없어야 한다. 즉 None은
    +20%보다도 항상 더 뒤에 있어야 한다 — (is None, value) 튜플만이
    이를 보장한다.
    """
    with connect(db) as conn:
        repo = Repository(conn)
        repo.save_snapshot(
            Snapshot(run_at="2026-09-12T03:00:00+00:00", as_of="2026-09-11",
                     config_hash="h3", config_json="{}"),
            [
                AnalysisResult(
                    ticker="888888.KS",
                    filters=FilterOutcome(),
                    stats=PriceStats(
                        price_current=50.0,
                        avg_price={},
                        high_5y=100.0,
                        drop_vs_avg={},
                        drop_vs_high=-0.5,
                    ),
                    valuation=Valuation(),
                ),
                result("035720.KS", 60.0, 100.0),    # -40%
                result("005930.KS", 100.0, 100.0),   # 0%
                result("091200.KS", 120.0, 100.0),   # +20%
            ],
        )
    rows = TestClient(create_app(db_path=db)).get(
        "/api/securities", params={"sort": "drop"}).json()

    assert rows[-1]["ticker"] == "888888.KS"
    assert rows[-1]["drop_vs_avg_5y"] is None
    assert {r["ticker"] for r in rows} >= {
        "888888.KS", "035720.KS", "005930.KS", "091200.KS"}


def test_search_matches_partial_korean_name(client):
    """사용자가 실제로 타이핑하는 방식 — 한글 이름을 끝까지 치지 않는다."""
    rows = client.get("/api/securities", params={"q": "카카"}).json()

    assert [r["ticker"] for r in rows] == ["035720.KS"]


def test_search_and_sort_compose(client):
    """q로 좁힌 결과 안에서 sort가 여전히 적용돼야 한다."""
    rows = client.get(
        "/api/securities", params={"q": "KS", "sort": "name"}).json()

    assert [r["name"] for r in rows] == ["KB금융", "삼성전자", "카카오"]


def test_watched_ticker_absent_from_snapshot_does_not_appear_or_crash(db):
    """관심종목에는 있지만 이번 스냅샷에는 없는 종목.

    watchlist_tickers()의 집합을 그대로 순회 기준으로 쓰지 않는 한
    문제될 게 없지만, 구현이 실수로 watchlist를 순회하면 스냅샷에 없는
    행이 나타나거나 마스터 조회가 없어 죽을 수 있다.
    """
    with connect(db) as conn:
        repo = Repository(conn)
        repo.upsert_security(Security(
            ticker="777777.KS", market="KR", name="안보이는종목",
            exchange="KOSPI", currency="KRW"))
        conn.execute(
            "INSERT INTO watchlist (ticker, added_at) VALUES (?, ?)",
            ("777777.KS", "2026-09-11"),
        )
        conn.commit()

    response = TestClient(create_app(db_path=db)).get("/api/securities")

    assert response.status_code == 200
    tickers = {r["ticker"] for r in response.json()}
    assert "777777.KS" not in tickers
