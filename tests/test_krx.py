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


# --- 결함 3: 수정주가는 상류 기본값이 아니라 명시적으로 요구한다 ----------
#
# 설계서 §9.1은 수정주가를 필수로 요구한다 — 2018년 삼성전자 50:1
# 액면분할처럼, 수정 없는 종가로 5년 평균을 구하면 분할 이전 가격이
# 그대로 섞여 하락률이 통째로 틀어진다. pyproject.toml이 pykrx를
# 상한 없이(>=1.0.45) 고정하고 있어, 상류가 adjusted의 기본값을
# 뒤집으면 이 스크리너의 존재 이유가 조용히 깨진다.
#
# KrxPriceSource.fetch_daily에 주입하는 ohlcv_fn으로는 이걸 검증할 수
# 없다 — adjusted=True는 _default_ohlcv_fn 내부에서 고정하는 것이지
# fetch_daily가 호출부에서 넘기는 인자가 아니다. 그래서 pykrx.stock의
# 실제 함수를 몽키패치해 _default_ohlcv_fn을 직접 호출한다.


def test_default_ohlcv_fn_requests_adjusted_prices(monkeypatch):
    import pykrx.stock as pykrx_stock

    from stockscout.sources.krx import _default_ohlcv_fn

    captured = {}

    def fake_get_market_ohlcv(fromdate, todate, ticker, **kwargs):
        captured["args"] = (fromdate, todate, ticker)
        captured["kwargs"] = kwargs
        return pd.DataFrame()

    monkeypatch.setattr(pykrx_stock, "get_market_ohlcv", fake_get_market_ohlcv)

    _default_ohlcv_fn("20260101", "20260131", "005930")

    assert captured["args"] == ("20260101", "20260131", "005930")
    assert captured["kwargs"].get("adjusted") is True


def test_universe_skips_tickers_whose_name_lookup_fails():
    def flaky_name(code):
        if code == "BAD":
            raise ValueError("no such ticker")
        return "삼성전자"

    uni = KrxUniverse(portfolio_fn=lambda idx: ["005930", "BAD"], name_fn=flaky_name)

    secs = uni.fetch()

    assert [s.ticker for s in secs] == ["005930.KS"]
