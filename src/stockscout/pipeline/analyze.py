"""분석 오케스트레이션. 저장 계층에서 읽어 순수 함수에 넘기고 스냅샷을 쓴다.

네트워크를 쓰지 않는다. 기준을 바꿔가며 반복 실행하는 것이 이 명령의
주 사용 패턴이므로(설계서 §12), 몇 초에 끝나야 한다.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone

from pydantic import BaseModel

from stockscout.analysis.filters import apply_price_filters
from stockscout.analysis.valuation import (
    HIGH_WINDOW_YEARS,
    compute_price_stats,
    compute_valuation,
    window_start,
)
from stockscout.config import Config
from stockscout.models import AnalysisResult, Snapshot
from stockscout.store.repository import Repository

SUPPORTED_MARKETS = ("KR", "US")


class AnalyzeSummary(BaseModel):
    snapshot_id: int | None = None
    analyzed: int = 0
    passed: int = 0
    excluded: int = 0
    skipped: int = 0
    failed: int = 0

    def render(self) -> str:
        lines = [
            f"{self.analyzed}종목 분석 · 통과 {self.passed} · 제외 {self.excluded}",
        ]
        if self.skipped:
            lines.append(f"주가 없어 건너뜀 {self.skipped}종목")
        if self.failed:
            lines.append(f"계산 실패 {self.failed}종목 (원인은 collect_errors 참고)")
        if self.snapshot_id is not None:
            lines.append(f"스냅샷 #{self.snapshot_id} 저장")
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
    as_of = repo.latest_price_date_any() or date.today()
    calendar = repo.trading_dates(
        _earliest_window_start(as_of, windows), as_of
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
                security, bars, as_of=as_of,
                min_history_years=config.hard_filters.min_history_years,
            )
            financials = repo.get_financials(security.ticker)
            valuation = compute_valuation(
                stats.price_current, financials[-1] if financials else None
            )
        except Exception as exc:
            repo.record_error(security.ticker, "analyze",
                              f"{type(exc).__name__}: {exc}")
            summary.failed += 1
            continue

        results.append(AnalysisResult(
            ticker=security.ticker, filters=outcome, stats=stats,
            valuation=valuation,
        ))
        summary.analyzed += 1
        if outcome.passed:
            summary.passed += 1
        else:
            summary.excluded += 1

    snapshot = Snapshot(
        run_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        config_hash=config_hash(config),
        config_json=json.dumps(config.model_dump(mode="json"),
                               sort_keys=True, ensure_ascii=False),
        note=note,
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
