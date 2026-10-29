"""원본 → 공통 스키마 변환. 네트워크를 쓰지 않는다."""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel

from stockscout.models import Financials
from stockscout.normalize.kr_mapping import (
    apply_audit_opinion,
    apply_share_counts,
    normalize_dart_statement,
)
from stockscout.normalize.us_mapping import normalize_edgar_statement
from stockscout.store.repository import Repository

# 보강 함수는 이미 만들어진 Financials를 제자리에서 고친다.
Enricher = Callable[[Financials, dict], None]

# 시장별 (원본 source 식별자, 정규화 함수). raw_financials는 (ticker, fiscal_year,
# source)로 dart/edgar를 함께 담으므로, 정규화기가 자기 것만 골라 보게
# source를 함께 등록한다.
NORMALIZERS = {
    "KR": ("dart", normalize_dart_statement),
    "US": ("edgar", normalize_edgar_statement),
}

# 재무제표 위에 얹는 보강 소스: (원본 source, 적용 함수).
# 재무제표가 있는 연도에만 얹는다 — 주식수만 있는 연도에 Financials 행을
# 만들면 필수 항목이 전부 비어 DATA_INSUFFICIENT가 될 뿐이다. 금융
# 7종목의 2021·2022년이 정확히 그 모양이다(DART에 재무제표가 없다).
ENRICHERS: dict[str, list[tuple[str, Enricher]]] = {
    "KR": [
        ("dart_shares", apply_share_counts),
        ("dart_audit", apply_audit_opinion),
    ],
}


class NormalizeSummary(BaseModel):
    tickers: int = 0
    normalized: int = 0
    incomplete: int = 0
    failed: int = 0
    missing_counts: dict[str, int] = {}

    def render(self) -> str:
        lines = [
            f"{self.tickers}종목 · {self.normalized}개 연도 정규화 "
            f"(필수항목 결측 {self.incomplete}건)"
        ]
        if self.failed:
            lines.append(
                f"실패 {self.failed}종목 (원인은 collect_errors 참고)"
            )
        if self.missing_counts:
            top = sorted(self.missing_counts.items(), key=lambda kv: -kv[1])[:8]
            lines.append(
                "결측 상위: " + ", ".join(f"{field} {n}건" for field, n in top)
            )
        return "\n".join(lines)


def normalize_market(repo: Repository, market: str) -> NormalizeSummary:
    entry = NORMALIZERS.get(market)
    if entry is None:
        raise ValueError(f"{market} 시장의 정규화 함수가 없습니다")
    source, normalizer = entry

    summary = NormalizeSummary()
    for security in repo.list_securities(market=market):
        ticker = security.ticker
        statements = repo.get_raw_statements(ticker, source=source)
        if not statements:
            continue

        # 종목 단위 격리(설계서 §13과 동일한 원칙). 한 종목의 깨진 payload가
        # 나머지 종목의 정규화나, 이 루프 뒤에서 계산되는 결측 상위
        # 보고서를 막아서는 안 된다 - 그 보고서를 보고 매핑을 보강하는 것이
        # normalize 명령의 존재 이유다.
        try:
            records = [normalizer(s) for s in statements]
            by_year = {r.fiscal_year: r for r in records}
            for enrich_source, enrich in ENRICHERS.get(market, []):
                for raw in repo.get_raw_statements(ticker, source=enrich_source):
                    target = by_year.get(raw.fiscal_year)
                    if target is not None:
                        enrich(target, raw.payload)
            repo.save_financials(records)
        except Exception as exc:
            repo.record_error(ticker, source, f"{type(exc).__name__}: {exc}")
            summary.failed += 1
            continue

        summary.tickers += 1
        summary.normalized += len(records)
        summary.incomplete += sum(1 for r in records if not r.has_required)

    summary.missing_counts = repo.missing_field_counts()
    return summary
