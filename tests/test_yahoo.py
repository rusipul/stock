"""yfinance 어댑터. 함수를 주입해 네트워크 없이 검증한다 (tests/test_krx.py와 같다)."""

from datetime import date

import pandas as pd

from stockscout.sources.yahoo import YahooPriceSource


def frame(rows):
    index = pd.DatetimeIndex([pd.Timestamp(d, tz="America/New_York") for d, _, _ in rows])
    return pd.DataFrame({"Close": [c for _, c, _ in rows],
                         "Volume": [v for _, _, v in rows]}, index=index)


def test_fetch_daily_returns_bars_in_date_order():
    calls = []

    def history(symbol, start, end):
        calls.append((symbol, start, end))
        return frame([("2026-09-09", 230.0, 100), ("2026-09-10", 231.5, 120)])

    bars = YahooPriceSource(history_fn=history).fetch_daily(
        "AAPL", date(2026, 9, 1), date(2026, 9, 10))

    assert [(b.date, b.close, b.volume) for b in bars] == [
        (date(2026, 9, 9), 230.0, 100), (date(2026, 9, 10), 231.5, 120)]
    assert all(b.ticker == "AAPL" for b in bars)


def test_end_is_inclusive():
    """yfinance의 end는 미포함이다. 하루를 더해 넘겨야 마지막 날이 들어온다."""
    calls = []

    def history(symbol, start, end):
        calls.append((start, end))
        return frame([])

    YahooPriceSource(history_fn=history).fetch_daily(
        "AAPL", date(2026, 9, 1), date(2026, 9, 10))

    assert calls == [(date(2026, 9, 1), date(2026, 9, 11))]


def test_the_date_is_the_exchange_date_not_utc():
    """뉴욕 장 마감은 UTC로 다음 날일 수 있다. 거래소 현지 날짜를 쓴다."""
    index = pd.DatetimeIndex([pd.Timestamp("2026-09-10 00:00", tz="America/New_York")])
    df = pd.DataFrame({"Close": [231.5], "Volume": [1]}, index=index)

    (bar,) = YahooPriceSource(history_fn=lambda *a: df).fetch_daily(
        "AAPL", date(2026, 9, 10), date(2026, 9, 10))

    assert bar.date == date(2026, 9, 10)


def test_missing_and_nonpositive_closes_are_skipped():
    df = frame([("2026-09-09", float("nan"), 0), ("2026-09-10", 0.0, 0),
                ("2026-09-11", 232.0, 5)])

    bars = YahooPriceSource(history_fn=lambda *a: df).fetch_daily(
        "AAPL", date(2026, 9, 9), date(2026, 9, 11))

    assert [b.close for b in bars] == [232.0]


def test_empty_frame_is_no_bars():
    assert YahooPriceSource(history_fn=lambda *a: pd.DataFrame()).fetch_daily(
        "AAPL", date(2026, 9, 1), date(2026, 9, 10)) == []


def test_source_name_is_recorded_on_raw_prices():
    assert YahooPriceSource(history_fn=lambda *a: None).name == "yfinance"



def test_open_high_low_are_kept_for_candles():
    index = pd.DatetimeIndex([pd.Timestamp("2026-09-10", tz="America/New_York")])
    df = pd.DataFrame({"Open": [229.0], "High": [233.0], "Low": [228.5],
                       "Close": [231.5], "Volume": [10]}, index=index)

    (bar,) = YahooPriceSource(history_fn=lambda *a: df).fetch_daily(
        "AAPL", date(2026, 9, 10), date(2026, 9, 10))

    assert (bar.open, bar.high, bar.low, bar.close) == (229.0, 233.0, 228.5, 231.5)


def test_missing_open_high_low_stay_empty():
    (bar,) = YahooPriceSource(history_fn=lambda *a: frame([("2026-09-10", 231.5, 1)])) \
        .fetch_daily("AAPL", date(2026, 9, 10), date(2026, 9, 10))

    assert bar.open is None and bar.high is None and bar.low is None
