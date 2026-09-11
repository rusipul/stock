import json
from datetime import date, timedelta

import pytest

from stockscout.config import Config
from stockscout.models import PriceBar, Security
from stockscout.pipeline.analyze import AnalyzeSummary, analyze_market
from stockscout.store.db import connect, init_db
from stockscout.store.repository import Repository


@pytest.fixture
def repo(tmp_path):
    with connect(tmp_path / "t.db") as conn:
        init_db(conn)
        yield Repository(conn)


def cfg(**overrides) -> Config:
    raw = {"lookback": {"price_years": [1, 3, 5], "primary_window": 5,
                        "min_data_coverage": 0.8},
           "hard_filters": {"min_history_years": 5}}
    raw.update(overrides)
    return Config.model_validate(raw)


def seed(repo, ticker="005930.KS", is_financial=False,
         first=date(2021, 9, 10), last=date(2026, 9, 10), close=100.0,
         market="KR", in_universe=True):
    repo.upsert_security(Security(
        ticker=ticker, market=market, name=ticker, exchange="KOSPI",
        currency="KRW", is_financial=is_financial, in_universe=in_universe))
    d, bars = first, []
    while d <= last:
        bars.append(PriceBar(ticker=ticker, date=d, close=close))
        d += timedelta(days=1)
    repo.save_price_bars(bars, source="pykrx")


def test_analyze_creates_snapshot_with_one_row_per_security(repo):
    seed(repo, "005930.KS")
    seed(repo, "000660.KS")

    summary = analyze_market(repo, market="KR", config=cfg())

    rows = repo.get_analysis_results(summary.snapshot_id)
    assert {r["ticker"] for r in rows} == {"005930.KS", "000660.KS"}
    assert summary.analyzed == 2


def test_analyze_records_filter_failures(repo):
    seed(repo, "105560.KS", is_financial=True)

    summary = analyze_market(repo, market="KR", config=cfg())

    row = repo.get_analysis_results(summary.snapshot_id)[0]
    assert row["passed_filters"] == 0
    assert "FINANCIAL_SECTOR" in json.loads(row["filter_failures"])
    assert summary.excluded == 1
    assert summary.passed == 0


def test_analyze_skips_securities_without_prices(repo):
    repo.upsert_security(Security(ticker="EMPTY.KS", market="KR",
                                  name="빈종목", currency="KRW"))

    summary = analyze_market(repo, market="KR", config=cfg())

    assert summary.analyzed == 0
    assert summary.skipped == 1


def test_analyze_stores_config_hash_and_json(repo):
    seed(repo)

    summary = analyze_market(repo, market="KR", config=cfg())

    snap = repo.get_latest_snapshot()
    assert snap.config_hash
    assert json.loads(snap.config_json)["signal"]["min_score"] == 70


def test_same_config_produces_same_hash(repo):
    seed(repo)

    a = analyze_market(repo, market="KR", config=cfg())
    b = analyze_market(repo, market="KR", config=cfg())

    rows = repo.conn.execute("SELECT config_hash FROM snapshots").fetchall()
    assert rows[0]["config_hash"] == rows[1]["config_hash"]
    assert a.snapshot_id != b.snapshot_id


def test_different_threshold_produces_different_hash(repo):
    seed(repo)

    analyze_market(repo, market="KR", config=cfg())
    analyze_market(repo, market="KR",
                   config=cfg(signal={"drop_vs_avg_threshold": -0.35}))

    hashes = {r["config_hash"] for r in
              repo.conn.execute("SELECT config_hash FROM snapshots")}
    assert len(hashes) == 2


def test_analyze_note_is_stored(repo):
    seed(repo)

    analyze_market(repo, market="KR", config=cfg(), note="임계치 -35% 실험")

    assert repo.get_latest_snapshot().note == "임계치 -35% 실험"


def test_unknown_market_raises(repo):
    with pytest.raises(ValueError, match="XX"):
        analyze_market(repo, market="XX", config=cfg())


def test_analyze_isolates_per_security_failures(repo, monkeypatch):
    """한 종목의 계산 실패가 전체 분석을 멈추면 안 된다.

    실패를 주입해서 검증한다. 데이터를 이상하게 만드는 방식(음수 종가
    등)으로는 검증되지 않는다 — compute_price_stats가 음수 종가에
    예외를 던지지 않아서, 격리를 테스트한다면서 실제로는 아무것도
    실패시키지 않는 테스트가 된다.
    """
    import stockscout.pipeline.analyze as analyze_mod

    seed(repo, "AAA.KS")
    seed(repo, "BBB.KS")

    real = analyze_mod.compute_price_stats

    def explode_for_bbb(bars, **kwargs):
        if bars and bars[0].ticker == "BBB.KS":
            raise RuntimeError("계산 폭발")
        return real(bars, **kwargs)

    monkeypatch.setattr(analyze_mod, "compute_price_stats", explode_for_bbb)

    summary = analyze_market(repo, market="KR", config=cfg())

    assert summary.failed == 1
    assert summary.analyzed == 1
    tickers = {r["ticker"] for r in repo.get_analysis_results(summary.snapshot_id)}
    assert tickers == {"AAA.KS"}
    assert repo.recent_errors()[0]["ticker"] == "BBB.KS"


def test_summary_render_is_printable_on_cp949_console(repo):
    seed(repo)

    summary = analyze_market(repo, market="KR", config=cfg())

    summary.render().encode("cp949")   # 예외가 나면 실패


def test_summary_render_reports_counts(repo):
    seed(repo, "005930.KS")
    seed(repo, "105560.KS", is_financial=True)

    text = analyze_market(repo, market="KR", config=cfg()).render()

    assert "2" in text and "1" in text


# --- 계획이 준 11개 밖의 추가 테스트 -----------------------------------
#
# list_securities(market=..., in_universe_only=True 기본값)가 이미 올바른
# 필터링을 하고 있지만, analyze_market이 그 인자들을 실제로 넘기는지
# 아무 테스트도 못박지 않는다. 아래 세 테스트가 그것을 고정한다.


def test_analyze_excludes_out_of_universe_securities(repo):
    """편출된(in_universe=False) 종목은 스냅샷에 나타나면 안 된다."""
    seed(repo, "005930.KS")
    seed(repo, "999999.KS", in_universe=False)

    summary = analyze_market(repo, market="KR", config=cfg())

    tickers = {r["ticker"] for r in repo.get_analysis_results(summary.snapshot_id)}
    assert tickers == {"005930.KS"}
    assert summary.analyzed == 1


def test_analyze_excludes_other_markets(repo):
    """market="KR"로 실행하면 US 종목은 절대 섞이면 안 된다."""
    seed(repo, "005930.KS", market="KR")
    seed(repo, "AAPL", market="US")

    summary = analyze_market(repo, market="KR", config=cfg())

    tickers = {r["ticker"] for r in repo.get_analysis_results(summary.snapshot_id)}
    assert tickers == {"005930.KS"}
    assert summary.analyzed == 1


def test_analyze_price_window_excludes_stale_bars(repo):
    """price_years를 좁힌 뒤에도 옛 봉이 raw_prices에 그대로 남는다
    (Repository에 삭제 메서드가 없으므로). get_price_bars를 창으로
    제한하지 않고 무경계로 읽으면:

    1) 창 밖 옛 봉이 평균에 섞여 avg_price_5y가 왜곡되고,
    2) 창과 최근 데이터 사이에 공백이 있어도 그 옛 봉의 날짜가
       min_history_years 이력 요건을 채워버려 INSUFFICIENT_HISTORY를
       놓친다 (사실은 최근 창 안에 5년치 데이터가 없는데도).

    이 테스트는 두 증상을 모두 확인한다. 특히 2)는 _earliest_window_start가
    실제로 get_price_bars(start=..., end=...)에 전달되는지를 못박는다 —
    compute_price_stats 자체가 창별로 날짜를 다시 거르기 때문에, 평균값
    하나만으로는 무경계 조회 버그를 잡아내지 못한다.
    """
    ticker = "GAP.KS"
    repo.upsert_security(Security(
        ticker=ticker, market="KR", name=ticker, exchange="KOSPI",
        currency="KRW"))

    # 5년 창(2021-09-10 ~ 2026-09-10)보다 훨씬 이전의 옛 봉. 가격도
    # 다르게 두어 평균에 섞이면 즉시 드러나게 한다.
    repo.save_price_bars(
        [PriceBar(ticker=ticker, date=date(2010, 1, 1), close=999.0)],
        source="pykrx",
    )
    # 옛 봉과 최근 구간 사이에 공백을 두고, 최근 구간은 5년에 못 미치는
    # 2.3년치만 채운다 — 이력 요건 미달이 "정답"이다.
    d, recent = date(2024, 6, 1), []
    while d <= date(2026, 9, 10):
        recent.append(PriceBar(ticker=ticker, date=d, close=200.0))
        d += timedelta(days=1)
    repo.save_price_bars(recent, source="pykrx")

    summary = analyze_market(repo, market="KR", config=cfg())

    row = repo.get_analysis_results(summary.snapshot_id)[0]
    assert row["avg_price_5y"] == pytest.approx(200.0)
    assert "INSUFFICIENT_HISTORY" in json.loads(row["filter_failures"])


def test_analyzed_count_matches_persisted_rows(repo):
    """summary.analyzed가 실제로 저장된 행 수와 어긋나면 요약이 거짓말을
    하는 셈이다."""
    seed(repo, "005930.KS")
    seed(repo, "000660.KS")
    seed(repo, "105560.KS", is_financial=True)  # 필터 탈락이지만 분석은 됨

    summary = analyze_market(repo, market="KR", config=cfg())

    rows = repo.get_analysis_results(summary.snapshot_id)
    assert summary.analyzed == len(rows)
    assert summary.analyzed == 3


def test_high_5y_survives_narrowing_price_years(repo):
    """price_years에서 5를 빼도 고점 창은 5년이어야 한다.

    고점 창은 설계서 §9.1이 5년으로 못박은 값이라 price_years와
    무관하다. 조회 구간을 max(price_years)로만 잡으면 price_years가
    [1, 3]일 때 3년치만 읽혀, 3년 고점이 "5년 고점"으로 조용히
    보고되고 그 값이 계획 C의 고점 대비 등급 판정에 들어간다.
    """
    repo.upsert_security(Security(ticker="PEAK.KS", market="KR", name="PEAK.KS",
                                  exchange="KOSPI", currency="KRW"))
    # 4년 전에 고점(500), 최근 3년은 평탄(100). 고점은 3년 창 밖, 5년 창 안이다.
    bars, d, last = [], date(2021, 9, 10), date(2026, 9, 10)
    peak_day = date(2022, 9, 10)
    while d <= last:
        bars.append(PriceBar(ticker="PEAK.KS", date=d,
                             close=500.0 if d == peak_day else 100.0))
        d += timedelta(days=1)
    repo.save_price_bars(bars, source="pykrx")

    narrow = Config.model_validate({
        "lookback": {"price_years": [1, 3], "primary_window": 3,
                     "min_data_coverage": 0.8},
        "hard_filters": {"min_history_years": 5},
    })

    summary = analyze_market(repo, market="KR", config=narrow)
    row = repo.get_analysis_results(summary.snapshot_id)[0]

    assert row["high_5y"] == 500.0          # 3년 창 밖의 고점을 놓치지 않는다
    assert row["avg_price_5y"] is None      # 5년 평균은 요청되지 않았다
    assert row["avg_price_3y"] is not None
