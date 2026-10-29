"""신호 검증(백테스트). 과거 시점마다 그때 보였던 데이터로 신호를 내고 이후 성과를 잰다."""

from datetime import date, timedelta

import pytest

from stockscout.config import Config, load_config
from stockscout.models import Financials, PriceBar, Security
from stockscout.pipeline.backtest import available_financials, backtest_market
from stockscout.store.db import connect, init_db
from stockscout.store.repository import Repository

SCORING = load_config().model_dump(mode="json")["scoring"]


@pytest.fixture
def repo(tmp_path):
    with connect(tmp_path / "t.db") as conn:
        init_db(conn)
        yield Repository(conn)


def cfg():
    return Config.model_validate({"scoring": SCORING})


def weekdays(start, end):
    d, out = start, []
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def healthy(ticker, years=range(2016, 2026)):
    return [Financials(
        ticker=ticker, fiscal_year=y, currency="KRW",
        revenue=1_000.0 * 1.1 ** (y - 2019), operating_income=150.0 * 1.1 ** (y - 2019),
        net_income=120.0, total_assets=2_000.0, total_liabilities=500.0,
        total_equity=1_000.0, current_assets=800.0, current_liabilities=400.0,
        interest_expense=10.0, operating_cash_flow=180.0, capex=30.0,
    ) for y in years]


def seed(repo, ticker, closes_by_day):
    repo.upsert_security(Security(ticker=ticker, market="KR", name=ticker,
                                  exchange="KOSPI", currency="KRW"))
    repo.save_price_bars([PriceBar(ticker=ticker, date=d, close=c)
                          for d, c in closes_by_day], source="pykrx")


DAYS = weekdays(date(2023, 1, 2), date(2025, 6, 30))


def crash_and_rebound(crash_at=400, depth=0.6, rebound=1.5):
    """평탄 -> crash_at 거래일에 40% 급락 후 30일 횡보 -> 그 뒤 50% 반등."""
    out = []
    for i, d in enumerate(DAYS):
        if i < crash_at:
            c = 100.0
        elif i < crash_at + 30:
            c = 100.0 * depth
        else:
            c = 100.0 * depth * rebound
        out.append((d, c))
    return out


def test_point_in_time_financials_exclude_reports_not_yet_filed():
    """FY2024 사업보고서는 2025년 3월 말에 나온다. 그 전 시점에는 쓸 수 없다."""
    fins = healthy("A")

    before = available_financials(fins, date(2025, 3, 31))
    after = available_financials(fins, date(2025, 4, 1))

    assert max(f.fiscal_year for f in before) == 2023
    assert max(f.fiscal_year for f in after) == 2024


def test_a_crash_signals_once_per_grade_and_measures_forward_returns(repo):
    """급락 당일 별 하나(아직 하락 중), 10거래일 새 저점이 없자 별 둘.

    같은 하락 안에서 등급마다 첫 등장만 센다 - 화면에 그 별이 처음 뜬 날 산 경우다.
    """
    seed(repo, "A.KS", crash_and_rebound())
    repo.save_financials(healthy("A.KS"))

    events = backtest_market(repo, market="KR", config=cfg(), step_days=1).events

    assert [(e.grade, e.date) for e in events] == [
        ("WATCH", DAYS[400]), ("STRONG", DAYS[410])]
    watch = events[0]
    assert watch.returns[60] == pytest.approx(0.5)      # 60 -> 90
    assert watch.max_adverse == pytest.approx(0.0)      # 더 빠지지 않았다


def test_a_new_drop_after_recovering_is_a_new_episode(repo):
    """회복해 조건을 벗어났다가 다시 빠지면 새 사례로 센다."""
    closes = []
    for i, d in enumerate(DAYS):
        if 300 <= i < 330 or 500 <= i < 530:
            c = 60.0
        else:
            c = 100.0
        closes.append((d, c))
    seed(repo, "A.KS", closes)
    repo.save_financials(healthy("A.KS"))

    events = backtest_market(repo, market="KR", config=cfg(), step_days=1).events

    assert [e.date for e in events if e.grade == "WATCH"] == [DAYS[300], DAYS[500]]


def test_a_non_quality_crash_is_counted_separately(repo):
    seed(repo, "B.KS", crash_and_rebound())
    repo.save_financials([f.model_copy(update={"operating_cash_flow": -50.0})
                          for f in healthy("B.KS")])

    report = backtest_market(repo, market="KR", config=cfg(), step_days=1)

    assert [e.grade for e in report.events] == ["NOT_QUALITY"]


def test_excess_return_subtracts_the_index(repo):
    seed(repo, "A.KS", crash_and_rebound())
    repo.save_financials(healthy("A.KS"))
    # 지수도 같은 기간 20% 오른다
    repo.save_price_bars([PriceBar(ticker="^KS11", date=d, close=100.0 if i < 400 else 120.0)
                          for i, d in enumerate(DAYS)], source="yfinance")

    e = backtest_market(repo, market="KR", config=cfg(), step_days=1).events[0]

    assert e.excess[60] == pytest.approx(0.5 - 0.0)   # 신호일 이후 지수는 평탄(120 -> 120)


def test_signals_without_enough_future_are_not_scored(repo):
    """마지막 120거래일 안의 신호는 성과를 잴 수 없어 넣지 않는다."""
    seed(repo, "A.KS", crash_and_rebound(crash_at=len(DAYS) - 50))
    repo.save_financials(healthy("A.KS"))

    assert backtest_market(repo, market="KR", config=cfg(), step_days=1).events == []


def test_the_summary_groups_by_grade_and_reports_the_caveats(repo):
    seed(repo, "A.KS", crash_and_rebound())
    repo.save_financials(healthy("A.KS"))

    text = backtest_market(repo, market="KR", config=cfg(), step_days=1).render()

    assert "★ 하락 중" in text
    assert "생존자 편향" in text
    assert "거래비용" in text



# --- 영탁센스 (신고가 돌파) -----------------------------------------------------

from stockscout.pipeline.backtest import backtest_breakouts  # noqa: E402


def breakout_then_run(at=400, volume=3000):
    """평탄(100) -> at일에 큰 거래량으로 110 돌파 -> 그 뒤 계속 올라 150."""
    out = []
    for i, d in enumerate(DAYS):
        c = 100.0 if i < at else 110.0 if i < at + 10 else 150.0
        v = volume if i == at else 1000
        out.append((d, c, v))
    return out


def seed_v(repo, ticker, rows):
    repo.upsert_security(Security(ticker=ticker, market="KR", name=ticker,
                                  exchange="KOSPI", currency="KRW"))
    repo.save_price_bars([PriceBar(ticker=ticker, date=d, close=c, volume=v)
                          for d, c, v in rows], source="pykrx")


def test_a_volume_breakout_is_one_event_with_its_forward_returns(repo):
    seed_v(repo, "A.KS", breakout_then_run())

    events = backtest_breakouts(repo, market="KR", config=cfg(), step_days=1).events

    assert [(e.grade, e.date) for e in events] == [("BREAKOUT", DAYS[400])]
    assert events[0].returns[20] == pytest.approx(150 / 110 - 1)


def test_a_thin_volume_breakout_is_counted_separately(repo):
    """거래량 조건이 정말 도움이 되는지 보려고 거래량 없는 돌파도 따로 센다."""
    seed_v(repo, "A.KS", breakout_then_run(volume=1100))

    events = backtest_breakouts(repo, market="KR", config=cfg(), step_days=1).events

    assert [e.grade for e in events] == ["BREAKOUT_THIN"]


def test_the_breakout_report_names_its_groups(repo):
    seed_v(repo, "A.KS", breakout_then_run())

    text = backtest_breakouts(repo, market="KR", config=cfg(), step_days=1).render()

    assert "돌파(거래량 동반)" in text and "생존자 편향" in text
