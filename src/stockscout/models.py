"""공유 데이터 모델.

이 파일은 프로젝트의 바닥이며 stockscout의 어떤 모듈도 import하지 않는다.
sources·normalize·store가 서로를 모른 채 같은 모델로 대화하게 하는 것이 목적이다.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class Security(BaseModel):
    """종목 마스터 한 건."""

    ticker: str                      # '005930.KS' | 'AAPL'
    market: str                      # 'KR' | 'US'
    name: str
    name_en: str | None = None
    exchange: str | None = None
    sector: str | None = None
    is_financial: bool = False
    source_id: str | None = None     # DART corp_code | SEC CIK
    currency: str
    listed_at: str | None = None
    in_universe: bool = True


class RawStatement(BaseModel):
    """소스 응답을 가공 없이 담는다. 해석은 normalize 계층의 책임이다."""

    ticker: str
    fiscal_year: int
    source: str          # 'dart' | 'dart_shares' | 'edgar'
    payload: dict


class PriceBar(BaseModel):
    ticker: str
    date: date
    close: float         # 수정종가
    volume: int | None = None


# 없으면 하드필터 DATA_INSUFFICIENT로 제외된다 (설계서 §7.3)
REQUIRED_FIELDS = {"revenue", "operating_income", "total_equity", "total_liabilities"}

# 없으면 해당 지표를 점수에서 빼고 유효만점을 줄여 환산한다 (설계서 §8.3)
OPTIONAL_FIELDS = {
    "net_income",
    "total_assets",
    "current_assets",
    "current_liabilities",
    "interest_expense",
    "operating_cash_flow",
    "capex",
    "shares_outstanding",
    "dividend_per_share",
}


# 감사의견 결격 판정에 쓰는 단어. 설계서 §8.1이 "적정이 아니면 제외"라고
# 하지만, 모르는 문자열을 결격으로 처리하면 표기가 조금 다른 회사가
# 통째로 사라지고 아무도 이유를 모른다. 아는 단어에만 건다.
#
# 회계감사기준이 정하는 비적정 의견은 세 종류뿐이다 — 한정의견·부적정의견
# ·의견거절. 아래 목록은 그 셋을 덮는다. KOSPI 42종목 210건 전수 조사에서
# 비적정은 한 건도 없었으므로(적정의견 506행·적정 298행·적 정 9행 등),
# 이 목록은 실측이 아니라 감사기준에서 온 것이다.
ADVERSE_OPINION_WORDS = ("한정", "부적정", "의견거절", "의견 거절")


def is_adverse_opinion(opinion: str | None) -> bool:
    """감사의견이 결격인가. 모르는 값은 결격으로 보지 않는다.

    공백을 지울 때 스페이스만 지우면 안 된다. 이 값을 주는 DART
    엔드포인트는 셀 한가운데에 줄바꿈을 박아 보낸다 — 실측에서
    '삼일\\n회계법인', '적정\\n예외사항 없음', '제56기\\n(당기)'가 나왔다.
    같은 방식으로 '의견\\n거절'이 오면 스페이스만 지우는 구현은 가장 강한
    결격 신호를 조용히 흘려보낸다. 모든 공백류를 지운 뒤 비교한다.
    """
    if not opinion:
        return False
    text = "".join(opinion.split())
    return any("".join(word.split()) in text for word in ADVERSE_OPINION_WORDS)


class Financials(BaseModel):
    """한국·미국 재무 데이터가 수렴하는 공통 스키마.

    DART 계정과목(매출액 등)과 SEC EDGAR US-GAAP 태그(Revenues 등)는
    모두 이 모델로 정규화된 뒤에만 분석 계층에 들어온다. 이 아래로는
    어떤 코드도 시장(KR/US)을 구분하지 않는다 (설계서 §7.1).
    """

    ticker: str
    fiscal_year: int
    currency: str

    revenue: float | None = None
    operating_income: float | None = None
    net_income: float | None = None
    total_assets: float | None = None
    total_liabilities: float | None = None
    total_equity: float | None = None
    current_assets: float | None = None
    current_liabilities: float | None = None
    interest_expense: float | None = None
    operating_cash_flow: float | None = None
    capex: float | None = None          # 양수 크기로 저장한다
    shares_outstanding: float | None = None
    dividend_per_share: float | None = None
    shares_treasury: float | None = None      # 자기주식수 (설계서 §9.2)
    audit_opinion: str | None = None          # '적정' 등 원문 (설계서 §8.1)

    # 정확한 계정을 못 찾아 대체 계정으로 채운 필드 이름들 (설계서 §17.3).
    # 이자보상배율을 금융비용으로 계산했다면 판정 근거가 그렇게 말해야 한다.
    proxy_fields: list[str] = []

    # --- 결측 추적 ---------------------------------------------------

    @property
    def missing_fields(self) -> list[str]:
        return sorted(
            f
            for f in REQUIRED_FIELDS | OPTIONAL_FIELDS
            if getattr(self, f) is None
        )

    @property
    def has_required(self) -> bool:
        return all(getattr(self, f) is not None for f in REQUIRED_FIELDS)

    # --- 파생 지표 ---------------------------------------------------

    @property
    def debt_ratio(self) -> float | None:
        """부채비율(%). 자본이 0 이하면 의미가 없다."""
        if self.total_liabilities is None or not self.total_equity or self.total_equity <= 0:
            return None
        return self.total_liabilities / self.total_equity * 100

    @property
    def current_ratio(self) -> float | None:
        if not self.current_liabilities or self.current_liabilities <= 0:
            return None
        if self.current_assets is None:
            return None
        return self.current_assets / self.current_liabilities

    @property
    def operating_margin(self) -> float | None:
        if self.operating_income is None or not self.revenue or self.revenue <= 0:
            return None
        return self.operating_income / self.revenue

    @property
    def roe(self) -> float | None:
        if self.net_income is None or not self.total_equity or self.total_equity <= 0:
            return None
        return self.net_income / self.total_equity

    @property
    def interest_coverage(self) -> float | None:
        """이자보상배율. 이자비용이 0이면 부채 부담이 없다는 뜻이므로 None이 아니라 inf."""
        if self.operating_income is None or self.interest_expense is None:
            return None
        if self.interest_expense == 0:
            return float("inf")
        return self.operating_income / abs(self.interest_expense)

    @property
    def free_cash_flow(self) -> float | None:
        if self.operating_cash_flow is None or self.capex is None:
            return None
        return self.operating_cash_flow - abs(self.capex)


# --- 분석 계층 (설계서 §8·§9) ---------------------------------------


class FilterOutcome(BaseModel):
    """하드필터 판정 결과. 실패 사유를 모두 모은다 (설계서 §8.1).

    첫 실패에서 멈추지 않는 이유: 화면에서 "왜 제외됐나"를 보여줄 때
    사유가 하나만 있으면 고치고 다시 돌렸을 때 또 다른 사유로 걸린다.
    """

    failures: list[str] = []

    @property
    def passed(self) -> bool:
        return not self.failures


class PriceStats(BaseModel):
    """가격 통계 (설계서 §9.1).

    avg_price·drop_vs_avg는 창(년) → 값 매핑이다. 커버리지가 미달한
    창은 None이며 등급 판정에서 제외된다.
    """

    price_current: float
    avg_price: dict[int, float | None]
    high_5y: float
    drop_vs_avg: dict[int, float | None]
    drop_vs_high: float


class Valuation(BaseModel):
    """밸류에이션 지표 (설계서 §9.2). 화면 표시·정렬 전용, 점수에 넣지 않는다.

    shares_outstanding이 없으면 셋 다 None이다. DART의 전체 재무제표
    API가 발행주식수를 주지 않으므로 당분간 항상 None이다.
    """

    market_cap: float | None = None
    per: float | None = None
    pbr: float | None = None


class Snapshot(BaseModel):
    """분석 1회 실행의 메타데이터 (설계서 §6)."""

    snapshot_id: int | None = None   # 저장 시 부여된다
    run_at: str
    as_of: str | None = None         # 이 스냅샷이 고정한 가격 기준일(ISO 날짜).
                                      # run_at과 달리 실행 시각이 아니라 그
                                      # 실행이 "무엇을 기준으로" 계산했는지를
                                      # 말한다. 기존 스냅샷·직접 조립한
                                      # Snapshot과의 호환을 위해 선택값이다.
    config_hash: str
    config_json: str
    note: str | None = None


class AnalysisResult(BaseModel):
    """한 종목의 분석 결과 한 줄.

    점수·등급 칸은 계획 C가 채운다. 그때까지 None이다.
    """

    ticker: str
    filters: FilterOutcome
    stats: PriceStats
    valuation: Valuation

    score_total: float | None = None
    score_profit: float | None = None
    score_growth: float | None = None
    score_stability: float | None = None
    score_cashflow: float | None = None
    score_detail: str | None = None
    signal_grade: str | None = None
    signal_reason: str | None = None

    @property
    def passed_filters(self) -> bool:
        return self.filters.passed
