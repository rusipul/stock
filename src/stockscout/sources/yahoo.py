"""yfinance 어댑터. 미국 주식의 수정종가를 받는다.

yfinance 함수를 주입받아 테스트에서 네트워크 없이 검증할 수 있게 한다
(sources/krx.py와 같은 모양이다).
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Callable

import pandas as pd

from stockscout.models import PriceBar
from stockscout.sources.krx import _ohlc

CLOSE_COLUMN = "Close"
VOLUME_COLUMN = "Volume"


def _default_history_fn(symbol: str, start: date, end_exclusive: date) -> pd.DataFrame:
    import yfinance as yf

    # auto_adjust=True를 명시한다. 설계서 §9.1은 수정주가를 필수로 요구하고,
    # 기본값에 기대면 상류가 그것을 뒤집는 날 하락률이 조용히 틀어진다
    # (sources/krx.py의 adjusted=True와 같은 이유).
    return yf.Ticker(symbol).history(
        start=start.isoformat(), end=end_exclusive.isoformat(),
        auto_adjust=True, actions=False,
    )


class YahooPriceSource:
    """PriceSource 프로토콜 구현."""

    name = "yfinance"

    def __init__(
        self, history_fn: Callable[[str, date, date], pd.DataFrame] | None = None
    ) -> None:
        self._history = history_fn or _default_history_fn

    def fetch_daily(self, ticker: str, start: date, end: date) -> list[PriceBar]:
        # yfinance의 end는 미포함이다. 하루를 더해 마지막 날까지 받는다.
        df = self._history(ticker, start, end + timedelta(days=1))
        if df is None or df.empty:
            return []

        bars: list[PriceBar] = []
        for idx, row in df.sort_index().iterrows():
            close = float(row[CLOSE_COLUMN])
            if math.isnan(close) or close <= 0:
                continue  # 거래정지·결측. 평균을 오염시킨다
            volume = row.get(VOLUME_COLUMN)
            bars.append(PriceBar(
                ticker=ticker,
                # 인덱스는 거래소 시간대(America/New_York)다. UTC로 바꾸지 않고
                # 그 현지 날짜를 쓴다.
                date=pd.Timestamp(idx).date(),
                close=close,
                volume=int(volume) if volume is not None and not pd.isna(volume) else None,
                **_ohlc(row, "Open", "High", "Low"),
            ))
        return bars
