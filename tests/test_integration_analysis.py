"""실제 DB에 대한 검증. `stockscout analyze` 실행 후에 의미가 있다.

DB가 없거나 스냅샷이 없으면 모듈 전체가 skip되므로, 분석 전에는
`pytest tests/`가 그냥 통과한다.
"""

import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from stockscout.analysis.valuation import window_start
from stockscout.store.db import DEFAULT_DB_PATH, connect
from stockscout.store.repository import Repository

pytestmark = pytest.mark.skipif(
    not Path(DEFAULT_DB_PATH).exists(),
    reason="stockscout analyze 를 먼저 실행해야 한다",
)

# 교차검증에 쓸 대형주 두 종목. 이 유니버스에 항상 있고(KOSPI 대형주),
# 비금융이라 필터를 통과하며, 2021-09-10부터 5년 이력이 꽉 차 있다.
CROSS_CHECK_TICKERS = ("005930.KS", "000270.KS")

KNOWN_FILTER_IDS = {
    # 가격 필터 (계획 B)
    "FINANCIAL_SECTOR", "INSUFFICIENT_HISTORY",
    # 재무 필터 (계획 C)
    "CAPITAL_IMPAIRED", "CONSECUTIVE_LOSS", "HIGH_DEBT", "INTEREST_UNCOVERED",
    "REVENUE_COLLAPSE", "DATA_INSUFFICIENT", "ADVERSE_AUDIT_OPINION",
    # 12개월 자금 여력 · 거래대금
    "LIQUIDITY_SHORTFALL", "LOW_TRADING_VALUE",
}


@pytest.fixture
def repo():
    with connect(DEFAULT_DB_PATH) as conn:
        yield Repository(conn)


@pytest.fixture
def rows(repo):
    snap = repo.get_latest_snapshot(market="KR")
    if snap is None:
        pytest.skip("스냅샷이 없다")
    return repo.get_analysis_results(snap.snapshot_id)


@pytest.fixture
def raw_conn():
    """Repository를 거치지 않는 별도 연결.

    raw_prices를 직접 읽어 파이프라인 결과와 원본을 대조하기 위한
    것이다 — Repository의 메서드를 다시 쓰면 저장 계층 코드를 저장
    계층 코드로 검증하는 셈이 되어 교차검증의 의미가 없어진다.
    """
    conn = sqlite3.connect(str(DEFAULT_DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def test_every_security_has_a_row(repo, rows):
    assert len(rows) == len(repo.list_securities(market="KR"))


def test_financial_sector_is_excluded(rows):
    financial = [r for r in rows
                 if "FINANCIAL_SECTOR" in json.loads(r["filter_failures"])]

    assert len(financial) == 7, f"금융 7종목이어야 하는데 {len(financial)}개"


def test_recent_listing_is_excluded_for_history(rows):
    row = next(r for r in rows if r["ticker"] == "373220.KS")

    assert "INSUFFICIENT_HISTORY" in json.loads(row["filter_failures"])


def test_passing_rows_have_all_three_averages(rows):
    """5년 이력이 있는 종목은 1·3·5년 평균이 모두 있어야 한다."""
    for r in rows:
        if not r["passed_filters"]:
            continue
        for w in (1, 3, 5):
            assert r[f"avg_price_{w}y"] is not None, \
                f"{r['ticker']}의 {w}년 평균이 없다"


def test_drop_rates_are_consistent_with_averages(rows):
    """하락률 = 현재가 / 평균 - 1 이 실제로 성립하는가."""
    for r in rows:
        if r["avg_price_5y"] is None:
            continue
        expected = r["price_current"] / r["avg_price_5y"] - 1
        assert r["drop_vs_avg_5y"] == pytest.approx(expected, abs=1e-9)


def test_high_is_never_below_current_or_average(rows):
    for r in rows:
        assert r["high_5y"] >= r["price_current"] - 1e-6
        if r["avg_price_5y"] is not None:
            assert r["high_5y"] >= r["avg_price_5y"] - 1e-6


def test_drop_vs_high_is_never_above_drop_vs_avg(rows):
    """설계서 §9.4: 고점은 정의상 평균보다 크거나 같다."""
    for r in rows:
        if r["avg_price_5y"] is None:
            continue
        assert r["drop_vs_high"] <= r["drop_vs_avg_5y"] + 1e-9


def test_market_cap_is_present_once_shares_are_collected(rows):
    """계획 C가 발행주식수를 수집한다. 시가총액은 전 종목에 있어야 한다.

    PER은 적자 종목에서 NULL이 정상이므로 여기서 보지 않는다.
    """
    for r in rows:
        assert r["market_cap"] is not None, r["ticker"]


def test_only_passing_rows_are_scored(rows):
    """하드필터에 걸린 종목은 점수가 없다 - '제외됐지만 85점'이 나오면 안 된다."""
    for r in rows:
        if r["passed_filters"]:
            assert r["score_total"] is not None, r["ticker"]
        else:
            assert r["score_total"] is None, r["ticker"]
        assert r["signal_grade"] in ("STRONG", "WATCH", "NONE")


def test_lg_chem_gets_no_grade_despite_the_largest_drop(rows):
    """계획 C의 시금석. 5년 평균 대비 하락 1위지만 영업이익률이 무너졌다.

    많이 떨어졌다는 것만으로 등급이 나오면 이 제품은 위험하다.
    """
    row = next(r for r in rows if r["ticker"] == "051910.KS")

    assert row["drop_vs_avg_5y"] <= -0.40
    assert row["score_total"] < 70
    assert row["signal_grade"] == "NONE"


def test_snapshot_config_json_is_valid_json(repo):
    snap = repo.get_latest_snapshot(market="KR")
    if snap is None:
        pytest.skip("스냅샷이 없다")

    json.loads(snap.config_json)


# --- 계획이 준 10개 밖의 추가 테스트 -------------------------------------
#
# 위 테스트들은 대부분 저장된 컬럼끼리 서로 비교하는 자기 일관성
# 검사다. 파이프라인이 체계적으로 잘못됐어도(예: 두 컬럼이 저장 시점에
# 같은 방식으로 뒤바뀌었다면) 자기 일관성 검사는 전부 통과할 수 있다.
# 아래 네 개는 저장된 값을 원본(raw_prices)이나 필터 코드와 직접
# 대조해서, 파이프라인 자체를 검증하려 한다.


def test_filter_failures_use_known_ids_only(rows):
    """필터가 조용히 바뀌면(오탈자, 새 필터 추가) 여기서 드러난다."""
    for r in rows:
        failures = json.loads(r["filter_failures"])
        assert set(failures) <= KNOWN_FILTER_IDS, \
            f"{r['ticker']}: 알 수 없는 필터 ID {failures}"


def test_passed_and_excluded_counts_cover_all_rows(rows):
    """모든 행은 통과했거나 제외됐거나 둘 중 하나다 — 제3의 상태가 없다."""
    assert all(r["passed_filters"] in (0, 1) for r in rows)
    passed = sum(1 for r in rows if r["passed_filters"])
    excluded = sum(1 for r in rows if not r["passed_filters"])
    assert passed + excluded == len(rows)


def test_price_current_matches_last_raw_bar(rows, raw_conn):
    """price_current는 raw_prices의 최신 종가와 일치해야 한다 (원본 대조)."""
    by_ticker = {r["ticker"]: r for r in rows}
    for ticker in CROSS_CHECK_TICKERS:
        row = by_ticker[ticker]
        last_close = raw_conn.execute(
            "SELECT close FROM raw_prices WHERE ticker = ? "
            "ORDER BY date DESC LIMIT 1",
            (ticker,),
        ).fetchone()["close"]
        assert row["price_current"] == pytest.approx(last_close)


def test_high_5y_matches_raw_prices_max_close(rows, raw_conn):
    """high_5y는 5년 창 내 raw_prices의 실제 최고 종가와 일치해야 한다
    (원본 대조). window_start()는 순수한 달력 산술이라 경계 계산으로
    재사용해도 무방하다 — 검증 대상은 저장된 high_5y가 원본 데이터의
    최댓값과 맞는지이지, 창의 경계 정의 자체가 아니다.
    """
    as_of_str = raw_conn.execute(
        "SELECT MAX(date) FROM raw_prices"
    ).fetchone()[0]
    as_of = date.fromisoformat(as_of_str)
    start = window_start(as_of, 5)

    by_ticker = {r["ticker"]: r for r in rows}
    for ticker in CROSS_CHECK_TICKERS:
        row = by_ticker[ticker]
        true_max = raw_conn.execute(
            "SELECT MAX(close) FROM raw_prices "
            "WHERE ticker = ? AND date >= ? AND date <= ?",
            (ticker, start.isoformat(), as_of_str),
        ).fetchone()[0]
        assert row["high_5y"] == pytest.approx(true_max)
