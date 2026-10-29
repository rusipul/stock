"""설정 로딩. 임계치·배점은 전부 YAML에 있고 코드에는 기본값만 둔다."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, Field, model_validator

DEFAULT_CONFIG_PATH = Path("config/settings.yaml")
DEFAULT_ENV_PATH = Path("config/.env")

# 저장 가능한 창은 스키마가 정한다. 1~5년 전부 analysis_results.window_stats
# (JSON)에 담기고, 1·3·5년은 따로 컬럼(avg_price_1y 등)도 있다(설계서 §6).
# 범위 밖 창을 설정하면 계산은 되지만 저장 시 조용히 버려지므로 설정 로드
# 시점에 분명한 메시지로 막는다.
SUPPORTED_PRICE_WINDOWS = (1, 2, 3, 4, 5)
# 주 기간은 컬럼이 있어야 한다. CLI 순위표와 판정 근거가 drop_vs_avg_{N}y를
# 읽는다 - 2년을 주 기간으로 두면 그 자리에서 KeyError로 죽는다.
COLUMN_PRICE_WINDOWS = (1, 3, 5)


class UniverseConfig(BaseModel):
    markets: list[Literal["KR", "US"]] = ["KR", "US"]
    kr_index: str = "KOSPI200"
    us_index: str = "SP500"
    exclude_financials: bool = True


class LookbackConfig(BaseModel):
    price_years: list[int] = [1, 3, 5]
    primary_window: int = 5
    financial_years: int = 5
    min_data_coverage: float = Field(default=0.8, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def windows_must_be_storable(self) -> "LookbackConfig":
        unsupported = sorted(set(self.price_years) - set(SUPPORTED_PRICE_WINDOWS))
        if unsupported:
            raise ValueError(
                f"price_years에 저장할 수 없는 창이 있습니다: {unsupported} "
                f"(가능: {list(SUPPORTED_PRICE_WINDOWS)}). "
                "analysis_results 테이블에 해당 컬럼이 없습니다"
            )
        return self

    @model_validator(mode="after")
    def primary_window_must_be_available(self) -> "LookbackConfig":
        if self.primary_window not in COLUMN_PRICE_WINDOWS:
            raise ValueError(
                f"primary_window({self.primary_window})는 "
                f"{list(COLUMN_PRICE_WINDOWS)} 중 하나여야 합니다"
            )
        if self.primary_window not in self.price_years:
            raise ValueError(
                f"primary_window({self.primary_window})가 "
                f"price_years({self.price_years})에 없습니다"
            )
        return self


class HardFilterConfig(BaseModel):
    """설계서 §8.1의 하드필터 임계치.

    연수 필드에 ge=1을 두는 이유: 0을 허용하면 이자보상배율 필터가
    퇴화 설정에서 정확히 뒤집힌다. interest_coverage_years=0이면
    coverages가 빈 목록이 될 수 있고, 그때 len([]) == 0이 참이면서
    all([])도 True라 **통과 조건이 곧 실패 조건이 된다** — 이자비용을
    측정할 수 없는 회사(NULL이거나 금융원가 대체값)가 "이자 미충당"으로
    제외된다. 실측 196행 중 157행이 대체값이므로 유니버스 대부분이
    여기 해당한다. "모르는 것으로 떨어뜨리지 않는다"의 정확한 역전이다.

    형제 필터는 같은 설정에서 그냥 꺼지므로(창 길이로 재기 때문에)
    같은 모양의 두 필터가 정반대로 움직인다. 필터 코드를 고쳐
    len(coverage_window)로 재는 대안도 있으나, 그러면 재무가 1년뿐인
    회사가 2년 필터에 걸리게 되어 동작이 나빠진다. 애초에 들어올 수
    없게 설정 로드 시점에 막는 것이 맞다 — LookbackConfig가 같은
    자리에서 하는 일과 같다.
    """

    max_debt_ratio: float = 200.0
    consecutive_operating_loss_years: int = Field(default=3, ge=1)
    min_interest_coverage: float = 1.0
    interest_coverage_years: int = Field(default=2, ge=1)
    max_revenue_decline_3y: float = -0.5
    min_history_years: int = Field(default=5, ge=1)


class MetricConfig(BaseModel):
    """지표 하나의 배점과 구간. bands는 [기준값, 점수] 목록이며 위에서부터 평가한다."""

    points: float
    bands: list[tuple[float, float]] = []
    lower_is_better: bool = False


class SignalConfig(BaseModel):
    """등급 판정 기준. 우량주가 최근 몇 달 사이 크게 빠졌고 멈췄는가.

    후보 = 점수 >= min_score 이고 최근 drawdown_months개월 고점 대비
    drawdown_threshold 이하. 그중 하락이 멈춘 종목이 별 둘, 아직 빠지는
    중이면 별 하나다. 하락 멈춤 = 저점 이후 stable_days 거래일 이상 경과
    또는 현재가 >= ma_days 이동평균.
    """

    min_score: float = 70.0
    drawdown_months: int = 3
    drawdown_threshold: float = -0.25
    # 계산해 두는 기간(개월). 화면의 하락 기간 슬라이더가 이 중에서 고른다.
    drawdown_choices: list[int] = [1, 3, 6, 12]
    stable_days: int = Field(default=10, ge=1)
    ma_days: int = Field(default=20, ge=1)
    # 5년 평균·고점 기준(계획 C). 판정에는 더 쓰지 않고 근거 표시에만 남는다.
    drop_vs_avg_threshold: float = -0.40
    drop_vs_high_threshold: float = -0.40

    @model_validator(mode="after")
    def drawdown_months_must_be_computed(self) -> "SignalConfig":
        if self.drawdown_months not in self.drawdown_choices:
            raise ValueError(
                f"drawdown_months({self.drawdown_months})가 "
                f"drawdown_choices({self.drawdown_choices})에 없습니다"
            )
        return self


class SourcesConfig(BaseModel):
    kr_fundamental: str = "dart"
    kr_price: str = "pykrx"
    us_fundamental: str = "edgar"
    us_price: str = "yfinance"


class Config(BaseModel):
    universe: UniverseConfig = UniverseConfig()
    lookback: LookbackConfig = LookbackConfig()
    hard_filters: HardFilterConfig = HardFilterConfig()
    # 시장별로 hard_filters의 일부 필드만 덮어쓴다 (설계서 §18.4).
    # 예: 미국은 자사주 매입으로 자본이 작은 대형주가 많아 부채비율이
    # 무의미하다 - {"US": {"max_debt_ratio": .inf}}.
    hard_filters_by_market: dict[str, dict] = {}
    scoring: dict[str, dict[str, MetricConfig]] = {}
    signal: SignalConfig = SignalConfig()
    sources: SourcesConfig = SourcesConfig()

    def hard_filters_for(self, market: str) -> HardFilterConfig:
        """그 시장에 적용할 하드필터. 덮어쓴 결과도 같은 검증을 거친다.

        모르는 필드는 거부한다. 오타(max_dept_ratio)가 조용히 무시되면
        끄려던 필터가 켜진 채로 남고, 아무도 그것을 알 수 없다.
        """
        override = self.hard_filters_by_market.get(market) or {}
        unknown = set(override) - set(HardFilterConfig.model_fields)
        if unknown:
            raise ValueError(
                f"hard_filters_by_market.{market}에 모르는 필드가 있습니다: "
                f"{sorted(unknown)}"
            )
        return HardFilterConfig.model_validate(
            {**self.hard_filters.model_dump(), **override})


class Secrets(BaseModel):
    """시장마다 필요한 비밀값이 다르다. 그 시장에 필요 없는 값은 None일 수 있다."""

    dart_api_key: str | None = None
    sec_user_agent: str | None = None


SECRETS_MARKETS = ("KR", "US")


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> Config:
    """YAML을 읽어 검증된 Config를 반환한다. 없는 섹션은 기본값을 쓴다."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"설정 파일이 없습니다: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Config.model_validate(raw)


def load_secrets(path: Path | str = DEFAULT_ENV_PATH, market: str = "KR") -> Secrets:
    """.env와 프로세스 환경변수를 합쳐 비밀값을 읽는다. 환경변수가 우선한다.

    market에 필요한 값만 요구한다. 미국만 수집하는데 DART 키가 없다고
    죽으면 안 되고, 한국만 수집하는데 SEC 연락처를 요구할 이유도 없다.
    """
    if market not in SECRETS_MARKETS:
        raise ValueError(f"알 수 없는 시장입니다: {market} (가능: {SECRETS_MARKETS})")

    path = Path(path)
    values: dict[str, str | None] = {}
    if path.exists():
        values.update(dotenv_values(path))
    for key in ("DART_API_KEY", "SEC_USER_AGENT"):
        if os.environ.get(key):
            values[key] = os.environ[key]

    if market == "KR" and not values.get("DART_API_KEY"):
        raise ValueError(
            f"DART_API_KEY가 없습니다. {path}에 넣거나 환경변수로 설정하세요. "
            "키 발급: https://opendart.fss.or.kr"
        )

    user_agent = values.get("SEC_USER_AGENT") or None
    # SEC는 연락처 없는 User-Agent를 403으로 막는다(설계서 §18.2, 실측).
    # 이메일 형식까지 검사하지 않는다 - '@' 하나가 "연락처를 넣었는가"를
    # 가르기에 충분하고, 그 이상은 SEC가 판단한다.
    if market == "US" and (not user_agent or "@" not in user_agent):
        raise ValueError(
            f"SEC_USER_AGENT에 연락처(이메일)가 없습니다. {path}에 "
            "'SEC_USER_AGENT=stockscout you@example.com' 형식으로 넣으세요. "
            "SEC는 연락처 없는 요청을 403으로 거부합니다."
        )

    return Secrets(
        dart_api_key=values.get("DART_API_KEY") or None,
        sec_user_agent=user_agent,
    )
