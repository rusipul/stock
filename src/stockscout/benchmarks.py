"""시장 비교 지수. 종목의 하락이 시장 전체의 하락인지 그 종목만의 하락인지 가른다.

지수 주가는 raw_prices에 지수 기호(^KS11 등)로 담는다. securities에는 넣지
않는다 - 시장 단위 조회(trading_dates, latest_price_date_any)가
securities와 조인하므로 지수 봉이 종목 계산에 섞이지 않는다.
기호는 Yahoo Finance 표기다 (sources/yahoo.py로 받는다).
"""

from __future__ import annotations

from stockscout.models import Security

KOSPI = "^KS11"
KOSDAQ = "^KQ11"
SP500 = "^GSPC"

_BY_MARKET = {"KR": [KOSPI, KOSDAQ], "US": [SP500]}


def benchmark_for(security: Security) -> str:
    """종목과 비교할 지수. 한국은 거래소별(코스피·코스닥), 미국은 S&P 500."""
    if security.market == "US":
        return SP500
    return KOSDAQ if security.exchange == "KOSDAQ" else KOSPI


def benchmarks_of(market: str) -> list[str]:
    """그 시장을 분석하려면 수집해야 하는 지수들."""
    return list(_BY_MARKET.get(market, []))
