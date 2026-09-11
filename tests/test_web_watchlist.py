from datetime import datetime

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
        for ticker, name in [("005930.KS", "삼성전자"), ("035720.KS", "카카오")]:
            repo.upsert_security(Security(
                ticker=ticker, market="KR", name=name, currency="KRW"))
    return path


@pytest.fixture
def client(db):
    return TestClient(create_app(db_path=db))


def test_watchlist_starts_empty(client):
    assert client.get("/api/watchlist").json() == []


def test_add_then_list(client):
    client.post("/api/watchlist/005930.KS")

    assert client.get("/api/watchlist").json() == ["005930.KS"]


def test_add_is_idempotent(client):
    client.post("/api/watchlist/005930.KS")
    client.post("/api/watchlist/005930.KS")

    assert client.get("/api/watchlist").json() == ["005930.KS"]


def test_delete_removes(client):
    client.post("/api/watchlist/005930.KS")
    client.delete("/api/watchlist/005930.KS")

    assert client.get("/api/watchlist").json() == []


def test_delete_missing_is_not_an_error(client):
    """이미 지워진 것을 또 지우는 것은 실패가 아니다."""
    response = client.delete("/api/watchlist/005930.KS")

    assert response.status_code == 204


def test_add_unknown_ticker_is_404(client):
    response = client.post("/api/watchlist/NOPE.KS")

    assert response.status_code == 404
    assert "NOPE.KS" in response.json()["detail"]


def test_list_is_sorted(client):
    client.post("/api/watchlist/035720.KS")
    client.post("/api/watchlist/005930.KS")

    assert client.get("/api/watchlist").json() == ["005930.KS", "035720.KS"]


# --- 추가 테스트 -------------------------------------------------------
#
# 위 7개는 플랜 D의 지정 테스트다. 아래는 "신중히 확인할 것" 항목들을
# 실제로 테스트로 굳힌 것 — 구현이 조금만 틀어져도 여기서 잡혀야 한다.


def test_add_response_has_no_body(client):
    """204는 몸통이 없어야 한다. FastAPI가 status_code=204와 별개로

    Response(status_code=204)를 명시적으로 반환하지 않으면 response_model
    직렬화가 끼어들어 본문이 생길 수 있다 — HTTP 위반이고 일부 클라이언트는
    거부한다.
    """
    response = client.post("/api/watchlist/005930.KS")

    assert response.status_code == 204
    assert response.content == b""


def test_delete_response_has_no_body(client):
    client.post("/api/watchlist/005930.KS")

    response = client.delete("/api/watchlist/005930.KS")

    assert response.status_code == 204
    assert response.content == b""


def test_add_writes_a_real_timestamp(client, db):
    """add_to_watchlist가 added_at을 실제로 채우는지 컬럼을 직접 읽어 확인한다."""
    client.post("/api/watchlist/005930.KS")

    with connect(db) as conn:
        row = conn.execute(
            "SELECT added_at FROM watchlist WHERE ticker = ?",
            ("005930.KS",),
        ).fetchone()

    assert row is not None
    assert row["added_at"]
    datetime.fromisoformat(row["added_at"])  # 파싱 가능한 ISO 문자열이어야 한다


def test_repeated_add_preserves_original_added_at(client, db):
    """ON CONFLICT DO NOTHING이라 두 번째 add가 added_at을 덮어쓰지 않아야 한다.

    "언제부터 지켜봤는가"가 의미를 가지려면 최초 등록 시각이 유지돼야
    한다. 타이밍(같은 초 안의 두 호출)에 기대지 않도록, 먼저 오래된
    시각으로 직접 행을 넣어두고 API로 add를 한 번 더 호출해 값이 그대로
    인지 본다.
    """
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO watchlist (ticker, added_at) VALUES (?, ?)",
            ("005930.KS", "2020-01-01T00:00:00+00:00"),
        )
        conn.commit()

    response = client.post("/api/watchlist/005930.KS")

    assert response.status_code == 204
    with connect(db) as conn:
        row = conn.execute(
            "SELECT added_at FROM watchlist WHERE ticker = ?",
            ("005930.KS",),
        ).fetchone()
    assert row["added_at"] == "2020-01-01T00:00:00+00:00"


def test_add_via_api_is_reflected_in_securities_list(db):
    """이 API로 추가한 것이 /api/securities의 in_watchlist에도 보여야 한다.

    두 기능이 서로 다른 답을 하면 둘 다 쓸모가 없다 — 이것이 실제로
    중요한 통합이다.
    """
    with connect(db) as conn:
        repo = Repository(conn)
        repo.save_snapshot(
            Snapshot(run_at="2026-09-11T02:00:00+00:00", as_of="2026-09-10",
                     config_hash="h1", config_json="{}"),
            [
                result("005930.KS", 100.0, 100.0),
                result("035720.KS", 60.0, 100.0),
            ],
        )
    client = TestClient(create_app(db_path=db))

    client.post("/api/watchlist/005930.KS")

    rows = {r["ticker"]: r for r in client.get("/api/securities").json()}
    assert rows["005930.KS"]["in_watchlist"] is True
    assert rows["035720.KS"]["in_watchlist"] is False


def test_can_watch_ticker_absent_from_latest_snapshot(db):
    """securities 마스터에는 있지만 최신 스냅샷에는 없는 종목도 watch할 수 있다.

    get_security는 securities를 보지 스냅샷을 보지 않는다 — 유니버스에서
    빠진 종목도 계속 지켜보고 싶을 수 있다. 동시에 /api/securities는
    스냅샷 기준이므로 이 종목은 거기 나타나지 않아야 한다.
    """
    with connect(db) as conn:
        repo = Repository(conn)
        repo.save_snapshot(
            Snapshot(run_at="2026-09-12T02:00:00+00:00", as_of="2026-09-11",
                     config_hash="h4", config_json="{}"),
            [result("035720.KS", 60.0, 100.0)],   # 005930.KS는 스냅샷에 없음
        )
    client = TestClient(create_app(db_path=db))

    response = client.post("/api/watchlist/005930.KS")

    assert response.status_code == 204
    assert client.get("/api/watchlist").json() == ["005930.KS"]

    tickers_in_list = {r["ticker"] for r in client.get("/api/securities").json()}
    assert "005930.KS" not in tickers_in_list
