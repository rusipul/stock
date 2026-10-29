"""분석 오케스트레이션. 저장 계층에서 읽어 순수 함수에 넘기고 스냅샷을 쓴다.

네트워크를 쓰지 않는다. 기준을 바꿔가며 반복 실행하는 것이 이 명령의
주 사용 패턴이므로(설계서 §12), 몇 초에 끝나야 한다.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone

from pydantic import BaseModel

from stockscout.analysis.filters import apply_financial_filters, apply_price_filters
from stockscout.analysis.grading import grade_signal
from stockscout.analysis.scoring import score_security
from stockscout.analysis.valuation import (
    HIGH_WINDOW_YEARS,
    compute_price_stats,
    compute_valuation,
    window_start,
)
from stockscout.config import Config
from stockscout.models import AnalysisResult, FilterOutcome, ScoreCard, Snapshot
from stockscout.store.repository import Repository

SUPPORTED_MARKETS = ("KR", "US")


class AnalyzeSummary(BaseModel):
    snapshot_id: int | None = None
    as_of: date | None = None   # 이 실행이 고정한 가격 기준일
    analyzed: int = 0
    passed: int = 0
    excluded: int = 0
    skipped: int = 0
    failed: int = 0

    def render(self) -> str:
        lines = [
            f"{self.analyzed}종목 분석 · 통과 {self.passed} · 제외 {self.excluded}",
        ]
        if self.as_of is not None:
            lines.append(f"기준일 {self.as_of}")
        if self.skipped:
            lines.append(f"주가 없어 건너뜀 {self.skipped}종목")
        if self.failed:
            lines.append(f"계산 실패 {self.failed}종목 (원인은 collect_errors 참고)")
        if self.snapshot_id is not None:
            lines.append(f"스냅샷 #{self.snapshot_id} 저장")
        else:
            # 결과가 0건이면 스냅샷을 남기지 않는다 (analyze_market 참고) —
            # 그러지 않으면 직전의 진짜 스냅샷이 이 빈 실행에 가려진다.
            # 운영자가 exit code 0만 보고 "잘 됐나 보다"로 넘기면 안 되므로
            # 그 이유를 여기서 밝힌다.
            lines.append("스냅샷 저장 안 함 (분석 결과 없음 · 직전 스냅샷 유지)")
        return "\n".join(lines)


def config_hash(config: Config) -> str:
    """설정의 지문. 같은 설정이면 같은 값이어야 한다.

    sort_keys로 키 순서를 고정한다. 그러지 않으면 같은 설정이 실행마다
    다른 해시를 내놓아 "어느 기준으로 돌린 결과인가"를 추적할 수 없다.
    """
    payload = json.dumps(config.model_dump(mode="json"), sort_keys=True,
                         ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def analyze_market(
    repo: Repository,
    market: str,
    config: Config,
    note: str | None = None,
) -> AnalyzeSummary:
    """한 시장을 분석해 스냅샷 하나를 남긴다."""
    if market not in SUPPORTED_MARKETS:
        raise ValueError(f"지원하지 않는 시장입니다: {market}")

    summary = AnalyzeSummary()
    windows = config.lookback.price_years
    # market으로 한정한다: 시장마다 마감 시각이 달라(예: 미국 장은 한국보다
    # 늦게 마감) 시장을 구분하지 않으면 다른 시장의 최신 봉 날짜가 이
    # 시장의 as_of를 밀어버려, 아직 그 시장에 존재하지 않는 날짜를
    # 기준으로 전 종목을 평가하게 된다.
    as_of = repo.latest_price_date_any(market=market) or date.today()
    summary.as_of = as_of
    # 커버리지 분모가 되는 거래일도 같은 이유로 시장을 한정한다. 시장마다
    # 휴장일이 달라, 섞으면 다른 시장의 거래일이 이 시장 종목의 '빠진 봉'이
    # 된다.
    calendar = repo.trading_dates(
        _earliest_window_start(as_of, windows), as_of, market=market
    )

    results: list[AnalysisResult] = []
    for security in repo.list_securities(market=market):
        bars = repo.get_price_bars(
            security.ticker,
            start=_earliest_window_start(as_of, windows),
            end=as_of,
        )
        if not bars:
            summary.skipped += 1
            continue

        try:
            stats = compute_price_stats(
                bars, as_of=as_of, windows=windows, calendar=calendar,
                min_coverage=config.lookback.min_data_coverage,
            )
            outcome = apply_price_filters(
                security,
                repo.first_price_date(security.ticker),
                as_of=as_of,
                min_history_years=config.hard_filters.min_history_years,
            )
            financials = repo.get_financials(security.ticker)
            valuation = compute_valuation(
                stats.price_current, financials[-1] if financials else None
            )

            # 가격 필터와 재무 필터가 모두 INSUFFICIENT_HISTORY를 낼 수 있다.
            # dict.fromkeys로 순서를 지키면서 중복만 없앤다 - 화면이 같은
            # 사유를 두 번 보여주면 안 된다.
            financial_outcome = apply_financial_filters(
                financials, config.hard_filters
            )
            outcome = FilterOutcome(failures=list(dict.fromkeys(
                outcome.failures + financial_outcome.failures
            )))

            # 하드필터에 걸린 종목은 점수를 계산하지 않는다 (설계서 §8.1).
            # 매겨두면 화면에 "제외됐지만 85점"이라는 모순된 표시가 난다.
            card = score_security(financials, config.scoring) if outcome.passed else None
            grade, reason = grade_signal(
                stats,
                score_total=card.total if card else None,
                passed_filters=outcome.passed,
                config=config.signal,
                primary_window=config.lookback.primary_window,
            )
        except Exception as exc:
            repo.record_error(security.ticker, "analyze",
                              f"{type(exc).__name__}: {exc}")
            summary.failed += 1
            continue

        results.append(AnalysisResult(
            ticker=security.ticker, filters=outcome, stats=stats,
            valuation=valuation,
            score_total=card.total if card else None,
            score_profit=_axis_points(card, "profitability"),
            score_growth=_axis_points(card, "growth"),
            score_stability=_axis_points(card, "stability"),
            score_cashflow=_axis_points(card, "cashflow"),
            score_detail=card.model_dump_json() if card else None,
            signal_grade=grade,
            signal_reason=reason,
        ))
        summary.analyzed += 1
        if outcome.passed:
            summary.passed += 1
        else:
            summary.excluded += 1

    # 결과가 없으면 스냅샷을 남기지 않는다. Repository.save_snapshot 자체는
    # 빈 결과도 기록하는 합리적인 저장 계층 계약이지만(그 계약은
    # test_repository_snapshots.py::test_empty_results_still_creates_a_snapshot로
    # 고정돼 있다), 그 빈 스냅샷이 get_latest_snapshot()의 최신으로 뽑히면
    # 직전의 진짜(0건이 아닌) 스냅샷을 가려버린다. 설계서 §13이 예외에
    # 대해서만 약속한 "직전 스냅샷이 유지되므로 화면은 계속 동작한다"가
    # 성공했지만 텅 빈 실행(오타난 --market, 빈 DB, 가격 없는 DB 등)에는
    # 예외가 없다는 이유만으로 조용히 깨지지 않게 한다. exit code는
    # 그대로 0이다 — 빈 분석은 장애가 아니라 "볼 게 없다"는 사실이다.
    if results:
        snapshot = Snapshot(
            run_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            as_of=as_of.isoformat(),
            config_hash=config_hash(config),
            config_json=json.dumps(config.model_dump(mode="json"),
                                   sort_keys=True, ensure_ascii=False),
            note=note,
            market=market,
        )
        summary.snapshot_id = repo.save_snapshot(snapshot, results)
    return summary


def _earliest_window_start(as_of: date, windows: list[int]) -> date:
    """가장 긴 창의 시작일. 주가는 반드시 구간을 지정해 읽는다.

    Repository에 삭제 메서드가 없어 raw_prices는 append/upsert만 된다.
    price_years를 줄이면 옛 봉이 남아 있으므로, 무인자로 읽으면 설정을
    바꿔도 옛 데이터가 평균에 섞인다.

    HIGH_WINDOW_YEARS를 함께 고려하는 이유: 고점 창은 설계서 §9.1이
    5년으로 못박은 값이라 price_years와 무관하다. price_years를
    [1, 3]으로 좁히면 조회 구간이 3년이 되어, 3년 고점이 "5년 고점"으로
    조용히 보고되고 그 값이 계획 C의 고점 대비 등급 판정에 들어간다.
    """
    return window_start(as_of, max(max(windows), HIGH_WINDOW_YEARS))


def _axis_points(card: ScoreCard | None, axis: str) -> float | None:
    """축 점수 0~배점. 카드가 없거나 그 축이 없으면 None."""
    if card is None:
        return None
    for entry in card.axes:
        if entry.axis == axis:
            return entry.points
    return None
