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
    def primary_window_must_be_available(self) -> "LookbackConfig":
        if self.primary_window not in self.price_years:
            raise ValueError(
                f"primary_window({self.primary_window})가 "
                f"price_years({self.price_years})에 없습니다"
            )
        return self


class HardFilterConfig(BaseModel):
    max_debt_ratio: float = 200.0
    consecutive_operating_loss_years: int = 3
    min_interest_coverage: float = 1.0
    interest_coverage_years: int = 2
    max_revenue_decline_3y: float = -0.5
    min_history_years: int = 5


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
