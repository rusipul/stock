"""수집 오케스트레이션. 소스·저장 계층을 조립한다. 설계서 §5.3·§13."""

from __future__ import annotations

from datetime import date, timedelta

from pydantic import BaseModel

from stockscout.sources.base import FundamentalSource, PriceSource
from stockscout.sources.ratelimit import DailyQuotaExceeded
from stockscout.store.repository import Repository


class CollectSummary(BaseModel):
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    statements: int = 0
    price_bars: int = 0
    aborted: bool = False
    remaining: list[str] = []

    def render(self) -> str:
        lines = [
            f"대상 {self.total}종목 · 성공 {self.succeeded} · 실패 {self.failed}",
            f"재무제표 {self.statements}건 · 주가 {self.price_bars}봉 저장",
        ]
        if self.aborted:
            lines.append(
                f"⚠ 일일 한도 소진으로 중단. 남은 종목 {len(self.remaining)}개: "
                f"{', '.join(self.remaining[:5])}"
                + (" …" if len(self.remaining) > 5 else "")
            )
        return "\n".join(lines)


def sync_universe(repo: Repository, universe, market: str) -> int:
    """지수 구성종목을 갱신한다. 편출된 종목은 삭제하지 않고 in_universe=0으로 둔다.

    과거 수집 데이터를 보존하기 위해서다.
    """
    fetched = universe.fetch()
    fetched_tickers = {s.ticker for s in fetched}

    for security in fetched:
        repo.upsert_security(security)

    previously = {s.ticker for s in repo.list_securities(market=market)}
    dropped = sorted(previously - fetched_tickers)
    if dropped:
        repo.mark_out_of_universe(dropped)

    return len(fetched)


def collect_market(
    repo: Repository,
    market: str,
    fundamentals: FundamentalSource,
    prices: PriceSource,
    years: list[int],
    price_start: date,
    price_end: date,
    tickers: list[str] | None = None,
    force: bool = False,
) -> CollectSummary:
    """한 시장의 재무·주가를 수집한다.

    한 종목의 실패는 그 종목에만 머문다(설계서 §13). 일일 한도 소진만이 배치를 멈춘다.
    """
    targets = [
        s for s in repo.list_securities(market=market)
        if tickers is None or s.ticker in set(tickers)
    ]
    summary = CollectSummary(total=len(targets))

    for i, security in enumerate(targets):
        ticker = security.ticker
        try:
            summary.statements += _collect_statements(
                repo, ticker, fundamentals, years, force
            )
            summary.price_bars += _collect_prices(
                repo, ticker, prices, price_start, price_end, force
            )
            summary.succeeded += 1
        except DailyQuotaExceeded as exc:
            repo.record_error(ticker, fundamentals.name, str(exc))
            summary.aborted = True
            summary.remaining = [s.ticker for s in targets[i:]]
            break
        except Exception as exc:  # 종목 단위 격리
            repo.record_error(ticker, fundamentals.name, f"{type(exc).__name__}: {exc}")
            summary.failed += 1

    return summary


def _collect_statements(
    repo: Repository,
    ticker: str,
    source: FundamentalSource,
    years: list[int],
    force: bool,
) -> int:
    if not years:
        return 0
    wanted = list(years)
    if not force:
        have = repo.existing_statement_years(ticker, source.name)
        wanted = [y for y in years if y not in have]
    if not wanted:
        return 0

    statements = source.fetch_annual(ticker, wanted)
    if statements:
        repo.save_raw_statements(statements)
    return len(statements)


def _collect_prices(
    repo: Repository,
    ticker: str,
    source: PriceSource,
    start: date,
    end: date,
    force: bool,
) -> int:
    fetch_from = start
    if not force:
        latest = repo.latest_price_date(ticker)
        if latest:
            fetch_from = latest + timedelta(days=1)
    if fetch_from > end:
        return 0

    bars = source.fetch_daily(ticker, fetch_from, end)
    if bars:
        repo.save_price_bars(bars, source=source.name)
    return len(bars)
