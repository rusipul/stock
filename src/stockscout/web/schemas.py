"""API 응답 모델. 계약을 한곳에 모은다.

저장 계층의 원본 행을 그대로 내보내지 않는 이유: 컬럼 이름이 바뀌면
프론트가 조용히 깨진다. 여기서 한 번 형태를 고정하면 변경이 컴파일
에러처럼 드러난다.
"""

from __future__ import annotations

from pydantic import BaseModel


class SnapshotInfo(BaseModel):
    snapshot_id: int
    run_at: str
    as_of: str | None
    config_hash: str
    note: str | None

    total: int
    passed: int
    excluded: int

    # 프론트가 등급·점수 UI를 켤지 결정하는 근거.
    # 점수가 없는데 등급을 보여주면 품질을 검증한 것처럼 보이는 거짓말이 된다.
    has_scores: bool
    has_financials: bool


class SecurityRow(BaseModel):
    """좌측 목록 한 줄. 슬라이더 재판정에 필요한 지표를 모두 담는다.

    42종목을 한 번에 내려보내고 프론트가 필터·정렬·재판정을 한다.
    설계서 §6.1 — 임계치는 지표를 바꾸지 않고 등급만 나누므로
    서버 왕복이 필요 없다.
    """

    ticker: str
    name: str
    exchange: str | None
    is_financial: bool

    passed_filters: bool
    filter_failures: list[str]

    price_current: float
    avg_price_1y: float | None
    avg_price_3y: float | None
    avg_price_5y: float | None
    high_5y: float
    drop_vs_avg_1y: float | None
    drop_vs_avg_3y: float | None
    drop_vs_avg_5y: float | None
    drop_vs_high: float

    score_total: float | None
    market_cap: float | None
    per: float | None
    pbr: float | None

    in_watchlist: bool
