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
    제한하지 않고 무경계로 읽으면 창 밖 옛 봉이 평균에 섞여
    avg_price_5y가 왜곡된다. 이 테스트는 그 한 가지 증상을 고정한다.

    이 테스트는 원래 두 번째 증상도 함께 확인했다: "창과 최근 데이터
    사이에 공백이 있어도 그 옛 봉의 날짜가 min_history_years 이력
    요건을 채워버려 진짜 이력 부족을 놓친다"는 것이었다. 그 증상은
    이력 판정이 get_price_bars가 돌려준(구간으로 잘린) 봉 목록에서
    min()을 구하던 옛 설계에서만 성립했다 — 그 목록엔 2010년 봉이
    아예 없었으니, 공백이 있으면 안 걸릴 이유가 없었다.

    지금은 반대다: 이력 판정은 Repository.first_price_date()로 조회
    구간과 무관하게 진짜 첫 봉을 직접 구한다. 2010-01-01에 실제로 봉이
    있다는 것은 이 종목이 진짜로 5년보다 오래됐다는 뜻이고, 그것은
    버그가 아니라 사실이다. 평균 창과 이력 판정은 서로 다른 질문에
    답한다 — 평균은 "최근 5년 안의 가격이 뭐였나", 이력은 "이 종목이
    5년보다 오래됐는가". 평균 창 밖에 있다는 이유로 이력 판정까지
    영향받으면 안 된다. 아래 두 번째 테스트가 이를 고정한다.
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
    # 2.3년치만 채운다.
    d, recent = date(2024, 6, 1), []
    while d <= date(2026, 9, 10):
        recent.append(PriceBar(ticker=ticker, date=d, close=200.0))
        d += timedelta(days=1)
    repo.save_price_bars(recent, source="pykrx")

    summary = analyze_market(repo, market="KR", config=cfg())

    row = repo.get_analysis_results(summary.snapshot_id)[0]
    assert row["avg_price_5y"] == pytest.approx(200.0)


def test_history_check_is_unaffected_by_gaps_outside_the_average_window(repo):
    """위 테스트와 같은 데이터: 2010년에 봉 하나, 그 뒤 2024-06-01까지
    공백, 그다음 2026-09-10까지 2.3년치. 5년 평균 창 밖에 있는 옛 봉도
    이력 판정에는 그대로 반영돼야 한다 — 이 종목은 실제로 2010년부터
    존재했다.
    """
    ticker = "GAP.KS"
    repo.upsert_security(Security(
        ticker=ticker, market="KR", name=ticker, exchange="KOSPI",
        currency="KRW"))

    repo.save_price_bars(
        [PriceBar(ticker=ticker, date=date(2010, 1, 1), close=999.0)],
        source="pykrx",
    )
    d, recent = date(2024, 6, 1), []
    while d <= date(2026, 9, 10):
        recent.append(PriceBar(ticker=ticker, date=d, close=200.0))
        d += timedelta(days=1)
    repo.save_price_bars(recent, source="pykrx")

    summary = analyze_market(repo, market="KR", config=cfg())

    row = repo.get_analysis_results(summary.snapshot_id)[0]
    assert "INSUFFICIENT_HISTORY" not in json.loads(row["filter_failures"])


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


# --- 이력 필터 회귀: 조회 구간에서 이력을 다시 구하면 안 된다 -----------
#
# analyze.py는 평균 계산을 위해 주가를 5년 창으로 잘라 읽는다
# (_earliest_window_start). 그 읽기 자체는 옳다 — Repository에 삭제
# 메서드가 없어 옛 설정으로 저장된 봉이 raw_prices에 남기 때문이다.
# 버그는 그 "잘린 목록"을 이력 필터에도 그대로 넘긴 데 있었다.
# min_history_years=5(기본값)일 때 그 목록의 최솟값은 구성상 항상
# 창의 시작일 이상이므로, 이력 판정이 "창 시작일에 정확히 거래일이
# 있는가"로 퇴화한다. 5년을 뒤로 미는 시프트가 요일을 바꾸므로 이는
# 동전 던지기가 된다.
#
# seed()가 아니라 seed_weekdays()를 쓰는 이유: seed()는 주말까지 매일
# 봉을 채워서 "그 날짜에 봉이 없다"는 상황 자체가 재현되지 않는다.


def seed_weekdays(repo, ticker="005930.KS", is_financial=False,
                  first=date(2021, 9, 10), last=date(2026, 9, 10), close=100.0,
                  market="KR", in_universe=True):
    """실제 KRX 데이터처럼 평일에만 봉을 채운다."""
    repo.upsert_security(Security(
        ticker=ticker, market=market, name=ticker, exchange="KOSPI",
        currency="KRW", is_financial=is_financial, in_universe=in_universe))
    d, bars = first, []
    while d <= last:
        if d.weekday() < 5:
            bars.append(PriceBar(ticker=ticker, date=d, close=close))
        d += timedelta(days=1)
    repo.save_price_bars(bars, source="pykrx")


@pytest.mark.parametrize("as_of", [
    date(2026, 9, 11),   # 금. 5년 전 2021-09-11 = 토요일
    date(2026, 9, 18),   # 금. 5년 전 2021-09-18 = 토요일
    date(2026, 9, 25),   # 금. 5년 전 2021-09-25 = 토요일
    date(2026, 10, 2),   # 금. 5년 전 2021-10-02 = 토요일
])
def test_universe_survives_when_five_years_ago_is_not_a_trading_day(repo, as_of):
    """as_of의 5년 전 날짜가 거래일이 아니어도(주말 등) 전 종목이
    제외되면 안 된다. 실제 DB에서 42종목 전부가 탈락한 바로 그 시나리오.
    """
    seed_weekdays(repo, "005930.KS", first=date(2021, 9, 10), last=as_of)

    summary = analyze_market(repo, market="KR", config=cfg())

    assert summary.excluded == 0
    assert summary.passed == 1


def test_first_bar_before_read_window_still_counts_toward_history(repo):
    """진짜 첫 봉이 5년 조회 창보다 훨씬 이전이어도 이력으로 인정돼야
    한다 — 이것이 빠져 있던 구성(composition) 테스트다.

    get_price_bars가 5년 창으로 잘라 읽으므로, 2018년의 첫 봉은 그
    목록에 아예 나타나지 않는다. 이력 판정이 그 목록에서 min()을 구하면
    2018년 봉의 존재를 영영 알 수 없다.
    """
    seed_weekdays(repo, "005930.KS", first=date(2018, 1, 2), last=date(2026, 9, 11))

    summary = analyze_market(repo, market="KR", config=cfg())

    assert summary.excluded == 0
    assert summary.passed == 1


def test_min_history_years_six_with_five_years_of_data_fails(repo):
    """5년치 데이터만 있으면 6년 기준을 채울 수 없다 — 진짜 이력 부족."""
    seed_weekdays(repo, "005930.KS", first=date(2021, 9, 10), last=date(2026, 9, 10))

    summary = analyze_market(
        repo, market="KR", config=cfg(hard_filters={"min_history_years": 6}),
    )

    row = repo.get_analysis_results(summary.snapshot_id)[0]
    assert "INSUFFICIENT_HISTORY" in json.loads(row["filter_failures"])


def test_min_history_years_six_with_seven_years_of_data_passes(repo):
    """7년치 데이터가 실제로 있으면 6년 기준을 통과해야 한다.

    읽기 구간은 max(price_years, 5)로 묶여 있으므로(그 자체는 옳다)
    6~7년 전 봉은 get_price_bars가 돌려주는 목록에 없다. 이력 판정이
    그 목록에서 min()을 구하는 한, DB에 실제로 7년치가 있어도 "6년
    이상"을 절대 확인할 수 없다 — 현재 코드가 두 경우를 구분하지
    못하는 지점이 바로 여기다.
    """
    seed_weekdays(repo, "005930.KS", first=date(2019, 9, 10), last=date(2026, 9, 10))

    summary = analyze_market(
        repo, market="KR", config=cfg(hard_filters={"min_history_years": 6}),
    )

    row = repo.get_analysis_results(summary.snapshot_id)[0]
    assert "INSUFFICIENT_HISTORY" not in json.loads(row["filter_failures"])


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


# --- 결함 1: 빈 결과가 직전의 진짜 스냅샷을 가리면 안 된다 ----------------
#
# Repository.save_snapshot 자체는 빈 결과도 기록하는 게 합리적인 저장
# 계층 계약이다 — "언제 뭘 돌렸는지"는 결과가 0건이어도 남길 가치가
# 있다 (tests/test_repository_snapshots.py::
# test_empty_results_still_creates_a_snapshot 참고). 하지만 그 스냅샷이
# get_latest_snapshot()에서 최신으로 뽑히면 직전의 진짜(0건이 아닌)
# 스냅샷을 가려버린다 — 설계서 §13이 약속한 "직전 스냅샷이 유지되므로
# 화면은 계속 동작한다"가 예외가 아닌 "성공했지만 텅 빈" 실행에 의해
# 조용히 깨진다. 그 판단은 저장 계층이 아니라 파이프라인의 몫이다:
# 결과가 없으면 애초에 save_snapshot을 부르지 않는다.


def test_analyze_does_not_save_snapshot_when_no_results(repo):
    """KR 전용 DB에 --market US로 돌리면(오타 재현) 스냅샷을 남기지 않는다."""
    seed(repo, "005930.KS", market="KR")

    summary = analyze_market(repo, market="US", config=cfg())

    assert summary.analyzed == 0
    assert summary.snapshot_id is None
    assert repo.get_latest_snapshot() is None


def test_analyze_empty_run_does_not_shadow_previous_snapshot(repo):
    """빈 실행 뒤에도 직전의 진짜 스냅샷이 최신으로 남아야 한다."""
    seed(repo, "005930.KS", market="KR")
    good = analyze_market(repo, market="KR", config=cfg())

    empty = analyze_market(repo, market="US", config=cfg())  # 이 DB엔 US 종목이 없다

    assert empty.snapshot_id is None
    latest = repo.get_latest_snapshot()
    assert latest is not None
    assert latest.snapshot_id == good.snapshot_id


def test_analyze_empty_db_saves_no_snapshot(repo):
    """빈 DB(종목도 없음)에서 실행해도 스냅샷을 남기지 않는다."""
    summary = analyze_market(repo, market="KR", config=cfg())

    assert summary.snapshot_id is None
    assert repo.get_latest_snapshot() is None


def test_render_explains_why_nothing_was_saved(repo):
    """운영자가 화면만 보고도 "왜 0종목이 됐고 무엇이 저장됐는지" 알아야 한다."""
    summary = analyze_market(repo, market="US", config=cfg())  # 이 repo는 비어 있다

    text = summary.render()

    assert "저장" in text
    assert "#" not in text   # "#None" 같은 오해의 소지가 있는 표기가 없어야 한다


# --- 결함 2: 스냅샷이 자신을 재현할 수 있어야 한다 -------------------------
#
# snapshots는 run_at(실행 시각)만 남기고 as_of(분석이 고정된 가격
# 기준일)는 남기지 않았다. 같은 config_hash라도 수집을 며칠 건너뛰면
# run_at은 흘러가지만 as_of는 그대로거나 그 반대일 수 있어, run_at으로
# as_of를 추정할 수 없다.


def test_analyze_persists_as_of_on_snapshot(repo):
    seed(repo, "005930.KS", last=date(2026, 9, 10))

    summary = analyze_market(repo, market="KR", config=cfg())

    stored = repo.get_latest_snapshot()
    assert stored.as_of == "2026-09-10"
    assert summary.as_of == date(2026, 9, 10)


def test_summary_render_shows_as_of(repo):
    seed(repo, "005930.KS", last=date(2026, 9, 10))

    text = analyze_market(repo, market="KR", config=cfg()).render()

    assert "2026-09-10" in text


# --- 결함 4: as_of는 분석 중인 시장으로 한정돼야 한다 ----------------------
#
# Repository.latest_price_date_any()가 시장을 몰랐다. 미국 장은 한국
# 장보다 늦게 마감하므로, US 종목의 최신 봉 날짜가 KR 분석의 as_of를
# 밀어버리면 모든 KR 종목이 아직 그 시장에 존재하지 않는 날짜를 기준으로
# 평가된다.


def test_analyze_as_of_is_scoped_to_market(repo):
    seed(repo, "005930.KS", market="KR",
         first=date(2021, 9, 10), last=date(2026, 9, 10))
    seed(repo, "AAPL", market="US",
         first=date(2021, 9, 10), last=date(2026, 9, 11))  # 미국 장은 더 늦게 마감한다

    summary = analyze_market(repo, market="KR", config=cfg())

    assert summary.as_of == date(2026, 9, 10)
