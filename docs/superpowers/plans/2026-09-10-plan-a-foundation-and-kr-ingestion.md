# 계획 A — 기반 구조와 한국 시장 데이터 수집·정규화

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `stockscout collect --market KR` 한 줄로 KOSPI 200 전 종목의 5개년 재무제표와 5년치 수정주가를 수집해, 한·미 공통 스키마로 정규화된 상태로 SQLite에 적재한다.

**Architecture:** 설계서(`docs/superpowers/specs/2026-09-10-stock-screener-design.md`) §4의 5계층 중 ①수집·②정규화·④저장을 구현한다. 모든 데이터 소스는 `PriceSource`/`FundamentalSource` 프로토콜 뒤에 격리되어, 나중에 소스를 교체해도 위 계층이 바뀌지 않는다. 원본 응답은 `raw_*` 테이블에 가공 없이 보존하고, 정규화 결과를 별도 테이블에 쓴다.

**Tech Stack:** Python 3.11+ / pydantic 2 / httpx / pandas / pykrx / PyYAML / python-dotenv / pytest

**참조 문서:** 설계서 §4(아키텍처) §5(데이터 소스) §6(데이터 모델) §7(정규화) §10(설정) §13(오류 처리) §14(테스트)

---

## 이 계획을 실행하기 전에

**DART API 키가 필요합니다.** https://opendart.fss.or.kr 에서 무료로 발급받아 `config/.env`에 넣습니다 (Task 2에서 설정). 키 없이는 Task 7 이후를 실행할 수 없습니다.

**외부 API를 다루는 방식에 대하여** — Task 7·9는 실제 응답 형태를 눈으로 확인한 뒤 파서를 작성합니다. DART의 계정과목 ID와 pykrx 함수 시그니처는 버전에 따라 다를 수 있으므로, 응답을 픽스처로 저장하고 그 픽스처에 대해 TDD를 합니다. 이후 모든 테스트는 네트워크 없이 돌아갑니다.

---

## 파일 구조

이 계획이 만드는 파일과 각각의 책임입니다.

| 파일 | 책임 |
|---|---|
| `pyproject.toml` | 패키지 정의, 의존성, pytest 설정 |
| `config/settings.yaml` | 기준·배점·임계치 (설계서 §10) |
| `config/.env` | `DART_API_KEY` 등 비밀값. git 제외 |
| `src/stockscout/config.py` | YAML·env → 검증된 `Config`/`Secrets` 객체 |
| `src/stockscout/models.py` | 공유 데이터 모델 — `Security` `RawStatement` `PriceBar` `Financials` |
| `src/stockscout/store/schema.sql` | 테이블 DDL (설계서 §6) |
| `src/stockscout/store/db.py` | SQLite 연결·초기화 |
| `src/stockscout/store/repository.py` | 테이블 읽기·쓰기. SQL은 여기에만 존재 |
| `src/stockscout/sources/base.py` | `PriceSource`/`FundamentalSource` 프로토콜과 예외 |
| `src/stockscout/sources/ratelimit.py` | 토큰버킷 + 재시도 데코레이터 |
| `src/stockscout/sources/dart.py` | OpenDART 클라이언트 (corp_code, 재무제표) |
| `src/stockscout/sources/krx.py` | pykrx 주가·유니버스 |
| `src/stockscout/normalize/kr_mapping.py` | DART 계정 → `Financials` 매핑 |
| `src/stockscout/pipeline/collect.py` | 수집 오케스트레이션 (증분·격리·요약) |
| `src/stockscout/pipeline/normalize.py` | 원본 → 공통 스키마 변환 |
| `src/stockscout/cli.py` | `universe` / `collect` / `normalize` 하위명령 |
| `tests/fixtures/` | 실제 API 응답 저장본 |

**경계 원칙:** `models.py`가 바닥이며 아무것도 import하지 않는다. 그 위에서 `sources/`는 원본을 반환할 뿐 해석하지 않고, `normalize/`는 순수 함수이며 네트워크·DB를 모르고, `store/`는 SQL만 안다. 이 셋은 서로를 모르며 `models.py`만 공유한다. `pipeline/`이 셋을 조립하고, `cli.py`가 pipeline을 부른다.

```
        cli
         ↓
      pipeline
     ↙   ↓   ↘
sources normalize store      ← 서로를 import하지 않는다
     ↘   ↓   ↙
       models                ← 아무것도 import하지 않는다
```

**모델을 별도 파일로 두는 이유:** 모델을 `store/repository.py`에 두면 `sources/`가 저장 계층을 import하게 되고, `normalize/`와 `store/`가 서로를 참조하는 순환이 생긴다. 데이터 모델은 어느 계층의 소유물도 아니므로 바닥에 둔다.

### 설계서와의 차이

설계서 §12의 CLI에는 `normalize`가 없고 정규화가 암묵적으로 수행된다. 이 계획은 **`normalize`를 독립 명령으로 분리**한다. 계정과목 매핑은 실제 데이터를 보기 전에는 완성할 수 없어서 여러 번 고쳐 돌리게 되는데, 그때마다 API를 다시 긁지 않으려면 별도 명령이어야 한다(Task 16 Step 5). 설계서 §5.3의 "수집과 분석 분리" 원칙을 정규화에도 적용한 것이다.

---

## 실행 중 발견된 변경 (2026-09-10)

### KRX 지수 API가 로그인 필수로 바뀌었다

Task 9의 탐색 단계에서 확인했다. KRX가 `data.krx.co.kr`를 로그인 기반 "데이터 마켓플레이스"로 전환하면서 pykrx의 시장·지수 단위 엔드포인트가 전부 막혔다. 실측 결과:

| pykrx 기능 | 익명 접근 |
|---|---|
| `get_market_ohlcv` (종목별 일봉) | ✅ 동작 |
| `get_market_ticker_name` (종목명) | ✅ 동작 |
| `get_index_portfolio_deposit_file` (지수 구성종목) | ❌ HTTP 400 `LOGOUT` |
| `get_market_ticker_list` (시장 전체) | ❌ 빈 결과 |
| `get_market_cap_by_ticker` (시가총액 일괄) | ❌ 예외 |

대안으로 FinanceDataReader도 시험했으나 백엔드가 HTTP 404로 응답한다.

**주가 수집은 영향이 없고, 유니버스 구성만 막힌다.** `KrxUniverse`(Task 9)는 코드를 그대로 두되, `KRX_ID`·`KRX_PW`가 있을 때 쓰는 경로가 된다.

한 가지 확인된 좋은 소식: `get_market_ohlcv`는 **기본값이 수정주가**다. 삼성전자 2018-05-04 50:1 액면분할 구간을 뽑아 가격 연속성을 확인했고, `adjusted=True`를 명시한 결과와 동일했다. 5년 평균 대비 하락률이 이 제품의 핵심 신호이므로 이것이 아니었으면 계산 전체가 무의미했다.

### Task 9b — 파일 기반 유니버스 (추가)

설계서 §4.1이 이미 `config/universe.yaml`(지수 구성종목 정의)을 명시하고 있으므로, 유니버스를 파일로 옮기는 것은 원래 설계와 어긋나지 않는다.

**Files:**
- Create: `config/universe.yaml` — KOSPI 대표 42종목. 코드는 pykrx 종목명 조회로 42개 전부 실측 검증했다
- Create: `src/stockscout/sources/universe.py` — `YamlUniverse`. `fetch() -> list[Security]`로 `KrxUniverse`와 교체 가능
- Test: `tests/test_universe_yaml.py`

`YamlUniverse`가 `KrxUniverse`와 다른 점 하나: **종목명 조회에 실패해도 그 종목을 버리지 않고** 코드를 이름 삼아 포함한다. 손으로 고른 목록이므로 조회 실패는 잘못된 티커가 아니라 일시적 네트워크 문제로 보는 것이 맞다.

**YAML에서 종목코드는 반드시 따옴표로 묶는다.** `005930`을 그냥 쓰면 정수 `5930`이 되어 앞자리 0이 사라진다. 실제 파일을 로드해 6자리 문자열인지 확인하는 회귀 테스트를 둔다.

Task 15의 CLI는 `KRX_ID`·`KRX_PW` 유무로 두 provider 중 하나를 고른다.

### 발행주식수·배당은 다른 엔드포인트에 있다 — 계획 B가 알아야 한다

Task 12의 `ACCOUNT_MAP`이 `Financials`의 13개 필드 중 11개만 채운다. 빠진 둘은 `shares_outstanding`과 `dividend_per_share`이며, 이는 매핑 누락이 아니라 소스의 구조다. DART의 단일회사 전체 재무제표 API(`fnlttSinglAcntAll`)는 재무제표 본문만 준다. 발행주식수는 「주식의 총수 현황」, 배당은 「배당에 관한 사항」이라는 별도 공시 항목이다.

결과적으로 설계서 §9.2의 세 지표가 전부 계산되지 않는다.

```
market_cap = 현재가 × shares_outstanding   → NULL
PER        = 현재가 ÷ EPS                  → NULL
PBR        = 현재가 ÷ BPS                  → NULL
```

설계서 §9.2가 이미 "`shares_outstanding`이 결측이면 세 값 모두 NULL이며 화면에는 `—`로 표시한다"고 정해두었으므로 시스템이 깨지지는 않는다. 다만 설계서 §11.1의 **시가총액순 정렬**과 §11.2의 **PER KPI 카드**가 항상 비어 있게 된다.

**계획 B 착수 전에 결정해야 한다.** 선택지는 세 가지다.

1. DART 추가 엔드포인트를 수집한다 — 종목당 API 호출이 늘지만 데이터가 정확하다
2. pykrx의 종목별 조회로 시가총액을 받는다 — 시장 단위 조회는 로그인이 필요하지만 종목별은 익명으로 동작할 가능성이 있다(미확인)
3. 세 지표를 v1에서 빼고 화면에서도 내린다 — 우량주 점수와 하락률만으로 판정한다. 설계서 §8.2가 이미 밸류에이션을 점수에서 제외했으므로 판정 로직에는 영향이 없다

### Task 8·12·16은 DART API 키를 기다린다

Task 7은 오프라인 범위(파서 + 클라이언트 골격)만 수행했다. 실제 API를 호출하는 Step 1·7은 키가 생긴 뒤 실행한다. Task 8과 12는 삼성전자 실제 응답을 픽스처로 저장한 뒤 그에 맞춰 계정과목을 매핑하는 구조라, 키 없이는 추측이 된다.

---

## Task 1: 프로젝트 뼈대

**Files:**
- Create: `pyproject.toml`
- Create: `src/stockscout/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/test_smoke.py`

- [ ] **Step 1: 실패하는 테스트를 작성한다**

`tests/test_smoke.py`:

```python
def test_package_imports():
    import stockscout

    assert stockscout.__version__ == "0.1.0"
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

Run: `python -m pytest tests/test_smoke.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'stockscout'`

- [ ] **Step 3: 패키지 정의를 작성한다**

`pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "stockscout"
version = "0.1.0"
description = "한국·미국 우량주 낙폭 스크리너"
requires-python = ">=3.11"
dependencies = [
    "pydantic>=2.5",
    "PyYAML>=6.0",
    "python-dotenv>=1.0",
    "httpx>=0.27",
    "pandas>=2.1",
    "pykrx>=1.0.45",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-mock>=3.12"]

[project.scripts]
stockscout = "stockscout.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
addopts = "-q"
```

`src/stockscout/__init__.py`:

```python
__version__ = "0.1.0"
```

`tests/__init__.py`: 빈 파일로 생성한다.

- [ ] **Step 4: 가상환경을 만들고 설치한다**

```bash
python -m venv .venv
.venv/Scripts/activate      # Windows. macOS/Linux는 source .venv/bin/activate
pip install -e ".[dev]"
```

- [ ] **Step 5: 테스트가 통과하는지 확인한다**

Run: `python -m pytest tests/test_smoke.py -v`
Expected: PASS — `1 passed`

- [ ] **Step 6: 커밋한다**

```bash
git add pyproject.toml src/stockscout/__init__.py tests/__init__.py tests/test_smoke.py
git commit -m "chore: 프로젝트 뼈대와 pytest 설정"
```

---

## Task 2: 설정 로더

설계서 §10의 `settings.yaml`을 검증된 객체로 읽어들인다. 임계치를 코드에 박지 않기 위한 토대다.

**Files:**
- Create: `src/stockscout/config.py`
- Create: `config/settings.yaml`
- Create: `config/.env.example`
- Test: `tests/test_config.py`

- [ ] **Step 1: 실패하는 테스트를 작성한다**

`tests/test_config.py`:

```python
import textwrap

import pytest

from stockscout.config import Config, Secrets, load_config, load_secrets


def test_load_config_reads_yaml(tmp_path):
    path = tmp_path / "settings.yaml"
    path.write_text(
        textwrap.dedent(
            """
            universe:
              markets: [KR]
              exclude_financials: true
            lookback:
              primary_window: 3
            signal:
              min_score: 65
              drop_vs_avg_threshold: -0.35
            """
        ),
        encoding="utf-8",
    )

    cfg = load_config(path)

    assert cfg.universe.markets == ["KR"]
    assert cfg.lookback.primary_window == 3
    assert cfg.signal.min_score == 65
    assert cfg.signal.drop_vs_avg_threshold == -0.35


def test_load_config_applies_defaults_for_omitted_sections(tmp_path):
    path = tmp_path / "settings.yaml"
    path.write_text("universe:\n  markets: [KR]\n", encoding="utf-8")

    cfg = load_config(path)

    assert cfg.hard_filters.max_debt_ratio == 200.0
    assert cfg.lookback.price_years == [1, 3, 5]
    assert cfg.sources.kr_price == "pykrx"


def test_primary_window_must_be_in_price_years(tmp_path):
    path = tmp_path / "settings.yaml"
    path.write_text(
        "lookback:\n  price_years: [1, 3]\n  primary_window: 5\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="primary_window"):
        load_config(path)


def test_load_secrets_requires_dart_key(tmp_path, monkeypatch):
    monkeypatch.delenv("DART_API_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text("SEC_USER_AGENT=x\n", encoding="utf-8")

    with pytest.raises(ValueError, match="DART_API_KEY"):
        load_secrets(env)


def test_load_secrets_reads_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("DART_API_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text("DART_API_KEY=abc123\n", encoding="utf-8")

    secrets = load_secrets(env)

    assert secrets.dart_api_key == "abc123"
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'stockscout.config'`

- [ ] **Step 3: 설정 모델을 구현한다**

`src/stockscout/config.py`:

```python
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
```

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS — `5 passed`

- [ ] **Step 5: 실제 설정 파일을 만든다**

`config/settings.yaml` — 설계서 §10을 그대로 옮긴다:

```yaml
universe:
  markets: [KR]              # 계획 D에서 US 추가
  kr_index: KOSPI200
  us_index: SP500
  exclude_financials: true

lookback:
  price_years: [1, 3, 5]
  primary_window: 5
  financial_years: 5
  min_data_coverage: 0.8

hard_filters:
  max_debt_ratio: 200
  consecutive_operating_loss_years: 3
  min_interest_coverage: 1.0
  interest_coverage_years: 2
  max_revenue_decline_3y: -0.5
  min_history_years: 5

scoring:
  profitability:
    roe_avg3:        {points: 10, bands: [[0.15, 10], [0.10, 7], [0.05, 4], [0.0, 1]]}
    op_margin_avg3:  {points: 10, bands: [[0.15, 10], [0.10, 7], [0.05, 4], [0.0, 1]]}
    op_margin_trend: {points: 5}
  growth:
    revenue_cagr3:      {points: 10, bands: [[0.15, 10], [0.10, 8], [0.05, 5], [0.0, 2]]}
    op_income_cagr3:    {points: 10, bands: [[0.15, 10], [0.10, 8], [0.05, 5], [0.0, 2]]}
    growth_consistency: {points: 5}
  stability:
    debt_ratio:        {points: 10, bands: [[50, 10], [100, 7], [150, 4], [200, 1]], lower_is_better: true}
    current_ratio:     {points: 5,  bands: [[2.0, 5], [1.5, 3.5], [1.0, 2]]}
    interest_coverage: {points: 10, bands: [[10, 10], [5, 7], [3, 4], [1, 2]]}
  cashflow:
    ocf_positive_years: {points: 10, bands: [[5, 10], [4, 8], [3, 5], [2, 3]]}
    ocf_to_op_income:   {points: 10, bands: [[1.2, 10], [1.0, 8], [0.8, 5], [0.5, 2]]}
    fcf_positive_years: {points: 5,  bands: [[3, 5], [2, 3], [1, 1]]}

signal:
  min_score: 70
  drop_vs_avg_threshold: -0.40
  drop_vs_high_threshold: -0.40

sources:
  kr_fundamental: dart
  kr_price: pykrx
  us_fundamental: edgar
  us_price: yfinance
```

`config/.env.example`:

```
# https://opendart.fss.or.kr 에서 무료 발급
DART_API_KEY=여기에_발급받은_키
# SEC EDGAR는 연락처가 담긴 User-Agent를 요구한다 (계획 D에서 사용)
SEC_USER_AGENT=stockscout/0.1 (your@email.com)
```

- [ ] **Step 6: 실제 설정이 로드되는지 확인한다**

Run: `python -c "from stockscout.config import load_config; c = load_config(); print(c.signal, len(c.scoring))"`
Expected: `min_score=70.0 drop_vs_avg_threshold=-0.4 drop_vs_high_threshold=-0.4 4`

- [ ] **Step 7: 실제 .env를 만든다 (커밋하지 않는다)**

```bash
cp config/.env.example config/.env
```

`config/.env`를 열어 발급받은 DART 키를 넣는다. `.gitignore`에 이미 `config/.env`가 있으므로 커밋되지 않는다.

Run: `python -c "from stockscout.config import load_secrets; print(len(load_secrets().dart_api_key))"`
Expected: `40` (DART 키 길이. 값 자체는 출력하지 않는다)

- [ ] **Step 8: 커밋한다**

```bash
git add src/stockscout/config.py config/settings.yaml config/.env.example tests/test_config.py
git commit -m "feat: 설정 로더와 settings.yaml"
```

---

## Task 3: SQLite 스키마와 연결

**Files:**
- Create: `src/stockscout/store/__init__.py`
- Create: `src/stockscout/store/schema.sql`
- Create: `src/stockscout/store/db.py`
- Test: `tests/test_db.py`

- [ ] **Step 1: 실패하는 테스트를 작성한다**

`tests/test_db.py`:

```python
from stockscout.store.db import connect, init_db

EXPECTED_TABLES = {
    "securities",
    "raw_financials",
    "raw_prices",
    "financials",
    "snapshots",
    "analysis_results",
    "watchlist",
    "collect_errors",
}


def test_init_db_creates_all_tables(tmp_path):
    db_path = tmp_path / "test.db"

    with connect(db_path) as conn:
        init_db(conn)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()

    names = {r["name"] for r in rows}
    assert EXPECTED_TABLES <= names


def test_init_db_is_idempotent(tmp_path):
    db_path = tmp_path / "test.db"

    with connect(db_path) as conn:
        init_db(conn)
        conn.execute(
            "INSERT INTO watchlist (ticker, added_at) VALUES ('005930.KS', '2026-01-01')"
        )
        conn.commit()
        init_db(conn)  # 두 번째 호출이 데이터를 지우면 안 된다
        count = conn.execute("SELECT COUNT(*) AS n FROM watchlist").fetchone()["n"]

    assert count == 1


def test_connect_returns_dict_like_rows(tmp_path):
    with connect(tmp_path / "t.db") as conn:
        init_db(conn)
        conn.execute(
            "INSERT INTO watchlist (ticker, added_at) VALUES ('AAPL', '2026-01-01')"
        )
        row = conn.execute("SELECT ticker FROM watchlist").fetchone()

    assert row["ticker"] == "AAPL"


def test_foreign_keys_are_enabled(tmp_path):
    with connect(tmp_path / "t.db") as conn:
        on = conn.execute("PRAGMA foreign_keys").fetchone()[0]

    assert on == 1
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

Run: `python -m pytest tests/test_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'stockscout.store'`

- [ ] **Step 3: DDL을 작성한다**

`src/stockscout/store/schema.sql` — 설계서 §6 그대로:

```sql
CREATE TABLE IF NOT EXISTS securities (
  ticker        TEXT PRIMARY KEY,
  market        TEXT NOT NULL,
  name          TEXT NOT NULL,
  name_en       TEXT,
  exchange      TEXT,
  sector        TEXT,
  is_financial  INTEGER NOT NULL DEFAULT 0,
  source_id     TEXT,
  currency      TEXT NOT NULL,
  listed_at     TEXT,
  in_universe   INTEGER NOT NULL DEFAULT 1,
  updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_financials (
  ticker      TEXT NOT NULL,
  fiscal_year INTEGER NOT NULL,
  source      TEXT NOT NULL,
  payload     TEXT NOT NULL,
  fetched_at  TEXT NOT NULL,
  PRIMARY KEY (ticker, fiscal_year, source)
);

CREATE TABLE IF NOT EXISTS raw_prices (
  ticker TEXT NOT NULL,
  date   TEXT NOT NULL,
  close  REAL NOT NULL,
  volume INTEGER,
  source TEXT NOT NULL,
  PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS financials (
  ticker              TEXT NOT NULL,
  fiscal_year         INTEGER NOT NULL,
  currency            TEXT NOT NULL,
  revenue             REAL,
  operating_income    REAL,
  net_income          REAL,
  total_assets        REAL,
  total_liabilities   REAL,
  total_equity        REAL,
  current_assets      REAL,
  current_liabilities REAL,
  interest_expense    REAL,
  operating_cash_flow REAL,
  capex               REAL,
  shares_outstanding  REAL,
  dividend_per_share  REAL,
  missing_fields      TEXT,
  PRIMARY KEY (ticker, fiscal_year)
);

CREATE TABLE IF NOT EXISTS snapshots (
  snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_at      TEXT NOT NULL,
  config_hash TEXT NOT NULL,
  config_json TEXT NOT NULL,
  note        TEXT
);

CREATE TABLE IF NOT EXISTS analysis_results (
  snapshot_id     INTEGER NOT NULL,
  ticker          TEXT NOT NULL,
  passed_filters  INTEGER NOT NULL,
  filter_failures TEXT,
  score_total     REAL,
  score_profit    REAL,
  score_growth    REAL,
  score_stability REAL,
  score_cashflow  REAL,
  score_detail    TEXT,
  price_current   REAL,
  avg_price_1y    REAL,
  avg_price_3y    REAL,
  avg_price_5y    REAL,
  high_5y         REAL,
  drop_vs_avg_1y  REAL,
  drop_vs_avg_3y  REAL,
  drop_vs_avg_5y  REAL,
  drop_vs_high    REAL,
  market_cap      REAL,
  per             REAL,
  pbr             REAL,
  signal_grade    TEXT,
  signal_reason   TEXT,
  PRIMARY KEY (snapshot_id, ticker),
  FOREIGN KEY (snapshot_id) REFERENCES snapshots(snapshot_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS watchlist (
  ticker   TEXT PRIMARY KEY,
  added_at TEXT NOT NULL,
  memo     TEXT
);

CREATE TABLE IF NOT EXISTS collect_errors (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  ticker      TEXT NOT NULL,
  source      TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  message     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_raw_prices_ticker_date ON raw_prices(ticker, date);
CREATE INDEX IF NOT EXISTS idx_financials_ticker ON financials(ticker);
CREATE INDEX IF NOT EXISTS idx_analysis_snapshot ON analysis_results(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_securities_universe ON securities(market, in_universe);
```

`src/stockscout/store/db.py`:

```python
"""SQLite 연결과 초기화."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA_PATH = Path(__file__).parent / "schema.sql"
DEFAULT_DB_PATH = Path("data/stockscout.db")


@contextmanager
def connect(db_path: Path | str = DEFAULT_DB_PATH) -> Iterator[sqlite3.Connection]:
    """행을 dict처럼 다룰 수 있는 연결을 연다. 블록을 벗어나면 닫힌다."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
    finally:
        conn.close()


def init_db(conn: sqlite3.Connection) -> None:
    """스키마를 적용한다. 여러 번 호출해도 안전하다(CREATE TABLE IF NOT EXISTS)."""
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
```

`src/stockscout/store/__init__.py`: 빈 파일로 생성한다.

- [ ] **Step 4: schema.sql이 패키지에 포함되도록 설정한다**

`pyproject.toml`의 `[tool.setuptools.packages.find]` 바로 아래에 추가한다:

```toml
[tool.setuptools.package-data]
stockscout = ["store/*.sql"]
```

- [ ] **Step 5: 테스트가 통과하는지 확인한다**

Run: `python -m pytest tests/test_db.py -v`
Expected: PASS — `4 passed`

- [ ] **Step 6: 커밋한다**

```bash
git add src/stockscout/store/ tests/test_db.py pyproject.toml
git commit -m "feat: SQLite 스키마와 연결 계층"
```

---

## Task 4: 공유 모델과 종목 마스터 Repository

`models.py`는 이 프로젝트의 바닥이다. 아무것도 import하지 않으며, 모든 계층이 여기만 공유한다. SQL은 `repository.py`에만 존재한다.

**Files:**
- Create: `src/stockscout/models.py`
- Create: `src/stockscout/store/repository.py`
- Test: `tests/test_repository_securities.py`

- [ ] **Step 1: 실패하는 테스트를 작성한다**

`tests/test_repository_securities.py`:

```python
import pytest

from stockscout.models import Security
from stockscout.store.db import connect, init_db
from stockscout.store.repository import Repository


@pytest.fixture
def repo(tmp_path):
    with connect(tmp_path / "t.db") as conn:
        init_db(conn)
        yield Repository(conn)


def make_security(ticker="005930.KS", **overrides) -> Security:
    base = dict(
        ticker=ticker,
        market="KR",
        name="삼성전자",
        exchange="KOSPI",
        currency="KRW",
        source_id="00126380",
        is_financial=False,
        in_universe=True,
    )
    base.update(overrides)
    return Security(**base)


def test_upsert_then_get(repo):
    repo.upsert_security(make_security())

    got = repo.get_security("005930.KS")

    assert got is not None
    assert got.name == "삼성전자"
    assert got.source_id == "00126380"
    assert got.is_financial is False


def test_get_missing_returns_none(repo):
    assert repo.get_security("NOPE") is None


def test_upsert_overwrites_existing(repo):
    repo.upsert_security(make_security(name="구명칭"))
    repo.upsert_security(make_security(name="삼성전자"))

    assert repo.get_security("005930.KS").name == "삼성전자"
    assert len(repo.list_securities()) == 1


def test_list_securities_filters_by_market_and_universe(repo):
    repo.upsert_security(make_security("005930.KS", market="KR"))
    repo.upsert_security(make_security("AAPL", market="US", name="Apple", currency="USD"))
    repo.upsert_security(make_security("000660.KS", market="KR", name="탈락", in_universe=False))

    kr = repo.list_securities(market="KR")
    all_including_dropped = repo.list_securities(market="KR", in_universe_only=False)

    assert [s.ticker for s in kr] == ["005930.KS"]
    assert len(all_including_dropped) == 2


def test_mark_out_of_universe(repo):
    repo.upsert_security(make_security())

    repo.mark_out_of_universe(["005930.KS"])

    assert repo.list_securities(market="KR") == []
    assert repo.get_security("005930.KS").in_universe is False
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

Run: `python -m pytest tests/test_repository_securities.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'stockscout.models'`

- [ ] **Step 3: 공유 모델을 만든다**

`src/stockscout/models.py`:

```python
"""공유 데이터 모델.

이 파일은 프로젝트의 바닥이며 stockscout의 어떤 모듈도 import하지 않는다.
sources·normalize·store가 서로를 모른 채 같은 모델로 대화하게 하는 것이 목적이다.
"""

from __future__ import annotations

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
```

- [ ] **Step 4: Repository를 구현한다**

`src/stockscout/store/repository.py`:

```python
"""테이블 읽기·쓰기. 이 프로젝트의 SQL은 전부 여기에 있다."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from stockscout.models import Security


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Repository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # --- 종목 마스터 -------------------------------------------------

    def upsert_security(self, sec: Security) -> None:
        self.conn.execute(
            """
            INSERT INTO securities (
                ticker, market, name, name_en, exchange, sector,
                is_financial, source_id, currency, listed_at, in_universe, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                market=excluded.market,
                name=excluded.name,
                name_en=excluded.name_en,
                exchange=excluded.exchange,
                sector=excluded.sector,
                is_financial=excluded.is_financial,
                source_id=excluded.source_id,
                currency=excluded.currency,
                listed_at=excluded.listed_at,
                in_universe=excluded.in_universe,
                updated_at=excluded.updated_at
            """,
            (
                sec.ticker, sec.market, sec.name, sec.name_en, sec.exchange,
                sec.sector, int(sec.is_financial), sec.source_id, sec.currency,
                sec.listed_at, int(sec.in_universe), _now(),
            ),
        )
        self.conn.commit()

    def get_security(self, ticker: str) -> Security | None:
        row = self.conn.execute(
            "SELECT * FROM securities WHERE ticker = ?", (ticker,)
        ).fetchone()
        return self._to_security(row) if row else None

    def list_securities(
        self, market: str | None = None, in_universe_only: bool = True
    ) -> list[Security]:
        sql = "SELECT * FROM securities WHERE 1=1"
        params: list[object] = []
        if market:
            sql += " AND market = ?"
            params.append(market)
        if in_universe_only:
            sql += " AND in_universe = 1"
        sql += " ORDER BY ticker"
        rows = self.conn.execute(sql, params).fetchall()
        return [self._to_security(r) for r in rows]

    def mark_out_of_universe(self, tickers: list[str]) -> None:
        self.conn.executemany(
            "UPDATE securities SET in_universe = 0, updated_at = ? WHERE ticker = ?",
            [(_now(), t) for t in tickers],
        )
        self.conn.commit()

    @staticmethod
    def _to_security(row: sqlite3.Row) -> Security:
        return Security(
            ticker=row["ticker"],
            market=row["market"],
            name=row["name"],
            name_en=row["name_en"],
            exchange=row["exchange"],
            sector=row["sector"],
            is_financial=bool(row["is_financial"]),
            source_id=row["source_id"],
            currency=row["currency"],
            listed_at=row["listed_at"],
            in_universe=bool(row["in_universe"]),
        )
```

- [ ] **Step 5: 테스트가 통과하는지 확인한다**

Run: `python -m pytest tests/test_repository_securities.py -v`
Expected: PASS — `5 passed`

- [ ] **Step 6: 커밋한다**

```bash
git add src/stockscout/models.py src/stockscout/store/repository.py tests/test_repository_securities.py
git commit -m "feat: 공유 모델과 종목 마스터 repository"
```

---

## Task 5: Repository — 원본 데이터와 오류 기록

증분 수집(설계서 §5.3)에 필요한 "이미 있는지" 조회가 핵심이다.

**Files:**
- Modify: `src/stockscout/models.py`
- Modify: `src/stockscout/store/repository.py`
- Test: `tests/test_repository_raw.py`

- [ ] **Step 1: 실패하는 테스트를 작성한다**

`tests/test_repository_raw.py`:

```python
from datetime import date

import pytest

from stockscout.models import PriceBar, RawStatement
from stockscout.store.db import connect, init_db
from stockscout.store.repository import Repository


@pytest.fixture
def repo(tmp_path):
    with connect(tmp_path / "t.db") as conn:
        init_db(conn)
        yield Repository(conn)


def test_save_and_read_raw_statement(repo):
    stmt = RawStatement(
        ticker="005930.KS", fiscal_year=2025, source="dart",
        payload={"list": [{"account_id": "ifrs-full_Revenue", "thstrm_amount": "300"}]},
    )

    repo.save_raw_statements([stmt])
    got = repo.get_raw_statements("005930.KS")

    assert len(got) == 1
    assert got[0].payload["list"][0]["account_id"] == "ifrs-full_Revenue"


def test_existing_statement_years_supports_incremental_collect(repo):
    repo.save_raw_statements([
        RawStatement(ticker="005930.KS", fiscal_year=2024, source="dart", payload={}),
        RawStatement(ticker="005930.KS", fiscal_year=2025, source="dart", payload={}),
    ])

    years = repo.existing_statement_years("005930.KS", source="dart")

    assert years == {2024, 2025}


def test_save_raw_statements_overwrites_same_key(repo):
    repo.save_raw_statements([
        RawStatement(ticker="005930.KS", fiscal_year=2025, source="dart", payload={"v": 1})
    ])
    repo.save_raw_statements([
        RawStatement(ticker="005930.KS", fiscal_year=2025, source="dart", payload={"v": 2})
    ])

    got = repo.get_raw_statements("005930.KS")

    assert len(got) == 1
    assert got[0].payload["v"] == 2


def test_save_and_read_price_bars(repo):
    bars = [
        PriceBar(ticker="005930.KS", date=date(2026, 1, 5), close=71000.0, volume=1000),
        PriceBar(ticker="005930.KS", date=date(2026, 1, 6), close=72000.0, volume=2000),
    ]

    repo.save_price_bars(bars, source="pykrx")
    got = repo.get_price_bars("005930.KS")

    assert [b.close for b in got] == [71000.0, 72000.0]
    assert got[0].date == date(2026, 1, 5)


def test_latest_price_date_is_none_when_empty(repo):
    assert repo.latest_price_date("005930.KS") is None


def test_latest_price_date_returns_max(repo):
    repo.save_price_bars([
        PriceBar(ticker="005930.KS", date=date(2026, 1, 5), close=1.0),
        PriceBar(ticker="005930.KS", date=date(2026, 3, 2), close=2.0),
    ], source="pykrx")

    assert repo.latest_price_date("005930.KS") == date(2026, 3, 2)


def test_get_price_bars_respects_date_range(repo):
    repo.save_price_bars([
        PriceBar(ticker="A", date=date(2026, 1, 1), close=1.0),
        PriceBar(ticker="A", date=date(2026, 6, 1), close=2.0),
    ], source="pykrx")

    got = repo.get_price_bars("A", start=date(2026, 5, 1))

    assert [b.close for b in got] == [2.0]


def test_record_error_and_summarize(repo):
    repo.record_error("005930.KS", "dart", "timeout")
    repo.record_error("000660.KS", "dart", "404")

    errors = repo.recent_errors(limit=10)

    assert len(errors) == 2
    assert {e["ticker"] for e in errors} == {"005930.KS", "000660.KS"}
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

Run: `python -m pytest tests/test_repository_raw.py -v`
Expected: FAIL — `ImportError: cannot import name 'PriceBar'`

- [ ] **Step 3: 모델을 추가한다**

`src/stockscout/models.py`의 import 블록에 추가:

```python
from datetime import date
```

`Security` 클래스 **아래에** 두 모델을 추가한다:

```python
class RawStatement(BaseModel):
    """소스 응답을 가공 없이 담는다. 해석은 normalize 계층의 책임이다."""

    ticker: str
    fiscal_year: int
    source: str          # 'dart' | 'edgar'
    payload: dict


class PriceBar(BaseModel):
    ticker: str
    date: date
    close: float         # 수정종가
    volume: int | None = None
```

- [ ] **Step 4: Repository에 메서드를 추가한다**

`src/stockscout/store/repository.py`의 import 블록을 다음으로 교체한다:

```python
import json
import sqlite3
from datetime import date, datetime, timezone

from stockscout.models import PriceBar, RawStatement, Security
```

`Repository` 클래스 안, `_to_security` **앞에** 다음 메서드들을 추가한다:

```python
    # --- 원본 재무 ---------------------------------------------------

    def save_raw_statements(self, statements: list[RawStatement]) -> None:
        self.conn.executemany(
            """
            INSERT INTO raw_financials (ticker, fiscal_year, source, payload, fetched_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(ticker, fiscal_year, source) DO UPDATE SET
                payload=excluded.payload, fetched_at=excluded.fetched_at
            """,
            [
                (s.ticker, s.fiscal_year, s.source, json.dumps(s.payload, ensure_ascii=False), _now())
                for s in statements
            ],
        )
        self.conn.commit()

    def get_raw_statements(self, ticker: str, source: str | None = None) -> list[RawStatement]:
        sql = "SELECT * FROM raw_financials WHERE ticker = ?"
        params: list[object] = [ticker]
        if source:
            sql += " AND source = ?"
            params.append(source)
        sql += " ORDER BY fiscal_year"
        rows = self.conn.execute(sql, params).fetchall()
        return [
            RawStatement(
                ticker=r["ticker"],
                fiscal_year=r["fiscal_year"],
                source=r["source"],
                payload=json.loads(r["payload"]),
            )
            for r in rows
        ]

    def existing_statement_years(self, ticker: str, source: str) -> set[int]:
        """증분 수집용. 이미 받아둔 연도는 다시 요청하지 않는다."""
        rows = self.conn.execute(
            "SELECT fiscal_year FROM raw_financials WHERE ticker = ? AND source = ?",
            (ticker, source),
        ).fetchall()
        return {r["fiscal_year"] for r in rows}

    # --- 원본 주가 ---------------------------------------------------

    def save_price_bars(self, bars: list[PriceBar], source: str) -> None:
        self.conn.executemany(
            """
            INSERT INTO raw_prices (ticker, date, close, volume, source)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(ticker, date) DO UPDATE SET
                close=excluded.close, volume=excluded.volume, source=excluded.source
            """,
            [(b.ticker, b.date.isoformat(), b.close, b.volume, source) for b in bars],
        )
        self.conn.commit()

    def get_price_bars(
        self, ticker: str, start: date | None = None, end: date | None = None
    ) -> list[PriceBar]:
        sql = "SELECT * FROM raw_prices WHERE ticker = ?"
        params: list[object] = [ticker]
        if start:
            sql += " AND date >= ?"
            params.append(start.isoformat())
        if end:
            sql += " AND date <= ?"
            params.append(end.isoformat())
        sql += " ORDER BY date"
        rows = self.conn.execute(sql, params).fetchall()
        return [
            PriceBar(
                ticker=r["ticker"],
                date=date.fromisoformat(r["date"]),
                close=r["close"],
                volume=r["volume"],
            )
            for r in rows
        ]

    def latest_price_date(self, ticker: str) -> date | None:
        """증분 수집용. 이 날짜 다음날부터 받으면 된다."""
        row = self.conn.execute(
            "SELECT MAX(date) AS d FROM raw_prices WHERE ticker = ?", (ticker,)
        ).fetchone()
        return date.fromisoformat(row["d"]) if row and row["d"] else None

    # --- 오류 기록 ---------------------------------------------------

    def record_error(self, ticker: str, source: str, message: str) -> None:
        self.conn.execute(
            "INSERT INTO collect_errors (ticker, source, occurred_at, message) VALUES (?, ?, ?, ?)",
            (ticker, source, _now(), message),
        )
        self.conn.commit()

    def recent_errors(self, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            "SELECT ticker, source, occurred_at, message FROM collect_errors "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
```

- [ ] **Step 5: 테스트가 통과하는지 확인한다**

Run: `python -m pytest tests/test_repository_raw.py -v`
Expected: PASS — `8 passed`

- [ ] **Step 6: 커밋한다**

```bash
git add src/stockscout/models.py src/stockscout/store/repository.py tests/test_repository_raw.py
git commit -m "feat: 원본 재무·주가·오류 repository"
```

---

## Task 6: 소스 인터페이스와 레이트리밋

설계서 §5.2의 교체 지점. 이 두 프로토콜이 이 프로젝트에서 가장 중요한 경계다.

**Files:**
- Create: `src/stockscout/sources/__init__.py`
- Create: `src/stockscout/sources/base.py`
- Create: `src/stockscout/sources/ratelimit.py`
- Test: `tests/test_ratelimit.py`

- [ ] **Step 1: 실패하는 테스트를 작성한다**

`tests/test_ratelimit.py`:

```python
import pytest

from stockscout.sources.ratelimit import (
    DailyQuotaExceeded,
    DailyQuota,
    TokenBucket,
    retry_on_transient,
)


def test_token_bucket_allows_burst_up_to_capacity():
    clock = {"t": 0.0}
    bucket = TokenBucket(rate_per_sec=10, capacity=10, time_fn=lambda: clock["t"])

    waits = [bucket.acquire() for _ in range(10)]

    assert waits == [0.0] * 10


def test_token_bucket_reports_wait_when_drained():
    clock = {"t": 0.0}
    bucket = TokenBucket(rate_per_sec=10, capacity=10, time_fn=lambda: clock["t"])
    for _ in range(10):
        bucket.acquire()

    wait = bucket.acquire()

    assert wait == pytest.approx(0.1, abs=1e-6)


def test_token_bucket_refills_over_time():
    clock = {"t": 0.0}
    bucket = TokenBucket(rate_per_sec=10, capacity=10, time_fn=lambda: clock["t"])
    for _ in range(10):
        bucket.acquire()

    clock["t"] = 1.0  # 1초 뒤 10개 충전
    waits = [bucket.acquire() for _ in range(10)]

    assert waits == [0.0] * 10


def test_daily_quota_raises_when_exhausted():
    quota = DailyQuota(limit=2)
    quota.consume()
    quota.consume()

    with pytest.raises(DailyQuotaExceeded):
        quota.consume()


def test_retry_retries_transient_and_succeeds():
    calls = {"n": 0}

    @retry_on_transient(attempts=3, base_delay=0.0)
    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise TimeoutError("boom")
        return "ok"

    assert flaky() == "ok"
    assert calls["n"] == 3


def test_retry_gives_up_after_attempts():
    calls = {"n": 0}

    @retry_on_transient(attempts=2, base_delay=0.0)
    def always_fails():
        calls["n"] += 1
        raise TimeoutError("boom")

    with pytest.raises(TimeoutError):
        always_fails()
    assert calls["n"] == 2


def test_retry_does_not_retry_permanent_errors():
    calls = {"n": 0}

    @retry_on_transient(attempts=3, base_delay=0.0)
    def permanent():
        calls["n"] += 1
        raise PermissionError("bad key")

    with pytest.raises(PermissionError):
        permanent()
    assert calls["n"] == 1  # 재시도하지 않는다


def test_retry_covers_httpx_transport_errors():
    """httpx의 연결 오류는 내장 ConnectionError를 상속하지 않는다.

    내장 예외만 나열하면 실제 네트워크 장애에서 재시도가 죽는다.
    """
    import httpx

    calls = {"n": 0}

    @retry_on_transient(attempts=3, base_delay=0.0)
    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("connection refused")
        return "ok"

    assert flaky() == "ok"
    assert calls["n"] == 3


def test_retry_does_not_retry_http_status_errors():
    """404·401은 재시도해도 소용없다 (설계서 §5.3)."""
    import httpx

    calls = {"n": 0}

    @retry_on_transient(attempts=3, base_delay=0.0)
    def not_found():
        calls["n"] += 1
        raise httpx.HTTPStatusError(
            "404", request=httpx.Request("GET", "http://x"),
            response=httpx.Response(404),
        )

    with pytest.raises(httpx.HTTPStatusError):
        not_found()
    assert calls["n"] == 1
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

Run: `python -m pytest tests/test_ratelimit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'stockscout.sources'`

- [ ] **Step 3: 프로토콜을 구현한다**

`src/stockscout/sources/base.py`:

```python
"""데이터 소스 교체 지점. 모든 소스는 이 두 프로토콜만 구현한다.

여기를 지키면 유료 API로 옮기거나 소스가 깨졌을 때
파일 하나를 추가하고 설정에서 이름만 바꾸면 된다.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable

from stockscout.models import PriceBar, RawStatement


class SourceError(Exception):
    """소스 호출 실패. 종목 단위로 격리되어 배치를 멈추지 않는다."""


class PermanentSourceError(SourceError):
    """재시도해도 소용없는 실패 (404, 인증 오류, 데이터 없음)."""


@runtime_checkable
class PriceSource(Protocol):
    name: str

    def fetch_daily(self, ticker: str, start: date, end: date) -> list[PriceBar]:
        """수정종가 일봉을 날짜 오름차순으로 반환한다."""
        ...


@runtime_checkable
class FundamentalSource(Protocol):
    name: str

    def fetch_annual(self, ticker: str, years: list[int]) -> list[RawStatement]:
        """요청한 회계연도의 연간 재무제표 원본을 반환한다.

        일부 연도가 없으면 그 연도만 빠진 목록을 반환한다(예외를 던지지 않는다).
        """
        ...
```

`src/stockscout/sources/ratelimit.py`:

```python
"""레이트리밋과 재시도. 설계서 §5.3·§13."""

from __future__ import annotations

import functools
import time
from typing import Callable, TypeVar

import httpx

T = TypeVar("T")

# 재시도할 가치가 있는 예외. 네트워크 일시 장애만 해당한다.
#
# httpx.TransportError를 반드시 포함해야 한다. httpx의 연결·타임아웃 오류는
# 파이썬 내장 ConnectionError/TimeoutError를 상속하지 않으므로,
# 내장 예외만 나열하면 실제 네트워크 장애에서 재시도가 전혀 동작하지 않는다.
#
# 반대로 bare OSError는 절대 넣지 않는다. PermissionError·FileNotFoundError 같은
# "영구" 오류가 전부 OSError의 하위클래스라서, OSError를 통째로 넣으면
# 인증 오류까지 재시도하게 된다. TimeoutError·ConnectionError는 의미가 분명한
# 개별 하위클래스이므로 명시적으로 나열한다.
#
# httpx.HTTPStatusError(404·401 등)도 일부러 뺐다. 재시도해도 소용없다 (설계서 §5.3).
#
# 주의 — requests 기반 소스에는 이 튜플이 듣지 않는다.
# requests.exceptions.ConnectionError는 OSError의 하위클래스지만 내장
# ConnectionError도 httpx.TransportError도 아니다. pykrx·yfinance가 내부적으로
# requests를 쓰므로, 그쪽에 @retry_on_transient를 붙이려면 이 튜플에
# requests.exceptions.(ConnectionError, Timeout)을 명시적으로 더하고
# 회귀 테스트를 추가해야 한다. 지금은 어느 requests 기반 호출에도
# 재시도를 걸지 않으므로 의도적으로 비워둔다.
TRANSIENT = (TimeoutError, ConnectionError, httpx.TransportError)


class DailyQuotaExceeded(Exception):
    """일일 한도 소진. 배치를 중단하고 남은 종목을 보고해야 한다."""


class TokenBucket:
    """초당 호출 수 제한. acquire()는 대기해야 할 초를 반환한다.

    시간 함수를 주입받으므로 테스트에서 sleep 없이 검증할 수 있다.
    """

    def __init__(
        self,
        rate_per_sec: float,
        capacity: float | None = None,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self.rate = rate_per_sec
        self.capacity = capacity if capacity is not None else rate_per_sec
        self._tokens = self.capacity
        self._time = time_fn
        self._last = time_fn()

    def acquire(self) -> float:
        now = self._time()
        self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
        self._last = now
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return 0.0
        wait = (1.0 - self._tokens) / self.rate
        self._tokens = 0.0
        return wait

    def wait(self) -> None:
        """실제로 대기한다. 프로덕션 경로에서 사용."""
        delay = self.acquire()
        if delay > 0:
            time.sleep(delay)


class DailyQuota:
    """DART 일 20,000건 같은 일일 한도를 프로세스 내에서 센다."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0

    def consume(self, n: int = 1) -> None:
        if self.used + n > self.limit:
            raise DailyQuotaExceeded(
                f"일일 한도 {self.limit}건 소진 (사용 {self.used}건)"
            )
        self.used += n

    @property
    def remaining(self) -> int:
        return self.limit - self.used


def retry_on_transient(
    attempts: int = 3, base_delay: float = 1.0
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """일시 장애만 지수 백오프로 재시도한다. 404·인증 오류는 즉시 던진다."""

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs) -> T:
            last: Exception | None = None
            for i in range(attempts):
                try:
                    return fn(*args, **kwargs)
                except TRANSIENT as exc:
                    last = exc
                    if i < attempts - 1 and base_delay > 0:
                        time.sleep(base_delay * (2**i))
            assert last is not None
            raise last

        return wrapper

    return decorator
```

`src/stockscout/sources/__init__.py`: 빈 파일로 생성한다.

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

Run: `python -m pytest tests/test_ratelimit.py -v`
Expected: PASS — `9 passed`

- [ ] **Step 5: 커밋한다**

```bash
git add src/stockscout/sources/ tests/test_ratelimit.py
git commit -m "feat: 소스 프로토콜과 레이트리밋·재시도"
```

---

## Task 7: DART 클라이언트 — 종목코드 매핑

DART는 6자리 종목코드가 아니라 8자리 `corp_code`로 조회한다. 그 매핑표를 먼저 확보한다.

**Files:**
- Create: `src/stockscout/sources/dart.py`
- Create: `scripts/spike_dart.py`
- Create: `tests/fixtures/dart_corpcode_sample.xml`
- Test: `tests/test_dart_corpcode.py`

- [ ] **Step 1: 실제 응답을 눈으로 확인한다 (탐색)**

`scripts/spike_dart.py`:

```python
"""DART 실제 응답을 확인하고 픽스처를 만든다. 일회성 탐색 스크립트."""

import io
import sys
import zipfile

import httpx

from stockscout.config import load_secrets

BASE = "https://opendart.fss.or.kr/api"


def dump_corpcode() -> None:
    key = load_secrets().dart_api_key
    resp = httpx.get(f"{BASE}/corpCode.xml", params={"crtfc_key": key}, timeout=60)
    print("status:", resp.status_code, "content-type:", resp.headers.get("content-type"))
    print("first 200 bytes:", resp.content[:200])
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        print("zip 안의 파일:", zf.namelist())
        xml = zf.read(zf.namelist()[0]).decode("utf-8")
    print("xml 앞부분:\n", xml[:1200])


def dump_statement(corp_code: str = "00126380", year: int = 2024) -> None:
    """00126380 = 삼성전자"""
    key = load_secrets().dart_api_key
    resp = httpx.get(
        f"{BASE}/fnlttSinglAcntAll.json",
        params={
            "crtfc_key": key,
            "corp_code": corp_code,
            "bsns_year": str(year),
            "reprt_code": "11011",   # 사업보고서(연간)
            "fs_div": "CFS",         # 연결
        },
        timeout=60,
    )
    data = resp.json()
    print("status:", data.get("status"), data.get("message"))
    items = data.get("list", [])
    print("항목 수:", len(items))
    seen = set()
    for it in items:
        key_ = (it.get("sj_div"), it.get("account_id"), it.get("account_nm"))
        if key_ not in seen:
            seen.add(key_)
            print(f"  {it.get('sj_div'):4} | {it.get('account_id'):60} | "
                  f"{it.get('account_nm'):30} | {it.get('thstrm_amount')}")


if __name__ == "__main__":
    if sys.argv[1:] and sys.argv[1] == "statement":
        dump_statement()
    else:
        dump_corpcode()
```

Run: `python scripts/spike_dart.py`

Expected: ZIP 안에 `CORPCODE.xml`이 있고, XML이 `<list><corp_code>...</corp_code><corp_name>...</corp_name><stock_code>...</stock_code>...` 구조로 반복된다.

**출력을 보고 실제 태그 이름을 확인한다.** 아래 픽스처와 다르면 픽스처를 실제 구조에 맞춰 고친다.

- [ ] **Step 2: 픽스처를 만든다**

`tests/fixtures/dart_corpcode_sample.xml` — Step 1에서 확인한 구조를 축약해 손으로 작성한다:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<result>
  <list>
    <corp_code>00126380</corp_code>
    <corp_name>삼성전자</corp_name>
    <stock_code>005930</stock_code>
    <modify_date>20260101</modify_date>
  </list>
  <list>
    <corp_code>00164779</corp_code>
    <corp_name>SK하이닉스</corp_name>
    <stock_code>000660</stock_code>
    <modify_date>20260101</modify_date>
  </list>
  <list>
    <corp_code>00999999</corp_code>
    <corp_name>비상장회사</corp_name>
    <stock_code> </stock_code>
    <modify_date>20260101</modify_date>
  </list>
</result>
```

마지막 항목이 중요하다. DART에는 비상장사가 대부분이고 `stock_code`가 공백이다. 이들을 걸러내야 한다.

- [ ] **Step 3: 실패하는 테스트를 작성한다**

`tests/test_dart_corpcode.py`:

```python
from pathlib import Path

import pytest

from stockscout.sources.dart import parse_corp_codes

FIXTURE = Path(__file__).parent / "fixtures" / "dart_corpcode_sample.xml"


def test_parse_maps_stock_code_to_corp_code():
    mapping = parse_corp_codes(FIXTURE.read_bytes())

    assert mapping["005930"] == "00126380"
    assert mapping["000660"] == "00164779"


def test_parse_excludes_unlisted_companies():
    mapping = parse_corp_codes(FIXTURE.read_bytes())

    assert len(mapping) == 2
    assert all(code.strip() for code in mapping)


def test_parse_raises_on_garbage():
    with pytest.raises(ValueError, match="CORPCODE"):
        parse_corp_codes(b"not xml at all")
```

- [ ] **Step 4: 테스트가 실패하는지 확인한다**

Run: `python -m pytest tests/test_dart_corpcode.py -v`
Expected: FAIL — `ImportError: cannot import name 'parse_corp_codes'`

- [ ] **Step 5: 파서와 클라이언트 골격을 구현한다**

`src/stockscout/sources/dart.py`:

```python
"""OpenDART 클라이언트. https://opendart.fss.or.kr

응답을 해석하지 않고 원형 그대로 반환한다. 계정과목 해석은 normalize 계층의 몫이다.
"""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile

import httpx

from stockscout.models import RawStatement
from stockscout.sources.base import PermanentSourceError, SourceError
from stockscout.sources.ratelimit import DailyQuota, TokenBucket, retry_on_transient

BASE_URL = "https://opendart.fss.or.kr/api"

# DART 응답 status 코드
STATUS_OK = "000"
STATUS_NO_DATA = "013"
STATUS_RATE_LIMIT = "020"

REPORT_CODE_ANNUAL = "11011"  # 사업보고서


def parse_corp_codes(xml_bytes: bytes) -> dict[str, str]:
    """CORPCODE.xml → {6자리 종목코드: 8자리 corp_code}.

    stock_code가 비어 있는 비상장사는 제외한다.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise ValueError(f"CORPCODE XML을 파싱할 수 없습니다: {exc}") from exc

    mapping: dict[str, str] = {}
    for item in root.iter("list"):
        stock_code = (item.findtext("stock_code") or "").strip()
        corp_code = (item.findtext("corp_code") or "").strip()
        if stock_code and corp_code:
            mapping[stock_code] = corp_code

    if not mapping:
        raise ValueError("CORPCODE XML에 상장사가 하나도 없습니다")
    return mapping


class DartSource:
    """FundamentalSource 프로토콜 구현."""

    name = "dart"

    def __init__(
        self,
        api_key: str,
        daily_limit: int = 20_000,
        rate_per_sec: float = 5.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key
        self.quota = DailyQuota(daily_limit)
        self.bucket = TokenBucket(rate_per_sec)
        self._client = client or httpx.Client(timeout=30)

    # --- 종목코드 매핑 -------------------------------------------------

    @retry_on_transient(attempts=3)
    def fetch_corp_codes(self) -> dict[str, str]:
        """전체 상장사의 종목코드 → corp_code 매핑을 받는다 (호출 1건)."""
        self.bucket.wait()
        self.quota.consume()
        resp = self._client.get(
            f"{BASE_URL}/corpCode.xml", params={"crtfc_key": self.api_key}
        )
        resp.raise_for_status()
        try:
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                xml_bytes = zf.read(zf.namelist()[0])
        except zipfile.BadZipFile as exc:
            # 키가 잘못되면 ZIP이 아니라 XML 오류 메시지가 온다
            raise PermanentSourceError(
                f"corpCode 응답이 ZIP이 아닙니다. API 키를 확인하세요. "
                f"응답 앞부분: {resp.content[:200]!r}"
            ) from exc
        return parse_corp_codes(xml_bytes)

    def fetch_annual(self, ticker: str, years: list[int]) -> list[RawStatement]:
        raise NotImplementedError("Task 8에서 구현한다")
```

- [ ] **Step 6: 테스트가 통과하는지 확인한다**

Run: `python -m pytest tests/test_dart_corpcode.py -v`
Expected: PASS — `3 passed`

- [ ] **Step 7: 실제 API로 한 번 확인한다**

Run: `python -c "from stockscout.config import load_secrets; from stockscout.sources.dart import DartSource; m = DartSource(load_secrets().dart_api_key).fetch_corp_codes(); print(len(m), m.get('005930'))"`

Expected: 상장사 수(수천 건)와 `00126380`이 출력된다.

실패한다면 `config/.env`의 키를 확인한다. `PermanentSourceError`가 나면 키가 잘못된 것이다.

- [ ] **Step 8: 커밋한다**

```bash
git add src/stockscout/sources/dart.py scripts/spike_dart.py tests/fixtures/dart_corpcode_sample.xml tests/test_dart_corpcode.py
git commit -m "feat: DART 종목코드 매핑"
```

---

## Task 8: DART 재무제표 수집

**Files:**
- Modify: `src/stockscout/sources/dart.py`
- Create: `tests/fixtures/dart_statement_005930_2024.json`
- Test: `tests/test_dart_statements.py`

- [ ] **Step 1: 실제 재무제표 응답을 확인한다 (탐색)**

Run: `python scripts/spike_dart.py statement`

Expected: `status: 000`, 항목 수 수백 건, 그리고 `sj_div | account_id | account_nm | thstrm_amount` 형식의 목록.

**출력에서 다음 계정의 `account_id`를 적어둔다.** Task 12의 매핑표에 그대로 넣을 값들이다:

| 찾을 항목 | 어느 `sj_div`에 있나 |
|---|---|
| 매출액 | `IS` 또는 `CIS` |
| 영업이익 | `IS` 또는 `CIS` |
| 당기순이익 | `IS` 또는 `CIS` |
| 자산총계 / 부채총계 / 자본총계 | `BS` |
| 유동자산 / 유동부채 | `BS` |
| 영업활동현금흐름 | `CF` |
| 유형자산의 취득 | `CF` |

- [ ] **Step 2: 픽스처를 저장한다**

`scripts/spike_dart.py` 맨 아래 `if __name__` 블록 **위에** 함수를 추가한다:

```python
def save_fixture(corp_code: str = "00126380", year: int = 2024) -> None:
    import json
    from pathlib import Path

    key = load_secrets().dart_api_key
    resp = httpx.get(
        f"{BASE}/fnlttSinglAcntAll.json",
        params={
            "crtfc_key": key, "corp_code": corp_code, "bsns_year": str(year),
            "reprt_code": "11011", "fs_div": "CFS",
        },
        timeout=60,
    )
    out = Path("tests/fixtures") / f"dart_statement_005930_{year}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(resp.json(), ensure_ascii=False, indent=2), encoding="utf-8")
    print("저장:", out, resp.json().get("status"))
```

`if __name__` 블록을 다음으로 교체한다:

```python
if __name__ == "__main__":
    cmd = sys.argv[1] if sys.argv[1:] else "corpcode"
    {"corpcode": dump_corpcode, "statement": dump_statement, "fixture": save_fixture}[cmd]()
```

Run: `python scripts/spike_dart.py fixture`
Expected: `저장: tests/fixtures/dart_statement_005930_2024.json 000`

- [ ] **Step 3: 실패하는 테스트를 작성한다**

`tests/test_dart_statements.py`:

```python
import json
from pathlib import Path

import httpx
import pytest

from stockscout.sources.base import PermanentSourceError
from stockscout.sources.dart import DartSource

FIXTURE = Path(__file__).parent / "fixtures" / "dart_statement_005930_2024.json"


def make_source(handler) -> DartSource:
    """httpx MockTransport로 네트워크를 대체한다."""
    transport = httpx.MockTransport(handler)
    return DartSource("dummy-key", client=httpx.Client(transport=transport))


def test_fetch_annual_returns_raw_statement_per_year():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def handler(request):
        return httpx.Response(200, json=payload)

    src = make_source(handler)
    src.corp_codes = {"005930": "00126380"}

    stmts = src.fetch_annual("005930.KS", [2023, 2024])

    assert len(stmts) == 2
    assert {s.fiscal_year for s in stmts} == {2023, 2024}
    assert all(s.source == "dart" for s in stmts)
    assert all(s.ticker == "005930.KS" for s in stmts)
    assert stmts[0].payload["list"], "payload에 계정 목록이 그대로 들어 있어야 한다"


def test_fetch_annual_skips_years_with_no_data():
    def handler(request):
        year = request.url.params["bsns_year"]
        if year == "2020":
            return httpx.Response(200, json={"status": "013", "message": "데이터 없음"})
        return httpx.Response(200, json=json.loads(FIXTURE.read_text(encoding="utf-8")))

    src = make_source(handler)
    src.corp_codes = {"005930": "00126380"}

    stmts = src.fetch_annual("005930.KS", [2020, 2024])

    assert [s.fiscal_year for s in stmts] == [2024]


def test_fetch_annual_raises_on_unknown_ticker():
    src = make_source(lambda r: httpx.Response(200, json={"status": "000", "list": []}))
    src.corp_codes = {}

    with pytest.raises(PermanentSourceError, match="corp_code"):
        src.fetch_annual("999999.KS", [2024])


def test_rate_limit_status_raises():
    def handler(request):
        return httpx.Response(200, json={"status": "020", "message": "요청 제한 초과"})

    src = make_source(handler)
    src.corp_codes = {"005930": "00126380"}

    with pytest.raises(PermanentSourceError, match="요청 제한"):
        src.fetch_annual("005930.KS", [2024])


def test_ticker_to_stock_code_strips_suffix():
    src = make_source(lambda r: httpx.Response(200, json={}))

    assert src._stock_code("005930.KS") == "005930"
    assert src._stock_code("005930") == "005930"
```

- [ ] **Step 4: 테스트가 실패하는지 확인한다**

Run: `python -m pytest tests/test_dart_statements.py -v`
Expected: FAIL — `NotImplementedError: Task 8에서 구현한다`

- [ ] **Step 5: `fetch_annual`을 구현한다**

`src/stockscout/sources/dart.py`의 `__init__` 마지막 줄 뒤에 추가:

```python
        self.corp_codes: dict[str, str] = {}
```

`fetch_annual`의 `raise NotImplementedError(...)` 줄을 다음으로 교체한다:

```python
    @staticmethod
    def _stock_code(ticker: str) -> str:
        """'005930.KS' → '005930'"""
        return ticker.split(".")[0]

    def ensure_corp_codes(self) -> None:
        if not self.corp_codes:
            self.corp_codes = self.fetch_corp_codes()

    def fetch_annual(self, ticker: str, years: list[int]) -> list[RawStatement]:
        self.ensure_corp_codes()
        stock_code = self._stock_code(ticker)
        corp_code = self.corp_codes.get(stock_code)
        if not corp_code:
            raise PermanentSourceError(f"{ticker}의 corp_code를 찾을 수 없습니다")

        statements: list[RawStatement] = []
        for year in years:
            payload = self._fetch_one_year(corp_code, year)
            if payload is None:
                continue  # 해당 연도 데이터 없음. 정상 상황이다
            statements.append(
                RawStatement(
                    ticker=ticker, fiscal_year=year, source=self.name, payload=payload
                )
            )
        return statements

    @retry_on_transient(attempts=3)
    def _fetch_one_year(self, corp_code: str, year: int) -> dict | None:
        """한 회계연도의 연결 재무제표 원본. 데이터가 없으면 None."""
        self.bucket.wait()
        self.quota.consume()
        resp = self._client.get(
            f"{BASE_URL}/fnlttSinglAcntAll.json",
            params={
                "crtfc_key": self.api_key,
                "corp_code": corp_code,
                "bsns_year": str(year),
                "reprt_code": REPORT_CODE_ANNUAL,
                "fs_div": "CFS",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        status = data.get("status")

        if status == STATUS_NO_DATA:
            return None
        if status == STATUS_RATE_LIMIT:
            raise PermanentSourceError(f"DART 요청 제한 초과: {data.get('message')}")
        if status != STATUS_OK:
            raise SourceError(f"DART 오류 {status}: {data.get('message')}")
        return data
```

**연결(CFS) 우선에 대하여:** 지주회사·모회사 실적을 보려면 연결이 맞다. 연결 재무제표를 작성하지 않는 회사는 `status=013`이 오므로 Task 13에서 별도(OFS) 재시도를 붙인다.

- [ ] **Step 6: 테스트가 통과하는지 확인한다**

Run: `python -m pytest tests/test_dart_statements.py -v`
Expected: PASS — `5 passed`

- [ ] **Step 7: 커밋한다**

```bash
git add src/stockscout/sources/dart.py scripts/spike_dart.py tests/fixtures/dart_statement_005930_2024.json tests/test_dart_statements.py
git commit -m "feat: DART 연간 재무제표 수집"
```

---

## Task 9: pykrx 주가 어댑터와 유니버스

**Files:**
- Create: `src/stockscout/sources/krx.py`
- Create: `scripts/spike_krx.py`
- Test: `tests/test_krx.py`

- [ ] **Step 1: pykrx 실제 동작을 확인한다 (탐색)**

`scripts/spike_krx.py`:

```python
"""pykrx 함수 시그니처와 반환 형태를 확인한다. 일회성 탐색 스크립트."""

from pykrx import stock

KOSPI200_INDEX = "1028"

print("=== KOSPI 200 구성종목 ===")
tickers = stock.get_index_portfolio_deposit_file(KOSPI200_INDEX)
print("종목 수:", len(tickers))
print("앞 5개:", tickers[:5])

print("\n=== 종목명 ===")
print("005930 →", stock.get_market_ticker_name("005930"))

print("\n=== 일봉 (수정주가) ===")
df = stock.get_market_ohlcv("20260101", "20260131", "005930")
print("컬럼:", list(df.columns))
print("인덱스 타입:", type(df.index))
print(df.head())
```

Run: `python scripts/spike_krx.py`

Expected: 구성종목 200여 개, 종목명 `삼성전자`, 일봉 DataFrame의 컬럼은 `['시가','고가','저가','종가','거래량']` 형태이고 인덱스는 `DatetimeIndex`.

**출력이 위와 다르면** — 컬럼명이 영문이거나 `get_index_portfolio_deposit_file`이 다른 이름이면 — Step 3의 상수와 Step 5의 코드를 실제에 맞춰 고친다. 어댑터를 얇게 유지한 이유가 이것이다.

- [ ] **Step 2: 실패하는 테스트를 작성한다**

`tests/test_krx.py`:

```python
from datetime import date

import pandas as pd
import pytest

from stockscout.sources.krx import KrxPriceSource, KrxUniverse, to_ticker


def test_to_ticker_appends_suffix():
    assert to_ticker("005930") == "005930.KS"
    assert to_ticker("005930.KS") == "005930.KS"


def test_fetch_daily_converts_dataframe_to_price_bars():
    df = pd.DataFrame(
        {"시가": [70000, 71000], "고가": [71000, 72000], "저가": [69000, 70500],
         "종가": [70500, 71800], "거래량": [1000, 2000]},
        index=pd.to_datetime(["2026-01-05", "2026-01-06"]),
    )
    src = KrxPriceSource(ohlcv_fn=lambda *a, **k: df)

    bars = src.fetch_daily("005930.KS", date(2026, 1, 1), date(2026, 1, 31))

    assert [b.close for b in bars] == [70500.0, 71800.0]
    assert [b.date for b in bars] == [date(2026, 1, 5), date(2026, 1, 6)]
    assert [b.volume for b in bars] == [1000, 2000]
    assert all(b.ticker == "005930.KS" for b in bars)


def test_fetch_daily_returns_empty_for_empty_frame():
    src = KrxPriceSource(ohlcv_fn=lambda *a, **k: pd.DataFrame())

    assert src.fetch_daily("005930.KS", date(2026, 1, 1), date(2026, 1, 31)) == []


def test_fetch_daily_drops_rows_with_zero_close():
    """거래정지일은 종가가 0으로 온다. 평균 계산을 오염시키므로 버린다."""
    df = pd.DataFrame(
        {"종가": [70000, 0, 71000], "거래량": [10, 0, 20]},
        index=pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"]),
    )
    src = KrxPriceSource(ohlcv_fn=lambda *a, **k: df)

    bars = src.fetch_daily("005930.KS", date(2026, 1, 1), date(2026, 1, 31))

    assert [b.close for b in bars] == [70000.0, 71000.0]


def test_universe_returns_securities_with_ticker_and_name():
    uni = KrxUniverse(
        portfolio_fn=lambda idx: ["005930", "000660"],
        name_fn=lambda code: {"005930": "삼성전자", "000660": "SK하이닉스"}[code],
    )

    secs = uni.fetch()

    assert [s.ticker for s in secs] == ["005930.KS", "000660.KS"]
    assert secs[0].name == "삼성전자"
    assert secs[0].market == "KR"
    assert secs[0].currency == "KRW"
    assert secs[0].exchange == "KOSPI"


def test_universe_skips_tickers_whose_name_lookup_fails():
    def flaky_name(code):
        if code == "BAD":
            raise ValueError("no such ticker")
        return "삼성전자"

    uni = KrxUniverse(portfolio_fn=lambda idx: ["005930", "BAD"], name_fn=flaky_name)

    secs = uni.fetch()

    assert [s.ticker for s in secs] == ["005930.KS"]
```

- [ ] **Step 3: 테스트가 실패하는지 확인한다**

Run: `python -m pytest tests/test_krx.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'stockscout.sources.krx'`

- [ ] **Step 4: 어댑터를 구현한다**

`src/stockscout/sources/krx.py`:

```python
"""pykrx 어댑터. KRX 공개데이터에서 수정주가와 지수 구성종목을 받는다.

pykrx 함수를 주입받아 테스트에서 네트워크 없이 검증할 수 있게 한다.
"""

from __future__ import annotations

from datetime import date
from typing import Callable

import pandas as pd

from stockscout.models import PriceBar, Security

KOSPI200_INDEX_CODE = "1028"
CLOSE_COLUMN = "종가"
VOLUME_COLUMN = "거래량"


def to_ticker(stock_code: str) -> str:
    """'005930' → '005930.KS'. 이미 접미사가 있으면 그대로 둔다."""
    return stock_code if "." in stock_code else f"{stock_code}.KS"


def _default_ohlcv_fn(fromdate: str, todate: str, ticker: str) -> pd.DataFrame:
    from pykrx import stock

    return stock.get_market_ohlcv(fromdate, todate, ticker)


class KrxPriceSource:
    """PriceSource 프로토콜 구현."""

    name = "pykrx"

    def __init__(self, ohlcv_fn: Callable[..., pd.DataFrame] | None = None) -> None:
        self._ohlcv = ohlcv_fn or _default_ohlcv_fn

    def fetch_daily(self, ticker: str, start: date, end: date) -> list[PriceBar]:
        code = ticker.split(".")[0]
        df = self._ohlcv(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), code)
        if df is None or df.empty:
            return []

        bars: list[PriceBar] = []
        for idx, row in df.iterrows():
            close = float(row[CLOSE_COLUMN])
            if close <= 0:
                continue  # 거래정지일. 평균을 오염시킨다
            volume = row.get(VOLUME_COLUMN)
            bars.append(
                PriceBar(
                    ticker=ticker,
                    date=pd.Timestamp(idx).date(),
                    close=close,
                    volume=int(volume) if volume is not None and not pd.isna(volume) else None,
                )
            )
        return bars


def _default_portfolio_fn(index_code: str) -> list[str]:
    from pykrx import stock

    return stock.get_index_portfolio_deposit_file(index_code)


def _default_name_fn(stock_code: str) -> str:
    from pykrx import stock

    return stock.get_market_ticker_name(stock_code)


class KrxUniverse:
    """KOSPI 200 구성종목 목록."""

    def __init__(
        self,
        index_code: str = KOSPI200_INDEX_CODE,
        portfolio_fn: Callable[[str], list[str]] | None = None,
        name_fn: Callable[[str], str] | None = None,
    ) -> None:
        self.index_code = index_code
        self._portfolio = portfolio_fn or _default_portfolio_fn
        self._name = name_fn or _default_name_fn

    def fetch(self) -> list[Security]:
        securities: list[Security] = []
        for code in self._portfolio(self.index_code):
            try:
                name = self._name(code)
            except Exception:
                continue  # 종목명 조회 실패는 그 종목만 건너뛴다
            securities.append(
                Security(
                    ticker=to_ticker(code),
                    market="KR",
                    name=name,
                    exchange="KOSPI",
                    currency="KRW",
                    in_universe=True,
                )
            )
        return securities
```

- [ ] **Step 5: 테스트가 통과하는지 확인한다**

Run: `python -m pytest tests/test_krx.py -v`
Expected: PASS — `6 passed`

- [ ] **Step 6: 실제 pykrx로 확인한다**

Run: `python -c "from stockscout.sources.krx import KrxUniverse; s = KrxUniverse().fetch(); print(len(s), s[0].ticker, s[0].name)"`
Expected: 종목 수 200 내외와 첫 종목의 티커·이름

- [ ] **Step 7: 커밋한다**

```bash
git add src/stockscout/sources/krx.py scripts/spike_krx.py tests/test_krx.py
git commit -m "feat: pykrx 주가·유니버스 어댑터"
```

---

## Task 10: 공통 재무 스키마

설계서 §7.1. 한국·미국 데이터가 수렴하는 지점이며, 이 아래로는 분석 계층이 시장을 구분하지 않는다. Task 4에서 만든 `models.py`에 추가한다.

**Files:**
- Modify: `src/stockscout/models.py`
- Test: `tests/test_models_financials.py`

- [ ] **Step 1: 실패하는 테스트를 작성한다**

`tests/test_models_financials.py`:

```python
import pytest

from stockscout.models import OPTIONAL_FIELDS, REQUIRED_FIELDS, Financials


def make(**overrides) -> Financials:
    base = dict(
        ticker="005930.KS", fiscal_year=2025, currency="KRW",
        revenue=300.0, operating_income=30.0, total_equity=200.0, total_liabilities=60.0,
        # 선택 필드도 기본값을 채운다. 비워두면 missing_fields가 손대지 않은
        # 선택 필드까지 전부 잡아내서, 특정 필드의 결측만 확인하려는 테스트가
        # 격리되지 않는다.
        net_income=10.0, total_assets=100.0, current_assets=50.0,
        current_liabilities=20.0, interest_expense=1.0, operating_cash_flow=15.0,
        capex=5.0, shares_outstanding=1000.0, dividend_per_share=1.0,
    )
    base.update(overrides)
    return Financials(**base)


def test_required_and_optional_sets_do_not_overlap():
    assert REQUIRED_FIELDS & OPTIONAL_FIELDS == set()


def test_all_declared_fields_exist_on_model():
    fields = set(Financials.model_fields)
    assert REQUIRED_FIELDS <= fields
    assert OPTIONAL_FIELDS <= fields


def test_missing_fields_lists_absent_required_values():
    fin = make(revenue=None, operating_income=None)

    assert set(fin.missing_fields) == {"revenue", "operating_income"}


def test_missing_fields_lists_absent_optional_values():
    fin = make(capex=None, interest_expense=None, current_assets=1.0,
               current_liabilities=1.0, dividend_per_share=0.0,
               net_income=1.0, total_assets=1.0, operating_cash_flow=1.0,
               shares_outstanding=1.0)

    assert set(fin.missing_fields) == {"capex", "interest_expense"}


def test_has_required_is_false_when_any_required_missing():
    assert make().has_required is True
    assert make(total_equity=None).has_required is False


def test_debt_ratio_computed():
    fin = make(total_liabilities=100.0, total_equity=50.0)

    assert fin.debt_ratio == pytest.approx(200.0)


def test_debt_ratio_is_none_when_equity_nonpositive():
    assert make(total_equity=0.0).debt_ratio is None
    assert make(total_equity=-10.0).debt_ratio is None


def test_operating_margin_and_roe():
    fin = make(revenue=1000.0, operating_income=150.0, net_income=100.0, total_equity=500.0)

    assert fin.operating_margin == pytest.approx(0.15)
    assert fin.roe == pytest.approx(0.20)


def test_margin_is_none_when_revenue_nonpositive():
    assert make(revenue=0.0).operating_margin is None


def test_fcf_uses_capex_as_positive_magnitude():
    fin = make(operating_cash_flow=100.0, capex=30.0)

    assert fin.free_cash_flow == pytest.approx(70.0)


def test_fcf_is_none_without_capex():
    assert make(operating_cash_flow=100.0, capex=None).free_cash_flow is None
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

Run: `python -m pytest tests/test_models_financials.py -v`
Expected: FAIL — `ImportError: cannot import name 'Financials' from 'stockscout.models'`

- [ ] **Step 3: 스키마를 구현한다**

`src/stockscout/models.py` 맨 아래에 추가한다. 한국·미국 데이터가 이 모델로 수렴한 뒤에는 분석 계층이 시장을 구분하지 않는다 (설계서 §7.1).

```python
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


class Financials(BaseModel):
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
```

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

Run: `python -m pytest tests/test_models_financials.py -v`
Expected: PASS — `11 passed`

- [ ] **Step 5: 커밋한다**

```bash
git add src/stockscout/models.py tests/test_models_financials.py
git commit -m "feat: 한미 공통 재무 스키마"
```

---

## Task 11: 재무 데이터 저장 Repository

**Files:**
- Modify: `src/stockscout/store/repository.py`
- Test: `tests/test_repository_financials.py`

- [ ] **Step 1: 실패하는 테스트를 작성한다**

`tests/test_repository_financials.py`:

```python
import pytest

from stockscout.models import Financials
from stockscout.store.db import connect, init_db
from stockscout.store.repository import Repository


@pytest.fixture
def repo(tmp_path):
    with connect(tmp_path / "t.db") as conn:
        init_db(conn)
        yield Repository(conn)


def make(year: int, **overrides) -> Financials:
    base = dict(
        ticker="005930.KS", fiscal_year=year, currency="KRW",
        revenue=300.0, operating_income=30.0, total_equity=200.0,
        total_liabilities=60.0, capex=None,
    )
    base.update(overrides)
    return Financials(**base)


def test_save_and_read_back(repo):
    repo.save_financials([make(2024), make(2025)])

    got = repo.get_financials("005930.KS")

    assert [f.fiscal_year for f in got] == [2024, 2025]
    assert got[0].revenue == 300.0
    assert got[0].currency == "KRW"


def test_missing_fields_round_trips(repo):
    repo.save_financials([make(2025)])

    got = repo.get_financials("005930.KS")[0]

    assert "capex" in got.missing_fields


def test_save_overwrites_same_year(repo):
    repo.save_financials([make(2025, revenue=100.0)])
    repo.save_financials([make(2025, revenue=999.0)])

    got = repo.get_financials("005930.KS")

    assert len(got) == 1
    assert got[0].revenue == 999.0


def test_get_financials_returns_ascending_years(repo):
    repo.save_financials([make(2025), make(2021), make(2023)])

    got = repo.get_financials("005930.KS")

    assert [f.fiscal_year for f in got] == [2021, 2023, 2025]


def test_get_financials_empty_for_unknown_ticker(repo):
    assert repo.get_financials("NOPE") == []


def test_coverage_report_counts_missing_per_field(repo):
    repo.save_financials([
        make(2024, capex=None, net_income=1.0),
        make(2025, capex=5.0, net_income=None),
    ])

    report = repo.missing_field_counts()

    assert report["capex"] == 1
    assert report["net_income"] == 1
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

Run: `python -m pytest tests/test_repository_financials.py -v`
Expected: FAIL — `AttributeError: 'Repository' object has no attribute 'save_financials'`

- [ ] **Step 3: 메서드를 추가한다**

`src/stockscout/store/repository.py`의 모델 import 줄에 `Financials`를 더한다:

```python
from stockscout.models import Financials, PriceBar, RawStatement, Security
```

`Repository` 클래스 안, `record_error` **앞에** 추가한다:

```python
    # --- 정규화 재무 -------------------------------------------------

    _FIN_COLUMNS = (
        "ticker", "fiscal_year", "currency", "revenue", "operating_income",
        "net_income", "total_assets", "total_liabilities", "total_equity",
        "current_assets", "current_liabilities", "interest_expense",
        "operating_cash_flow", "capex", "shares_outstanding", "dividend_per_share",
    )

    def save_financials(self, records: list[Financials]) -> None:
        cols = ", ".join(self._FIN_COLUMNS) + ", missing_fields"
        placeholders = ", ".join("?" * (len(self._FIN_COLUMNS) + 1))
        updates = ", ".join(
            f"{c}=excluded.{c}" for c in self._FIN_COLUMNS[2:]
        ) + ", missing_fields=excluded.missing_fields"
        self.conn.executemany(
            f"""
            INSERT INTO financials ({cols}) VALUES ({placeholders})
            ON CONFLICT(ticker, fiscal_year) DO UPDATE SET {updates}
            """,
            [
                tuple(getattr(r, c) for c in self._FIN_COLUMNS)
                + (json.dumps(r.missing_fields, ensure_ascii=False),)
                for r in records
            ],
        )
        self.conn.commit()

    def get_financials(self, ticker: str) -> list[Financials]:
        rows = self.conn.execute(
            "SELECT * FROM financials WHERE ticker = ? ORDER BY fiscal_year",
            (ticker,),
        ).fetchall()
        return [
            Financials(**{c: row[c] for c in self._FIN_COLUMNS}) for row in rows
        ]

    def missing_field_counts(self) -> dict[str, int]:
        """정규화 품질 보고용. 어떤 계정과목 매핑이 자주 실패하는지 보여준다."""
        counts: dict[str, int] = {}
        for row in self.conn.execute("SELECT missing_fields FROM financials"):
            for field in json.loads(row["missing_fields"] or "[]"):
                counts[field] = counts.get(field, 0) + 1
        return counts
```

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

Run: `python -m pytest tests/test_repository_financials.py -v`
Expected: PASS — `6 passed`

- [ ] **Step 5: 커밋한다**

```bash
git add src/stockscout/store/repository.py tests/test_repository_financials.py
git commit -m "feat: 정규화 재무 repository와 결측 집계"
```

---

## Task 12: 한국 계정과목 매핑

설계서 §7.2. DART 응답의 계정과목을 `Financials`로 옮긴다. **Task 8 Step 1에서 적어둔 실제 `account_id`를 여기에 넣는다.**

**Files:**
- Create: `src/stockscout/normalize/__init__.py`
- Create: `src/stockscout/normalize/kr_mapping.py`
- Test: `tests/test_kr_mapping.py`

- [ ] **Step 1: 실패하는 테스트를 작성한다**

`tests/test_kr_mapping.py`:

```python
import json
from pathlib import Path

import pytest

from stockscout.models import RawStatement
from stockscout.normalize.kr_mapping import normalize_dart_statement, pick_amount

FIXTURE = Path(__file__).parent / "fixtures" / "dart_statement_005930_2024.json"


def item(sj_div, account_id, account_nm, amount):
    return {
        "sj_div": sj_div, "account_id": account_id,
        "account_nm": account_nm, "thstrm_amount": amount,
    }


def test_pick_amount_matches_by_account_id_first():
    items = [
        item("IS", "ifrs-full_Revenue", "엉뚱한이름", "1,000"),
        item("IS", "other", "매출액", "9,999"),
    ]

    assert pick_amount(items, ids=["ifrs-full_Revenue"], names=["매출액"]) == 1000.0


def test_pick_amount_falls_back_to_account_name():
    items = [item("IS", "unmapped_id", "영업수익", "2,500")]

    assert pick_amount(items, ids=["ifrs-full_Revenue"], names=["매출액", "영업수익"]) == 2500.0


def test_pick_amount_handles_negative_parentheses():
    items = [item("CF", "x", "유형자산의 취득", "(1,234)")]

    assert pick_amount(items, ids=[], names=["유형자산의 취득"]) == -1234.0


def test_pick_amount_returns_none_when_absent():
    assert pick_amount([], ids=["a"], names=["b"]) is None


def test_pick_amount_returns_none_for_blank_amount():
    items = [item("IS", "ifrs-full_Revenue", "매출액", "-")]

    assert pick_amount(items, ids=["ifrs-full_Revenue"], names=[]) is None


def test_normalize_produces_required_fields():
    stmt = RawStatement(
        ticker="005930.KS", fiscal_year=2024, source="dart",
        payload={"list": [
            item("IS", "ifrs-full_Revenue", "매출액", "300,000"),
            item("IS", "dart_OperatingIncomeLoss", "영업이익", "30,000"),
            item("BS", "ifrs-full_Liabilities", "부채총계", "60,000"),
            item("BS", "ifrs-full_Equity", "자본총계", "200,000"),
        ]},
    )

    fin = normalize_dart_statement(stmt)

    assert fin.has_required
    assert fin.revenue == 300000.0
    assert fin.operating_income == 30000.0
    assert fin.currency == "KRW"
    assert fin.fiscal_year == 2024


def test_normalize_stores_capex_as_positive_magnitude():
    stmt = RawStatement(
        ticker="A", fiscal_year=2024, source="dart",
        payload={"list": [item("CF", "x", "유형자산의 취득", "(5,000)")]},
    )

    fin = normalize_dart_statement(stmt)

    assert fin.capex == 5000.0


def test_normalize_reports_missing_when_nothing_matches():
    stmt = RawStatement(
        ticker="A", fiscal_year=2024, source="dart", payload={"list": []}
    )

    fin = normalize_dart_statement(stmt)

    assert fin.has_required is False
    assert "revenue" in fin.missing_fields


@pytest.mark.skipif(not FIXTURE.exists(), reason="Task 8에서 픽스처를 만들어야 한다")
def test_real_fixture_maps_all_required_fields():
    """실제 삼성전자 응답에서 필수 4개 항목이 전부 잡혀야 한다.

    실패하면 매핑표에 실제 account_id를 추가한다. 실패 메시지가 무엇이 빠졌는지 알려준다.
    """
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    stmt = RawStatement(
        ticker="005930.KS", fiscal_year=2024, source="dart", payload=payload
    )

    fin = normalize_dart_statement(stmt)

    assert fin.has_required, (
        f"필수 항목 매핑 실패: {fin.missing_fields}. "
        "scripts/spike_dart.py statement 로 실제 account_id를 확인해 "
        "kr_mapping.py의 ACCOUNT_MAP에 추가하세요."
    )


@pytest.mark.skipif(not FIXTURE.exists(), reason="Task 8에서 픽스처를 만들어야 한다")
def test_real_fixture_maps_most_optional_fields():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    stmt = RawStatement(
        ticker="005930.KS", fiscal_year=2024, source="dart", payload=payload
    )

    fin = normalize_dart_statement(stmt)

    for field in ("net_income", "total_assets", "current_assets",
                  "current_liabilities", "operating_cash_flow"):
        assert getattr(fin, field) is not None, (
            f"{field} 매핑 실패. spike_dart.py 출력에서 실제 account_id를 찾아 추가하세요."
        )
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

Run: `python -m pytest tests/test_kr_mapping.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'stockscout.normalize.kr_mapping'`

- [ ] **Step 3: 매핑을 구현한다**

`src/stockscout/normalize/kr_mapping.py`:

```python
"""DART 계정과목 → 공통 Financials 매핑. 설계서 §7.2.

DART는 IFRS 표준계정ID(account_id)를 주지만 회사마다 편차가 있다.
account_id를 먼저 보고, 없으면 계정과목명(account_nm)으로 폴백한다.
둘 다 동의어 목록을 두고 위에서부터 첫 매치를 취한다.
"""

from __future__ import annotations

from stockscout.models import Financials, RawStatement

# 값이 없음을 나타내는 DART 표기
BLANK_AMOUNTS = {"", "-", "－", "N/A"}

# 필드 → (account_id 후보, account_nm 후보)
# Task 8 Step 1의 spike 출력으로 확인·보강한다.
ACCOUNT_MAP: dict[str, tuple[list[str], list[str]]] = {
    "revenue": (
        ["ifrs-full_Revenue", "ifrs-full_RevenueFromContractsWithCustomers"],
        ["매출액", "수익(매출액)", "영업수익", "매출"],
    ),
    "operating_income": (
        ["dart_OperatingIncomeLoss", "ifrs-full_ProfitLossFromOperatingActivities"],
        ["영업이익", "영업이익(손실)", "영업손익"],
    ),
    "net_income": (
        ["ifrs-full_ProfitLoss"],
        ["당기순이익", "당기순이익(손실)", "당기순손익"],
    ),
    "total_assets": (["ifrs-full_Assets"], ["자산총계"]),
    "total_liabilities": (["ifrs-full_Liabilities"], ["부채총계"]),
    "total_equity": (
        ["ifrs-full_Equity"],
        ["자본총계", "자본총계(지배기업의 소유주지분)"],
    ),
    "current_assets": (["ifrs-full_CurrentAssets"], ["유동자산"]),
    "current_liabilities": (["ifrs-full_CurrentLiabilities"], ["유동부채"]),
    "interest_expense": (
        ["ifrs-full_InterestExpense", "dart_InterestExpense"],
        ["이자비용", "금융원가"],
    ),
    "operating_cash_flow": (
        ["ifrs-full_CashFlowsFromUsedInOperatingActivities"],
        ["영업활동현금흐름", "영업활동으로 인한 현금흐름", "영업활동으로인한현금흐름"],
    ),
    "capex": (
        [
            "ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
        ],
        ["유형자산의 취득", "유형자산의취득", "유형자산의 증가"],
    ),
}

# 값을 양수 크기로 저장할 필드 (DART 현금흐름표는 지출을 음수로 표기한다)
ABS_FIELDS = {"capex", "interest_expense"}


def _parse_amount(text: str | None) -> float | None:
    """'1,234' → 1234.0, '(1,234)' → -1234.0, '-' → None"""
    if text is None:
        return None
    cleaned = str(text).strip().replace(",", "").replace(" ", "")
    if cleaned in BLANK_AMOUNTS:
        return None
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    if negative:
        cleaned = cleaned[1:-1]
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return -value if negative else value


def pick_amount(
    items: list[dict], ids: list[str], names: list[str]
) -> float | None:
    """account_id 우선, account_nm 폴백으로 첫 매치의 금액을 반환한다."""
    by_id = {}
    by_name = {}
    for it in items:
        amount = _parse_amount(it.get("thstrm_amount"))
        if amount is None:
            continue
        account_id = (it.get("account_id") or "").strip()
        account_nm = (it.get("account_nm") or "").strip().replace(" ", "")
        by_id.setdefault(account_id, amount)
        by_name.setdefault(account_nm, amount)

    for candidate in ids:
        if candidate in by_id:
            return by_id[candidate]
    for candidate in names:
        if candidate.replace(" ", "") in by_name:
            return by_name[candidate.replace(" ", "")]
    return None


def normalize_dart_statement(stmt: RawStatement) -> Financials:
    """DART 원본 하나를 공통 스키마로 옮긴다. 순수 함수이며 네트워크·DB를 모른다."""
    items = stmt.payload.get("list", []) or []

    values: dict[str, float | None] = {}
    for field, (ids, names) in ACCOUNT_MAP.items():
        amount = pick_amount(items, ids, names)
        if amount is not None and field in ABS_FIELDS:
            amount = abs(amount)
        values[field] = amount

    return Financials(
        ticker=stmt.ticker,
        fiscal_year=stmt.fiscal_year,
        currency="KRW",
        **values,
    )
```

`src/stockscout/normalize/__init__.py`: 빈 파일로 생성한다.

- [ ] **Step 4: 합성 데이터 테스트가 통과하는지 확인한다**

Run: `python -m pytest tests/test_kr_mapping.py -v -k "not real_fixture"`
Expected: PASS — `8 passed`

- [ ] **Step 5: 실제 픽스처로 매핑을 검증하고 보강한다**

Run: `python -m pytest tests/test_kr_mapping.py -v -k real_fixture`

**실패하면** 실패 메시지가 어떤 필드가 안 잡혔는지 알려준다. 그 필드에 대해:

```bash
python scripts/spike_dart.py statement | grep -i "매출\|영업이익\|자본총계"
```

출력에서 실제 `account_id`를 찾아 `ACCOUNT_MAP`의 해당 항목에 **추가**한다(기존 값은 지우지 않는다 — 다른 회사가 쓸 수 있다). 그리고 다시 실행한다.

Expected (최종): PASS — `2 passed`

- [ ] **Step 6: 전체 테스트를 돌린다**

Run: `python -m pytest tests/ -v`
Expected: 지금까지의 모든 테스트가 PASS

- [ ] **Step 7: 커밋한다**

```bash
git add src/stockscout/normalize/ tests/test_kr_mapping.py
git commit -m "feat: DART 계정과목 매핑"
```

---

## Task 13: 수집 파이프라인

설계서 §5.3·§13. 증분 수집, 종목 단위 오류 격리, 요약 보고를 조립한다.

**Files:**
- Create: `src/stockscout/pipeline/__init__.py`
- Create: `src/stockscout/pipeline/collect.py`
- Test: `tests/test_pipeline_collect.py`

- [ ] **Step 1: 실패하는 테스트를 작성한다**

`tests/test_pipeline_collect.py`:

```python
from datetime import date

import pytest

from stockscout.models import PriceBar, RawStatement, Security
from stockscout.pipeline.collect import CollectSummary, collect_market, sync_universe
from stockscout.sources.base import PermanentSourceError
from stockscout.store.db import connect, init_db
from stockscout.store.repository import Repository


@pytest.fixture
def repo(tmp_path):
    with connect(tmp_path / "t.db") as conn:
        init_db(conn)
        yield Repository(conn)


class FakeUniverse:
    def __init__(self, securities): self._s = securities
    def fetch(self): return self._s


class FakeFundamentals:
    name = "dart"

    def __init__(self, per_ticker=None, failures=None):
        self._per_ticker = per_ticker or {}
        self._failures = failures or {}
        self.requested: list[tuple[str, list[int]]] = []

    def fetch_annual(self, ticker, years):
        self.requested.append((ticker, list(years)))
        if ticker in self._failures:
            raise self._failures[ticker]
        return [
            RawStatement(ticker=ticker, fiscal_year=y, source="dart", payload={"y": y})
            for y in years
            if y in self._per_ticker.get(ticker, years)
        ]


class FakePrices:
    name = "pykrx"

    def __init__(self, bars_by_ticker=None, failures=None):
        self._bars = bars_by_ticker or {}
        self._failures = failures or {}
        self.requested: list[tuple[str, date, date]] = []

    def fetch_daily(self, ticker, start, end):
        self.requested.append((ticker, start, end))
        if ticker in self._failures:
            raise self._failures[ticker]
        return self._bars.get(ticker, [])


def sec(ticker="005930.KS", name="삼성전자"):
    return Security(ticker=ticker, market="KR", name=name, exchange="KOSPI", currency="KRW")


# --- 유니버스 -------------------------------------------------------

def test_sync_universe_inserts_securities(repo):
    n = sync_universe(repo, FakeUniverse([sec(), sec("000660.KS", "SK하이닉스")]), market="KR")

    assert n == 2
    assert {s.ticker for s in repo.list_securities("KR")} == {"005930.KS", "000660.KS"}


def test_sync_universe_marks_dropped_tickers_out(repo):
    sync_universe(repo, FakeUniverse([sec(), sec("000660.KS", "SK하이닉스")]), market="KR")

    sync_universe(repo, FakeUniverse([sec()]), market="KR")

    assert {s.ticker for s in repo.list_securities("KR")} == {"005930.KS"}
    assert repo.get_security("000660.KS").in_universe is False


# --- 수집 -----------------------------------------------------------

def test_collect_saves_statements_and_prices(repo):
    repo.upsert_security(sec())
    fundamentals = FakeFundamentals()
    prices = FakePrices({"005930.KS": [
        PriceBar(ticker="005930.KS", date=date(2026, 1, 5), close=70000.0)
    ]})

    summary = collect_market(
        repo, "KR", fundamentals, prices,
        years=[2024, 2025], price_start=date(2021, 1, 1), price_end=date(2026, 1, 31),
    )

    assert summary.succeeded == 1
    assert summary.failed == 0
    assert len(repo.get_raw_statements("005930.KS")) == 2
    assert len(repo.get_price_bars("005930.KS")) == 1


def test_collect_skips_years_already_stored(repo):
    repo.upsert_security(sec())
    repo.save_raw_statements([
        RawStatement(ticker="005930.KS", fiscal_year=2024, source="dart", payload={})
    ])
    fundamentals = FakeFundamentals()

    collect_market(
        repo, "KR", fundamentals, FakePrices(),
        years=[2024, 2025], price_start=date(2021, 1, 1), price_end=date(2026, 1, 31),
    )

    assert fundamentals.requested == [("005930.KS", [2025])]


def test_collect_resumes_prices_from_last_stored_date(repo):
    repo.upsert_security(sec())
    repo.save_price_bars(
        [PriceBar(ticker="005930.KS", date=date(2026, 1, 5), close=1.0)], source="pykrx"
    )
    prices = FakePrices()

    collect_market(
        repo, "KR", FakeFundamentals(), prices,
        years=[], price_start=date(2021, 1, 1), price_end=date(2026, 1, 31),
    )

    assert prices.requested == [("005930.KS", date(2026, 1, 6), date(2026, 1, 31))]


def test_collect_isolates_failures_per_ticker(repo):
    repo.upsert_security(sec("005930.KS"))
    repo.upsert_security(sec("000660.KS", "SK하이닉스"))
    fundamentals = FakeFundamentals(
        failures={"005930.KS": PermanentSourceError("corp_code 없음")}
    )

    summary = collect_market(
        repo, "KR", fundamentals, FakePrices(),
        years=[2025], price_start=date(2021, 1, 1), price_end=date(2026, 1, 31),
    )

    assert summary.succeeded == 1
    assert summary.failed == 1
    assert len(repo.get_raw_statements("000660.KS")) == 1
    errors = repo.recent_errors()
    assert errors[0]["ticker"] == "005930.KS"


def test_collect_force_refetches_everything(repo):
    repo.upsert_security(sec())
    repo.save_raw_statements([
        RawStatement(ticker="005930.KS", fiscal_year=2024, source="dart", payload={})
    ])
    fundamentals = FakeFundamentals()

    collect_market(
        repo, "KR", fundamentals, FakePrices(),
        years=[2024, 2025], price_start=date(2021, 1, 1), price_end=date(2026, 1, 31),
        force=True,
    )

    assert fundamentals.requested == [("005930.KS", [2024, 2025])]


def test_collect_limits_to_requested_tickers(repo):
    repo.upsert_security(sec("005930.KS"))
    repo.upsert_security(sec("000660.KS", "SK하이닉스"))
    fundamentals = FakeFundamentals()

    collect_market(
        repo, "KR", fundamentals, FakePrices(),
        years=[2025], price_start=date(2021, 1, 1), price_end=date(2026, 1, 31),
        tickers=["000660.KS"],
    )

    assert [t for t, _ in fundamentals.requested] == ["000660.KS"]


def test_summary_renders_human_readable_text():
    summary = CollectSummary(total=10, succeeded=8, failed=2, statements=40, price_bars=1000)

    text = summary.render()

    assert "8" in text and "2" in text and "40" in text
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

Run: `python -m pytest tests/test_pipeline_collect.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'stockscout.pipeline'`

- [ ] **Step 3: 파이프라인을 구현한다**

`src/stockscout/pipeline/collect.py`:

```python
"""수집 오케스트레이션. 소스·저장 계층을 조립한다. 설계서 §5.3·§13."""

from __future__ import annotations

from datetime import date, timedelta

from pydantic import BaseModel

from stockscout.sources.base import FundamentalSource, PriceSource
from stockscout.sources.ratelimit import DailyQuotaExceeded
from stockscout.store.repository import Repository


class CollectSummary(BaseModel):
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    statements: int = 0
    price_bars: int = 0
    aborted: bool = False
    remaining: list[str] = []

    def render(self) -> str:
        lines = [
            f"대상 {self.total}종목 · 성공 {self.succeeded} · 실패 {self.failed}",
            f"재무제표 {self.statements}건 · 주가 {self.price_bars}봉 저장",
        ]
        if self.aborted:
            lines.append(
                f"⚠ 일일 한도 소진으로 중단. 남은 종목 {len(self.remaining)}개: "
                f"{', '.join(self.remaining[:5])}"
                + (" …" if len(self.remaining) > 5 else "")
            )
        return "\n".join(lines)


def sync_universe(repo: Repository, universe, market: str) -> int:
    """지수 구성종목을 갱신한다. 편출된 종목은 삭제하지 않고 in_universe=0으로 둔다.

    과거 수집 데이터를 보존하기 위해서다.
    """
    fetched = universe.fetch()
    fetched_tickers = {s.ticker for s in fetched}

    for security in fetched:
        repo.upsert_security(security)

    previously = {s.ticker for s in repo.list_securities(market=market)}
    dropped = sorted(previously - fetched_tickers)
    if dropped:
        repo.mark_out_of_universe(dropped)

    return len(fetched)


def collect_market(
    repo: Repository,
    market: str,
    fundamentals: FundamentalSource,
    prices: PriceSource,
    years: list[int],
    price_start: date,
    price_end: date,
    tickers: list[str] | None = None,
    force: bool = False,
) -> CollectSummary:
    """한 시장의 재무·주가를 수집한다.

    한 종목의 실패는 그 종목에만 머문다(설계서 §13). 일일 한도 소진만이 배치를 멈춘다.
    """
    targets = [
        s for s in repo.list_securities(market=market)
        if tickers is None or s.ticker in set(tickers)
    ]
    summary = CollectSummary(total=len(targets))

    for i, security in enumerate(targets):
        ticker = security.ticker
        try:
            summary.statements += _collect_statements(
                repo, ticker, fundamentals, years, force
            )
            summary.price_bars += _collect_prices(
                repo, ticker, prices, price_start, price_end, force
            )
            summary.succeeded += 1
        except DailyQuotaExceeded as exc:
            repo.record_error(ticker, fundamentals.name, str(exc))
            summary.aborted = True
            summary.remaining = [s.ticker for s in targets[i:]]
            break
        except Exception as exc:  # 종목 단위 격리
            repo.record_error(ticker, fundamentals.name, f"{type(exc).__name__}: {exc}")
            summary.failed += 1

    return summary


def _collect_statements(
    repo: Repository,
    ticker: str,
    source: FundamentalSource,
    years: list[int],
    force: bool,
) -> int:
    if not years:
        return 0
    wanted = list(years)
    if not force:
        have = repo.existing_statement_years(ticker, source.name)
        wanted = [y for y in years if y not in have]
    if not wanted:
        return 0

    statements = source.fetch_annual(ticker, wanted)
    if statements:
        repo.save_raw_statements(statements)
    return len(statements)


def _collect_prices(
    repo: Repository,
    ticker: str,
    source: PriceSource,
    start: date,
    end: date,
    force: bool,
) -> int:
    fetch_from = start
    if not force:
        latest = repo.latest_price_date(ticker)
        if latest:
            fetch_from = latest + timedelta(days=1)
    if fetch_from > end:
        return 0

    bars = source.fetch_daily(ticker, fetch_from, end)
    if bars:
        repo.save_price_bars(bars, source=source.name)
    return len(bars)
```

`src/stockscout/pipeline/__init__.py`: 빈 파일로 생성한다.

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

Run: `python -m pytest tests/test_pipeline_collect.py -v`
Expected: PASS — `9 passed`

- [ ] **Step 5: 커밋한다**

```bash
git add src/stockscout/pipeline/ tests/test_pipeline_collect.py
git commit -m "feat: 수집 파이프라인 (증분·격리·요약)"
```

---

## Task 14: 정규화 파이프라인

원본을 읽어 `financials` 테이블을 채운다. 네트워크를 쓰지 않으므로 몇 초에 끝난다.

**Files:**
- Create: `src/stockscout/pipeline/normalize.py`
- Test: `tests/test_pipeline_normalize.py`

- [ ] **Step 1: 실패하는 테스트를 작성한다**

`tests/test_pipeline_normalize.py`:

```python
import pytest

from stockscout.models import RawStatement, Security
from stockscout.pipeline.normalize import NormalizeSummary, normalize_market
from stockscout.store.db import connect, init_db
from stockscout.store.repository import Repository


@pytest.fixture
def repo(tmp_path):
    with connect(tmp_path / "t.db") as conn:
        init_db(conn)
        yield Repository(conn)


def item(sj_div, account_id, account_nm, amount):
    return {"sj_div": sj_div, "account_id": account_id,
            "account_nm": account_nm, "thstrm_amount": amount}


COMPLETE_PAYLOAD = {"list": [
    item("IS", "ifrs-full_Revenue", "매출액", "300,000"),
    item("IS", "dart_OperatingIncomeLoss", "영업이익", "30,000"),
    item("BS", "ifrs-full_Liabilities", "부채총계", "60,000"),
    item("BS", "ifrs-full_Equity", "자본총계", "200,000"),
]}


def setup_ticker(repo, ticker="005930.KS", years=(2024, 2025), payload=None):
    repo.upsert_security(
        Security(ticker=ticker, market="KR", name="삼성전자", exchange="KOSPI", currency="KRW")
    )
    repo.save_raw_statements([
        RawStatement(ticker=ticker, fiscal_year=y, source="dart",
                     payload=payload if payload is not None else COMPLETE_PAYLOAD)
        for y in years
    ])


def test_normalize_writes_financials(repo):
    setup_ticker(repo)

    summary = normalize_market(repo, market="KR")

    fins = repo.get_financials("005930.KS")
    assert [f.fiscal_year for f in fins] == [2024, 2025]
    assert fins[0].revenue == 300000.0
    assert summary.normalized == 2
    assert summary.incomplete == 0


def test_normalize_counts_incomplete_records(repo):
    setup_ticker(repo, payload={"list": []})

    summary = normalize_market(repo, market="KR")

    assert summary.normalized == 2
    assert summary.incomplete == 2


def test_normalize_is_idempotent(repo):
    setup_ticker(repo)

    normalize_market(repo, market="KR")
    normalize_market(repo, market="KR")

    assert len(repo.get_financials("005930.KS")) == 2


def test_normalize_skips_tickers_without_raw_data(repo):
    repo.upsert_security(
        Security(ticker="EMPTY.KS", market="KR", name="빈종목", currency="KRW")
    )

    summary = normalize_market(repo, market="KR")

    assert summary.normalized == 0
    assert repo.get_financials("EMPTY.KS") == []


def test_summary_reports_missing_field_counts(repo):
    setup_ticker(repo, years=(2025,))

    summary = normalize_market(repo, market="KR")

    assert summary.missing_counts["capex"] == 1
    assert "capex" in summary.render()
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

Run: `python -m pytest tests/test_pipeline_normalize.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'stockscout.pipeline.normalize'`

- [ ] **Step 3: 구현한다**

`src/stockscout/pipeline/normalize.py`:

```python
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
```

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

Run: `python -m pytest tests/test_pipeline_normalize.py -v`
Expected: PASS — `5 passed`

- [ ] **Step 5: 커밋한다**

```bash
git add src/stockscout/pipeline/normalize.py tests/test_pipeline_normalize.py
git commit -m "feat: 정규화 파이프라인과 결측 보고"
```

---

## Task 15: CLI 배선

**Files:**
- Create: `src/stockscout/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: 실패하는 테스트를 작성한다**

`tests/test_cli.py`:

```python
from datetime import date

import pytest

from stockscout.cli import build_parser, price_window


def test_parser_universe_command():
    args = build_parser().parse_args(["universe", "--market", "KR"])

    assert args.command == "universe"
    assert args.market == "KR"


def test_parser_collect_defaults():
    args = build_parser().parse_args(["collect"])

    assert args.command == "collect"
    assert args.market == "KR"
    assert args.force is False
    assert args.tickers is None


def test_parser_collect_accepts_tickers_and_force():
    args = build_parser().parse_args(
        ["collect", "--tickers", "005930.KS", "000660.KS", "--force"]
    )

    assert args.tickers == ["005930.KS", "000660.KS"]
    assert args.force is True


def test_parser_normalize_command():
    args = build_parser().parse_args(["normalize"])

    assert args.command == "normalize"


def test_parser_requires_a_command():
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_price_window_spans_requested_years():
    start, end = price_window(years=5, today=date(2026, 9, 10))

    assert end == date(2026, 9, 10)
    assert start == date(2021, 9, 10)


def test_fiscal_years_excludes_current_year():
    """올해 사업보고서는 아직 안 나왔다. 작년부터 거슬러 올라간다."""
    from stockscout.cli import fiscal_years

    assert fiscal_years(count=5, today=date(2026, 9, 10)) == [2021, 2022, 2023, 2024, 2025]
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

Run: `python -m pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'stockscout.cli'`

- [ ] **Step 3: CLI를 구현한다**

`src/stockscout/cli.py`:

```python
"""명령줄 진입점."""

from __future__ import annotations

import argparse
import sys
from datetime import date

from stockscout.config import DEFAULT_CONFIG_PATH, load_config, load_secrets
from stockscout.pipeline.collect import collect_market, sync_universe
from stockscout.pipeline.normalize import normalize_market
from stockscout.sources.dart import DartSource
from stockscout.sources.krx import KrxPriceSource, KrxUniverse
from stockscout.store.db import DEFAULT_DB_PATH, connect, init_db
from stockscout.store.repository import Repository


def price_window(years: int, today: date | None = None) -> tuple[date, date]:
    end = today or date.today()
    try:
        start = end.replace(year=end.year - years)
    except ValueError:  # 2월 29일
        start = end.replace(year=end.year - years, day=28)
    return start, end


def fiscal_years(count: int, today: date | None = None) -> list[int]:
    """수집할 회계연도. 올해 사업보고서는 아직 공시되지 않았으므로 작년부터."""
    last = (today or date.today()).year - 1
    return list(range(last - count + 1, last + 1))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stockscout", description="한국·미국 우량주 낙폭 스크리너"
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    sub = parser.add_subparsers(dest="command", required=True)

    p_uni = sub.add_parser("universe", help="지수 구성종목 목록을 갱신한다")
    p_uni.add_argument("--market", default="KR", choices=["KR", "US"])

    p_col = sub.add_parser("collect", help="재무제표와 주가를 수집한다")
    p_col.add_argument("--market", default="KR", choices=["KR", "US"])
    p_col.add_argument("--tickers", nargs="+", default=None, help="특정 종목만")
    p_col.add_argument("--force", action="store_true", help="이미 받은 것도 다시 받는다")

    p_norm = sub.add_parser("normalize", help="원본을 공통 스키마로 변환한다")
    p_norm.add_argument("--market", default="KR", choices=["KR", "US"])

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)

    with connect(args.db) as conn:
        init_db(conn)
        repo = Repository(conn)

        if args.command == "universe":
            n = sync_universe(repo, KrxUniverse(), market=args.market)
            print(f"유니버스 갱신 완료: {n}종목")

        elif args.command == "collect":
            secrets = load_secrets()
            start, end = price_window(max(config.lookback.price_years))
            summary = collect_market(
                repo,
                market=args.market,
                fundamentals=DartSource(secrets.dart_api_key),
                prices=KrxPriceSource(),
                years=fiscal_years(config.lookback.financial_years),
                price_start=start,
                price_end=end,
                tickers=args.tickers,
                force=args.force,
            )
            print(summary.render())
            if summary.failed:
                print("\n최근 오류:")
                for err in repo.recent_errors(limit=5):
                    print(f"  {err['ticker']}: {err['message']}")

        elif args.command == "normalize":
            print(normalize_market(repo, market=args.market).render())

    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

Run: `python -m pytest tests/test_cli.py -v`
Expected: PASS — `7 passed`

- [ ] **Step 5: 커밋한다**

```bash
git add src/stockscout/cli.py tests/test_cli.py
git commit -m "feat: CLI (universe/collect/normalize)"
```

---

## Task 16: 실제 데이터로 관통 검증

여기서 처음으로 진짜 KOSPI 200 데이터를 끝까지 통과시킨다.

**Files:**
- Create: `tests/test_integration_kr.py`

- [ ] **Step 1: 소수 종목으로 먼저 확인한다**

```bash
stockscout universe --market KR
```
Expected: `유니버스 갱신 완료: 200종목` (전후 몇 개 차이는 정상)

```bash
stockscout collect --market KR --tickers 005930.KS 000660.KS
```
Expected: `대상 2종목 · 성공 2 · 실패 0` 과 재무제표 건수·주가 봉수

```bash
stockscout normalize --market KR
```
Expected: `2종목 · N개 연도 정규화 (필수항목 결측 0건)` 과 결측 상위 목록

**필수항목 결측이 0이 아니면** Task 12 Step 5로 돌아가 매핑을 보강한다.

- [ ] **Step 2: 통합 테스트를 작성한다**

`tests/test_integration_kr.py`:

```python
"""실제 DB에 대한 검증. `stockscout collect` 실행 후에 의미가 있다.

DB 파일이 없으면 모듈 전체가 skip되므로, 수집 전에는 `pytest tests/`가 그냥 통과한다.
"""

from datetime import date
from pathlib import Path

import pytest

from stockscout.store.db import DEFAULT_DB_PATH, connect
from stockscout.store.repository import Repository

pytestmark = pytest.mark.skipif(
    not Path(DEFAULT_DB_PATH).exists(),
    reason="stockscout collect 를 먼저 실행해야 한다",
)


@pytest.fixture
def repo():
    with connect(DEFAULT_DB_PATH) as conn:
        yield Repository(conn)


def test_samsung_has_five_years_of_financials(repo):
    fins = repo.get_financials("005930.KS")

    assert len(fins) >= 5, f"5개년이 필요한데 {len(fins)}개년만 있습니다"


def test_samsung_required_fields_all_present(repo):
    for fin in repo.get_financials("005930.KS"):
        assert fin.has_required, (
            f"{fin.fiscal_year}년 필수항목 결측: {fin.missing_fields}"
        )


def test_samsung_revenue_is_plausible(repo):
    """삼성전자 연매출은 100조~500조원 범위여야 한다.

    단위 오류(천원 단위로 읽는 등)를 잡는다.
    """
    latest = repo.get_financials("005930.KS")[-1]

    assert 100e12 <= latest.revenue <= 500e12, (
        f"매출 {latest.revenue:,.0f}원이 상식 범위를 벗어납니다. 단위를 확인하세요."
    )


def test_samsung_debt_ratio_is_plausible(repo):
    latest = repo.get_financials("005930.KS")[-1]

    assert 0 < latest.debt_ratio < 100, (
        f"부채비율 {latest.debt_ratio:.1f}%가 상식 범위를 벗어납니다"
    )


def test_price_history_covers_five_years(repo):
    bars = repo.get_price_bars("005930.KS")
    if not bars:
        pytest.skip("주가 미수집")

    span_days = (bars[-1].date - bars[0].date).days

    assert span_days >= 365 * 4.5, f"주가 이력이 {span_days}일뿐입니다"
    assert len(bars) >= 1000, f"5년이면 1,200봉 내외인데 {len(bars)}봉입니다"


def test_prices_are_positive(repo):
    bars = repo.get_price_bars("005930.KS")
    if not bars:
        pytest.skip("주가 미수집")

    assert all(b.close > 0 for b in bars)
```

- [ ] **Step 3: 통합 테스트를 실행한다**

Run: `python -m pytest tests/test_integration_kr.py -v`
Expected: PASS — 6개 모두 통과

**`test_samsung_revenue_is_plausible`이 실패하면** 매핑이 잘못된 계정을 잡은 것이다 (예: 매출총이익을 매출액으로). `scripts/spike_dart.py statement` 출력을 다시 보고 `ACCOUNT_MAP`을 고친다.

- [ ] **Step 4: 전체 KOSPI 200을 수집한다**

```bash
stockscout collect --market KR
```

200종목 × (재무 5회 + 주가 1회) ≈ 1,200건. DART 초당 5건 제한이므로 **5~10분** 걸린다. 진행 중 실패가 나와도 배치는 계속된다.

Expected: `대상 200종목 · 성공 190+ · 실패 10 미만`

실패가 20종목을 넘으면 출력된 오류 메시지를 확인한다. 흔한 원인:
- `corp_code를 찾을 수 없습니다` — DART에 없는 종목(외국계 등). 정상이다
- `요청 제한 초과` — 잠시 후 재실행하면 이미 받은 것은 건너뛴다

- [ ] **Step 5: 전체를 정규화하고 품질을 확인한다**

```bash
stockscout normalize --market KR
```

Expected: `190+종목 · 900+개 연도 정규화` 와 결측 상위 목록

**필수항목 결측이 전체의 10%를 넘으면** 결측 상위 목록을 보고 `ACCOUNT_MAP`에 동의어를 추가한 뒤 `normalize`를 다시 돌린다. 재수집은 필요 없다 — 원본이 이미 DB에 있기 때문이다. **이것이 수집과 분석을 분리한 이유다.**

- [ ] **Step 6: 전체 테스트를 돌린다**

Run: `python -m pytest tests/ -v`
Expected: 모든 테스트 PASS

- [ ] **Step 7: 커밋한다**

```bash
git add tests/test_integration_kr.py
git commit -m "test: 실제 KOSPI 200 데이터 관통 검증"
```

---

## 완료 기준

이 계획이 끝나면 다음이 성립한다.

- [ ] `stockscout universe --market KR` → KOSPI 200 종목 마스터가 채워진다
- [ ] `stockscout collect --market KR` → 재무 5개년·주가 5년이 원본 그대로 적재된다
- [ ] `stockscout normalize --market KR` → 공통 스키마로 변환되고 결측 현황이 보고된다
- [ ] 한 종목이 실패해도 배치가 멈추지 않고, 실패 사유가 `collect_errors`에 남는다
- [ ] 재실행 시 이미 받은 데이터는 건너뛴다
- [ ] `pytest tests/` 전체 통과, 통합 테스트를 뺀 전부가 네트워크 없이 실행된다
- [ ] 삼성전자 매출·부채비율이 상식 범위 안에 있다 (단위 오류 없음)

## 다음 계획

**계획 B — 분석 엔진** (설계서 §8·§9): 하드필터, 4축 스코어링, 결측 만점 환산, 평균·고점 대비 하락률, ★★/★ 등급 판정, `analyze` 명령과 스냅샷 저장.

계획 B는 네트워크를 전혀 쓰지 않는다. 이 계획이 채워둔 `financials`·`raw_prices` 테이블만 읽는다.
