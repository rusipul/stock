"""API 응답 모델. 계약을 한곳에 모은다.

저장 계층의 원본 행을 그대로 내보내지 않는 이유: 컬럼 이름이 바뀌면
프론트가 조용히 깨진다. 여기서 한 번 형태를 고정하면 변경이 컴파일
에러처럼 드러난다.
"""

from __future__ import annotations

from pydantic import BaseModel

from stockscout.models import DrawdownStat


class SnapshotInfo(BaseModel):
    snapshot_id: int
    market: str        # 'KR' | 'US'. 화면이 통화 표시와 탭을 고른다
    run_at: str
    as_of: str | None
    config_hash: str
    note: str | None

    total: int
    passed: int
    excluded: int

    # 프론트가 어떤 UI를 켤지 결정하는 근거.
    # 점수가 없는데 등급을 보여주면 품질을 검증한 것처럼 보이는 거짓말이 된다.
    has_scores: bool

    # 서버가 등급을 매긴 기준. 화면 슬라이더의 기본값이 된다.
    drawdown_months: int = 3
    drawdown_threshold: float = -0.25
    drawdown_choices: list[int] = [1, 3, 6, 12]

    # 시가총액·PER·PBR을 계산할 수 있었는가. financials 테이블이 채워졌는지와
    # 다른 질문이다 — 발행주식수는 재무제표 API가 아니라 stockTotqySttus.json
    # 에서 오므로, 재무는 있는데 밸류에이션은 없는 상태가 가능하다.
    has_valuation: bool


class WindowStat(BaseModel):
    """한 비교 기간(년)의 평균·고점과 하락률. 화면의 비교 기간 슬라이더가 쓴다."""

    avg_price: float | None = None
    drop_vs_avg: float | None = None
    high: float | None = None
    drop_vs_high: float | None = None


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
    window_stats: dict[int, WindowStat] = {}
    # 개월 -> 최근 그 기간의 고점 대비 하락·하락 멈춤·같은 기간 시장 하락.
    drawdown: dict[int, DrawdownStat] = {}
    benchmark: str | None = None
    # 축 점수(0~배점)와 영역 최소점 미달 축. 화면의 등급 재판정이 쓴다.
    score_profit: float | None = None
    score_growth: float | None = None
    score_stability: float | None = None
    score_cashflow: float | None = None
    weak_axes: list[str] = []
    fcf_yield: float | None = None

    score_total: float | None
    market_cap: float | None
    per: float | None
    pbr: float | None

    in_watchlist: bool


class FinancialRow(BaseModel):
    """재무 추이 한 해. 파생 지표는 서버에서 계산해 내려보낸다.

    Financials 모델이 이미 이 계산을 갖고 있으므로 프론트에서 다시
    구현하지 않는다 — 같은 공식이 두 곳에 있으면 갈라진다.
    """

    fiscal_year: int
    revenue: float | None
    operating_income: float | None
    net_income: float | None
    operating_margin: float | None
    roe: float | None
    debt_ratio: float | None
    operating_cash_flow: float | None


class SecurityDetail(SecurityRow):
    """우측 패널. 목록 한 줄에 상세 정보를 더한다."""

    currency: str
    rationale: list[str]
    score_detail: str | None
    financials: list[FinancialRow]


class PricePoint(BaseModel):
    date: str
    close: float


class PriceSeries(BaseModel):
    """차트 한 장에 필요한 전부.

    기준선(평균·고점)은 스냅샷에서 그대로 가져온다. 프론트가 다시
    계산하면 창 정의나 반올림이 갈려 화면과 판정 근거가 어긋난다.
    """

    ticker: str
    as_of: str | None
    series: list[PricePoint]
    price_current: float
    avg_price_1y: float | None
    avg_price_3y: float | None
    avg_price_5y: float | None
    high_5y: float
