# 계획 B — 가격 분석과 스냅샷

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `stockscout analyze` 한 줄로 저장된 주가에서 1·3·5년 평균 대비·고점 대비 하락률을 계산해, 재현 가능한 스냅샷으로 남긴다.

**Architecture:** 설계서 §9.1(하락률)·§9.2(밸류에이션)과 §8.1의 하드필터 중 **재무제표가 필요 없는 두 가지**(`FINANCIAL_SECTOR`·`INSUFFICIENT_HISTORY`)를 구현한다. 새 `analysis/` 계층은 순수 함수만 담는다 — 네트워크도 DB도 모르고, 입력은 값, 출력은 값이다. `pipeline/analyze.py`가 저장 계층에서 읽어 조립하고 스냅샷을 쓴다.

**Tech Stack:** Python 3.11+ / pydantic 2 / SQLite (stdlib) / pytest. **새 의존성 없음.**

**참조 문서:** 설계서 `docs/superpowers/specs/2026-09-10-stock-screener-design.md` §6(데이터 모델) §8.1(하드필터) §9(하락 신호) §10(설정) / 계획 A `docs/superpowers/plans/2026-09-10-plan-a-foundation-and-kr-ingestion.md`의 「계획 B가 알아야 할 것」

---

## 시작 전 상태

계획 A가 `master`에 병합돼 있다. 테스트 162개 통과, 2개 skip.

`data/stockscout.db`에 실제 데이터가 있다.

| 테이블 | 상태 |
|---|---|
| `securities` | 42행 (KOSPI 대표종목, 7개가 `is_financial=1`) |
| `raw_prices` | 51,231행 · 2021-09-10 ~ 2026-09-10 · 중복 0 · 이상치 0 |
| `raw_financials` | **0행** — DART 키 대기 |
| `financials` | **0행** |
| `snapshots` / `analysis_results` | 0행 — 이 계획이 채운다 |

**이 계획은 DART 키가 없어도 끝까지 실행된다.** 실제 데이터로 전 구간을 검증할 수 있다는 뜻이다.

### 이 계획이 채우지 않는 컬럼

`analysis_results`에는 계획 C의 자리가 비어 있다. 전부 nullable이므로 문제없다.

- `score_total` · `score_profit` · `score_growth` · `score_stability` · `score_cashflow` · `score_detail`
- `signal_grade` · `signal_reason`

`passed_filters`는 `NOT NULL`이므로 이 계획이 값을 넣는다. 가격 기반 필터 두 개의 결과다. 계획 C가 재무 기반 필터를 더하면 같은 컬럼이 더 엄격해진다.

---

## 파일 구조

| 파일 | 책임 |
|---|---|
| `src/stockscout/models.py` (수정) | `PriceStats` · `Valuation` · `FilterOutcome` · `AnalysisResult` · `Snapshot` 추가 |
| `src/stockscout/analysis/__init__.py` | 빈 패키지 마커 |
| `src/stockscout/analysis/valuation.py` | 평균·고점·하락률·PER·PBR·시가총액. **순수 함수** |
| `src/stockscout/analysis/filters.py` | 하드필터. 이 계획은 가격 기반 2종 |
| `src/stockscout/store/repository.py` (수정) | `trading_dates` · `save_snapshot`(원자적) · `get_latest_snapshot` · `get_analysis_results` |
| `src/stockscout/pipeline/analyze.py` | 오케스트레이션 + 요약 |
| `src/stockscout/cli.py` (수정) | `analyze` 하위명령 |

**계층 규칙은 계획 A와 같다.** `analysis/`는 `stockscout.models`만 import한다 — `sources/`·`store/`·`pipeline/`을 import하면 안 된다. 계획 A의 최종 리뷰가 이 그래프가 깨끗함을 기계적으로 확인했으니 유지한다.

```
        cli
         ↓
      pipeline
   ↙   ↙  ↓  ↘   ↘
sources normalize analysis store   ← 서로를 import하지 않는다
   ↘   ↘  ↓  ↙   ↙
        models                      ← 아무것도 import하지 않는다
```

---

## 이 계획에서 반드시 지켜야 할 것들

계획 A의 실행과 리뷰에서 실측으로 확인된 함정들이다. 각각 해당 Task에서 다시 설명한다.

**1. 5년 이력 판정을 연분수로 하면 전 종목이 탈락한다.** `(MAX-MIN).days / 365.25`로 재면 2021-09-10 ~ 2026-09-10이 1,826일 = 4.9993년이 되어 5.0 미만이다. 42종목 전부가 `INSUFFICIENT_HISTORY`로 제외된다. **달력 기준으로 비교해야 한다** (Task 3).

**2. `json.dumps(float('inf'))`는 유효하지 않은 JSON을 뱉는다.** `Infinity`라는 토큰이 나오고 `JSON.parse`가 거부한다. 이 계획에서 `inf`가 생길 수 있는 곳은 없지만(PER·PBR은 분모가 0 이하이면 `None`), 계획 C의 `interest_coverage`가 `inf`를 반환하므로 직렬화 헬퍼를 지금 만들어 둔다 (Task 5).

**3. 주가는 반드시 날짜 구간을 지정해 읽는다.** `Repository`에 삭제 메서드가 없어 `raw_prices`는 append/upsert만 된다. `price_years`를 줄이면 옛 봉이 남아 있으므로 `get_price_bars(ticker, start=, end=)`를 쓰고 무인자 형태를 쓰지 않는다 (Task 6).

**4. 스냅샷 쓰기는 원자적이어야 한다.** 설계서 §13이 "배치 중 예외 → 스냅샷을 커밋하지 않는다"를 요구한다. 범용 트랜잭션 매니저는 만들지 않는다 — SQLite가 중첩 트랜잭션을 지원하지 않아 기존 메서드의 `commit()`이 바깥 트랜잭션까지 커밋한다. 전용 메서드 하나만 만든다 (Task 5).

**5. 콘솔이 cp949다.** `✓`(U+2713)·`⚠`(U+26A0)을 출력하면 `UnicodeEncodeError`로 죽는다. `render()` 계열은 ASCII 기호만 쓰고 회귀 테스트로 고정한다 (Task 7).

---

## Task 1: 분석 계층 데이터 모델

**Files:**
- Modify: `src/stockscout/models.py`
- Test: `tests/test_models_analysis.py`

- [ ] **Step 1: 실패하는 테스트를 작성한다**

`tests/test_models_analysis.py`:

```python
from datetime import date

import pytest

from stockscout.models import (
    AnalysisResult,
    FilterOutcome,
    PriceStats,
    Snapshot,
    Valuation,
)


def test_filter_outcome_passed_when_no_failures():
    assert FilterOutcome(failures=[]).passed is True


def test_filter_outcome_failed_when_any_failure():
    outcome = FilterOutcome(failures=["FINANCIAL_SECTOR"])

    assert outcome.passed is False
    assert outcome.failures == ["FINANCIAL_SECTOR"]


def test_price_stats_holds_windowed_values():
    stats = PriceStats(
        price_current=41300.0,
        avg_price={1: 55000.0, 3: 64200.0, 5: 71400.0},
        high_5y=88800.0,
        drop_vs_avg={1: -0.249, 3: -0.357, 5: -0.422},
        drop_vs_high=-0.535,
    )

    assert stats.avg_price[5] == 71400.0
    assert stats.drop_vs_avg[5] == pytest.approx(-0.422)


def test_price_stats_allows_missing_windows():
    """커버리지 미달 창은 NULL이다 (설계서 §9.1)."""
    stats = PriceStats(
        price_current=100.0,
        avg_price={1: 120.0, 3: None, 5: None},
        high_5y=150.0,
        drop_vs_avg={1: -0.1667, 3: None, 5: None},
        drop_vs_high=-0.3333,
    )

    assert stats.avg_price[3] is None
    assert stats.drop_vs_avg[5] is None


def test_valuation_all_none_is_allowed():
    """shares_outstanding이 없으면 셋 다 NULL (설계서 §9.2)."""
    v = Valuation()

    assert (v.market_cap, v.per, v.pbr) == (None, None, None)


def test_snapshot_carries_config_identity():
    snap = Snapshot(run_at="2026-09-11T02:00:00+00:00", config_hash="abc123",
                    config_json='{"signal": {}}', note="첫 실행")

    assert snap.snapshot_id is None      # 저장 전에는 없다
    assert snap.config_hash == "abc123"


def test_analysis_result_defaults_score_fields_to_none():
    """점수 칸은 계획 C가 채운다."""
    r = AnalysisResult(
        ticker="005930.KS",
        filters=FilterOutcome(failures=[]),
        stats=PriceStats(price_current=1.0, avg_price={}, high_5y=1.0,
                         drop_vs_avg={}, drop_vs_high=0.0),
        valuation=Valuation(),
    )

    assert r.score_total is None
    assert r.signal_grade is None
    assert r.passed_filters is True


def test_analysis_result_passed_filters_mirrors_outcome():
    r = AnalysisResult(
        ticker="X",
        filters=FilterOutcome(failures=["INSUFFICIENT_HISTORY"]),
        stats=PriceStats(price_current=1.0, avg_price={}, high_5y=1.0,
                         drop_vs_avg={}, drop_vs_high=0.0),
        valuation=Valuation(),
    )

    assert r.passed_filters is False
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

Run: `C:/Users/USER/DEV/주식투자/.venv/Scripts/python.exe -m pytest tests/test_models_analysis.py -v`
Expected: FAIL — `ImportError: cannot import name 'PriceStats' from 'stockscout.models'`

- [ ] **Step 3: 모델을 추가한다**

`src/stockscout/models.py` 맨 아래에 추가한다:

```python
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
```

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

Run: `C:/Users/USER/DEV/주식투자/.venv/Scripts/python.exe -m pytest tests/test_models_analysis.py -v`
Expected: PASS — `8 passed`

- [ ] **Step 5: `models.py`가 여전히 아무것도 import하지 않는지 확인한다**

Run: `grep -n "^from stockscout\|^import stockscout" src/stockscout/models.py || echo "없음"`
Expected: `없음`

- [ ] **Step 6: 전체 스위트를 돌린다**

Run: `C:/Users/USER/DEV/주식투자/.venv/Scripts/python.exe -m pytest -q`
Expected: `170 passed, 2 skipped`

- [ ] **Step 7: 커밋한다**

```bash
git add src/stockscout/models.py tests/test_models_analysis.py
git commit -m "feat: 분석 계층 데이터 모델"
```

---

## Task 2: 평균·고점·하락률 계산

설계서 §9.1의 핵심. **이 계획 전체에서 가장 중요한 계산이다** — 제품의 신호가 여기서 나온다.

**Files:**
- Create: `src/stockscout/analysis/__init__.py`
- Create: `src/stockscout/analysis/valuation.py`
- Test: `tests/test_analysis_valuation.py`

- [ ] **Step 1: 실패하는 테스트를 작성한다**

`tests/test_analysis_valuation.py`:

```python
from datetime import date, timedelta

import pytest

from stockscout.analysis.valuation import compute_price_stats, window_start
from stockscout.models import PriceBar


def bars(ticker="X", start=date(2021, 1, 1), closes=None):
    """closes 길이만큼 하루씩 증가하는 일봉을 만든다."""
    closes = closes or [100.0]
    return [
        PriceBar(ticker=ticker, date=start + timedelta(days=i), close=c)
        for i, c in enumerate(closes)
    ]


# --- 창 시작일 --------------------------------------------------------


def test_window_start_uses_calendar_years():
    assert window_start(date(2026, 9, 10), 5) == date(2021, 9, 10)
    assert window_start(date(2026, 9, 10), 1) == date(2025, 9, 10)


def test_window_start_handles_leap_day():
    assert window_start(date(2024, 2, 29), 5) == date(2019, 2, 28)


# --- 평균과 하락률 ----------------------------------------------------


def test_average_is_arithmetic_mean_of_closes_in_window():
    series = bars(closes=[100.0, 200.0, 300.0, 400.0])
    as_of = series[-1].date
    calendar = [b.date for b in series]

    stats = compute_price_stats(series, as_of=as_of, windows=[1],
                                calendar=calendar, min_coverage=0.8)

    assert stats.avg_price[1] == pytest.approx(250.0)
    assert stats.price_current == 400.0


def test_drop_vs_avg_is_current_over_average_minus_one():
    series = bars(closes=[100.0, 100.0, 100.0, 60.0])
    as_of = series[-1].date
    calendar = [b.date for b in series]

    stats = compute_price_stats(series, as_of=as_of, windows=[1],
                                calendar=calendar, min_coverage=0.8)

    # 평균 90, 현재가 60 → 60/90 - 1 = -0.3333
    assert stats.avg_price[1] == pytest.approx(90.0)
    assert stats.drop_vs_avg[1] == pytest.approx(-1 / 3)


def test_drop_vs_high_uses_max_close_in_five_year_window():
    series = bars(closes=[50.0, 200.0, 80.0, 100.0])
    as_of = series[-1].date
    calendar = [b.date for b in series]

    stats = compute_price_stats(series, as_of=as_of, windows=[5],
                                calendar=calendar, min_coverage=0.8)

    assert stats.high_5y == 200.0
    assert stats.drop_vs_high == pytest.approx(-0.5)


def test_average_only_counts_bars_inside_the_window():
    """1년 창은 1년 전 이후 봉만 센다."""
    old = [PriceBar(ticker="X", date=date(2020, 1, 1), close=1000.0)]
    recent = bars(start=date(2026, 1, 1), closes=[100.0, 100.0])
    series = old + recent
    as_of = date(2026, 1, 2)
    calendar = [b.date for b in recent]

    stats = compute_price_stats(series, as_of=as_of, windows=[1],
                                calendar=calendar, min_coverage=0.8)

    assert stats.avg_price[1] == pytest.approx(100.0)   # 1000은 창 밖


# --- 커버리지 관문 ----------------------------------------------------


def test_window_below_coverage_threshold_is_none():
    """설계서 §9.1: 해당 창 거래일의 80% 미만이면 NULL."""
    calendar = [date(2026, 1, 1) + timedelta(days=i) for i in range(10)]
    series = bars(start=date(2026, 1, 1), closes=[100.0] * 5)   # 50%

    stats = compute_price_stats(series, as_of=calendar[-1], windows=[1],
                                calendar=calendar, min_coverage=0.8)

    assert stats.avg_price[1] is None
    assert stats.drop_vs_avg[1] is None


def test_window_at_coverage_threshold_is_kept():
    """정확히 80%는 통과한다 (경계값)."""
    calendar = [date(2026, 1, 1) + timedelta(days=i) for i in range(10)]
    series = bars(start=date(2026, 1, 1), closes=[100.0] * 8)   # 80%

    stats = compute_price_stats(series, as_of=calendar[-1], windows=[1],
                                calendar=calendar, min_coverage=0.8)

    assert stats.avg_price[1] == pytest.approx(100.0)


def test_high_and_current_survive_coverage_failure():
    """고점과 현재가는 커버리지와 무관하게 산출된다.

    창별 평균만 관문의 대상이다. 고점 대비 하락률은 계획 C의 등급
    판정에서 쓰이므로 여기서 지워버리면 안 된다.
    """
    calendar = [date(2026, 1, 1) + timedelta(days=i) for i in range(10)]
    series = bars(start=date(2026, 1, 1), closes=[200.0, 100.0])   # 20%

    stats = compute_price_stats(series, as_of=calendar[-1], windows=[1],
                                calendar=calendar, min_coverage=0.8)

    assert stats.avg_price[1] is None
    assert stats.high_5y == 200.0
    assert stats.price_current == 100.0
    assert stats.drop_vs_high == pytest.approx(-0.5)


# --- 경계 조건 --------------------------------------------------------


def test_empty_series_raises():
    with pytest.raises(ValueError, match="주가"):
        compute_price_stats([], as_of=date(2026, 1, 1), windows=[1],
                            calendar=[date(2026, 1, 1)], min_coverage=0.8)


def test_empty_calendar_treats_every_window_as_uncovered():
    series = bars(closes=[100.0])

    stats = compute_price_stats(series, as_of=series[-1].date, windows=[1],
                                calendar=[], min_coverage=0.8)

    assert stats.avg_price[1] is None


def test_multiple_windows_computed_independently():
    calendar = [date(2021, 9, 10) + timedelta(days=i) for i in range(1827)]
    closes = [100.0] * 1000 + [50.0] * 827
    series = bars(start=date(2021, 9, 10), closes=closes)

    stats = compute_price_stats(series, as_of=series[-1].date, windows=[1, 3, 5],
                                calendar=calendar, min_coverage=0.8)

    assert set(stats.avg_price) == {1, 3, 5}
    assert stats.avg_price[1] == pytest.approx(50.0)     # 최근 1년은 전부 50
    assert stats.avg_price[5] > stats.avg_price[1]       # 5년은 100이 섞인다
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

Run: `C:/Users/USER/DEV/주식투자/.venv/Scripts/python.exe -m pytest tests/test_analysis_valuation.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'stockscout.analysis'`

- [ ] **Step 3: 구현한다**

`src/stockscout/analysis/valuation.py`:

```python
"""가격 통계와 밸류에이션. 설계서 §9.1·§9.2.

순수 함수만 둔다. 네트워크도 DB도 모르고, 입력은 값, 출력은 값이다.
덕분에 네트워크 없이 단독으로 검증할 수 있다.
"""

from __future__ import annotations

from datetime import date

from stockscout.models import PriceBar, PriceStats

# 고점 창은 설계서 §9.1이 5년으로 못박은 값이다. 창별 평균(1·3·5년)과
# 달리 설정으로 바뀌지 않으므로 상수로 둔다.
HIGH_WINDOW_YEARS = 5


def window_start(as_of: date, years: int) -> date:
    """as_of로부터 years년 전 날짜. 달력 기준이다.

    연분수(days / 365.25)로 계산하면 안 된다. 2021-09-10 ~ 2026-09-10은
    정확히 5달력년이지만 1,826일이고 1826/365.25 = 4.9993이라 5년
    미만으로 판정된다. 실측으로 확인했고 전 종목이 탈락했다.
    """
    try:
        return as_of.replace(year=as_of.year - years)
    except ValueError:  # 2월 29일
        return as_of.replace(year=as_of.year - years, day=28)


def compute_price_stats(
    bars: list[PriceBar],
    as_of: date,
    windows: list[int],
    calendar: list[date],
    min_coverage: float,
) -> PriceStats:
    """창별 평균·하락률과 5년 고점을 계산한다.

    calendar는 시장 전체의 관측된 거래일 목록이다. 거래소 달력을 따로
    두지 않고, 수집된 전 종목의 날짜 합집합을 거래일로 본다. 한 종목의
    봉 수를 그 창의 거래일 수로 나눈 값이 커버리지다(설계서 §9.1).

    커버리지 관문은 창별 평균에만 적용한다. 현재가와 5년 고점은
    관문과 무관하게 산출한다 — 고점 대비 하락률은 계획 C의 등급 판정에
    쓰이므로 평균이 없다고 함께 지워버리면 안 된다.
    """
    if not bars:
        raise ValueError("주가 데이터가 없습니다")

    # as_of 이후 봉은 전부 버린다. 이 함수는 "as_of 시점에 무엇이 보였는가"를
    # 답해야 한다. 현재가와 고점만 상한 없이 계산하면, 과거 시점을 물었을 때
    # 미래 가격이 섞여 들어온다(look-ahead bias). 파이프라인이 늘 최신
    # 날짜를 넘기므로 지금은 결과가 같지만, 그 호출 습관에 정확성을
    # 의존시키면 안 된다 — 백테스트나 "그때는 뭐라고 했나" 조회가
    # 조용히 낙관적인 답을 받게 되고 에러는 나지 않는다.
    ordered = [b for b in sorted(bars, key=lambda b: b.date) if b.date <= as_of]
    if not ordered:
        raise ValueError(f"주가 데이터가 {as_of} 이전에 없습니다")

    price_current = ordered[-1].close

    five_year_start = window_start(as_of, HIGH_WINDOW_YEARS)
    in_five = [b for b in ordered if b.date >= five_year_start]
    high_5y = max(b.close for b in (in_five or ordered))

    avg_price: dict[int, float | None] = {}
    drop_vs_avg: dict[int, float | None] = {}

    for years in windows:
        start = window_start(as_of, years)
        closes = [b.close for b in ordered if start <= b.date <= as_of]
        expected = sum(1 for d in calendar if start <= d <= as_of)

        if not closes or expected == 0 or len(closes) / expected < min_coverage:
            avg_price[years] = None
            drop_vs_avg[years] = None
            continue

        mean = sum(closes) / len(closes)
        avg_price[years] = mean
        drop_vs_avg[years] = price_current / mean - 1 if mean > 0 else None

    return PriceStats(
        price_current=price_current,
        avg_price=avg_price,
        high_5y=high_5y,
        drop_vs_avg=drop_vs_avg,
        drop_vs_high=price_current / high_5y - 1 if high_5y > 0 else 0.0,
    )
```

`src/stockscout/analysis/__init__.py`: 빈 파일로 생성한다.

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

Run: `C:/Users/USER/DEV/주식투자/.venv/Scripts/python.exe -m pytest tests/test_analysis_valuation.py -v`
Expected: PASS — `12 passed`

- [ ] **Step 5: `analysis/`의 import를 확인한다**

Run: `grep -n "^from stockscout\|^import stockscout" src/stockscout/analysis/valuation.py`
Expected: `from stockscout.models import PriceBar, PriceStats` 한 줄만

- [ ] **Step 6: 실제 데이터로 손계산과 대조한다**

```bash
C:/Users/USER/DEV/주식투자/.venv/Scripts/python.exe -c "
import sqlite3
from datetime import date
from stockscout.analysis.valuation import compute_price_stats
from stockscout.models import PriceBar

c = sqlite3.connect('data/stockscout.db'); c.row_factory = sqlite3.Row
as_of = date.fromisoformat(c.execute('SELECT MAX(date) d FROM raw_prices').fetchone()['d'])
calendar = [date.fromisoformat(r[0]) for r in c.execute('SELECT DISTINCT date FROM raw_prices')]
bars = [PriceBar(ticker=r['ticker'], date=date.fromisoformat(r['date']), close=r['close'])
        for r in c.execute(\"SELECT * FROM raw_prices WHERE ticker='005930.KS'\")]
s = compute_price_stats(bars, as_of, [1,3,5], calendar, 0.8)
print('삼성전자  현재가', f'{s.price_current:,.0f}')
print('  5년평균', f'{s.avg_price[5]:,.0f}', ' 평균대비', f'{s.drop_vs_avg[5]*100:.1f}%')
print('  5년고점', f'{s.high_5y:,.0f}', ' 고점대비', f'{s.drop_vs_high*100:.1f}%')
"
```

Expected: 현재가 269,000 / 5년평균 91,988 / 평균대비 +192.4% / 고점대비 -25.8%
(5년 고점은 2026-06-18의 362,500이다)

**평균 대비가 양수인 것이 정상이다.** 강세장이라 5년 평균이 현재가보다 한참 낮다. 이 도구가 찾는 종목은 반대 방향이다.

- [ ] **Step 7: 전체 스위트를 돌린다**

Run: `C:/Users/USER/DEV/주식투자/.venv/Scripts/python.exe -m pytest -q`
Expected: `182 passed, 2 skipped`

- [ ] **Step 8: 커밋한다**

```bash
git add src/stockscout/analysis/ tests/test_analysis_valuation.py
git commit -m "feat: 평균·고점 대비 하락률 계산"
```

---

## Task 3: 가격 기반 하드필터

설계서 §8.1 중 재무제표 없이 판정 가능한 두 가지.

**Files:**
- Create: `src/stockscout/analysis/filters.py`
- Test: `tests/test_analysis_filters.py`

- [ ] **Step 1: 실패하는 테스트를 작성한다**

`tests/test_analysis_filters.py`:

```python
from datetime import date, timedelta

import pytest

from stockscout.analysis.filters import (
    FINANCIAL_SECTOR,
    INSUFFICIENT_HISTORY,
    apply_price_filters,
)
from stockscout.models import PriceBar, Security


def sec(ticker="005930.KS", is_financial=False):
    return Security(ticker=ticker, market="KR", name="테스트",
                    exchange="KOSPI", currency="KRW", is_financial=is_financial)


def bars_from(first: date, last: date):
    """첫날과 마지막날만 있으면 이력 판정에는 충분하다."""
    return [
        PriceBar(ticker="X", date=first, close=100.0),
        PriceBar(ticker="X", date=last, close=100.0),
    ]


def test_financial_sector_is_excluded():
    outcome = apply_price_filters(
        sec(is_financial=True),
        bars_from(date(2021, 1, 1), date(2026, 9, 10)),
        as_of=date(2026, 9, 10), min_history_years=5,
    )

    assert outcome.passed is False
    assert FINANCIAL_SECTOR in outcome.failures


def test_non_financial_with_full_history_passes():
    outcome = apply_price_filters(
        sec(),
        bars_from(date(2021, 9, 10), date(2026, 9, 10)),
        as_of=date(2026, 9, 10), min_history_years=5,
    )

    assert outcome.passed is True
    assert outcome.failures == []


def test_exactly_five_calendar_years_passes():
    """2021-09-10 ~ 2026-09-10은 정확히 5달력년이다.

    (MAX-MIN).days / 365.25 로 재면 1826/365.25 = 4.9993 이 되어
    이 종목이 탈락한다. 실제로 그 방식을 쓰면 42종목 전부가 탈락했다.
    """
    outcome = apply_price_filters(
        sec(),
        bars_from(date(2021, 9, 10), date(2026, 9, 10)),
        as_of=date(2026, 9, 10), min_history_years=5,
    )

    assert INSUFFICIENT_HISTORY not in outcome.failures


def test_one_day_short_of_five_years_fails():
    outcome = apply_price_filters(
        sec(),
        bars_from(date(2021, 9, 11), date(2026, 9, 10)),
        as_of=date(2026, 9, 10), min_history_years=5,
    )

    assert INSUFFICIENT_HISTORY in outcome.failures


def test_recent_listing_fails_history():
    """LG에너지솔루션은 2022-01-27 상장이다."""
    outcome = apply_price_filters(
        sec("373220.KS"),
        bars_from(date(2022, 1, 27), date(2026, 9, 10)),
        as_of=date(2026, 9, 10), min_history_years=5,
    )

    assert INSUFFICIENT_HISTORY in outcome.failures


def test_no_bars_fails_history():
    outcome = apply_price_filters(
        sec(), [], as_of=date(2026, 9, 10), min_history_years=5,
    )

    assert INSUFFICIENT_HISTORY in outcome.failures


def test_all_failures_are_collected_not_short_circuited():
    """사유가 하나만 나오면 고쳐도 또 다른 사유로 걸린다."""
    outcome = apply_price_filters(
        sec(is_financial=True),
        bars_from(date(2025, 1, 1), date(2026, 9, 10)),
        as_of=date(2026, 9, 10), min_history_years=5,
    )

    assert set(outcome.failures) == {FINANCIAL_SECTOR, INSUFFICIENT_HISTORY}


def test_failures_are_stable_in_order():
    """화면과 스냅샷 비교를 위해 순서가 흔들리면 안 된다."""
    a = apply_price_filters(sec(is_financial=True), [],
                            as_of=date(2026, 9, 10), min_history_years=5)
    b = apply_price_filters(sec(is_financial=True), [],
                            as_of=date(2026, 9, 10), min_history_years=5)

    assert a.failures == b.failures
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

Run: `C:/Users/USER/DEV/주식투자/.venv/Scripts/python.exe -m pytest tests/test_analysis_filters.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'stockscout.analysis.filters'`

- [ ] **Step 3: 구현한다**

`src/stockscout/analysis/filters.py`:

```python
"""하드필터. 설계서 §8.1.

걸리면 점수를 계산하지 않고 제외한다. 이 계획은 재무제표 없이 판정
가능한 두 가지만 구현한다. 재무 기반 필터(자본잠식·연속적자·과다부채·
이자미충당·매출급감·필수항목결측)는 계획 C가 더한다.

순수 함수다. 네트워크도 DB도 모른다.
"""

from __future__ import annotations

from datetime import date

from stockscout.analysis.valuation import window_start
from stockscout.models import FilterOutcome, PriceBar, Security

FINANCIAL_SECTOR = "FINANCIAL_SECTOR"
INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"


def apply_price_filters(
    security: Security,
    bars: list[PriceBar],
    as_of: date,
    min_history_years: int,
) -> FilterOutcome:
    """가격·업종 기반 하드필터를 적용한다.

    첫 실패에서 멈추지 않고 모든 사유를 모은다. 화면에서 "왜 제외됐나"를
    보여줄 때 사유가 하나만 있으면 고치고 다시 돌렸을 때 또 다른 사유로
    걸려, 사용자가 같은 과정을 반복하게 된다.
    """
    failures: list[str] = []

    # 은행·보험·증권은 부채비율·유동비율이 구조적으로 달라 제조업과
    # 같은 잣대로 재면 무의미하다. 전용 필터셋은 설계서 §16의 과제다.
    if security.is_financial:
        failures.append(FINANCIAL_SECTOR)

    # 달력 기준으로 잰다. 연분수(days / 365.25)로 재면 정확히 5달력년인
    # 구간이 4.9993년이 되어 전 종목이 탈락한다.
    required_start = window_start(as_of, min_history_years)
    first_bar = min((b.date for b in bars), default=None)
    if first_bar is None or first_bar > required_start:
        failures.append(INSUFFICIENT_HISTORY)

    return FilterOutcome(failures=failures)
```

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

Run: `C:/Users/USER/DEV/주식투자/.venv/Scripts/python.exe -m pytest tests/test_analysis_filters.py -v`
Expected: PASS — `8 passed`

- [ ] **Step 5: 실제 데이터로 제외 종목을 확인한다**

```bash
C:/Users/USER/DEV/주식투자/.venv/Scripts/python.exe -c "
import sqlite3
from datetime import date
from stockscout.analysis.filters import apply_price_filters
from stockscout.models import PriceBar, Security

c = sqlite3.connect('data/stockscout.db'); c.row_factory = sqlite3.Row
as_of = date.fromisoformat(c.execute('SELECT MAX(date) d FROM raw_prices').fetchone()['d'])
excluded = []
for r in c.execute('SELECT * FROM securities'):
    s = Security(ticker=r['ticker'], market=r['market'], name=r['name'],
                 currency=r['currency'], is_financial=bool(r['is_financial']))
    bars = [PriceBar(ticker=r['ticker'], date=date.fromisoformat(x[0]), close=x[1])
            for x in c.execute('SELECT date, close FROM raw_prices WHERE ticker=?', (r['ticker'],))]
    o = apply_price_filters(s, bars, as_of, 5)
    if not o.passed:
        excluded.append((r['name'], o.failures))
print('제외', len(excluded), '종목 / 통과', 42 - len(excluded), '종목')
for n, f in excluded:
    print(' ', n, f)
"
```

Expected: 제외 8종목 / 통과 34종목. 금융 7종목(KB금융·신한지주·하나금융지주·우리금융지주·삼성생명·삼성화재·기업은행) + LG에너지솔루션(`INSUFFICIENT_HISTORY`).

- [ ] **Step 6: 전체 스위트를 돌린다**

Run: `C:/Users/USER/DEV/주식투자/.venv/Scripts/python.exe -m pytest -q`
Expected: `190 passed, 2 skipped`

- [ ] **Step 7: 커밋한다**

```bash
git add src/stockscout/analysis/filters.py tests/test_analysis_filters.py
git commit -m "feat: 가격 기반 하드필터 (금융업·이력부족)"
```

---

## Task 4: 밸류에이션 지표

설계서 §9.2. 발행주식수가 없어 당분간 전부 `None`이지만, 계획 A가 DART 추가 엔드포인트를 붙이면 바로 살아난다.

**Files:**
- Modify: `src/stockscout/analysis/valuation.py`
- Test: `tests/test_analysis_valuation.py`

- [ ] **Step 1: 실패하는 테스트를 추가한다**

`tests/test_analysis_valuation.py` 맨 아래에 추가:

```python
# --- 밸류에이션 (설계서 §9.2) -----------------------------------------

from stockscout.analysis.valuation import compute_valuation   # noqa: E402
from stockscout.models import Financials                      # noqa: E402


def fin(**overrides) -> Financials:
    base = dict(ticker="X", fiscal_year=2025, currency="KRW",
                revenue=1000.0, operating_income=100.0, total_equity=500.0,
                total_liabilities=200.0, net_income=50.0,
                shares_outstanding=100.0)
    base.update(overrides)
    return Financials(**base)


def test_valuation_computes_market_cap_per_pbr():
    v = compute_valuation(price=60.0, latest=fin())

    assert v.market_cap == pytest.approx(6000.0)   # 60 x 100
    assert v.per == pytest.approx(120.0)           # 60 / (50/100)
    assert v.pbr == pytest.approx(12.0)            # 60 / (500/100)


def test_valuation_is_all_none_without_shares():
    v = compute_valuation(price=60.0, latest=fin(shares_outstanding=None))

    assert (v.market_cap, v.per, v.pbr) == (None, None, None)


def test_valuation_is_all_none_without_financials():
    v = compute_valuation(price=60.0, latest=None)

    assert (v.market_cap, v.per, v.pbr) == (None, None, None)


def test_per_is_none_when_eps_nonpositive():
    """적자 기업의 PER은 의미가 없다 (설계서 §9.2)."""
    assert compute_valuation(price=60.0, latest=fin(net_income=-10.0)).per is None
    assert compute_valuation(price=60.0, latest=fin(net_income=0.0)).per is None


def test_pbr_is_none_when_bps_nonpositive():
    assert compute_valuation(price=60.0, latest=fin(total_equity=-10.0)).pbr is None


def test_market_cap_survives_when_per_is_none():
    """지표는 각각 독립이다. 하나가 없다고 나머지를 지우지 않는다."""
    v = compute_valuation(price=60.0, latest=fin(net_income=-10.0))

    assert v.market_cap == pytest.approx(6000.0)
    assert v.per is None
    assert v.pbr == pytest.approx(12.0)


def test_zero_shares_does_not_divide_by_zero():
    v = compute_valuation(price=60.0, latest=fin(shares_outstanding=0.0))

    assert (v.market_cap, v.per, v.pbr) == (None, None, None)
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

Run: `C:/Users/USER/DEV/주식투자/.venv/Scripts/python.exe -m pytest tests/test_analysis_valuation.py -v -k valuation`
Expected: FAIL — `ImportError: cannot import name 'compute_valuation'`

- [ ] **Step 3: 구현한다**

`src/stockscout/analysis/valuation.py`의 import에 추가:

```python
from stockscout.models import Financials, PriceBar, PriceStats, Valuation
```

파일 맨 아래에 추가:

```python
def compute_valuation(price: float, latest: Financials | None) -> Valuation:
    """시가총액·PER·PBR (설계서 §9.2).

    화면 표시와 정렬에만 쓴다. 점수에는 넣지 않는다 — 설계서 §8.2가
    밸류에이션을 스코어링에서 뺐기 때문이다.

    발행주식수가 없으면 셋 다 None이다. DART의 단일회사 전체 재무제표
    API(fnlttSinglAcntAll)는 발행주식수를 주지 않고 「주식의 총수 현황」
    이라는 별도 공시 항목에 있으므로, 그것을 수집하기 전까지는 항상
    None이 된다.

    세 지표는 서로 독립이다. 적자 기업이라 PER이 없어도 시가총액과
    PBR은 남긴다.
    """
    if latest is None or not latest.shares_outstanding:
        return Valuation()

    shares = latest.shares_outstanding
    if shares <= 0:
        return Valuation()

    market_cap = price * shares

    per = None
    if latest.net_income is not None:
        eps = latest.net_income / shares
        if eps > 0:
            per = price / eps

    pbr = None
    if latest.total_equity is not None:
        bps = latest.total_equity / shares
        if bps > 0:
            pbr = price / bps

    return Valuation(market_cap=market_cap, per=per, pbr=pbr)
```

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

Run: `C:/Users/USER/DEV/주식투자/.venv/Scripts/python.exe -m pytest tests/test_analysis_valuation.py -v`
Expected: PASS — `19 passed`

- [ ] **Step 5: 전체 스위트를 돌린다**

Run: `C:/Users/USER/DEV/주식투자/.venv/Scripts/python.exe -m pytest -q`
Expected: `197 passed, 2 skipped`

- [ ] **Step 6: 커밋한다**

```bash
git add src/stockscout/analysis/valuation.py tests/test_analysis_valuation.py
git commit -m "feat: 시가총액·PER·PBR 계산"
```

---

## Task 5: 스냅샷 저장 (원자적)

설계서 §6·§13. 계획 A의 최종 리뷰가 지적한 I2의 해법이다.

**Files:**
- Modify: `src/stockscout/store/repository.py`
- Test: `tests/test_repository_snapshots.py`

- [ ] **Step 1: 실패하는 테스트를 작성한다**

`tests/test_repository_snapshots.py`:

```python
import json
import math

import pytest

from stockscout.models import (
    AnalysisResult,
    FilterOutcome,
    PriceStats,
    Snapshot,
    Valuation,
)
from stockscout.store.db import connect, init_db
from stockscout.store.repository import Repository, json_safe


@pytest.fixture
def repo(tmp_path):
    with connect(tmp_path / "t.db") as conn:
        init_db(conn)
        yield Repository(conn)


def snap(note="테스트"):
    return Snapshot(run_at="2026-09-11T02:00:00+00:00", config_hash="h1",
                    config_json='{"signal": {"min_score": 70}}', note=note)


def result(ticker="005930.KS", failures=None, drop5=-0.42):
    return AnalysisResult(
        ticker=ticker,
        filters=FilterOutcome(failures=failures or []),
        stats=PriceStats(
            price_current=41300.0,
            avg_price={1: 55000.0, 3: 64200.0, 5: 71400.0},
            high_5y=88800.0,
            drop_vs_avg={1: -0.249, 3: -0.357, 5: drop5},
            drop_vs_high=-0.535,
        ),
        valuation=Valuation(market_cap=2.5e14, per=9.8, pbr=1.2),
    )


# --- json_safe --------------------------------------------------------


def test_json_safe_converts_inf_to_none():
    """json.dumps(float('inf'))는 Infinity라는 유효하지 않은 JSON을 뱉는다.

    계획 C의 interest_coverage가 무차입 기업에 inf를 반환하므로,
    그 값이 score_detail에 들어가면 웹 화면의 JSON.parse가 깨진다.
    """
    cleaned = json_safe({"coverage": math.inf, "ratio": 1.5, "none": None})

    assert cleaned == {"coverage": None, "ratio": 1.5, "none": None}
    json.loads(json.dumps(cleaned))   # 예외가 나면 실패


def test_json_safe_handles_nan_and_negative_inf():
    cleaned = json_safe({"a": math.nan, "b": -math.inf})

    assert cleaned == {"a": None, "b": None}


def test_json_safe_recurses_into_nested_structures():
    cleaned = json_safe({"axes": [{"v": math.inf}, {"v": 2.0}]})

    assert cleaned == {"axes": [{"v": None}, {"v": 2.0}]}


def test_json_safe_leaves_ordinary_values_alone():
    payload = {"s": "매출액", "i": 3, "b": True, "l": [1, 2]}

    assert json_safe(payload) == payload


# --- 저장과 조회 ------------------------------------------------------


def test_save_snapshot_returns_id_and_persists_results(repo):
    sid = repo.save_snapshot(snap(), [result(), result("000660.KS")])

    assert isinstance(sid, int)
    rows = repo.get_analysis_results(sid)
    assert {r["ticker"] for r in rows} == {"005930.KS", "000660.KS"}


def test_saved_result_round_trips_every_price_column(repo):
    sid = repo.save_snapshot(snap(), [result()])

    row = repo.get_analysis_results(sid)[0]

    assert row["price_current"] == 41300.0
    assert row["avg_price_1y"] == 55000.0
    assert row["avg_price_3y"] == 64200.0
    assert row["avg_price_5y"] == 71400.0
    assert row["high_5y"] == 88800.0
    assert row["drop_vs_avg_5y"] == pytest.approx(-0.42)
    assert row["drop_vs_high"] == pytest.approx(-0.535)
    assert row["market_cap"] == pytest.approx(2.5e14)
    assert row["per"] == pytest.approx(9.8)
    assert row["pbr"] == pytest.approx(1.2)
    assert row["passed_filters"] == 1


def test_filter_failures_round_trip_as_json(repo):
    sid = repo.save_snapshot(snap(), [result(failures=["FINANCIAL_SECTOR"])])

    row = repo.get_analysis_results(sid)[0]

    assert row["passed_filters"] == 0
    assert json.loads(row["filter_failures"]) == ["FINANCIAL_SECTOR"]


def test_missing_window_is_stored_as_null(repo):
    r = result()
    r.stats.avg_price[3] = None
    r.stats.drop_vs_avg[3] = None

    sid = repo.save_snapshot(snap(), [r])
    row = repo.get_analysis_results(sid)[0]

    assert row["avg_price_3y"] is None
    assert row["drop_vs_avg_3y"] is None


def test_get_latest_snapshot_returns_most_recent(repo):
    repo.save_snapshot(snap("첫번째"), [result()])
    second = repo.save_snapshot(snap("두번째"), [result()])

    latest = repo.get_latest_snapshot()

    assert latest is not None
    assert latest.snapshot_id == second
    assert latest.note == "두번째"


def test_get_latest_snapshot_is_none_when_empty(repo):
    assert repo.get_latest_snapshot() is None


# --- 원자성 (설계서 §13) ----------------------------------------------


def test_failed_snapshot_leaves_no_partial_rows(repo):
    """설계서 §13: 배치 중 예외 → 스냅샷을 커밋하지 않는다."""
    good = result("005930.KS")
    broken = result("000660.KS")
    broken.ticker = None       # NOT NULL 위반을 유발한다

    with pytest.raises(Exception):
        repo.save_snapshot(snap(), [good, broken])

    assert repo.get_latest_snapshot() is None
    assert repo.conn.execute("SELECT COUNT(*) FROM analysis_results").fetchone()[0] == 0


def test_previous_snapshot_survives_a_failed_run(repo):
    """직전 스냅샷이 유지되므로 화면은 계속 동작한다."""
    first = repo.save_snapshot(snap("성공"), [result()])

    broken = result()
    broken.ticker = None
    with pytest.raises(Exception):
        repo.save_snapshot(snap("실패"), [broken])

    latest = repo.get_latest_snapshot()
    assert latest.snapshot_id == first
    assert latest.note == "성공"


def test_empty_results_still_creates_a_snapshot(repo):
    """분석 대상이 0종목이어도 실행 기록은 남는다."""
    sid = repo.save_snapshot(snap(), [])

    assert repo.get_latest_snapshot().snapshot_id == sid
    assert repo.get_analysis_results(sid) == []
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

Run: `C:/Users/USER/DEV/주식투자/.venv/Scripts/python.exe -m pytest tests/test_repository_snapshots.py -v`
Expected: FAIL — `ImportError: cannot import name 'json_safe'`

- [ ] **Step 3: 구현한다**

`src/stockscout/store/repository.py`의 import를 다음으로 교체한다:

```python
import json
import math
import sqlite3
from datetime import date, datetime, timezone

from stockscout.models import (
    AnalysisResult,
    Financials,
    PriceBar,
    RawStatement,
    Security,
    Snapshot,
)
```

`_now()` 아래에 모듈 함수를 추가한다:

```python
def json_safe(value):
    """JSON으로 직렬화할 수 없는 float를 None으로 바꾼다.

    json.dumps(float('inf'))는 표준 JSON이 아닌 Infinity 토큰을 내보내고
    JSON.parse가 이를 거부한다. 계획 C의 interest_coverage가 무차입
    기업에 대해 inf를 반환하므로(설계서 §8.2의 만점 조건), 그 값이
    score_detail에 들어가면 웹 화면이 통째로 깨진다.

    직렬화 경계에서 한 번만 처리한다. 계산 쪽은 inf를 그대로 쓴다 —
    "이자 부담 없음"과 "데이터 없음"은 다른 상태이기 때문이다.
    """
    if isinstance(value, float):
        return None if math.isinf(value) or math.isnan(value) else value
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value
```

`Repository` 클래스 안, `record_error` 앞에 추가한다:

```python
    # --- 스냅샷 -------------------------------------------------------

    def save_snapshot(self, snapshot: Snapshot, results: list[AnalysisResult]) -> int:
        """스냅샷과 결과를 한 트랜잭션으로 쓴다. 실패하면 아무것도 남지 않는다.

        설계서 §13이 "배치 중 예외 → 스냅샷을 커밋하지 않는다. 직전
        스냅샷이 유지되므로 화면은 계속 동작한다"를 요구한다.

        범용 transaction() 컨텍스트 매니저를 만들지 않는 이유: SQLite는
        중첩 트랜잭션을 지원하지 않으므로, 안쪽 메서드가 commit()을 부르는
        한 바깥 트랜잭션까지 함께 커밋돼 원자성이 깨진다. 스냅샷 쓰기는
        개념적으로 한 번의 연산이므로 전용 메서드로 둔다.
        """
        try:
            cur = self.conn.execute(
                "INSERT INTO snapshots (run_at, config_hash, config_json, note) "
                "VALUES (?, ?, ?, ?)",
                (snapshot.run_at, snapshot.config_hash, snapshot.config_json,
                 snapshot.note),
            )
            snapshot_id = cur.lastrowid

            self.conn.executemany(
                """
                INSERT INTO analysis_results (
                    snapshot_id, ticker, passed_filters, filter_failures,
                    score_total, score_profit, score_growth, score_stability,
                    score_cashflow, score_detail,
                    price_current, avg_price_1y, avg_price_3y, avg_price_5y,
                    high_5y, drop_vs_avg_1y, drop_vs_avg_3y, drop_vs_avg_5y,
                    drop_vs_high, market_cap, per, pbr,
                    signal_grade, signal_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?, ?, ?)
                """,
                [self._result_row(snapshot_id, r) for r in results],
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return snapshot_id

    @staticmethod
    def _result_row(snapshot_id: int, r: AnalysisResult) -> tuple:
        s = r.stats
        return (
            snapshot_id, r.ticker, int(r.passed_filters),
            json.dumps(r.filters.failures, ensure_ascii=False),
            r.score_total, r.score_profit, r.score_growth, r.score_stability,
            r.score_cashflow, r.score_detail,
            s.price_current, s.avg_price.get(1), s.avg_price.get(3),
            s.avg_price.get(5), s.high_5y,
            s.drop_vs_avg.get(1), s.drop_vs_avg.get(3), s.drop_vs_avg.get(5),
            s.drop_vs_high,
            r.valuation.market_cap, r.valuation.per, r.valuation.pbr,
            r.signal_grade, r.signal_reason,
        )

    def get_latest_snapshot(self) -> Snapshot | None:
        row = self.conn.execute(
            "SELECT * FROM snapshots ORDER BY snapshot_id DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        return Snapshot(
            snapshot_id=row["snapshot_id"], run_at=row["run_at"],
            config_hash=row["config_hash"], config_json=row["config_json"],
            note=row["note"],
        )

    def get_analysis_results(self, snapshot_id: int) -> list[dict]:
        """스냅샷의 결과를 원본 행 그대로 반환한다.

        AnalysisResult로 되돌리지 않는 이유: 읽는 쪽(요약 출력, 나중의
        웹 API)이 컬럼을 그대로 쓰는 편이 단순하고, 창 매핑을 다시
        조립할 이유가 없다.
        """
        rows = self.conn.execute(
            "SELECT * FROM analysis_results WHERE snapshot_id = ? ORDER BY ticker",
            (snapshot_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def trading_dates(self, start: date, end: date) -> list[date]:
        """수집된 전 종목의 날짜 합집합. 거래소 달력 대신 쓴다.

        별도 거래일 달력을 두지 않는 이유: 이미 수집한 데이터가 곧
        관측된 거래일이고, 휴장일 정의를 따로 관리하면 소스와 어긋난다.
        """
        rows = self.conn.execute(
            "SELECT DISTINCT date FROM raw_prices WHERE date BETWEEN ? AND ? "
            "ORDER BY date",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        return [date.fromisoformat(r["date"]) for r in rows]
```

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

Run: `C:/Users/USER/DEV/주식투자/.venv/Scripts/python.exe -m pytest tests/test_repository_snapshots.py -v`
Expected: PASS — `13 passed`

- [ ] **Step 5: 전체 스위트를 돌린다**

Run: `C:/Users/USER/DEV/주식투자/.venv/Scripts/python.exe -m pytest -q`
Expected: `210 passed, 2 skipped`

- [ ] **Step 6: 커밋한다**

```bash
git add src/stockscout/store/repository.py tests/test_repository_snapshots.py
git commit -m "feat: 원자적 스냅샷 저장과 JSON 안전 직렬화"
```

---

## Task 6: 분석 파이프라인

**Files:**
- Create: `src/stockscout/pipeline/analyze.py`
- Test: `tests/test_pipeline_analyze.py`

- [ ] **Step 1: 실패하는 테스트를 작성한다**

`tests/test_pipeline_analyze.py`:

```python
import json
from datetime import date, timedelta

import pytest

from stockscout.config import Config
from stockscout.models import PriceBar, Security
from stockscout.pipeline.analyze import AnalyzeSummary, analyze_market
from stockscout.store.db import connect, init_db
from stockscout.store.repository import Repository


@pytest.fixture
def repo(tmp_path):
    with connect(tmp_path / "t.db") as conn:
        init_db(conn)
        yield Repository(conn)


def cfg(**overrides) -> Config:
    raw = {"lookback": {"price_years": [1, 3, 5], "primary_window": 5,
                        "min_data_coverage": 0.8},
           "hard_filters": {"min_history_years": 5}}
    raw.update(overrides)
    return Config.model_validate(raw)


def seed(repo, ticker="005930.KS", is_financial=False,
         first=date(2021, 9, 10), last=date(2026, 9, 10), close=100.0):
    repo.upsert_security(Security(
        ticker=ticker, market="KR", name=ticker, exchange="KOSPI",
        currency="KRW", is_financial=is_financial))
    d, bars = first, []
    while d <= last:
        bars.append(PriceBar(ticker=ticker, date=d, close=close))
        d += timedelta(days=1)
    repo.save_price_bars(bars, source="pykrx")


def test_analyze_creates_snapshot_with_one_row_per_security(repo):
    seed(repo, "005930.KS")
    seed(repo, "000660.KS")

    summary = analyze_market(repo, market="KR", config=cfg())

    rows = repo.get_analysis_results(summary.snapshot_id)
    assert {r["ticker"] for r in rows} == {"005930.KS", "000660.KS"}
    assert summary.analyzed == 2


def test_analyze_records_filter_failures(repo):
    seed(repo, "105560.KS", is_financial=True)

    summary = analyze_market(repo, market="KR", config=cfg())

    row = repo.get_analysis_results(summary.snapshot_id)[0]
    assert row["passed_filters"] == 0
    assert "FINANCIAL_SECTOR" in json.loads(row["filter_failures"])
    assert summary.excluded == 1
    assert summary.passed == 0


def test_analyze_skips_securities_without_prices(repo):
    repo.upsert_security(Security(ticker="EMPTY.KS", market="KR",
                                  name="빈종목", currency="KRW"))

    summary = analyze_market(repo, market="KR", config=cfg())

    assert summary.analyzed == 0
    assert summary.skipped == 1


def test_analyze_stores_config_hash_and_json(repo):
    seed(repo)

    summary = analyze_market(repo, market="KR", config=cfg())

    snap = repo.get_latest_snapshot()
    assert snap.config_hash
    assert json.loads(snap.config_json)["signal"]["min_score"] == 70


def test_same_config_produces_same_hash(repo):
    seed(repo)

    a = analyze_market(repo, market="KR", config=cfg())
    b = analyze_market(repo, market="KR", config=cfg())

    rows = repo.conn.execute("SELECT config_hash FROM snapshots").fetchall()
    assert rows[0]["config_hash"] == rows[1]["config_hash"]
    assert a.snapshot_id != b.snapshot_id


def test_different_threshold_produces_different_hash(repo):
    seed(repo)

    analyze_market(repo, market="KR", config=cfg())
    analyze_market(repo, market="KR",
                   config=cfg(signal={"drop_vs_avg_threshold": -0.35}))

    hashes = {r["config_hash"] for r in
              repo.conn.execute("SELECT config_hash FROM snapshots")}
    assert len(hashes) == 2


def test_analyze_note_is_stored(repo):
    seed(repo)

    analyze_market(repo, market="KR", config=cfg(), note="임계치 -35% 실험")

    assert repo.get_latest_snapshot().note == "임계치 -35% 실험"


def test_unknown_market_raises(repo):
    with pytest.raises(ValueError, match="XX"):
        analyze_market(repo, market="XX", config=cfg())


def test_analyze_isolates_per_security_failures(repo, monkeypatch):
    """한 종목의 계산 실패가 전체 분석을 멈추면 안 된다.

    실패를 주입해서 검증한다. 데이터를 이상하게 만드는 방식(음수 종가
    등)으로는 검증되지 않는다 — compute_price_stats가 음수 종가에
    예외를 던지지 않아서, 격리를 테스트한다면서 실제로는 아무것도
    실패시키지 않는 테스트가 된다.
    """
    import stockscout.pipeline.analyze as analyze_mod

    seed(repo, "AAA.KS")
    seed(repo, "BBB.KS")

    real = analyze_mod.compute_price_stats

    def explode_for_bbb(bars, **kwargs):
        if bars and bars[0].ticker == "BBB.KS":
            raise RuntimeError("계산 폭발")
        return real(bars, **kwargs)

    monkeypatch.setattr(analyze_mod, "compute_price_stats", explode_for_bbb)

    summary = analyze_market(repo, market="KR", config=cfg())

    assert summary.failed == 1
    assert summary.analyzed == 1
    tickers = {r["ticker"] for r in repo.get_analysis_results(summary.snapshot_id)}
    assert tickers == {"AAA.KS"}
    assert repo.recent_errors()[0]["ticker"] == "BBB.KS"


def test_summary_render_is_printable_on_cp949_console(repo):
    seed(repo)

    summary = analyze_market(repo, market="KR", config=cfg())

    summary.render().encode("cp949")   # 예외가 나면 실패


def test_summary_render_reports_counts(repo):
    seed(repo, "005930.KS")
    seed(repo, "105560.KS", is_financial=True)

    text = analyze_market(repo, market="KR", config=cfg()).render()

    assert "2" in text and "1" in text
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

Run: `C:/Users/USER/DEV/주식투자/.venv/Scripts/python.exe -m pytest tests/test_pipeline_analyze.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'stockscout.pipeline.analyze'`

- [ ] **Step 3: 구현한다**

`src/stockscout/pipeline/analyze.py`:

```python
"""분석 오케스트레이션. 저장 계층에서 읽어 순수 함수에 넘기고 스냅샷을 쓴다.

네트워크를 쓰지 않는다. 기준을 바꿔가며 반복 실행하는 것이 이 명령의
주 사용 패턴이므로(설계서 §12), 몇 초에 끝나야 한다.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone

from pydantic import BaseModel

from stockscout.analysis.filters import apply_price_filters
from stockscout.analysis.valuation import (
    compute_price_stats,
    compute_valuation,
    window_start,
)
from stockscout.config import Config
from stockscout.models import AnalysisResult, Snapshot
from stockscout.store.repository import Repository

SUPPORTED_MARKETS = ("KR", "US")


class AnalyzeSummary(BaseModel):
    snapshot_id: int | None = None
    analyzed: int = 0
    passed: int = 0
    excluded: int = 0
    skipped: int = 0
    failed: int = 0

    def render(self) -> str:
        lines = [
            f"{self.analyzed}종목 분석 · 통과 {self.passed} · 제외 {self.excluded}",
        ]
        if self.skipped:
            lines.append(f"주가 없어 건너뜀 {self.skipped}종목")
        if self.failed:
            lines.append(f"계산 실패 {self.failed}종목 (원인은 collect_errors 참고)")
        if self.snapshot_id is not None:
            lines.append(f"스냅샷 #{self.snapshot_id} 저장")
        return "\n".join(lines)


def config_hash(config: Config) -> str:
    """설정의 지문. 같은 설정이면 같은 값이어야 한다.

    sort_keys로 키 순서를 고정한다. 그러지 않으면 같은 설정이 실행마다
    다른 해시를 내놓아 "어느 기준으로 돌린 결과인가"를 추적할 수 없다.
    """
    payload = json.dumps(config.model_dump(mode="json"), sort_keys=True,
                         ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def analyze_market(
    repo: Repository,
    market: str,
    config: Config,
    note: str | None = None,
) -> AnalyzeSummary:
    """한 시장을 분석해 스냅샷 하나를 남긴다."""
    if market not in SUPPORTED_MARKETS:
        raise ValueError(f"지원하지 않는 시장입니다: {market}")

    summary = AnalyzeSummary()
    windows = config.lookback.price_years
    as_of = repo.latest_price_date_any() or date.today()
    calendar = repo.trading_dates(
        _earliest_window_start(as_of, windows), as_of
    )

    results: list[AnalysisResult] = []
    for security in repo.list_securities(market=market):
        bars = repo.get_price_bars(
            security.ticker,
            start=_earliest_window_start(as_of, windows),
            end=as_of,
        )
        if not bars:
            summary.skipped += 1
            continue

        try:
            stats = compute_price_stats(
                bars, as_of=as_of, windows=windows, calendar=calendar,
                min_coverage=config.lookback.min_data_coverage,
            )
            outcome = apply_price_filters(
                security, bars, as_of=as_of,
                min_history_years=config.hard_filters.min_history_years,
            )
            financials = repo.get_financials(security.ticker)
            valuation = compute_valuation(
                stats.price_current, financials[-1] if financials else None
            )
        except Exception as exc:
            repo.record_error(security.ticker, "analyze",
                              f"{type(exc).__name__}: {exc}")
            summary.failed += 1
            continue

        results.append(AnalysisResult(
            ticker=security.ticker, filters=outcome, stats=stats,
            valuation=valuation,
        ))
        summary.analyzed += 1
        if outcome.passed:
            summary.passed += 1
        else:
            summary.excluded += 1

    snapshot = Snapshot(
        run_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        config_hash=config_hash(config),
        config_json=json.dumps(config.model_dump(mode="json"),
                               sort_keys=True, ensure_ascii=False),
        note=note,
    )
    summary.snapshot_id = repo.save_snapshot(snapshot, results)
    return summary


def _earliest_window_start(as_of: date, windows: list[int]) -> date:
    """가장 긴 창의 시작일. 주가는 반드시 구간을 지정해 읽는다.

    Repository에 삭제 메서드가 없어 raw_prices는 append/upsert만 된다.
    price_years를 줄이면 옛 봉이 남아 있으므로, 무인자로 읽으면 설정을
    바꿔도 옛 데이터가 평균에 섞인다.

    HIGH_WINDOW_YEARS를 함께 고려하는 이유: 고점 창은 설계서 §9.1이
    5년으로 못박은 값이라 price_years와 무관하다. price_years를
    [1, 3]으로 좁히면 조회 구간이 3년이 되어, 3년 고점이 "5년 고점"으로
    조용히 보고되고 그 값이 계획 C의 고점 대비 등급 판정에 들어간다.
    """
    return window_start(as_of, max(max(windows), HIGH_WINDOW_YEARS))
```

- [ ] **Step 4: `latest_price_date_any`를 Repository에 추가한다**

`src/stockscout/store/repository.py`의 `trading_dates` 앞에 추가한다:

```python
    def latest_price_date_any(self) -> date | None:
        """전 종목 통틀어 가장 최근 거래일. 분석 기준일이 된다."""
        row = self.conn.execute("SELECT MAX(date) AS d FROM raw_prices").fetchone()
        return date.fromisoformat(row["d"]) if row and row["d"] else None
```

- [ ] **Step 5: 테스트가 통과하는지 확인한다**

Run: `C:/Users/USER/DEV/주식투자/.venv/Scripts/python.exe -m pytest tests/test_pipeline_analyze.py -v`
Expected: PASS — `11 passed`

- [ ] **Step 6: 전체 스위트를 돌린다**

Run: `C:/Users/USER/DEV/주식투자/.venv/Scripts/python.exe -m pytest -q`
Expected: `221 passed, 2 skipped`

- [ ] **Step 7: 커밋한다**

```bash
git add src/stockscout/pipeline/analyze.py src/stockscout/store/repository.py tests/test_pipeline_analyze.py
git commit -m "feat: 분석 파이프라인과 스냅샷 생성"
```

---

## Task 7: analyze 명령

**Files:**
- Modify: `src/stockscout/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: 실패하는 테스트를 추가한다**

`tests/test_cli.py` 맨 아래에 추가:

```python
# --- analyze 명령 -----------------------------------------------------


def test_parser_analyze_command():
    args = build_parser().parse_args(["analyze"])

    assert args.command == "analyze"
    assert args.market == "KR"
    assert args.note is None
    assert args.top == 20


def test_parser_analyze_accepts_note_and_top():
    args = build_parser().parse_args(
        ["analyze", "--note", "임계치 실험", "--top", "5"])

    assert args.note == "임계치 실험"
    assert args.top == 5


def test_main_analyze_on_empty_db_returns_zero(tmp_path, capsys):
    from stockscout.cli import main

    code = main(["--db", str(tmp_path / "t.db"), "--config",
                 "config/settings.yaml", "analyze"])

    assert code == 0
    assert "0종목 분석" in capsys.readouterr().out
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

Run: `C:/Users/USER/DEV/주식투자/.venv/Scripts/python.exe -m pytest tests/test_cli.py -v -k analyze`
Expected: FAIL — `argparse` 가 `analyze` 를 모른다 (`SystemExit: 2`)

- [ ] **Step 3: 구현한다**

`src/stockscout/cli.py`의 import에 추가:

```python
from stockscout.pipeline.analyze import analyze_market
```

`build_parser()` 안, `p_norm` 블록 뒤에 추가:

```python
    p_ana = sub.add_parser("analyze", help="저장된 데이터로 하락률을 계산한다")
    p_ana.add_argument("--market", default="KR", choices=["KR", "US"])
    p_ana.add_argument("--note", default=None, help="스냅샷에 남길 메모")
    p_ana.add_argument("--top", type=int, default=20,
                       help="하락률 상위 몇 종목을 출력할지")
```

`main()` 안, `normalize` 분기 뒤에 추가:

```python
        elif args.command == "analyze":
            summary = analyze_market(repo, market=args.market, config=config,
                                     note=args.note)
            print(summary.render())
            if summary.snapshot_id is not None and args.top > 0:
                print()
                print(render_ranking(repo, summary.snapshot_id, args.top,
                                     config.lookback.primary_window))
```

`build_parser()` 앞에 출력 헬퍼를 추가한다:

```python
def render_ranking(repo, snapshot_id: int, top: int, window: int) -> str:
    """하락률 상위 종목표. ASCII 기호만 쓴다 (콘솔이 cp949다)."""
    rows = [r for r in repo.get_analysis_results(snapshot_id)
            if r["passed_filters"] and r[f"drop_vs_avg_{window}y"] is not None]
    rows.sort(key=lambda r: r[f"drop_vs_avg_{window}y"])

    names = {s.ticker: s.name for s in repo.list_securities()}
    lines = [f"{'종목':<18}{'현재가':>12}{f'{window}년평균':>12}"
             f"{'평균대비':>10}{'고점대비':>10}",
             "-" * 62]
    for r in rows[:top]:
        lines.append(
            f"{names.get(r['ticker'], r['ticker']):<18}"
            f"{r['price_current']:>12,.0f}"
            f"{r[f'avg_price_{window}y']:>12,.0f}"
            f"{r[f'drop_vs_avg_{window}y'] * 100:>9.1f}%"
            f"{r['drop_vs_high'] * 100:>9.1f}%"
        )
    if not rows:
        lines.append("(통과 종목 없음)")
    return "\n".join(lines)
```

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

Run: `C:/Users/USER/DEV/주식투자/.venv/Scripts/python.exe -m pytest tests/test_cli.py -v`
Expected: PASS — `30 passed`

- [ ] **Step 5: 전체 스위트를 돌린다**

Run: `C:/Users/USER/DEV/주식투자/.venv/Scripts/python.exe -m pytest -q`
Expected: `224 passed, 2 skipped`

- [ ] **Step 6: 커밋한다**

```bash
git add src/stockscout/cli.py tests/test_cli.py
git commit -m "feat: analyze 명령과 하락률 순위 출력"
```

---

## Task 8: 실제 데이터 관통 검증

**Files:**
- Create: `tests/test_integration_analysis.py`

- [ ] **Step 1: 실제로 실행한다**

```bash
C:/Users/USER/DEV/주식투자/.venv/Scripts/stockscout.exe analyze --market KR --top 10
```

Expected:
```
42종목 분석 · 통과 34 · 제외 8
스냅샷 #1 저장

종목                     현재가      5년평균     평균대비     고점대비
--------------------------------------------------------------
LG생활건강             295,000   499,833    -41.0%   -78.8%
카카오                  34,900    59,029    -40.9%   -73.2%
CJ제일제당             180,500   303,297    -40.5%   -59.1%
LG화학                 280,500   461,877    -39.3%   -67.0%
...
```

한국어 Windows 콘솔에서는 mojibake로 보인다. 정확한 텍스트를 확인하려면 파일로 리다이렉트해 읽는다:

```bash
PYTHONUTF8=1 C:/Users/USER/DEV/주식투자/.venv/Scripts/stockscout.exe analyze --top 10 > out.txt 2>&1
```

**통과 34 · 제외 8이 아니면 멈추고 원인을 확인한다.** 제외 8은 금융 7종목 + LG에너지솔루션(이력부족)이어야 한다.

- [ ] **Step 2: 통합 테스트를 작성한다**

`tests/test_integration_analysis.py`:

```python
"""실제 DB에 대한 검증. `stockscout analyze` 실행 후에 의미가 있다.

DB가 없거나 스냅샷이 없으면 모듈 전체가 skip되므로, 분석 전에는
`pytest tests/`가 그냥 통과한다.
"""

from pathlib import Path

import pytest

from stockscout.store.db import DEFAULT_DB_PATH, connect
from stockscout.store.repository import Repository

pytestmark = pytest.mark.skipif(
    not Path(DEFAULT_DB_PATH).exists(),
    reason="stockscout analyze 를 먼저 실행해야 한다",
)


@pytest.fixture
def repo():
    with connect(DEFAULT_DB_PATH) as conn:
        yield Repository(conn)


@pytest.fixture
def rows(repo):
    snap = repo.get_latest_snapshot()
    if snap is None:
        pytest.skip("스냅샷이 없다")
    return repo.get_analysis_results(snap.snapshot_id)


def test_every_security_has_a_row(repo, rows):
    assert len(rows) == len(repo.list_securities(market="KR"))


def test_financial_sector_is_excluded(rows):
    import json
    financial = [r for r in rows
                 if "FINANCIAL_SECTOR" in json.loads(r["filter_failures"])]

    assert len(financial) == 7, f"금융 7종목이어야 하는데 {len(financial)}개"


def test_recent_listing_is_excluded_for_history(rows):
    import json
    row = next(r for r in rows if r["ticker"] == "373220.KS")

    assert "INSUFFICIENT_HISTORY" in json.loads(row["filter_failures"])


def test_passing_rows_have_all_three_averages(rows):
    """5년 이력이 있는 종목은 1·3·5년 평균이 모두 있어야 한다."""
    for r in rows:
        if not r["passed_filters"]:
            continue
        for w in (1, 3, 5):
            assert r[f"avg_price_{w}y"] is not None, \
                f"{r['ticker']}의 {w}년 평균이 없다"


def test_drop_rates_are_consistent_with_averages(rows):
    """하락률 = 현재가 / 평균 - 1 이 실제로 성립하는가."""
    for r in rows:
        if r["avg_price_5y"] is None:
            continue
        expected = r["price_current"] / r["avg_price_5y"] - 1
        assert r["drop_vs_avg_5y"] == pytest.approx(expected, abs=1e-9)


def test_high_is_never_below_current_or_average(rows):
    for r in rows:
        assert r["high_5y"] >= r["price_current"] - 1e-6
        if r["avg_price_5y"] is not None:
            assert r["high_5y"] >= r["avg_price_5y"] - 1e-6


def test_drop_vs_high_is_never_above_drop_vs_avg(rows):
    """설계서 §9.4: 고점은 정의상 평균보다 크거나 같다."""
    for r in rows:
        if r["avg_price_5y"] is None:
            continue
        assert r["drop_vs_high"] <= r["drop_vs_avg_5y"] + 1e-9


def test_valuation_is_null_without_financials(rows):
    """발행주식수를 수집하기 전까지 셋 다 NULL이다."""
    for r in rows:
        assert r["market_cap"] is None
        assert r["per"] is None
        assert r["pbr"] is None


def test_score_columns_are_empty_until_plan_c(rows):
    for r in rows:
        assert r["score_total"] is None
        assert r["signal_grade"] is None


def test_snapshot_config_json_is_valid_json(repo):
    import json
    snap = repo.get_latest_snapshot()
    if snap is None:
        pytest.skip("스냅샷이 없다")

    json.loads(snap.config_json)
```

- [ ] **Step 3: 통합 테스트를 실행한다**

Run: `C:/Users/USER/DEV/주식투자/.venv/Scripts/python.exe -m pytest tests/test_integration_analysis.py -v`
Expected: PASS — `10 passed`

`test_drop_rates_are_consistent_with_averages`가 실패하면 저장 시 컬럼이 뒤바뀐 것이다. `Repository._result_row`의 순서를 `save_snapshot`의 INSERT 컬럼 목록과 대조한다.

- [ ] **Step 4: 재현성을 확인한다**

같은 설정으로 두 번 돌려 결과가 동일한지 본다. 설계서 §6.1이 약속하는 동작이다.

```bash
PYTHONUTF8=1 C:/Users/USER/DEV/주식투자/.venv/Scripts/python.exe -c "
import sqlite3
from stockscout.cli import main
main(['analyze','--market','KR','--top','0'])
main(['analyze','--market','KR','--top','0'])
c = sqlite3.connect('data/stockscout.db')
ids = [r[0] for r in c.execute('SELECT snapshot_id FROM snapshots ORDER BY snapshot_id DESC LIMIT 2')]
a = c.execute('SELECT ticker, drop_vs_avg_5y FROM analysis_results WHERE snapshot_id=? ORDER BY ticker', (ids[0],)).fetchall()
b = c.execute('SELECT ticker, drop_vs_avg_5y FROM analysis_results WHERE snapshot_id=? ORDER BY ticker', (ids[1],)).fetchall()
print('두 스냅샷 결과 동일:', a == b)
h = [r[0] for r in c.execute('SELECT config_hash FROM snapshots ORDER BY snapshot_id DESC LIMIT 2')]
print('설정 해시 동일:', h[0] == h[1])
"
```

Expected: `두 스냅샷 결과 동일: True` / `설정 해시 동일: True`

**False가 나오면 멈춘다.** 같은 입력에 다른 결과가 나온다는 뜻이고, 계획 C의 점수가 그 위에 쌓이면 원인을 찾기 어려워진다.

- [ ] **Step 5: 전체 스위트를 돌린다**

Run: `C:/Users/USER/DEV/주식투자/.venv/Scripts/python.exe -m pytest -q`
Expected: `234 passed, 2 skipped`

- [ ] **Step 6: 커밋한다**

```bash
git add tests/test_integration_analysis.py
git commit -m "test: 실제 데이터 분석 관통 검증"
```

---

## 완료 기준

- [ ] `stockscout analyze --market KR` 이 42종목을 분석하고 스냅샷을 남긴다
- [ ] 통과 34종목 · 제외 8종목(금융 7 + 이력부족 1)
- [ ] 하락률 상위표가 출력되고, 상위권이 LG생활건강·카카오·CJ제일제당이다
- [ ] 같은 설정으로 두 번 돌리면 결과와 설정 해시가 동일하다
- [ ] 스냅샷 저장 중 예외가 나면 직전 스냅샷이 그대로 유지된다
- [ ] `analysis/`가 `stockscout.models`만 import한다
- [ ] `pytest tests/` 전체 통과, 통합 테스트를 뺀 전부가 DB 없이 실행된다

## 다음 계획

**계획 C — 우량주 판정과 등급** (설계서 §8 전체·§9.3): 재무 기반 하드필터 6종, 4축 100점 스코어링, 결측 환산(축 단위 하한 포함), ★★/★ 등급 판정. **DART 키가 있어야 한다.**

계획 C가 채우는 것은 이 계획이 비워둔 컬럼들이다 — `score_*`·`signal_grade`·`signal_reason`. 파이프라인·스냅샷·필터 구조는 그대로 쓴다.

계획 A의 「계획 B가 알아야 할 것」에 적힌 나머지 항목(§8.3 축 단위 하한, `ACCOUNT_MAP` 검증 시 볼 세 곳, `bands`가 빈 지표 둘)은 계획 C에서 쓴다.
