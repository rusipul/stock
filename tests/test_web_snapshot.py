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


def stats(current=41300.0, avg5=71400.0, high=88800.0):
    return PriceStats(
        price_current=current,
        avg_price={1: 55000.0, 3: 64200.0, 5: avg5},
        high_5y=high,
        drop_vs_avg={1: -0.249, 3: -0.357, 5: current / avg5 - 1},
        drop_vs_high=current / high - 1,
    )


def result(ticker="005930.KS", failures=None, **kw):
    return AnalysisResult(
        ticker=ticker,
        filters=FilterOutcome(failures=failures or []),
        stats=stats(**kw),
        valuation=Valuation(),
    )


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "t.db"
    with connect(path) as conn:
        init_db(conn)
        repo = Repository(conn)
        repo.upsert_security(Security(
            ticker="005930.KS", market="KR", name="삼성전자",
            exchange="KOSPI", currency="KRW"))
        repo.upsert_security(Security(
            ticker="105560.KS", market="KR", name="KB금융",
            exchange="KOSPI", currency="KRW", is_financial=True))
        repo.save_snapshot(
            Snapshot(run_at="2026-09-11T02:00:00+00:00", as_of="2026-09-10",
                     config_hash="h1", config_json='{"signal": {"min_score": 70}}',
                     note="테스트"),
            [result(), result("105560.KS", failures=["FINANCIAL_SECTOR"])],
        )
    return path


@pytest.fixture
def client(db):
    return TestClient(create_app(db_path=db))


def test_snapshot_returns_metadata(client):
    body = client.get("/api/snapshot").json()

    assert body["snapshot_id"] == 1
    assert body["as_of"] == "2026-09-10"
    assert body["note"] == "테스트"


def test_snapshot_reports_counts(client):
    body = client.get("/api/snapshot").json()

    assert body["total"] == 2
    assert body["passed"] == 1
    assert body["excluded"] == 1


def test_snapshot_reports_whether_scores_exist(client):
    """점수가 없으면 프론트가 등급 UI를 비활성화해야 한다."""
    body = client.get("/api/snapshot").json()

    assert body["has_scores"] is False
    assert body["has_valuation"] is False


def test_snapshot_404_when_no_snapshot(tmp_path):
    path = tmp_path / "empty.db"
    with connect(path) as conn:
        init_db(conn)
    client = TestClient(create_app(db_path=path))

    response = client.get("/api/snapshot")

    assert response.status_code == 404
    assert "분석" in response.json()["detail"]


def test_index_is_served(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_multiple_sequential_requests_succeed(client):
    """연결을 요청마다 새로 여는 패턴이 새지도, 충돌하지도 않는지 확인한다."""
    for _ in range(5):
        response = client.get("/api/snapshot")
        assert response.status_code == 200
        assert response.json()["total"] == 2


def test_has_scores_true_when_scores_exist(db):
    with connect(db) as conn:
        repo = Repository(conn)
        repo.save_snapshot(
            Snapshot(run_at="2026-09-11T03:00:00+00:00", as_of="2026-09-11",
                     config_hash="h2", config_json="{}", note="점수 있음"),
            [
                AnalysisResult(
                    ticker="005930.KS",
                    filters=FilterOutcome(),
                    stats=stats(),
                    valuation=Valuation(),
                    score_total=82.5,
                ),
            ],
        )
    client = TestClient(create_app(db_path=db))

    body = client.get("/api/snapshot").json()

    assert body["has_scores"] is True


def test_404_detail_names_the_command_to_run(tmp_path):
    path = tmp_path / "empty2.db"
    with connect(path) as conn:
        init_db(conn)
    client = TestClient(create_app(db_path=path))

    response = client.get("/api/snapshot")

    assert "analyze" in response.json()["detail"]
