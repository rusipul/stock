"""수집 오케스트레이션. 소스·저장 계층을 조립한다. 설계서 §5.3·§13."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel

from stockscout.sources.base import FatalSourceError, FundamentalSource, PriceSource
from stockscout.sources.dart import SOURCE_SHARES
from stockscout.sources.ratelimit import DailyQuotaExceeded
from stockscout.store.repository import Repository

# 배치를 통째로 멈추는 예외. DailyQuotaExceeded(일일 한도 소진)와
# FatalSourceError(등록되지 않은 키 등, 남은 모든 종목이 똑같이 실패할 오류)
# 둘 다 "이 종목만 격리하고 계속 진행"이 아니라 "원인 하나만 보고하고 중단"이
# 맞다 — 아래에서 같은 분기로 묶어 처리한다.
ABORTING_ERRORS = (DailyQuotaExceeded, FatalSourceError)


class CollectSummary(BaseModel):
    """수집 결과 요약.

    render()는 ASCII 기호만 쓴다. 한국어 Windows 콘솔이 cp949라서
    U+26A0(경고 표시) 같은 문자를 출력하면 UnicodeEncodeError로 죽는다.
    하필 일일 한도 소진처럼 "남은 종목이 무엇인가"를 가장 알아야 할
    순간에 크래시하므로, 기호를 쓰지 않는다.
    """
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    statements: int = 0
    shares: int = 0
    price_bars: int = 0
    aborted: bool = False
    abort_reason: str | None = None
    remaining: list[str] = []

    def render(self) -> str:
        lines = [
            f"대상 {self.total}종목 · 성공 {self.succeeded} · 실패 {self.failed}",
            f"재무제표 {self.statements}건 · 주식수 {self.shares}건 "
            f"· 주가 {self.price_bars}봉 저장",
        ]
        if self.aborted:
            # 중단 사유는 늘 일일 한도 소진이 아니다 (예: 등록되지 않은 API
            # 키). abort_reason이 없으면(방어적 기본값) 그렇게만 말한다.
            reason = self.abort_reason or "알 수 없는 이유"
            lines.append(
                f"[중단] {reason}. 남은 종목 {len(self.remaining)}개: "
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

    한 종목의 실패는 그 종목에만 머문다(설계서 §13). 배치를 멈추는 것은
    ABORTING_ERRORS뿐이다 — 일일 한도 소진(DailyQuotaExceeded)과, 남은
    모든 종목이 똑같이 실패할 치명적 오류(FatalSourceError, 예: 등록되지
    않은 DART API 키)다. 둘 다 "이 종목만"이 아니라 "무엇을 시도해도
    똑같다"는 신호라서, 종목 단위로 격리해봐야 같은 오류를 반복 기록할
    뿐이다.
    """
    targets = [
        s for s in repo.list_securities(market=market)
        if tickers is None or s.ticker in set(tickers)
    ]
    summary = CollectSummary(total=len(targets))

    for i, security in enumerate(targets):
        ticker = security.ticker
        aborted = False
        ticker_failed = False

        try:
            summary.statements += _collect_statements(
                repo, ticker, fundamentals, years, force
            )
        except ABORTING_ERRORS as exc:
            repo.record_error(ticker, fundamentals.name, str(exc))
            summary.abort_reason = str(exc)
            aborted = True
        except Exception as exc:  # 종목 단위 격리 (설계서 §13)
            repo.record_error(ticker, fundamentals.name, f"{type(exc).__name__}: {exc}")
            ticker_failed = True

        # 재무제표와 별개의 try. 주식수 수집이 실패해도 재무제표와 주가는
        # 남아야 한다 — 계획 A의 결함 C2가 정확히 이 실수였다(두 수집을 한
        # try에 묶었더니 DART 실패가 주가 5년치를 통째로 날렸다).
        if not aborted:
            try:
                summary.shares += _collect_optional(
                    repo, ticker, fundamentals, "fetch_shares",
                    SOURCE_SHARES, years, force,
                )
            except ABORTING_ERRORS as exc:
                repo.record_error(ticker, SOURCE_SHARES, str(exc))
                summary.abort_reason = str(exc)
                aborted = True
            except Exception as exc:  # 종목 단위 격리 (설계서 §13)
                repo.record_error(ticker, SOURCE_SHARES,
                                  f"{type(exc).__name__}: {exc}")
                ticker_failed = True

        # 재무제표 수집과 별개의 try. DART(fundamentals)가 실패해도
        # pykrx(prices)는 독립 소스이므로 계속 시도해야 한다 — 그렇지 않으면
        # "corp_code를 찾을 수 없습니다" 같은 정상적으로 발생하는 DART 실패가
        # 매 실행마다 여러 종목의 주가 5년치를 통째로 날려버린다 (설계서 §13, 결함 C2).
        if not aborted:
            try:
                summary.price_bars += _collect_prices(
                    repo, ticker, prices, price_start, price_end
                )
            except ABORTING_ERRORS as exc:
                repo.record_error(ticker, prices.name, str(exc))
                summary.abort_reason = str(exc)
                aborted = True
            except Exception as exc:  # 종목 단위 격리 (설계서 §13)
                repo.record_error(ticker, prices.name, f"{type(exc).__name__}: {exc}")
                ticker_failed = True

        if aborted:
            summary.aborted = True
            summary.remaining = [s.ticker for s in targets[i:]]
            break

        if ticker_failed:
            summary.failed += 1
        else:
            summary.succeeded += 1

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


def _collect_optional(
    repo: Repository,
    ticker: str,
    source: FundamentalSource,
    method: str,
    raw_source: str,
    years: list[int],
    force: bool,
) -> int:
    """소스가 선택적으로 제공하는 공시를 수집한다 (주식수·감사의견).

    메서드가 없으면 0을 돌려준다. DART에만 있는 엔드포인트라서, 앞으로
    붙일 EDGAR 소스는 이 메서드를 갖지 않는다 — 프로토콜을 넓혀 모든
    소스가 빈 구현을 갖게 하는 것보다 낫다.
    """
    fetch = getattr(source, method, None)
    if fetch is None:
        return 0
    if not years:
        return 0
    wanted = list(years)
    if not force:
        have = repo.existing_statement_years(ticker, raw_source)
        wanted = [y for y in years if y not in have]
    if not wanted:
        return 0

    statements = fetch(ticker, wanted)
    if statements:
        repo.save_raw_statements(statements)
    return len(statements)


def _collect_prices(
    repo: Repository,
    ticker: str,
    source: PriceSource,
    start: date,
    end: date,
) -> int:
    """[start, end] 전체 구간을 매번 다시 요청한다. 절대로 재무제표처럼
    증분(고수위선) 수집으로 "최적화"하지 말 것 — 다음 사람에게 남기는 메모.

    DART(재무제표)와 달리 pykrx(주가)는 회계연도별로 호출을 나누지 않는다.
    구간 전체를 호출 한 번으로 반환하므로, latest_price_date() 이후만
    요청하는 증분 수집은 네트워크 호출을 단 한 건도 절약하지 못한다.
    절약 없이 대가만 있다:

    1. pykrx는 "수정종가"를 반환한다. 액면분할 등 기업 행위가 일어나면
       과거 전체 구간이 새 기준으로 재계산되어 나온다. 이미 저장된
       구간을 건너뛰면, 분할 이전 구간은 옛 기준에 남고 이후 구간만
       새 기준이 되어 한 시계열에 두 조정 기준이 섞인다(설계서 §5.3
       결함 C1). 예: 저장된 [100000×5, 20000×5] → 현재가 20,000,
       평균 60,000으로 계산되어 하락률이 -66.7%로 조작된다.
    2. settings.yaml의 price_years를 넓혀(예: [1,3] → [1,3,5]) 더 이른
       price_start로 재수집해도, 고수위선은 이미 최신이라 아무것도
       재요청되지 않아 avg_price_5y가 영구히 3년치로만 계산된다.

    save_price_bars()가 (ticker, date) upsert이므로 전체 재요청은
    멱등하다 — 재실행 때마다 저장된 구간이 "현재" 조정 기준으로
    다시 맞춰진다는 뜻이라 오히려 바람직하다.

    latest_price_date()는 여기서 쓰지 않을 뿐 Repository에는 남겨둔다.
    데이터 최신성 조회 등 다른 용도로 여전히 유효하다.
    """
    if start > end:
        return 0

    bars = source.fetch_daily(ticker, start, end)
    if bars:
        repo.save_price_bars(bars, source=source.name)
    return len(bars)
