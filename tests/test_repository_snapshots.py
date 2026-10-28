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


def snap(note="테스트", as_of=None):
    return Snapshot(run_at="2026-09-11T02:00:00+00:00", config_hash="h1",
                    config_json='{"signal": {"min_score": 70}}', note=note,
                    as_of=as_of)


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


def test_snapshot_as_of_round_trips(repo):
    """스냅샷은 어떤 가격 기준일이 그 숫자들을 만들었는지 스스로 밝혀야 한다.

    run_at(실행 시각)만으로는 재현할 수 없다 — 수집을 일주일 건너뛰면
    run_at은 흘러가도 as_of(가장 최근 주가일)는 그대로일 수 있다.
    """
    sid = repo.save_snapshot(snap(as_of="2026-09-10"), [result()])

    stored = repo.get_latest_snapshot()

    assert stored.snapshot_id == sid
    assert stored.as_of == "2026-09-10"


def test_snapshot_as_of_defaults_to_none(repo):
    """as_of를 넘기지 않은 기존 호출 패턴도 계속 동작해야 한다."""
    repo.save_snapshot(snap(), [result()])

    assert repo.get_latest_snapshot().as_of is None


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
    """분석 대상이 0종목이어도 실행 기록은 남는다.

    이것은 저장 계층 하나만의 계약이다: save_snapshot()은 빈 결과도
    "언제 뭘 돌렸는지" 기록할 가치가 있다고 본다. 이 스냅샷이
    get_latest_snapshot()에서 최신으로 뽑혀 직전의 진짜 결과를 가려도
    되는지는 별개 질문이고, 그 답은 "아니오"다 — 그 판단은 파이프라인의
    몫이다. tests/test_pipeline_analyze.py::
    test_analyze_does_not_save_snapshot_when_no_results가 그쪽을 고정한다:
    analyze_market은 결과가 없으면 애초에 save_snapshot을 부르지 않는다.
    두 테스트는 서로 다른 계층을 보므로 모순이 아니다.
    """
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


def test_trading_dates_can_be_scoped_to_a_market(repo):
    """시장마다 휴장일이 다르다. 미국 장이 열린 한국 공휴일이 한국 달력에
    섞이면 한국 종목 전부의 커버리지가 깎인다."""
    from datetime import date

    from stockscout.models import PriceBar, Security

    repo.upsert_security(Security(ticker="005930.KS", market="KR",
                                  name="삼성전자", currency="KRW"))
    repo.upsert_security(Security(ticker="AAPL", market="US",
                                  name="Apple", currency="USD"))
    repo.save_price_bars(
        [
            PriceBar(ticker="005930.KS", date=date(2026, 1, 5), close=1.0),
            PriceBar(ticker="AAPL", date=date(2026, 1, 5), close=1.0),
            PriceBar(ticker="AAPL", date=date(2026, 1, 6), close=1.0),   # 한국 휴장
        ],
        source="test",
    )

    start, end = date(2026, 1, 1), date(2026, 1, 10)
    assert repo.trading_dates(start, end, market="KR") == [date(2026, 1, 5)]
    assert repo.trading_dates(start, end, market="US") == [date(2026, 1, 5),
                                                          date(2026, 1, 6)]
    # market을 생략하면 지금까지처럼 전 시장의 합집합이다
    assert repo.trading_dates(start, end) == [date(2026, 1, 5), date(2026, 1, 6)]


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


def test_latest_price_date_any_is_scoped_to_market_when_given(repo):
    """미국 장은 한국 장보다 늦게 마감한다. market을 넘기면 그 시장의
    종목에 달린 봉만 봐야 한다 — 그러지 않으면 KR 분석의 as_of가 아직
    KR에는 봉이 없는 날짜로 밀려버린다 (실측: US 종목 봉 하나가
    KR 전 종목의 as_of를 2026-09-11로 밀었다).
    """
    from datetime import date

    from stockscout.models import PriceBar, Security

    repo.upsert_security(Security(ticker="005930.KS", market="KR",
                                  name="삼성전자", currency="KRW"))
    repo.upsert_security(Security(ticker="AAPL", market="US",
                                  name="Apple", currency="USD"))
    repo.save_price_bars(
        [
            PriceBar(ticker="005930.KS", date=date(2026, 9, 10), close=1.0),
            PriceBar(ticker="AAPL", date=date(2026, 9, 11), close=1.0),
        ],
        source="test",
    )

    assert repo.latest_price_date_any(market="KR") == date(2026, 9, 10)
    assert repo.latest_price_date_any(market="US") == date(2026, 9, 11)
    assert repo.latest_price_date_any() == date(2026, 9, 11)  # 시장 미지정 시 기존 동작 유지


# --- first_price_date -----------------------------------------------------
#
# 이력 필터가 "진짜" 첫 봉을 조회 구간과 무관하게 알아야 하기 때문에
# 추가됐다. get_price_bars(start=..., end=...)로 잘라 읽은 목록에서
# min()을 구하던 옛 방식은, min_history_years가 그 구간의 길이와 같을
# 때(기본값 5년) 목록의 최솟값이 구성상 항상 구간 시작일 이상이 되어
# "구간 시작일이 거래일인가"만 묻는 것으로 퇴화했다. first_price_date는
# 구간 인자를 아예 받지 않음으로써 그 함정을 원천 차단한다.


def test_first_price_date_is_none_when_empty(repo):
    assert repo.first_price_date("005930.KS") is None


def test_first_price_date_returns_true_minimum_regardless_of_insert_order(repo):
    from datetime import date

    from stockscout.models import PriceBar

    repo.save_price_bars(
        [
            PriceBar(ticker="005930.KS", date=date(2024, 1, 1), close=1.0),
            PriceBar(ticker="005930.KS", date=date(2019, 3, 4), close=1.0),
            PriceBar(ticker="005930.KS", date=date(2021, 9, 10), close=1.0),
        ],
        source="test",
    )

    assert repo.first_price_date("005930.KS") == date(2019, 3, 4)


def test_first_price_date_ignores_any_read_window(repo):
    """이 메서드는 구간 인자를 받지 않는다 — 그것이 핵심이다.

    get_price_bars로 5년 창을 좁혀 읽으면 2015년의 봉은 그 목록에 아예
    나타나지 않는다. first_price_date는 raw_prices 전체를 보므로 그
    창과 무관하게 진짜 첫 봉을 돌려준다.
    """
    from datetime import date

    from stockscout.models import PriceBar

    repo.save_price_bars(
        [
            PriceBar(ticker="005930.KS", date=date(2015, 1, 1), close=1.0),
            PriceBar(ticker="005930.KS", date=date(2026, 9, 10), close=1.0),
        ],
        source="test",
    )

    bounded = repo.get_price_bars(
        "005930.KS", start=date(2021, 9, 10), end=date(2026, 9, 10)
    )
    assert all(b.date >= date(2021, 9, 10) for b in bounded)  # 2015년 봉은 안 보인다

    assert repo.first_price_date("005930.KS") == date(2015, 1, 1)


def test_first_price_date_is_scoped_to_ticker(repo):
    from datetime import date

    from stockscout.models import PriceBar

    repo.save_price_bars(
        [
            PriceBar(ticker="005930.KS", date=date(2022, 1, 1), close=1.0),
            PriceBar(ticker="000660.KS", date=date(2010, 1, 1), close=1.0),
        ],
        source="test",
    )

    assert repo.first_price_date("005930.KS") == date(2022, 1, 1)
