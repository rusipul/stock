from datetime import date

import pandas as pd
import pytest

from stockscout.sources.krx import KrxPriceSource, KrxUniverse, to_ticker


def test_to_ticker_appends_suffix():
    assert to_ticker("005930") == "005930.KS"
    assert to_ticker("005930.KS") == "005930.KS"


def test_fetch_daily_converts_dataframe_to_price_bars():
    df = pd.DataFrame(
        {"시가": [70000, 71000], "고가": [71000, 72000], "저가": [69000, 70500],
         "종가": [70500, 71800], "거래량": [1000, 2000]},
        index=pd.to_datetime(["2026-01-05", "2026-01-06"]),
    )
    src = KrxPriceSource(ohlcv_fn=lambda *a, **k: df)

    bars = src.fetch_daily("005930.KS", date(2026, 1, 1), date(2026, 1, 31))

    assert [b.close for b in bars] == [70500.0, 71800.0]
    assert [b.date for b in bars] == [date(2026, 1, 5), date(2026, 1, 6)]
    assert [b.volume for b in bars] == [1000, 2000]
    assert all(b.ticker == "005930.KS" for b in bars)


def test_fetch_daily_returns_empty_for_empty_frame():
    src = KrxPriceSource(ohlcv_fn=lambda *a, **k: pd.DataFrame())

    assert src.fetch_daily("005930.KS", date(2026, 1, 1), date(2026, 1, 31)) == []


def test_fetch_daily_drops_rows_with_zero_close():
    """거래정지일은 종가가 0으로 온다. 평균 계산을 오염시키므로 버린다."""
    df = pd.DataFrame(
        {"종가": [70000, 0, 71000], "거래량": [10, 0, 20]},
        index=pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"]),
    )
    src = KrxPriceSource(ohlcv_fn=lambda *a, **k: df)

    bars = src.fetch_daily("005930.KS", date(2026, 1, 1), date(2026, 1, 31))

    assert [b.close for b in bars] == [70000.0, 71000.0]


def test_universe_returns_securities_with_ticker_and_name():
    uni = KrxUniverse(
        portfolio_fn=lambda idx: ["005930", "000660"],
        name_fn=lambda code: {"005930": "삼성전자", "000660": "SK하이닉스"}[code],
    )

    secs = uni.fetch()

    assert [s.ticker for s in secs] == ["005930.KS", "000660.KS"]
    assert secs[0].name == "삼성전자"
    assert secs[0].market == "KR"
    assert secs[0].currency == "KRW"
    assert secs[0].exchange == "KOSPI"


def test_universe_skips_tickers_whose_name_lookup_fails():
    def flaky_name(code):
        if code == "BAD":
            raise ValueError("no such ticker")
        return "삼성전자"

    uni = KrxUniverse(portfolio_fn=lambda idx: ["005930", "BAD"], name_fn=flaky_name)

    secs = uni.fetch()

    assert [s.ticker for s in secs] == ["005930.KS"]
