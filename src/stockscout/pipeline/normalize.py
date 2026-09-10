"""원본 → 공통 스키마 변환. 네트워크를 쓰지 않는다."""

from __future__ import annotations

from pydantic import BaseModel

from stockscout.normalize.kr_mapping import normalize_dart_statement
from stockscout.store.repository import Repository

# 시장별 정규화 함수. 계획 D에서 "US": normalize_edgar_statement 를 추가한다.
NORMALIZERS = {"KR": normalize_dart_statement}


class NormalizeSummary(BaseModel):
    tickers: int = 0
    normalized: int = 0
    incomplete: int = 0
    missing_counts: dict[str, int] = {}

    def render(self) -> str:
        lines = [
            f"{self.tickers}종목 · {self.normalized}개 연도 정규화 "
            f"(필수항목 결측 {self.incomplete}건)"
        ]
        if self.missing_counts:
            top = sorted(self.missing_counts.items(), key=lambda kv: -kv[1])[:8]
            lines.append(
                "결측 상위: " + ", ".join(f"{field} {n}건" for field, n in top)
            )
        return "\n".join(lines)


def normalize_market(repo: Repository, market: str) -> NormalizeSummary:
    normalizer = NORMALIZERS.get(market)
    if normalizer is None:
        raise ValueError(f"{market} 시장의 정규화 함수가 없습니다")

    summary = NormalizeSummary()
    for security in repo.list_securities(market=market):
        statements = repo.get_raw_statements(security.ticker)
        if not statements:
            continue

        summary.tickers += 1
        records = [normalizer(s) for s in statements]
        repo.save_financials(records)
        summary.normalized += len(records)
        summary.incomplete += sum(1 for r in records if not r.has_required)

    summary.missing_counts = repo.missing_field_counts()
    return summary
