"""시장 비교 지수. 종목마다 같은 시장의 지수와 비교한다."""

from stockscout.benchmarks import benchmark_for, benchmarks_of
from stockscout.models import Security


def sec(ticker, market, exchange):
    return Security(ticker=ticker, market=market, name=ticker, exchange=exchange,
                    currency="KRW" if market == "KR" else "USD")


def test_each_exchange_has_its_own_index():
    assert benchmark_for(sec("005930.KS", "KR", "KOSPI")) == "^KS11"
    assert benchmark_for(sec("247540.KQ", "KR", "KOSDAQ")) == "^KQ11"
    assert benchmark_for(sec("AAPL", "US", "US")) == "^GSPC"


def test_a_market_collects_every_index_it_needs():
    assert benchmarks_of("KR") == ["^KS11", "^KQ11"]
    assert benchmarks_of("US") == ["^GSPC"]
