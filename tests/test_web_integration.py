"""실제 DB에 대한 웹 API 검증.

DB가 없거나 스냅샷이 없으면 모듈 전체가 skip된다.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stockscout.store.db import DEFAULT_DB_PATH
from stockscout.web.api import create_app

pytestmark = pytest.mark.skipif(
    not Path(DEFAULT_DB_PATH).exists(),
    reason="stockscout analyze 를 먼저 실행해야 한다",
)


@pytest.fixture
def client():
    return TestClient(create_app(DEFAULT_DB_PATH))


@pytest.fixture
def snapshot(client):
    res = client.get("/api/snapshot")
    if res.status_code == 404:
        pytest.skip("스냅샷이 없다")
    return res.json()


def test_snapshot_matches_cli_counts(snapshot):
    assert snapshot["total"] == 42
    assert snapshot["passed"] == 34
    assert snapshot["excluded"] == 8


def test_scores_are_absent_until_dart_key(snapshot):
    assert snapshot["has_scores"] is False
    assert snapshot["has_valuation"] is False


def test_list_returns_every_security(client, snapshot):
    rows = client.get("/api/securities").json()

    assert len(rows) == snapshot["total"]


def test_list_is_sorted_most_fallen_first(client, snapshot):
    rows = client.get("/api/securities", params={"sort": "drop"}).json()
    drops = [r["drop_vs_avg_5y"] for r in rows if r["drop_vs_avg_5y"] is not None]

    assert drops == sorted(drops)


def test_every_row_can_be_fetched_in_detail(client, snapshot):
    """목록에 있는 종목은 전부 상세를 열 수 있어야 한다."""
    for row in client.get("/api/securities").json():
        res = client.get(f"/api/securities/{row['ticker']}")
        assert res.status_code == 200, row["ticker"]


def test_every_passing_row_has_a_chart(client, snapshot):
    for row in client.get("/api/securities").json():
        if not row["passed_filters"]:
            continue
        body = client.get(f"/api/securities/{row['ticker']}/prices").json()
        assert len(body["series"]) > 1000, row["ticker"]


def test_chart_reference_lines_match_the_list_row(client, snapshot):
    """차트 기준선과 목록 지표가 같은 스냅샷에서 나와야 한다."""
    row = client.get("/api/securities").json()[0]
    chart = client.get(f"/api/securities/{row['ticker']}/prices").json()

    assert chart["avg_price_5y"] == row["avg_price_5y"]
    assert chart["high_5y"] == row["high_5y"]
    assert chart["price_current"] == row["price_current"]


def test_rationale_contains_the_actual_numbers(client, snapshot):
    body = client.get("/api/securities/051900.KS").json()
    joined = " ".join(body["rationale"])

    assert "295,000" in joined
    assert "-41.1%" in joined


def test_excluded_row_names_its_filter(client, snapshot):
    body = client.get("/api/securities/105560.KS").json()

    assert body["passed_filters"] is False
    assert any("FINANCIAL_SECTOR" in line for line in body["rationale"])


def test_financials_are_empty_everywhere(client, snapshot):
    for row in client.get("/api/securities").json()[:5]:
        body = client.get(f"/api/securities/{row['ticker']}").json()
        assert body["financials"] == []


def test_watchlist_round_trips(client, snapshot):
    client.delete("/api/watchlist/005930.KS")
    assert "005930.KS" not in client.get("/api/watchlist").json()

    client.post("/api/watchlist/005930.KS")
    assert "005930.KS" in client.get("/api/watchlist").json()

    rows = {r["ticker"]: r for r in client.get("/api/securities").json()}
    assert rows["005930.KS"]["in_watchlist"] is True

    client.delete("/api/watchlist/005930.KS")
