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
    source: str          # 'dart' | 'dart_shares' | 'dart_audit' | 'edgar'
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
#
# 공백을 넣은 변형('의견 거절', '한 정')은 여기 적지 마라. 아래에서 찾는
# 문자열도 값과 똑같이 공백류를 지우고 비교하므로 '의견거절'과 같은
# 항목이 되어 아무것도 늘리지 않는다.
ADVERSE_OPINION_WORDS = ("한정", "부적정", "의견거절")


def is_adverse_opinion(opinion: str | None) -> bool:
    """감사의견이 결격인가. 모르는 값은 결격으로 보지 않는다.

    화이트리스트가 아니라 블랙리스트다 — "적정이 아니면 결격"이 아니라
    "아는 결격 단어가 들어 있으면 결격"이다. '검토의견 없음'도 '(*1)'도
    처음 보는 표기도 전부 False다. 조이러 온 사람에게: 이건 의도한
    안전장치이지 느슨한 구현이 아니다(설계서 §8.1의 "적정이 아니면 제외"를
    실측에 맞춰 좁힌 것이다). DART 문서가 값 목록을 명시하지 않아서,
    모르는 문자열을 결격으로 처리하면 표기가 조금 다른 우량주가 통째로
    사라지고 아무도 이유를 모른다.

    비교할 때 찾는 단어도 값과 똑같이 공백류를 지운다. 스페이스만 지우면
    안 된다. 이 값을 주는 DART
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
    # 창(년) -> 그 창의 고점과 고점 대비 하락률. 화면의 비교 기간 슬라이더가
    # 쓴다. high_5y·drop_vs_high는 등급 판정(§9.1의 5년 고점)이 그대로 쓴다.
    high_by_window: dict[int, float] = {}
    drop_vs_high_by_window: dict[int, float] = {}


class Valuation(BaseModel):
    """밸류에이션 지표 (설계서 §9.2). 화면 표시·정렬 전용, 점수에 넣지 않는다.

    shares_outstanding이 없으면 셋 다 None이다. 발행주식수는 「주식의
    총수 현황」에서 따로 수집하므로, 그 수집이 실패한 종목에서만 None이다.
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
    # 시장마다 기준일·통화·수집 시점이 달라 따로 저장한다 (설계서 §18.5).
    # 기본값이 KR인 이유: 계획 E 이전 코드와 스냅샷은 전부 한국 것이다.
    market: str = "KR"


# 지표·축 상태 (설계서 §8.2·§8.3)
STATUS_OK = "OK"
STATUS_MISSING = "MISSING"                            # 데이터가 없어 계산 불가
STATUS_BASE_NEGATIVE = "BASE_NEGATIVE"                # CAGR 기저가 0 이하
STATUS_END_NONPOSITIVE = "END_NONPOSITIVE"            # CAGR 기저는 양수인데 최근 값이 0 이하
STATUS_DENOMINATOR_NONPOSITIVE = "DENOMINATOR_NONPOSITIVE"   # 3년 누계 영업이익 <= 0
AXIS_UNMEASURABLE = "AXIS_UNMEASURABLE"               # 축 유효만점이 배점의 절반 미만

AXIS_MAX = 25.0   # 축 하나의 배점. 4축 x 25 = 100점


class MetricScore(BaseModel):
    """지표 하나의 채점 결과. 화면이 근거를 보여줄 수 있게 원값을 함께 남긴다."""

    metric: str
    value: float | None
    points: float
    max_points: float
    status: str


class AxisScore(BaseModel):
    """축 하나의 채점 결과.

    earned/denominator를 그대로 들고 다니는 이유: 총점은 축 점수를
    평균내는 것이 아니라 전체 획득을 전체 유효만점으로 나눈 값이다
    (설계서 §8.3). 축에서 25점으로 환산해 버리면 그 계산을 할 수 없다.
    """

    axis: str
    metrics: list[MetricScore]
    earned: float
    denominator: float
    status: str
    # 이 축의 배점 합. 기본값 AXIS_MAX는 4축 x 25라는 표준 배치일 뿐이고,
    # 축을 조립하는 쪽이 설정에서 읽은 실제 배점 합으로 덮는다 - 사용자가
    # YAML에서 배점을 바꿀 수 있기 때문이다.
    max_points: float = AXIS_MAX

    @property
    def points(self) -> float:
        """화면 표시용 0~배점 점수."""
        if self.denominator <= 0:
            return 0.0
        return self.earned / self.denominator * self.max_points


class ScoreCard(BaseModel):
    """한 종목의 4축 채점 결과 전체 (설계서 §8.2)."""

    axes: list[AxisScore]

    @property
    def total(self) -> float:
        """설계서 §8.3: (획득점수 합계 / 유효만점 합계) x 100.

        축 점수를 평균내면 안 된다. 평균은 모든 축을 같은 무게로 세는데,
        결측이 많아 유효만점이 작아진 축은 적은 근거로 매긴 점수다.
        그것을 온전한 축과 같은 무게로 세면 **가장 모르는 축이 총점을
        가장 많이 흔든다.** 전체 획득을 전체 유효만점으로 나누면 축의
        무게가 실제로 잰 점수에 비례한다.
        """
        denominator = sum(a.denominator for a in self.axes)
        if denominator <= 0:
            return 0.0
        return sum(a.earned for a in self.axes) / denominator * 100


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
