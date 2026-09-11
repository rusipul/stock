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


def test_collect_requests_full_price_window_regardless_of_stored_data(repo):
    """C1: 가격은 항상 [start, end] 전체를 다시 요청해야 한다.

    이전에는 latest_price_date()+1일부터만 요청했다(MAX(date) 고수위선).
    pykrx는 구간 전체를 호출 한 번으로 반환하므로 그 "증분" 최적화는
    네트워크 호출을 하나도 아끼지 못하면서, 수정주가 기준이 바뀌는
    경우(액면분할 등) 옛 기준과 새 기준이 섞이는 대가만 치른다.
    이미 저장된 데이터가 있어도 start~end 전체를 다시 요청해야 한다.
    """
    repo.upsert_security(sec())
    repo.save_price_bars(
        [PriceBar(ticker="005930.KS", date=date(2026, 1, 5), close=1.0)], source="pykrx"
    )
    prices = FakePrices()

    collect_market(
        repo, "KR", FakeFundamentals(), prices,
        years=[], price_start=date(2021, 1, 1), price_end=date(2026, 1, 31),
    )

    assert prices.requested == [("005930.KS", date(2021, 1, 1), date(2026, 1, 31))]


def test_collect_price_recollect_rebases_whole_window_on_adjustment_change(repo):
    """C1 재현: 액면분할 등으로 수정주가 기준이 바뀌면, 재수집 시 저장된 전체
    구간이 새 기준으로 덮어써져야 한다. 고수위선(MAX(date)) 방식이었다면
    분할 이전 구간은 옛 기준 그대로 남아, 한 시계열에 두 기준이 섞여
    평균 대비 하락률이 조작된 값으로 계산된다.
    """
    repo.upsert_security(sec())
    old_bars = [
        PriceBar(ticker="005930.KS", date=date(2026, 1, 2), close=100000.0),
        PriceBar(ticker="005930.KS", date=date(2026, 1, 5), close=100000.0),
    ]
    collect_market(
        repo, "KR", FakeFundamentals(), FakePrices({"005930.KS": old_bars}),
        years=[], price_start=date(2026, 1, 1), price_end=date(2026, 1, 5),
    )
    assert [b.close for b in repo.get_price_bars("005930.KS")] == [100000.0, 100000.0]

    # 1:5 액면분할 후 재수집: pykrx가 같은 날짜에 대해 새 기준(1/5)의 종가를 반환한다.
    new_bars = [
        PriceBar(ticker="005930.KS", date=date(2026, 1, 2), close=20000.0),
        PriceBar(ticker="005930.KS", date=date(2026, 1, 5), close=20000.0),
    ]
    collect_market(
        repo, "KR", FakeFundamentals(), FakePrices({"005930.KS": new_bars}),
        years=[], price_start=date(2026, 1, 1), price_end=date(2026, 1, 5),
    )

    assert [b.close for b in repo.get_price_bars("005930.KS")] == [20000.0, 20000.0]


def test_collect_widened_price_window_backfills_earlier_dates(repo):
    """C1 재현: settings.yaml의 price_years를 넓혀([1,3] → [1,3,5]) 더 이른
    price_start로 재수집하면, 이전에 못 받은 구간도 실제로 채워져야 한다.
    고수위선 방식이었다면 MAX(date)가 이미 최신이라 아무것도 요청되지 않아
    avg_price_5y가 영구히 3년치 데이터로만 계산된다.
    """
    repo.upsert_security(sec())
    prices_first = FakePrices({"005930.KS": [
        PriceBar(ticker="005930.KS", date=date(2024, 1, 2), close=50000.0),
    ]})
    collect_market(
        repo, "KR", FakeFundamentals(), prices_first,
        years=[], price_start=date(2023, 1, 1), price_end=date(2026, 1, 31),
    )
    assert len(repo.get_price_bars("005930.KS")) == 1

    prices_second = FakePrices({"005930.KS": [
        PriceBar(ticker="005930.KS", date=date(2021, 1, 4), close=40000.0),
        PriceBar(ticker="005930.KS", date=date(2024, 1, 2), close=50000.0),
    ]})
    collect_market(
        repo, "KR", FakeFundamentals(), prices_second,
        years=[], price_start=date(2021, 1, 1), price_end=date(2026, 1, 31),
    )

    assert prices_second.requested == [("005930.KS", date(2021, 1, 1), date(2026, 1, 31))]
    stored = repo.get_price_bars("005930.KS")
    assert len(stored) == 2
    assert stored[0].date == date(2021, 1, 4)


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


def test_collect_fundamentals_failure_still_collects_prices(repo):
    """C2 재현: DART 실패(예: corp_code를 찾을 수 없는 종목)가 나도 pykrx는
    독립된 소스이므로 같은 종목의 주가 수집은 그대로 시도되어야 한다.
    실제로 매 실행마다 이런 식으로 정상 실패하는 종목이 ~10개 있고,
    DART 실패는 영구적이라 재실행해도 회복되지 않으므로, 한 try에
    묶여 있으면 그 종목들은 5년치 주가를 영영 잃는다.
    """
    repo.upsert_security(sec())
    fundamentals = FakeFundamentals(
        failures={"005930.KS": PermanentSourceError("corp_code를 찾을 수 없습니다")}
    )
    prices = FakePrices({"005930.KS": [
        PriceBar(ticker="005930.KS", date=date(2026, 1, 5), close=70000.0)
    ]})

    summary = collect_market(
        repo, "KR", fundamentals, prices,
        years=[2025], price_start=date(2021, 1, 1), price_end=date(2026, 1, 31),
    )

    assert prices.requested == [("005930.KS", date(2021, 1, 1), date(2026, 1, 31))]
    assert len(repo.get_price_bars("005930.KS")) == 1
    assert summary.total == 1
    assert summary.failed == 1
    assert summary.succeeded == 0


def test_collect_both_substeps_failing_counts_ticker_once(repo):
    """C2: 재무·주가 두 하위 단계가 모두 실패해도 종목은 정확히 한 번만
    failed로 집계되어야 한다 (succeeded와 failed 양쪽에 잡히거나 두 번
    잡히면 안 된다)."""
    repo.upsert_security(sec())
    fundamentals = FakeFundamentals(
        failures={"005930.KS": PermanentSourceError("corp_code 없음")}
    )
    prices = FakePrices(failures={"005930.KS": PermanentSourceError("가격 없음")})

    summary = collect_market(
        repo, "KR", fundamentals, prices,
        years=[2025], price_start=date(2021, 1, 1), price_end=date(2026, 1, 31),
    )

    assert summary.total == 1
    assert summary.failed == 1
    assert summary.succeeded == 0
    assert len(repo.recent_errors()) == 2  # 하위 단계별로 각각 기록되지만 종목 카운트는 1회


def test_collect_price_failure_records_error_with_price_source_name(repo):
    """I1 재현: pykrx 실패는 source='pykrx'로 기록되어야 한다.
    이전에는 두 record_error 호출 모두 fundamentals.name을 넘겨서,
    가격 소스 실패조차 source='dart'(또는 --skip-financials일 때는
    'skipped')로 잘못 기록됐다."""
    repo.upsert_security(sec())
    prices = FakePrices(failures={"005930.KS": PermanentSourceError("가격 없음")})

    collect_market(
        repo, "KR", FakeFundamentals(), prices,
        years=[], price_start=date(2021, 1, 1), price_end=date(2026, 1, 31),
    )

    errors = repo.recent_errors()
    assert errors[0]["ticker"] == "005930.KS"
    assert errors[0]["source"] == "pykrx"


def test_collect_fundamentals_failure_records_error_with_fundamentals_source_name(repo):
    """I1: DART 실패는 source='dart'로 기록되어야 한다."""
    repo.upsert_security(sec())
    fundamentals = FakeFundamentals(
        failures={"005930.KS": PermanentSourceError("corp_code 없음")}
    )

    collect_market(
        repo, "KR", fundamentals, FakePrices(),
        years=[2025], price_start=date(2021, 1, 1), price_end=date(2026, 1, 31),
    )

    errors = repo.recent_errors()
    assert errors[0]["ticker"] == "005930.KS"
    assert errors[0]["source"] == "dart"


def test_collect_price_quota_exceeded_aborts_batch_including_current_ticker(repo):
    """C2/설계서 §13: 일일 한도 소진은 재무·주가 어느 하위 단계에서
    터지든 배치를 멈춰야 하고, remaining은 한도를 소진시킨 현재 종목부터
    포함해야 한다 (그 종목은 아직 수집이 끝나지 않았으므로).
    """
    # list_securities()는 ticker 알파벳순: 000660 < 005930 < 035420.
    repo.upsert_security(sec("005930.KS"))
    repo.upsert_security(sec("000660.KS", "SK하이닉스"))
    repo.upsert_security(sec("035420.KS", "NAVER"))
    prices = FakePrices(
        failures={"005930.KS": DailyQuotaExceeded("일일 한도 소진")}
    )

    summary = collect_market(
        repo, "KR", FakeFundamentals(), prices,
        years=[], price_start=date(2021, 1, 1), price_end=date(2026, 1, 31),
    )

    assert summary.aborted is True
    assert summary.remaining == ["005930.KS", "035420.KS"]


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
