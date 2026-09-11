"""pykrx 어댑터. KRX 공개데이터에서 수정주가와 지수 구성종목을 받는다.

pykrx 함수를 주입받아 테스트에서 네트워크 없이 검증할 수 있게 한다.
"""

from __future__ import annotations

from datetime import date
from typing import Callable

import pandas as pd

from stockscout.models import PriceBar, Security

KOSPI200_INDEX_CODE = "1028"
CLOSE_COLUMN = "종가"
VOLUME_COLUMN = "거래량"


def to_ticker(stock_code: str) -> str:
    """'005930' → '005930.KS'. 이미 접미사가 있으면 그대로 둔다."""
    return stock_code if "." in stock_code else f"{stock_code}.KS"


def _default_ohlcv_fn(fromdate: str, todate: str, ticker: str) -> pd.DataFrame:
    from pykrx import stock

    # adjusted=True는 지금 이 버전(pykrx 1.2.8)의 기본값과 같지만, 그
    # 기본값에 기대지 않고 명시한다. 설계서 §9.1은 수정주가를 필수로
    # 요구한다 — 2018년 삼성전자 50:1 액면분할처럼, 수정 없는 종가로
    # 5년 평균을 구하면 분할 이전 가격이 그대로 섞여 하락률이 통째로
    # 틀어진다. pyproject.toml이 pykrx를 상한 없이(>=1.0.45) 고정하므로,
    # 상류가 언젠가 기본값을 뒤집으면 이 스크리너가 존재하는 이유 자체가
    # 조용히 깨진다. 이 한 줄이 그 실패 모드를 막는다.
    return stock.get_market_ohlcv(fromdate, todate, ticker, adjusted=True)


class KrxPriceSource:
    """PriceSource 프로토콜 구현."""

    name = "pykrx"

    def __init__(self, ohlcv_fn: Callable[..., pd.DataFrame] | None = None) -> None:
        self._ohlcv = ohlcv_fn or _default_ohlcv_fn

    def fetch_daily(self, ticker: str, start: date, end: date) -> list[PriceBar]:
        code = ticker.split(".")[0]
        df = self._ohlcv(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), code)
        if df is None or df.empty:
            return []

        bars: list[PriceBar] = []
        for idx, row in df.iterrows():
            close = float(row[CLOSE_COLUMN])
            if close <= 0:
                continue  # 거래정지일. 평균을 오염시킨다
            volume = row.get(VOLUME_COLUMN)
            bars.append(
                PriceBar(
                    ticker=ticker,
                    date=pd.Timestamp(idx).date(),
                    close=close,
                    volume=int(volume) if volume is not None and not pd.isna(volume) else None,
                )
            )
        return bars


def _default_portfolio_fn(index_code: str) -> list[str]:
    from pykrx import stock

    return stock.get_index_portfolio_deposit_file(index_code)


def _default_name_fn(stock_code: str) -> str:
    from pykrx import stock

    return stock.get_market_ticker_name(stock_code)


class KrxUniverse:
    """KOSPI 200 구성종목 목록."""

    def __init__(
        self,
        index_code: str = KOSPI200_INDEX_CODE,
        portfolio_fn: Callable[[str], list[str]] | None = None,
        name_fn: Callable[[str], str] | None = None,
    ) -> None:
        self.index_code = index_code
        self._portfolio = portfolio_fn or _default_portfolio_fn
        self._name = name_fn or _default_name_fn

    def fetch(self) -> list[Security]:
        securities: list[Security] = []
        for code in self._portfolio(self.index_code):
            try:
                name = self._name(code)
            except Exception:
                continue  # 종목명 조회 실패는 그 종목만 건너뛴다
            securities.append(
                Security(
                    ticker=to_ticker(code),
                    market="KR",
                    name=name,
                    exchange="KOSPI",
                    currency="KRW",
                    in_universe=True,
                )
            )
        return securities
