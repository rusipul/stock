from datetime import date

import pytest

from stockscout.models import PriceBar, RawStatement, Security
from stockscout.pipeline.collect import CollectSummary, collect_market, sync_universe
from stockscout.sources.base import PermanentSourceError
from stockscout.sources.ratelimit import DailyQuotaExceeded
from stockscout.store.db import connect, init_db
from stockscout.store.repository import Repository


@pytest.fixture
def repo(tmp_path):
    with connect(tmp_path / "t.db") as conn:
        init_db(conn)
        yield Repository(conn)


class FakeUniverse:
    def __init__(self, securities): self._s = securities
    def fetch(self): return self._s


class FakeFundamentals:
    name = "dart"

    def __init__(self, per_ticker=None, failures=None):
        self._per_ticker = per_ticker or {}
        self._failures = failures or {}
        self.requested: list[tuple[str, list[int]]] = []

    def fetch_annual(self, ticker, years):
        self.requested.append((ticker, list(years)))
        if ticker in self._failures:
            raise self._failures[ticker]
        return [
            RawStatement(ticker=ticker, fiscal_year=y, source="dart", payload={"y": y})
            for y in years
            if y in self._per_ticker.get(ticker, years)
        ]


class FakePrices:
    name = "pykrx"

    def __init__(self, bars_by_ticker=None, failures=None):
        self._bars = bars_by_ticker or {}
        self._failures = failures or {}
        self.requested: list[tuple[str, date, date]] = []

    def fetch_daily(self, ticker, start, end):
        self.requested.append((ticker, start, end))
        if ticker in self._failures:
            raise self._failures[ticker]
        return self._bars.get(ticker, [])


def sec(ticker="005930.KS", name="삼성전자"):
    return Security(ticker=ticker, market="KR", name=name, exchange="KOSPI", currency="KRW")


# --- 유니버스 -------------------------------------------------------

def test_sync_universe_inserts_securities(repo):
    n = sync_universe(repo, FakeUniverse([sec(), sec("000660.KS", "SK하이닉스")]), market="KR")

    assert n == 2
    assert {s.ticker for s in repo.list_securities("KR")} == {"005930.KS", "000660.KS"}


def test_sync_universe_marks_dropped_tickers_out(repo):
    sync_universe(repo, FakeUniverse([sec(), sec("000660.KS", "SK하이닉스")]), market="KR")

    sync_universe(repo, FakeUniverse([sec()]), market="KR")

    assert {s.ticker for s in repo.list_securities("KR")} == {"005930.KS"}
    assert repo.get_security("000660.KS").in_universe is False


def test_sync_universe_re_adds_previously_dropped_ticker(repo):
    sync_universe(repo, FakeUniverse([sec(), sec("000660.KS", "SK하이닉스")]), market="KR")
    sync_universe(repo, FakeUniverse([sec()]), market="KR")
    assert repo.get_security("000660.KS").in_universe is False

    sync_universe(repo, FakeUniverse([sec(), sec("000660.KS", "SK하이닉스")]), market="KR")

    assert repo.get_security("000660.KS").in_universe is True


# --- 수집 -----------------------------------------------------------

def test_collect_saves_statements_and_prices(repo):
    repo.upsert_security(sec())
    fundamentals = FakeFundamentals()
    prices = FakePrices({"005930.KS": [
        PriceBar(ticker="005930.KS", date=date(2026, 1, 5), close=70000.0)
    ]})

    summary = collect_market(
        repo, "KR", fundamentals, prices,
        years=[2024, 2025], price_start=date(2021, 1, 1), price_end=date(2026, 1, 31),
    )

    assert summary.succeeded == 1
    assert summary.failed == 0
    assert len(repo.get_raw_statements("005930.KS")) == 2
    assert len(repo.get_price_bars("005930.KS")) == 1


def test_collect_skips_years_already_stored(repo):
    repo.upsert_security(sec())
    repo.save_raw_statements([
        RawStatement(ticker="005930.KS", fiscal_year=2024, source="dart", payload={})
    ])
    fundamentals = FakeFundamentals()

    collect_market(
        repo, "KR", fundamentals, FakePrices(),
        years=[2024, 2025], price_start=date(2021, 1, 1), price_end=date(2026, 1, 31),
    )

    assert fundamentals.requested == [("005930.KS", [2025])]


def test_collect_resumes_prices_from_last_stored_date(repo):
    repo.upsert_security(sec())
    repo.save_price_bars(
        [PriceBar(ticker="005930.KS", date=date(2026, 1, 5), close=1.0)], source="pykrx"
    )
    prices = FakePrices()

    collect_market(
        repo, "KR", FakeFundamentals(), prices,
        years=[], price_start=date(2021, 1, 1), price_end=date(2026, 1, 31),
    )

    assert prices.requested == [("005930.KS", date(2026, 1, 6), date(2026, 1, 31))]


def test_collect_isolates_failures_per_ticker(repo):
    repo.upsert_security(sec("005930.KS"))
    repo.upsert_security(sec("000660.KS", "SK하이닉스"))
    fundamentals = FakeFundamentals(
        failures={"005930.KS": PermanentSourceError("corp_code 없음")}
    )

    summary = collect_market(
        repo, "KR", fundamentals, FakePrices(),
        years=[2025], price_start=date(2021, 1, 1), price_end=date(2026, 1, 31),
    )

    assert summary.succeeded == 1
    assert summary.failed == 1
    assert len(repo.get_raw_statements("000660.KS")) == 1
    errors = repo.recent_errors()
    assert errors[0]["ticker"] == "005930.KS"


def test_collect_isolates_price_source_failures_too(repo):
    repo.upsert_security(sec("005930.KS"))
    repo.upsert_security(sec("000660.KS", "SK하이닉스"))
    prices = FakePrices(
        bars_by_ticker={"000660.KS": [
            PriceBar(ticker="000660.KS", date=date(2026, 1, 5), close=1.0)
        ]},
        failures={"005930.KS": PermanentSourceError("가격 없음")},
    )

    summary = collect_market(
        repo, "KR", FakeFundamentals(), prices,
        years=[], price_start=date(2021, 1, 1), price_end=date(2026, 1, 31),
    )

    assert summary.succeeded == 1
    assert summary.failed == 1
    assert len(repo.get_price_bars("000660.KS")) == 1
    assert len(repo.get_price_bars("005930.KS")) == 0
    errors = repo.recent_errors()
    assert errors[0]["ticker"] == "005930.KS"


def test_collect_force_refetches_everything(repo):
    repo.upsert_security(sec())
    repo.save_raw_statements([
        RawStatement(ticker="005930.KS", fiscal_year=2024, source="dart", payload={})
    ])
    fundamentals = FakeFundamentals()

    collect_market(
        repo, "KR", fundamentals, FakePrices(),
        years=[2024, 2025], price_start=date(2021, 1, 1), price_end=date(2026, 1, 31),
        force=True,
    )

    assert fundamentals.requested == [("005930.KS", [2024, 2025])]


def test_collect_limits_to_requested_tickers(repo):
    repo.upsert_security(sec("005930.KS"))
    repo.upsert_security(sec("000660.KS", "SK하이닉스"))
    fundamentals = FakeFundamentals()

    collect_market(
        repo, "KR", fundamentals, FakePrices(),
        years=[2025], price_start=date(2021, 1, 1), price_end=date(2026, 1, 31),
        tickers=["000660.KS"],
    )

    assert [t for t, _ in fundamentals.requested] == ["000660.KS"]


def test_collect_aborts_batch_on_daily_quota_exceeded(repo):
    # list_securities()는 ticker 알파벳순으로 반환한다: 000660 < 005930 < 035420.
    repo.upsert_security(sec("005930.KS"))
    repo.upsert_security(sec("000660.KS", "SK하이닉스"))
    repo.upsert_security(sec("035420.KS", "NAVER"))
    fundamentals = FakeFundamentals(
        failures={"005930.KS": DailyQuotaExceeded("일일 한도 소진")}
    )

    summary = collect_market(
        repo, "KR", fundamentals, FakePrices(),
        years=[2025], price_start=date(2021, 1, 1), price_end=date(2026, 1, 31),
    )

    assert summary.aborted is True
    assert [t for t, _ in fundamentals.requested] == ["000660.KS", "005930.KS"]
    assert summary.remaining == ["005930.KS", "035420.KS"]


def test_summary_renders_human_readable_text():
    summary = CollectSummary(total=10, succeeded=8, failed=2, statements=40, price_bars=1000)

    text = summary.render()

    assert "8" in text and "2" in text and "40" in text


def test_summary_render_is_printable_on_cp949_console():
    """한국어 Windows 콘솔은 cp949다.

    render()에 U+26A0 같은 기호가 있으면 출력 시 UnicodeEncodeError로
    죽는데, 하필 일일 한도 소진처럼 남은 종목을 가장 알아야 할 순간에
    크래시한다. 실제로 그랬던 버그의 회귀 테스트다.
    """
    summary = CollectSummary(
        total=3, succeeded=1, failed=0, aborted=True, remaining=["A", "B"]
    )

    summary.render().encode("cp949")  # 예외가 나면 실패


def test_summary_render_names_remaining_tickers_when_aborted():
    summary = CollectSummary(total=3, succeeded=1, aborted=True, remaining=["A", "B"])

    text = summary.render()

    assert "A" in text and "B" in text and "2" in text
