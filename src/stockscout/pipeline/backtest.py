"""신호 검증(백테스트). 과거 시점마다 그때 보였던 데이터로 신호를 내고 이후 성과를 잰다.

목적은 "3개월 고점 대비 -25% + 우량주 + 하락 멈춤"이라는 지금 기준이
반등을 실제로 잡는지, 아무 조건 없이 산 것보다 나은지를 보는 것이다.
기준값(-25%, 10거래일, 20일선)은 근거 없이 정한 출발점이라, 이 도구 없이
기준을 고치는 것은 감으로 고치는 것이다.

look-ahead를 막는 규칙:
- 주가: 평가일까지의 봉만 쓴다
- 재무: 그 평가일에 공시돼 있었을 회계연도만 쓴다 (available_financials)
- 성과: 평가일 종가에 사서 N거래일 뒤 종가에 판다고 본다

이 결과를 그대로 믿으면 안 되는 이유는 render()가 함께 말한다.
"""

from __future__ import annotations

import bisect
from datetime import date
from statistics import mean, median

from pydantic import BaseModel

from stockscout.analysis.breakout import (
    SCAN_DAYS,
    SIGNAL_BREAKOUT,
    SIGNAL_NEAR,
    breakout_signal,
    compute_breakouts,
)
from stockscout.analysis.filters import apply_financial_filters
from stockscout.analysis.grading import GRADE_NONE, grade_signal, weak_axes
from stockscout.analysis.scoring import score_security
from stockscout.analysis.valuation import compute_drawdowns, compute_price_stats
from stockscout.benchmarks import benchmark_for
from stockscout.config import Config
from stockscout.models import Financials, PriceBar
from stockscout.store.repository import Repository

HORIZONS = (20, 60, 120)        # 거래일
ADVERSE_HORIZON = 60            # 최대 추가 하락을 재는 기간
WARMUP_MONTHS_DAYS = 365        # 12개월 하락 계산에 필요한 앞쪽 데이터 (달력일)
ROUND_TRIP_COST = 0.003         # 왕복 거래비용 가정 (수수료 + 세금 + 슬리피지)

GROUP_STRONG = "STRONG"
GROUP_WATCH = "WATCH"
GROUP_NOT_QUALITY = "NOT_QUALITY"   # 급락했지만 우량주가 아니다
GROUP_BREAKOUT = "BREAKOUT"            # 영탁센스: 거래량을 동반한 신고가 돌파
GROUP_BREAKOUT_THIN = "BREAKOUT_THIN"  # 신고가는 넘었지만 거래량이 모자랐다
GROUP_NEAR = "NEAR"
GROUP_LABELS = {
    GROUP_STRONG: "★★ 하락 멈춤",
    GROUP_WATCH: "★ 하락 중",
    GROUP_NOT_QUALITY: "급락·비우량",
    GROUP_BREAKOUT: "돌파(거래량 동반)",
    GROUP_BREAKOUT_THIN: "돌파(거래량 미달)",
    GROUP_NEAR: "신고가 임박",
}


def available_financials(financials: list[Financials], on: date) -> list[Financials]:
    """on 시점에 공시돼 있었을 회계연도. 회계연도 Y는 Y+1년 4월 1일부터 쓴다.

    사업보고서(한국)와 10-K(미국 대형사) 제출 기한이 결산 후 약 90일이다.
    12월 결산 기준이라 그보다 일찍 결산하는 회사(애플 9월, 월마트 1월)는
    실제보다 늦게 쓰게 된다 - 보수적인 쪽(미래 정보를 쓰지 않는 쪽)으로 틀린다.
    """
    last = on.year - 1 if on >= date(on.year, 4, 1) else on.year - 2
    return [f for f in financials if f.fiscal_year <= last]


class Event(BaseModel):
    ticker: str
    date: date
    grade: str
    drop: float
    returns: dict[int, float]        # 거래일 -> 수익률
    excess: dict[int, float | None]  # 거래일 -> 지수 대비 초과수익
    max_adverse: float               # ADVERSE_HORIZON 안 최저점 수익률 (0 이하)


class GroupStats(BaseModel):
    n: int
    mean: dict[int, float]
    median: dict[int, float]
    win_rate: dict[int, float]
    mean_excess: dict[int, float | None]
    mean_adverse: float


class BacktestReport(BaseModel):
    market: str
    start: date | None = None
    end: date | None = None
    securities: int = 0
    events: list[Event] = []
    baseline: GroupStats | None = None   # 아무 조건 없이 산 경우
    cost: float = ROUND_TRIP_COST

    def groups(self) -> dict[str, GroupStats]:
        out = {}
        for key in GROUP_LABELS:
            rows = [e for e in self.events if e.grade == key]
            if rows:
                out[key] = _stats(rows)
        return out

    def render(self) -> str:
        """ASCII와 한글만 쓴다 (콘솔 cp949). 별은 cp949에 있다."""
        lines = [
            f"[{self.market}] {self.start} ~ {self.end} · {self.securities}종목 · "
            f"신호 {len(self.events)}건",
            "",
            f"{'구분':<14}{'건수':>6}" + "".join(
                f"{f'{h}일 평균':>10}{'승률':>7}{'초과':>8}" for h in HORIZONS)
            + f"{'최대하락':>9}",
        ]
        rows = list(self.groups().items())
        if self.baseline:
            rows.append(("BASELINE", self.baseline))
        for key, g in rows:
            label = GROUP_LABELS.get(key, "조건 없음(전체)")
            cells = "".join(
                f"{g.mean[h] * 100:9.1f}%{g.win_rate[h] * 100:6.0f}%"
                + (f"{g.mean_excess[h] * 100:7.1f}%" if g.mean_excess[h] is not None
                   else f"{'-':>8}")
                for h in HORIZONS)
            lines.append(f"{label:<14}{g.n:>6}{cells}{g.mean_adverse * 100:8.1f}%")
        if not self.events:
            lines.append("(신호 없음)")
        lines += [
            "",
            "읽을 때 주의:",
            "- 생존자 편향: 유니버스가 '지금' 남아 있는 대형주다. 그동안 상장폐지되거나",
            "  밀려난 종목이 빠져 있어 성과가 실제보다 좋게 나온다",
            f"- 거래비용: 수익률은 비용 전이다. 왕복 {self.cost * 100:.1f}%를 빼고 보라"
            " (단기 반전 전략은 비용에 민감하다)",
            "- 재무 공시일은 회계연도+1년 4월 1일로 근사했다. 상장 5년 요건은 데이터가",
            "  5년뿐이라 적용하지 않았다",
            "- 겹치는 기간: 같은 시장 하락에 여러 종목이 동시에 신호를 내면 서로 독립이",
            "  아니다. 건수가 많아 보여도 실제 독립 사례는 더 적다",
        ]
        return "\n".join(lines)


def _stats(rows: list) -> GroupStats:
    def col(h):
        return [r.returns[h] for r in rows]

    def excess(h):
        vals = [r.excess[h] for r in rows if r.excess.get(h) is not None]
        return mean(vals) if vals else None

    return GroupStats(
        n=len(rows),
        mean={h: mean(col(h)) for h in HORIZONS},
        median={h: median(col(h)) for h in HORIZONS},
        win_rate={h: sum(1 for v in col(h) if v > 0) / len(rows) for h in HORIZONS},
        mean_excess={h: excess(h) for h in HORIZONS},
        mean_adverse=mean(r.max_adverse for r in rows),
    )


class _Series:
    """한 종목의 종가열. 날짜로 '그날까지의 마지막 봉'을 빠르게 찾는다."""

    def __init__(self, bars: list[PriceBar]):
        self.bars = sorted(bars, key=lambda b: b.date)
        self.dates = [b.date for b in self.bars]

    def upto(self, d: date) -> int:
        """d 이하인 마지막 봉의 위치 + 1 (bars[:k]가 d까지의 봉)."""
        return bisect.bisect_right(self.dates, d)

    def close_on(self, d: date) -> float | None:
        k = self.upto(d)
        return self.bars[k - 1].close if k else None


def backtest_market(
    repo: Repository,
    market: str,
    config: Config,
    step_days: int = 5,
    cost: float = ROUND_TRIP_COST,
) -> BacktestReport:
    signal = config.signal
    months = signal.drawdown_months
    securities = repo.list_securities(market=market)
    report = BacktestReport(market=market, securities=len(securities), cost=cost)

    series = {s.ticker: _Series(repo.get_price_bars(s.ticker)) for s in securities}
    series = {t: s for t, s in series.items() if s.bars}
    if not series:
        return report
    first = min(s.dates[0] for s in series.values())
    last = max(s.dates[-1] for s in series.values())
    calendar = repo.trading_dates(first, last, market=market)

    # 평가일: 앞쪽 12개월(하락 계산용)과 뒤쪽 120거래일(성과용)을 남긴다.
    eligible = [i for i, d in enumerate(calendar)
                if (d - first).days >= WARMUP_MONTHS_DAYS
                and i + max(HORIZONS) < len(calendar)]
    if not eligible:
        return report
    eval_idx = eligible[::step_days]
    report.start, report.end = calendar[eval_idx[0]], calendar[eval_idx[-1]]

    financials = {s.ticker: repo.get_financials(s.ticker) for s in securities}
    index_series: dict[str, _Series] = {}
    baseline: list[Event] = []

    for security in securities:
        s = series.get(security.ticker)
        if s is None:
            continue
        bench = benchmark_for(security)
        if bench not in index_series:
            index_series[bench] = _Series(repo.get_price_bars(bench))
        idx = index_series[bench]
        # 한 번의 하락(후보 조건에 들어가서 벗어날 때까지) 안에서 등급마다
        # 첫 등장만 센다. 화면에 별이 처음 뜬 날 산 경우를 재는 것이다 -
        # 같은 하락을 매주 다시 세면 한 사례가 수십 건으로 부풀어 오른다.
        seen_in_episode: set[str] = set()

        for i in eval_idx:
            t = calendar[i]
            k = s.upto(t)
            if k == 0 or s.dates[k - 1] < calendar[max(i - 5, 0)]:
                continue   # 그날 근처에 봉이 없다 (거래정지·상장 전)
            outcome = _outcome(s, idx, calendar, i)
            if outcome is None:
                continue
            baseline.append(outcome.model_copy(update={"ticker": security.ticker,
                                                       "grade": "BASELINE"}))

            dd = compute_drawdowns(s.bars[:k], as_of=t, months=[months],
                                   stable_days=signal.stable_days,
                                   ma_days=signal.ma_days).get(months)
            if dd is None or dd.drop_from_high > signal.drawdown_threshold:
                seen_in_episode.clear()   # 조건을 벗어났다 - 다음 하락은 새 사례다
                continue

            group = _quality_group(security, s.bars[:k], t, financials[security.ticker],
                                   dd, config)
            if group in seen_in_episode:
                continue
            seen_in_episode.add(group)
            report.events.append(outcome.model_copy(update={
                "ticker": security.ticker, "grade": group, "drop": dd.drop_from_high}))

    report.baseline = _stats(baseline) if baseline else None
    return report


def _quality_group(security, bars, t, all_financials, dd, config: Config) -> str:
    """그 시점의 재무로 우량주 여부를 보고 등급 그룹을 낸다."""
    fins = available_financials(all_financials, t)
    hard = config.hard_filters_for(security.market)
    if security.is_financial or apply_financial_filters(fins, hard).failures:
        return GROUP_NOT_QUALITY
    card = score_security(fins, config.scoring)
    stats = compute_price_stats(bars, as_of=t, windows=[1], calendar=[],
                                min_coverage=0.0)
    stats.drawdown = {config.signal.drawdown_months: dd}
    grade, _ = grade_signal(stats, score_total=card.total, passed_filters=True,
                            config=config.signal, card=card)
    if grade == GRADE_NONE or weak_axes(card, config.signal.min_axis_ratio):
        return GROUP_NOT_QUALITY
    return grade


def _outcome(s: _Series, idx: _Series, calendar: list[date], i: int) -> Event | None:
    t = calendar[i]
    entry = s.close_on(t)
    if not entry:
        return None
    returns, excess = {}, {}
    i_entry = idx.close_on(t)
    for h in HORIZONS:
        exit_ = s.close_on(calendar[i + h])
        if exit_ is None:
            return None
        returns[h] = exit_ / entry - 1
        i_exit = idx.close_on(calendar[i + h])
        excess[h] = (returns[h] - (i_exit / i_entry - 1)) if i_entry and i_exit else None
    window = s.bars[s.upto(t):s.upto(calendar[i + ADVERSE_HORIZON])]
    low = min((b.close for b in window), default=entry)
    return Event(ticker="", date=t, grade="", drop=0.0, returns=returns,
                 excess=excess, max_adverse=min(low / entry - 1, 0.0))



def backtest_breakouts(
    repo: Repository,
    market: str,
    config: Config,
    step_days: int = 5,
    cost: float = ROUND_TRIP_COST,
) -> BacktestReport:
    """영탁센스 검증: 과거 시점마다 신고가 돌파·임박을 판정하고 이후 성과를 잰다.

    거래량 조건의 값을 보려고 신고가는 넘었지만 거래량이 모자란 돌파도 따로
    센다. 한 번의 신호(조건에 들어가서 벗어날 때까지) 안에서 그룹마다 첫
    등장만 센다. 위험 공시·자금 여력은 과거 시점 데이터가 없어 보지 않는다.
    """
    bc = config.breakout
    securities = repo.list_securities(market=market)
    report = BacktestReport(market=market, securities=len(securities), cost=cost)
    series = {s.ticker: _Series(repo.get_price_bars(s.ticker)) for s in securities}
    series = {t: s for t, s in series.items() if s.bars}
    if not series:
        return report
    first = min(s.dates[0] for s in series.values())
    last = max(s.dates[-1] for s in series.values())
    calendar = repo.trading_dates(first, last, market=market)
    eligible = [i for i, d in enumerate(calendar)
                if (d - first).days >= WARMUP_MONTHS_DAYS
                and i + max(HORIZONS) < len(calendar)]
    if not eligible:
        return report
    eval_idx = eligible[::step_days]
    report.start, report.end = calendar[eval_idx[0]], calendar[eval_idx[-1]]

    index_series: dict[str, _Series] = {}
    baseline: list[Event] = []
    window = bc.lookback + SCAN_DAYS + 25   # 판정에 필요한 만큼만 잘라 계산한다
    for security in securities:
        s = series.get(security.ticker)
        if s is None:
            continue
        bench = benchmark_for(security)
        if bench not in index_series:
            index_series[bench] = _Series(repo.get_price_bars(bench))
        idx = index_series[bench]
        seen: set[str] = set()
        for i in eval_idx:
            t = calendar[i]
            k = s.upto(t)
            if k == 0 or s.dates[k - 1] < calendar[max(i - 5, 0)]:
                continue
            outcome = _outcome(s, idx, calendar, i)
            if outcome is None:
                continue
            baseline.append(outcome.model_copy(update={"ticker": security.ticker,
                                                       "grade": "BASELINE"}))
            stat = compute_breakouts(s.bars[max(0, k - window):k], as_of=t,
                                     lookbacks=[bc.lookback]).get(bc.lookback)
            group = None
            if stat is not None:
                sig = breakout_signal(stat, bc)
                if sig == SIGNAL_BREAKOUT:
                    group = GROUP_BREAKOUT
                elif (stat.breakout_days_ago is not None
                      and stat.breakout_days_ago <= bc.recent_days and stat.holding):
                    group = GROUP_BREAKOUT_THIN
                elif sig == SIGNAL_NEAR:
                    group = GROUP_NEAR
            # 한 번의 신호 구간: 최근 20거래일 안에 돌파가 있거나 고점 근처인 동안.
            # 그 안에서는 첫 신호만 센다 - 다만 임박·거래량 미달로 시작했다가 거래량
            # 동반 돌파가 나오면 그것은 따로 센다(영탁센스의 핵심 신호다).
            in_episode = stat is not None and (
                stat.breakout_days_ago is not None
                or stat.distance_to_high >= -bc.near_pct)
            if not in_episode:
                seen.clear()
                continue
            if group is None or group in seen:
                continue
            if seen and group != GROUP_BREAKOUT:
                continue
            seen.add(group)
            report.events.append(outcome.model_copy(update={
                "ticker": security.ticker, "grade": group,
                "drop": stat.distance_to_high}))
    report.baseline = _stats(baseline) if baseline else None
    return report
