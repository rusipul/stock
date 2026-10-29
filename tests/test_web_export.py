"""정적 사이트 내보내기. Vercel 같은 정적 호스팅에 올릴 파일을 만든다."""

import json
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
from stockscout.web.export import export_site


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
    path = tmp_path / "t.db"
    with connect(path) as conn:
        init_db(conn)
        repo = Repository(conn)
        repo.upsert_security(Security(ticker="005930.KS", market="KR",
                                      name="삼성전자", currency="KRW"))
        repo.upsert_security(Security(ticker="BRK-B", market="US",
                                      name="Berkshire", currency="USD"))
        repo.save_price_bars(
            [PriceBar(ticker="005930.KS", date=date(2026, 9, 10), close=60000.0),
             PriceBar(ticker="BRK-B", date=date(2026, 9, 10), close=480.5)],
            source="test")
        repo.add_to_watchlist("005930.KS")
        for market, ticker, price in (("KR", "005930.KS", 60000.0),
                                      ("US", "BRK-B", 480.5)):
            repo.save_snapshot(
                Snapshot(run_at="2026-09-11T00:00:00+00:00", as_of="2026-09-10",
                         config_hash="h", config_json="{}", market=market),
                [result(ticker, price)])
    return path


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_export_writes_the_same_json_the_api_serves(db, tmp_path):
    """서버 화면과 공개 화면이 같은 숫자여야 한다. API를 그대로 호출해 담는다."""
    out = tmp_path / "site"
    export_site(db, out)
    api = TestClient(create_app(db_path=db))

    assert load(out / "data/KR/snapshot.json") == api.get(
        "/api/snapshot", params={"market": "KR"}).json()
    assert load(out / "data/US/securities.json") == api.get(
        "/api/securities", params={"market": "US"}).json()
    assert load(out / "data/securities/BRK-B.json") == api.get(
        "/api/securities/BRK-B").json()
    assert load(out / "data/securities/005930.KS.prices.json") == api.get(
        "/api/securities/005930.KS/prices").json()


def test_the_watchlist_is_not_published(db, tmp_path):
    """관심종목은 내 PC의 개인 기록이다. 공개 사이트에는 싣지 않는다."""
    out = tmp_path / "site"
    export_site(db, out)

    rows = load(out / "data/KR/securities.json")
    assert all(r["in_watchlist"] is False for r in rows)
    assert load(out / "data/securities/005930.KS.json")["in_watchlist"] is False


def test_the_page_and_assets_are_copied_in_static_mode(db, tmp_path):
    out = tmp_path / "site"
    export_site(db, out)

    html = (out / "index.html").read_text(encoding="utf-8")
    assert "window.STOCKSCOUT_STATIC = true" in html
    assert html.index("STOCKSCOUT_STATIC") < html.index("/static/app.js")
    assert (out / "static/app.js").exists()
    assert (out / "static/style.css").exists()


def test_a_market_without_a_snapshot_is_skipped(tmp_path):
    path = tmp_path / "kr-only.db"
    with connect(path) as conn:
        init_db(conn)
        Repository(conn).save_snapshot(
            Snapshot(run_at="x", config_hash="h", config_json="{}"), [])
    out = tmp_path / "site"

    summary = export_site(path, out)

    assert summary.markets == ["KR"]
    assert not (out / "data/US").exists()


def test_stale_files_from_a_previous_export_are_removed(db, tmp_path):
    """유니버스에서 뺀 종목의 옛 파일이 남으면 공개 사이트에 계속 보인다."""
    out = tmp_path / "site"
    stale = out / "data/securities/GONE.json"
    stale.parent.mkdir(parents=True)
    stale.write_text("{}", encoding="utf-8")

    export_site(db, out)

    assert not stale.exists()


def test_files_outside_data_are_left_alone(db, tmp_path):
    """out 폴더를 통째로 지우지 않는다. 사용자가 둔 파일이 있을 수 있다."""
    out = tmp_path / "site"
    out.mkdir()
    keep = out / "README.txt"
    keep.write_text("keep", encoding="utf-8")

    export_site(db, out)

    assert keep.read_text(encoding="utf-8") == "keep"


def test_summary_counts_securities(db, tmp_path):
    summary = export_site(db, tmp_path / "site")

    assert summary.securities == 2


def test_price_files_are_compacted():
    """소수 넷째 자리까지, 정수는 정수로. 화면은 센트까지만 보인다."""
    from stockscout.web.export import _compact

    assert _compact({"series": [{"close": 148.8123779296875, "open": 70700.0,
                                 "volume": 100, "date": "2026-01-01"}]}) ==         {"series": [{"close": 148.8124, "open": 70700, "volume": 100, "date": "2026-01-01"}]}

