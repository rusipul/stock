import json
import math

import pytest

from stockscout.models import (
    AnalysisResult,
    FilterOutcome,
    PriceStats,
    Snapshot,
    Valuation,
)
from stockscout.store.db import connect, init_db
from stockscout.store.repository import Repository, json_safe


@pytest.fixture
def repo(tmp_path):
    with connect(tmp_path / "t.db") as conn:
        init_db(conn)
        yield Repository(conn)


def snap(note="테스트"):
    return Snapshot(run_at="2026-09-11T02:00:00+00:00", config_hash="h1",
                    config_json='{"signal": {"min_score": 70}}', note=note)


def result(ticker="005930.KS", failures=None, drop5=-0.42):
    return AnalysisResult(
        ticker=ticker,
        filters=FilterOutcome(failures=failures or []),
        stats=PriceStats(
            price_current=41300.0,
            avg_price={1: 55000.0, 3: 64200.0, 5: 71400.0},
            high_5y=88800.0,
            drop_vs_avg={1: -0.249, 3: -0.357, 5: drop5},
            drop_vs_high=-0.535,
        ),
        valuation=Valuation(market_cap=2.5e14, per=9.8, pbr=1.2),
    )


# --- json_safe --------------------------------------------------------


def test_json_safe_converts_inf_to_none():
    """json.dumps(float('inf'))는 Infinity라는 유효하지 않은 JSON을 뱉는다.

    계획 C의 interest_coverage가 무차입 기업에 inf를 반환하므로,
    그 값이 score_detail에 들어가면 웹 화면의 JSON.parse가 깨진다.
    """
    cleaned = json_safe({"coverage": math.inf, "ratio": 1.5, "none": None})

    assert cleaned == {"coverage": None, "ratio": 1.5, "none": None}
    json.loads(json.dumps(cleaned))   # 예외가 나면 실패


def test_json_safe_handles_nan_and_negative_inf():
    cleaned = json_safe({"a": math.nan, "b": -math.inf})

    assert cleaned == {"a": None, "b": None}


def test_json_safe_recurses_into_nested_structures():
    cleaned = json_safe({"axes": [{"v": math.inf}, {"v": 2.0}]})

    assert cleaned == {"axes": [{"v": None}, {"v": 2.0}]}


def test_json_safe_leaves_ordinary_values_alone():
    payload = {"s": "매출액", "i": 3, "b": True, "l": [1, 2]}

    assert json_safe(payload) == payload


# --- 저장과 조회 ------------------------------------------------------


def test_save_snapshot_returns_id_and_persists_results(repo):
    sid = repo.save_snapshot(snap(), [result(), result("000660.KS")])

    assert isinstance(sid, int)
    rows = repo.get_analysis_results(sid)
    assert {r["ticker"] for r in rows} == {"005930.KS", "000660.KS"}


def test_saved_result_round_trips_every_price_column(repo):
    sid = repo.save_snapshot(snap(), [result()])

    row = repo.get_analysis_results(sid)[0]

    assert row["price_current"] == 41300.0
    assert row["avg_price_1y"] == 55000.0
    assert row["avg_price_3y"] == 64200.0
    assert row["avg_price_5y"] == 71400.0
    assert row["high_5y"] == 88800.0
    assert row["drop_vs_avg_5y"] == pytest.approx(-0.42)
    assert row["drop_vs_high"] == pytest.approx(-0.535)
    assert row["market_cap"] == pytest.approx(2.5e14)
    assert row["per"] == pytest.approx(9.8)
    assert row["pbr"] == pytest.approx(1.2)
    assert row["passed_filters"] == 1


def test_filter_failures_round_trip_as_json(repo):
    sid = repo.save_snapshot(snap(), [result(failures=["FINANCIAL_SECTOR"])])

    row = repo.get_analysis_results(sid)[0]

    assert row["passed_filters"] == 0
    assert json.loads(row["filter_failures"]) == ["FINANCIAL_SECTOR"]


def test_missing_window_is_stored_as_null(repo):
    r = result()
    r.stats.avg_price[3] = None
    r.stats.drop_vs_avg[3] = None

    sid = repo.save_snapshot(snap(), [r])
    row = repo.get_analysis_results(sid)[0]

    assert row["avg_price_3y"] is None
    assert row["drop_vs_avg_3y"] is None


def test_get_latest_snapshot_returns_most_recent(repo):
    repo.save_snapshot(snap("첫번째"), [result()])
    second = repo.save_snapshot(snap("두번째"), [result()])

    latest = repo.get_latest_snapshot()

    assert latest is not None
    assert latest.snapshot_id == second
    assert latest.note == "두번째"


def test_get_latest_snapshot_is_none_when_empty(repo):
    assert repo.get_latest_snapshot() is None


# --- 원자성 (설계서 §13) ----------------------------------------------


def test_failed_snapshot_leaves_no_partial_rows(repo):
    """설계서 §13: 배치 중 예외 → 스냅샷을 커밋하지 않는다."""
    good = result("005930.KS")
    broken = result("000660.KS")
    broken.ticker = None       # NOT NULL 위반을 유발한다

    with pytest.raises(Exception):
        repo.save_snapshot(snap(), [good, broken])

    assert repo.get_latest_snapshot() is None
    assert repo.conn.execute("SELECT COUNT(*) FROM analysis_results").fetchone()[0] == 0


def test_previous_snapshot_survives_a_failed_run(repo):
    """직전 스냅샷이 유지되므로 화면은 계속 동작한다."""
    first = repo.save_snapshot(snap("성공"), [result()])

    broken = result()
    broken.ticker = None
    with pytest.raises(Exception):
        repo.save_snapshot(snap("실패"), [broken])

    latest = repo.get_latest_snapshot()
    assert latest.snapshot_id == first
    assert latest.note == "성공"


def test_empty_results_still_creates_a_snapshot(repo):
    """분석 대상이 0종목이어도 실행 기록은 남는다."""
    sid = repo.save_snapshot(snap(), [])

    assert repo.get_latest_snapshot().snapshot_id == sid
    assert repo.get_analysis_results(sid) == []


# --- 추가 테스트 --------------------------------------------------------
#
# 아래는 계획이 준 13개 테스트 밖의 것들이다: 두 스냅샷의 결과가 섞이지
# 않는지, 창이 아예 빠진 avg_price 딕셔너리를 KeyError 없이 다루는지,
# 그리고 Task 6(파이프라인)이 의존하는 trading_dates·
# latest_price_date_any 두 메서드.


def test_results_do_not_bleed_between_snapshots(repo):
    """get_analysis_results(sid1)는 sid2의 행을 절대 반환하면 안 된다."""
    sid1 = repo.save_snapshot(snap("첫번째"), [result("005930.KS")])
    sid2 = repo.save_snapshot(snap("두번째"), [result("000660.KS")])

    rows1 = repo.get_analysis_results(sid1)
    rows2 = repo.get_analysis_results(sid2)

    assert {r["ticker"] for r in rows1} == {"005930.KS"}
    assert {r["ticker"] for r in rows2} == {"000660.KS"}


def test_save_snapshot_handles_avg_price_missing_a_window_key_entirely(repo):
    """파이프라인은 항상 모든 창을 채우지만, 저장 계층은 그걸 가정하면 안 된다."""
    r = result()
    r.stats.avg_price = {1: 100.0}
    r.stats.drop_vs_avg = {1: -0.1}

    sid = repo.save_snapshot(snap(), [r])
    row = repo.get_analysis_results(sid)[0]

    assert row["avg_price_1y"] == 100.0
    assert row["avg_price_3y"] is None
    assert row["avg_price_5y"] is None
    assert row["drop_vs_avg_3y"] is None
    assert row["drop_vs_avg_5y"] is None


# --- trading_dates ------------------------------------------------------


def test_trading_dates_returns_range_deduped_and_ascending(repo):
    from datetime import date

    from stockscout.models import PriceBar

    repo.save_price_bars(
        [
            PriceBar(ticker="005930.KS", date=date(2026, 1, 5), close=1.0),
            PriceBar(ticker="005930.KS", date=date(2026, 1, 6), close=1.0),
            PriceBar(ticker="000660.KS", date=date(2026, 1, 6), close=1.0),  # 중복 날짜
            PriceBar(ticker="000660.KS", date=date(2026, 1, 7), close=1.0),
            PriceBar(ticker="005930.KS", date=date(2026, 1, 20), close=1.0),  # 범위 밖
        ],
        source="test",
    )

    dates = repo.trading_dates(date(2026, 1, 1), date(2026, 1, 10))

    assert dates == [date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)]


def test_trading_dates_range_boundaries_are_inclusive(repo):
    from datetime import date

    from stockscout.models import PriceBar

    repo.save_price_bars(
        [PriceBar(ticker="005930.KS", date=date(2026, 1, 5), close=1.0)],
        source="test",
    )

    assert repo.trading_dates(date(2026, 1, 5), date(2026, 1, 5)) == [date(2026, 1, 5)]
    assert repo.trading_dates(date(2026, 1, 6), date(2026, 1, 10)) == []


def test_trading_dates_empty_when_no_data(repo):
    from datetime import date

    assert repo.trading_dates(date(2026, 1, 1), date(2026, 1, 31)) == []


# --- latest_price_date_any -----------------------------------------------


def test_latest_price_date_any_is_none_when_empty(repo):
    assert repo.latest_price_date_any() is None


def test_latest_price_date_any_is_the_true_max_across_tickers(repo):
    from datetime import date

    from stockscout.models import PriceBar

    repo.save_price_bars(
        [
            PriceBar(ticker="005930.KS", date=date(2026, 1, 5), close=1.0),
            PriceBar(ticker="000660.KS", date=date(2026, 2, 9), close=1.0),  # 최댓값이지만 첫 종목이 아니다
            PriceBar(ticker="AAPL", date=date(2026, 1, 10), close=1.0),
        ],
        source="test",
    )

    assert repo.latest_price_date_any() == date(2026, 2, 9)
