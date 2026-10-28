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

# 저장 가능한 창은 스키마가 정한다. analysis_results에 avg_price_1y/3y/5y와
# drop_vs_avg_1y/3y/5y 컬럼만 있으므로(설계서 §6), 다른 창을 설정하면
# 계산은 되지만 저장 시 조용히 버려지고 출력 단계에서 KeyError로 죽는다.
# 설정 로드 시점에 분명한 메시지로 막는다.
SUPPORTED_PRICE_WINDOWS = (1, 3, 5)


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
    min_score: float = 70.0
    drop_vs_avg_threshold: float = -0.40
    drop_vs_high_threshold: float = -0.40


class SourcesConfig(BaseModel):
    kr_fundamental: str = "dart"
    kr_price: str = "pykrx"
    us_fundamental: str = "edgar"
    us_price: str = "yfinance"


class Config(BaseModel):
    universe: UniverseConfig = UniverseConfig()
    lookback: LookbackConfig = LookbackConfig()
    hard_filters: HardFilterConfig = HardFilterConfig()
    scoring: dict[str, dict[str, MetricConfig]] = {}
    signal: SignalConfig = SignalConfig()
    sources: SourcesConfig = SourcesConfig()


class Secrets(BaseModel):
    dart_api_key: str
    sec_user_agent: str = "stockscout/0.1"


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> Config:
    """YAML을 읽어 검증된 Config를 반환한다. 없는 섹션은 기본값을 쓴다."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"설정 파일이 없습니다: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Config.model_validate(raw)


def load_secrets(path: Path | str = DEFAULT_ENV_PATH) -> Secrets:
    """.env와 프로세스 환경변수를 합쳐 비밀값을 읽는다. 환경변수가 우선한다."""
    path = Path(path)
    values: dict[str, str | None] = {}
    if path.exists():
        values.update(dotenv_values(path))
    for key in ("DART_API_KEY", "SEC_USER_AGENT"):
        if os.environ.get(key):
            values[key] = os.environ[key]

    if not values.get("DART_API_KEY"):
        raise ValueError(
            f"DART_API_KEY가 없습니다. {path}에 넣거나 환경변수로 설정하세요. "
            "키 발급: https://opendart.fss.or.kr"
        )

    return Secrets(
        dart_api_key=values["DART_API_KEY"],
        sec_user_agent=values.get("SEC_USER_AGENT") or "stockscout/0.1",
    )
