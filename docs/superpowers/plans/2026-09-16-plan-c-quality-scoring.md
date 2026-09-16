# 계획 C — 우량주 판정과 등급 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 재무제표로 우량주를 가려내고(하드필터 + 4축 100점), 하락 신호와 묶어 ★★/★ 등급을 매긴다.

**Architecture:** 순수 함수 두 개(`apply_financial_filters`, `score_security`)를 `analysis/`에 추가하고, `pipeline/analyze.py`가 가격 통계와 함께 호출해 스냅샷에 저장한다. 새 데이터(발행주식수·감사의견)는 기존 `raw_financials` 테이블에 `source`만 달리해 담는다 — 테이블을 늘리지 않는다. 화면은 `has_scores`가 true가 되는 순간 이미 켜지도록 계획 D가 만들어 두었으므로, 새로 붙이는 것은 §11.2의 점수 분해 막대 하나뿐이다.

**Tech Stack:** Python 3.14 · pydantic 2 · SQLite · FastAPI · pytest

---

## 시작 전 상태

`master` 기준. 계획 A·B·D가 병합돼 있고 테스트 409개가 통과한다.

DB(`data/stockscout.db`)에 이미 들어 있는 것:

| 데이터 | 양 |
|---|---|
| 종목 | 42 (KOSPI, 금융 7종목 포함) |
| 주가 | 51,357봉 (2021-09-10 ~ 2026-09-15) |
| 재무제표 원본 | 196건 (`raw_financials`, source=`dart`) |
| 정규화된 재무 | 196건 (`financials`) |
| 스냅샷 | #5 (42종목 · 통과 34 · 제외 8) |

**아직 비어 있는 것:** `analysis_results`의 `score_*`·`signal_grade`·`market_cap`·`per`·`pbr` 컬럼. 화면은 `has_scores=false`라 점수 슬라이더와 등급 칩이 비활성이다.

### 반드시 먼저 읽을 것

**설계서 §17** (`docs/superpowers/specs/2026-09-10-stock-screener-design.md`). 실제 42종목을 수집하고 나서야 드러난 것들이며, 이 계획의 Task 1·2·3이 전부 거기서 나왔다. 특히 §17.3의 이자비용 우선순위 문제는 "그냥 id 목록에 추가"하면 **LG생활건강을 포함한 13개 회사에서 틀린 값을 쓰게 된다.**

---

## 이 계획의 핵심 결정 세 가지

### 1. 금융비용은 이자비용의 대체값으로 쓰되, 썼다는 사실을 남긴다

`interest_expense`가 196건 중 129건 결측이다. 119건은 `ifrs-full_FinanceCosts`(금융비용)를 갖고 있다.

금융비용은 이자비용에 환차손·파생상품손실 등이 더해진 **상위 개념**이다. 대체값으로 쓰면 이자보상배율이 실제보다 **낮게** 나온다. 품질 필터에서는 보수적인 방향(좋은 회사를 가끔 떨어뜨림)이므로 반대보다 안전하다 — 이 제품은 "살 만한 회사"를 고르는 도구이고, 나쁜 회사를 통과시키는 실수가 좋은 회사를 놓치는 실수보다 비싸다.

그래서 쓴다. 다만 **판정 근거에 드러낸다.** 화면이 계산식을 그대로 보여주는 것과 같은 이유다(§11.2). 사용자가 "이 회사의 이자보상배율 3.2배"를 볼 때, 그것이 진짜 이자비용으로 계산된 것인지 금융비용으로 계산된 것인지 알아야 한다.

### 2. 점수가 낮아 제외되는 것과, 볼 수 없어 제외되는 것을 구분한다

설계서 §8.3의 축 하한 규칙이다. 현금흐름을 못 구한 회사가 80점 만점으로 후하게 채점되면, "볼 수 없다"가 감점이 아니라 **면제**가 된다. 우량주를 가리는 도구에서 방향이 뒤집힌 동작이다.

한 축의 유효만점이 배점의 절반 미만이면 그 축을 0점 처리하되, **분모에는 25점을 그대로 남긴다.** 분모에서도 빼면 0점 처리가 아무 효과가 없다. 이 계획의 Task 8이 그것을 테스트로 고정한다.

### 3. 등급은 점수와 하락이 **둘 다** 있어야 나온다

`quality_pass = passed_filters AND score_total >= min_score`가 먼저다. 아무리 많이 떨어져도 우량주가 아니면 등급이 없다.

LG화학이 이유다. 5년 평균 대비 -41.3%로 현재 1위지만 영업이익률이 11.8%(2021) → 2.6%(2025)로 무너졌고 작년 ROE는 -2.1%다. 41% 하락에는 이유가 있었다. **이 종목에 ★가 붙는지 아닌지가 이 계획이 제대로 됐는지를 가리는 시금석이다.**

---

## 파일 구조

| 파일 | 책임 | 상태 |
|---|---|---|
| `src/stockscout/models.py` | `Financials`에 3개 필드, `ScoreCard`·`MetricScore`·`AxisScore` 추가 | 수정 |
| `src/stockscout/store/schema.sql` | `financials`에 3개 컬럼 | 수정 |
| `src/stockscout/store/db.py` | ALTER TABLE 마이그레이션 | 수정 |
| `src/stockscout/store/repository.py` | 새 컬럼 읽기/쓰기 | 수정 |
| `src/stockscout/normalize/kr_mapping.py` | 우선순위 단계 매핑, 주식수·감사의견 정규화 | 수정 |
| `src/stockscout/sources/dart.py` | `fetch_shares`·`fetch_audit`, 상태 처리 공통화 | 수정 |
| `src/stockscout/pipeline/collect.py` | 새 두 소스 수집 | 수정 |
| `src/stockscout/pipeline/normalize.py` | 세 소스 병합 | 수정 |
| `src/stockscout/analysis/filters.py` | `apply_financial_filters` 추가 | 수정 |
| `src/stockscout/analysis/scoring.py` | 4축 스코어링 | **신규** |
| `src/stockscout/analysis/grading.py` | ★★/★ 판정 | **신규** |
| `src/stockscout/analysis/valuation.py` | 유통주식수 기반 EPS·BPS | 수정 |
| `src/stockscout/pipeline/analyze.py` | 배선 | 수정 |
| `src/stockscout/web/api.py` | 근거에 점수 산출 추가 | 수정 |
| `src/stockscout/web/static/app.js` | 점수 분해 4축 막대 | 수정 |

`scoring.py`를 `filters.py`에 넣지 않는 이유: 하드필터는 "통과/탈락"만 내고 스코어링은 지표별 근거 구조를 만든다. 한 파일에 두면 400줄을 넘고 두 책임이 섞인다.

`grading.py`를 따로 두는 이유: 등급은 점수와 **가격**을 함께 본다. `scoring.py`가 가격을 알 필요는 없다.

### 이 계획에서 빼는 것

**배당(`dividend_per_share`)은 수집하지 않는다.** 196건 전부 NULL이고 「배당에 관한 사항」이라는 세 번째 엔드포인트가 필요하다.

빼는 근거는 설계서 §8.2다 — 배당은 **점수에 넣지 않는다**(배당을 안 주는 우량 성장주가 부당하게 깎이기 때문). 화면에만 보여주는 참고 지표이므로, 우량주 판정이라는 이 계획의 목적에 아무 영향이 없다. 화면은 이미 `—`로 표시한다.

엔드포인트를 하나 더 붙이는 일 자체는 Task 2·3이 만든 `_collect_optional`과 `ENRICHERS` 덕분에 나중에 반나절이면 된다.

---

## 실행 규칙

- 인터프리터는 `.venv/Scripts/python.exe`다. 시스템 python이 아니다
- 테스트는 `.venv/Scripts/python.exe -m pytest ...`. `pyproject.toml`에 `addopts = "-q"`가 있으므로 `-q`를 **더 주지 마라** (`-qq`가 되어 요약 줄이 사라진다)
- 콘솔이 cp949다. `✓` `⚠` `★` `—` 를 `print`하면 `UnicodeEncodeError`가 난다. 소스 문자열에 담는 것은 파일이 UTF-8이라 괜찮다
- `filterwarnings = ["error", ...]`다. 새 경고가 뜨면 테스트가 실패한다
- 매 Task 끝에 커밋한다. 커밋 메시지 마지막 줄은 `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`

---

## Task 1: 이자비용 매핑 — 우선순위 단계

**왜:** `interest_expense`가 196건 중 129건 결측이다. 실제 계정명은 `"금융비용"`인데 매핑표에는 `"금융원가"`라고 적혀 있고, id `ifrs-full_FinanceCosts`는 아예 없다.

**함정:** `pick_amount`은 *모든 id를 먼저, 그다음 모든 name*을 본다. `ifrs-full_FinanceCosts`를 id 목록에 그냥 추가하면, 표준 id 없이 이름으로만 `이자비용`을 쓰는 회사에서 금융비용이 진짜 이자비용을 이긴다. 실측 13건이고 **LG생활건강·한국전력·삼성에스디에스**가 거기 있다.

그래서 매핑값을 **단계(tier)의 목록**으로 바꾼다. 한 단계 안에서는 id → name 순, 단계는 순서대로.

**Files:**
- Modify: `src/stockscout/models.py`
- Modify: `src/stockscout/normalize/kr_mapping.py`
- Modify: `src/stockscout/store/schema.sql`
- Modify: `src/stockscout/store/db.py`
- Modify: `src/stockscout/store/repository.py`
- Test: `tests/test_kr_mapping.py`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_kr_mapping.py` 맨 끝에 붙인다.

```python
def test_tier_one_wins_even_when_tier_two_has_a_standard_id():
    """진짜 이자비용이 이름으로만 있어도 금융비용에 지지 않아야 한다.

    실측 13건이 이 모양이다 (LG생활건강·한국전력·삼성에스디에스).
    pick_amount은 모든 id를 이름보다 먼저 보므로, FinanceCosts를 그냥
    id 목록에 넣으면 이 회사들이 전부 틀린 값을 쓰게 된다.
    """
    items = [
        item("IS", "-표준계정코드 미사용-", "이자비용", "100"),
        item("IS", "ifrs-full_FinanceCosts", "금융비용", "900"),
    ]

    value, tier = pick_tiered(items, [
        (["ifrs-full_InterestExpense"], ["이자비용"]),
        (["ifrs-full_FinanceCosts"], ["금융비용"]),
    ])

    assert value == 100.0
    assert tier == 0


def test_tier_two_is_used_when_tier_one_is_absent():
    items = [item("IS", "ifrs-full_FinanceCosts", "금융비용", "900")]

    value, tier = pick_tiered(items, [
        (["ifrs-full_InterestExpense"], ["이자비용"]),
        (["ifrs-full_FinanceCosts"], ["금융비용"]),
    ])

    assert value == 900.0
    assert tier == 1


def test_pick_tiered_returns_none_when_no_tier_matches():
    assert pick_tiered([item("IS", "x", "엉뚱한계정", "5")], [(["a"], ["b"])]) == (None, None)


def test_normalize_records_which_fields_used_a_fallback():
    """대체값을 썼다는 사실이 남아야 판정 근거에 드러낼 수 있다."""
    stmt = RawStatement(
        ticker="051900.KS", fiscal_year=2024, source="dart",
        payload={"list": [
            item("IS", "ifrs-full_Revenue", "매출액", "300,000"),
            item("IS", "dart_OperatingIncomeLoss", "영업이익", "30,000"),
            item("BS", "ifrs-full_Liabilities", "부채총계", "60,000"),
            item("BS", "ifrs-full_Equity", "자본총계", "200,000"),
            item("IS", "ifrs-full_FinanceCosts", "금융비용", "900"),
        ]},
    )

    fin = normalize_dart_statement(stmt)

    assert fin.interest_expense == 900.0
    assert fin.proxy_fields == ["interest_expense"]


def test_normalize_leaves_proxy_fields_empty_for_exact_matches():
    stmt = RawStatement(
        ticker="005930.KS", fiscal_year=2024, source="dart",
        payload={"list": [
            item("IS", "ifrs-full_Revenue", "매출액", "300,000"),
            item("IS", "dart_OperatingIncomeLoss", "영업이익", "30,000"),
            item("BS", "ifrs-full_Liabilities", "부채총계", "60,000"),
            item("BS", "ifrs-full_Equity", "자본총계", "200,000"),
            item("IS", "ifrs-full_InterestExpense", "이자비용", "700"),
        ]},
    )

    fin = normalize_dart_statement(stmt)

    assert fin.interest_expense == 700.0
    assert fin.proxy_fields == []
```

파일 맨 위 import 줄을 바꾼다.

```python
from stockscout.normalize.kr_mapping import (
    BLANK_AMOUNTS,
    normalize_dart_statement,
    pick_amount,
    pick_tiered,
)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_kr_mapping.py`
Expected: FAIL — `ImportError: cannot import name 'pick_tiered'`

- [ ] **Step 3: `Financials`에 세 필드를 더한다**

`src/stockscout/models.py`의 `Financials` 안, `dividend_per_share` 아래에 넣는다.

```python
    shares_treasury: float | None = None      # 자기주식수 (설계서 §9.2)
    audit_opinion: str | None = None          # '적정' 등 원문 (설계서 §8.1)

    # 정확한 계정을 못 찾아 대체 계정으로 채운 필드 이름들 (설계서 §17.3).
    # 이자보상배율을 금융비용으로 계산했다면 판정 근거가 그렇게 말해야 한다.
    proxy_fields: list[str] = []
```

`shares_treasury`와 `audit_opinion`은 Task 2·3에서 채운다. 컬럼을 한 번에 더해 마이그레이션을 한 번만 하려고 여기서 같이 선언한다.

**`OPTIONAL_FIELDS`에 `shares_treasury`·`audit_opinion`을 넣지 마라.** 그 집합은 §8.3의 "결측이면 유효만점에서 뺀다" 대상이고, 이 둘은 점수 지표가 아니다. `missing_fields` 출력만 지저분해진다.

- [ ] **Step 4: 매핑을 단계 구조로 바꾼다**

`src/stockscout/normalize/kr_mapping.py`에서 `ACCOUNT_MAP`의 타입과 값을 바꾼다. 각 필드의 값이 `tuple`에서 `list[tuple]`이 된다 — 기존 항목은 전부 단계 하나짜리 목록으로 감싼다.

```python
# 필드 → 우선순위 단계 목록. 한 단계는 (account_id 후보, account_nm 후보)다.
# 단계는 위에서부터 시도하고, 한 단계 안에서는 id를 먼저 본다.
#
# 단계를 나눈 이유는 interest_expense 하나다. pick_amount이 모든 id를
# 이름보다 먼저 보기 때문에, 대체 계정(금융비용)의 id가 진짜 계정
# (이자비용)의 이름을 이겨버린다. 실측 13건이 그 모양이었고 그 안에
# LG생활건강이 있었다. 설계서 §17.3 참조.
ACCOUNT_MAP: dict[str, list[tuple[list[str], list[str]]]] = {
    "revenue": [(
        ["ifrs-full_Revenue", "ifrs-full_RevenueFromContractsWithCustomers"],
        ["매출액", "수익(매출액)", "영업수익", "매출"],
    )],
    "operating_income": [(
        ["dart_OperatingIncomeLoss", "ifrs-full_ProfitLossFromOperatingActivities"],
        ["영업이익", "영업이익(손실)", "영업손익"],
    )],
    "net_income": [(
        ["ifrs-full_ProfitLoss"],
        ["당기순이익", "당기순이익(손실)", "당기순손익"],
    )],
    "total_assets": [(["ifrs-full_Assets"], ["자산총계"])],
    "total_liabilities": [(["ifrs-full_Liabilities"], ["부채총계"])],
    "total_equity": [(
        ["ifrs-full_Equity"],
        ["자본총계", "자본총계(지배기업의 소유주지분)"],
    )],
    "current_assets": [(["ifrs-full_CurrentAssets"], ["유동자산"])],
    "current_liabilities": [(["ifrs-full_CurrentLiabilities"], ["유동부채"])],
    "interest_expense": [
        # 1단계: 진짜 이자비용
        (["ifrs-full_InterestExpense", "dart_InterestExpense"], ["이자비용"]),
        # 2단계: 금융비용. 이자비용에 환차손·파생손실이 더해진 상위 개념이라
        # 이자보상배율이 실제보다 낮게 나온다. 품질 필터에서는 보수적인
        # 방향이므로 쓰되, proxy_fields에 남겨 근거에 드러낸다.
        (["ifrs-full_FinanceCosts"], ["금융비용", "금융원가"]),
    ],
    "operating_cash_flow": [(
        ["ifrs-full_CashFlowsFromUsedInOperatingActivities"],
        ["영업활동현금흐름", "영업활동으로 인한 현금흐름", "영업활동으로인한현금흐름"],
    )],
    "capex": [(
        [
            "ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
        ],
        ["유형자산의 취득", "유형자산의취득", "유형자산의 증가"],
    )],
}
```

`pick_amount` **아래**에 `pick_tiered`를 더한다. `pick_amount`은 지우지 않는다 — 단계 하나를 보는 기존 의미 그대로 쓰이고, 기존 테스트도 그대로 산다.

```python
def pick_tiered(
    items: list[dict], tiers: list[tuple[list[str], list[str]]]
) -> tuple[float | None, int | None]:
    """단계를 순서대로 시도해 (금액, 사용한 단계 번호)를 반환한다.

    단계 번호가 0보다 크면 대체 계정을 썼다는 뜻이다. 호출자는 그것을
    proxy_fields에 남겨 화면이 근거를 말할 수 있게 한다.
    """
    for index, (ids, names) in enumerate(tiers):
        amount = pick_amount(items, ids, names)
        if amount is not None:
            return amount, index
    return None, None
```

`normalize_dart_statement`의 본문을 바꾼다.

```python
def normalize_dart_statement(stmt: RawStatement) -> Financials:
    """DART 원본 하나를 공통 스키마로 옮긴다. 순수 함수이며 네트워크·DB를 모른다."""
    items = stmt.payload.get("list", []) or []

    values: dict[str, float | None] = {}
    proxies: list[str] = []
    for field, tiers in ACCOUNT_MAP.items():
        amount, tier = pick_tiered(items, tiers)
        if amount is not None and field in ABS_FIELDS:
            amount = abs(amount)
        if tier is not None and tier > 0:
            proxies.append(field)
        values[field] = amount

    return Financials(
        ticker=stmt.ticker,
        fiscal_year=stmt.fiscal_year,
        currency="KRW",
        proxy_fields=proxies,
        **values,
    )
```

- [ ] **Step 5: 테스트가 통과하는지 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_kr_mapping.py`
Expected: PASS — `17 passed`

- [ ] **Step 6: DB 스키마에 컬럼 3개를 더한다**

`src/stockscout/store/schema.sql`의 `financials` 테이블에서 `missing_fields      TEXT,` **앞**에 넣는다.

```sql
  shares_treasury     REAL,
  audit_opinion       TEXT,
  proxy_fields        TEXT,
```

`src/stockscout/store/db.py`의 `init_db`에 마이그레이션 호출을 더한다.

```python
def init_db(conn: sqlite3.Connection) -> None:
    """스키마를 적용한다. 여러 번 호출해도 안전하다(CREATE TABLE IF NOT EXISTS)."""
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    _add_snapshots_as_of_column_if_missing(conn)
    _add_financials_columns_if_missing(conn)
    conn.commit()


def _add_financials_columns_if_missing(conn: sqlite3.Connection) -> None:
    """계획 C가 더한 컬럼을 기존 DB에 얹는다.

    data/stockscout.db에는 몇 분 걸려 받은 실제 데이터가 들어 있다.
    스키마가 바뀔 때마다 다시 받을 수는 없다.
    _add_snapshots_as_of_column_if_missing과 같은 패턴이다.
    """
    columns = {row[1] for row in conn.execute("PRAGMA table_info(financials)")}
    for name, sql_type in (
        ("shares_treasury", "REAL"),
        ("audit_opinion", "TEXT"),
        ("proxy_fields", "TEXT"),
    ):
        if name not in columns:
            conn.execute(f"ALTER TABLE financials ADD COLUMN {name} {sql_type}")
```

- [ ] **Step 7: Repository가 새 컬럼을 읽고 쓰게 한다**

`src/stockscout/store/repository.py`의 `_FIN_COLUMNS`(205행)에 두 개를 더한다. 이 튜플이 `save_financials`의 INSERT와 `get_financials`의 조회를 **둘 다** 몰고 가므로, 여기만 고치면 양쪽이 따라온다.

```python
    _FIN_COLUMNS = (
        "ticker", "fiscal_year", "currency", "revenue", "operating_income",
        "net_income", "total_assets", "total_liabilities", "total_equity",
        "current_assets", "current_liabilities", "interest_expense",
        "operating_cash_flow", "capex", "shares_outstanding", "dividend_per_share",
        "shares_treasury", "audit_opinion",
    )
```

`proxy_fields`는 JSON이라 `missing_fields`와 같은 별도 처리가 필요하다. `save_financials`를 고친다.

```python
    def save_financials(self, records: list[Financials]) -> None:
        extra = ("missing_fields", "proxy_fields")
        cols = ", ".join(self._FIN_COLUMNS + extra)
        placeholders = ", ".join("?" * (len(self._FIN_COLUMNS) + len(extra)))
        updates = ", ".join(
            f"{c}=excluded.{c}" for c in self._FIN_COLUMNS[2:] + extra
        )
        self.conn.executemany(
            f"""
            INSERT INTO financials ({cols}) VALUES ({placeholders})
            ON CONFLICT(ticker, fiscal_year) DO UPDATE SET {updates}
            """,
            [
                tuple(getattr(r, c) for c in self._FIN_COLUMNS)
                + (
                    json.dumps(r.missing_fields, ensure_ascii=False),
                    json.dumps(r.proxy_fields, ensure_ascii=False),
                )
                for r in records
            ],
        )
        self.conn.commit()
```

`get_financials`를 고친다. **컬럼이 NULL이면 `[]`로 읽어야 한다** — 계획 C 이전에 저장된 196행이 전부 NULL이다.

```python
    def get_financials(self, ticker: str) -> list[Financials]:
        rows = self.conn.execute(
            "SELECT * FROM financials WHERE ticker = ? ORDER BY fiscal_year",
            (ticker,),
        ).fetchall()
        return [
            Financials(
                **{c: row[c] for c in self._FIN_COLUMNS},
                # 계획 C 이전 행은 이 컬럼이 NULL이다. json.loads(None)은 터진다.
                proxy_fields=json.loads(row["proxy_fields"] or "[]"),
            )
            for row in rows
        ]
```

- [ ] **Step 8: 저장·조회 왕복 테스트를 쓴다**

먼저 기존 재무 repository 테스트가 어디 있는지 찾아라.

```bash
grep -rln "save_financials" tests/
```

찾은 파일에 붙인다. fixture 이름(`repo`·`db` 등)은 그 파일의 것을 그대로 쓴다.

```python
def test_proxy_fields_round_trip(repo):
    repo.save_financials([Financials(
        ticker="051900.KS", fiscal_year=2024, currency="KRW",
        revenue=1.0, operating_income=1.0, total_equity=1.0,
        total_liabilities=1.0, interest_expense=900.0,
        proxy_fields=["interest_expense"],
    )])

    (loaded,) = repo.get_financials("051900.KS")

    assert loaded.proxy_fields == ["interest_expense"]


def test_proxy_fields_reads_as_empty_list_when_column_is_null(repo, db):
    """계획 C 이전에 저장된 196행은 이 컬럼이 NULL이다."""
    repo.save_financials([Financials(
        ticker="005930.KS", fiscal_year=2024, currency="KRW",
        revenue=1.0, operating_income=1.0, total_equity=1.0, total_liabilities=1.0,
    )])
    db.execute("UPDATE financials SET proxy_fields = NULL")

    (loaded,) = repo.get_financials("005930.KS")

    assert loaded.proxy_fields == []
```

- [ ] **Step 9: 전체 스위트를 돌린다**

Run: `.venv/Scripts/python.exe -m pytest`
Expected: 실패 0건

- [ ] **Step 10: 실제 데이터로 재정규화해 효과를 잰다**

```bash
.venv/Scripts/stockscout.exe normalize --market KR
```

Expected: 출력의 `결측 상위:` 줄에서 `interest_expense`가 **129건에서 10건 안팎으로** 줄어든다.

줄지 않았다면 매핑이 안 먹은 것이다. 아래로 실제 값을 확인해라.

```bash
.venv/Scripts/python.exe -c "import sqlite3; c=sqlite3.connect('data/stockscout.db'); print('결측', c.execute('SELECT COUNT(*) FROM financials WHERE interest_expense IS NULL').fetchone()[0]); print('대체', c.execute('SELECT COUNT(*) FROM financials WHERE proxy_fields LIKE \"%interest_expense%\"').fetchone()[0])"
```

Expected: 결측 10건 안팎, 대체 149건 안팎.

- [ ] **Step 11: 변이 테스트**

`pick_tiered`의 `for index, (ids, names) in enumerate(tiers)`를 `enumerate(reversed(tiers))`로 바꿔라.

Run: `.venv/Scripts/python.exe -m pytest tests/test_kr_mapping.py::test_tier_one_wins_even_when_tier_two_has_a_standard_id`
Expected: **FAIL**. 통과하면 테스트가 무의미한 것이니 테스트를 고쳐라.

확인 후 반드시 되돌리고 `git diff -- src/` 로 원복을 확인해라.

- [ ] **Step 12: 커밋**

```bash
git add -A
git commit -m "fix: 이자비용 매핑을 우선순위 단계로 바꾼다

실제 계정명은 금융원가가 아니라 금융비용이고, id
ifrs-full_FinanceCosts는 매핑표에 없었다. 196건 중 129건이 결측이었다.

그냥 id 목록에 추가하면 안 된다. pick_amount이 모든 id를 이름보다
먼저 보기 때문에, 표준 id 없이 이름으로만 이자비용을 쓰는 회사에서
금융비용이 이긴다. 실측 13건이고 LG생활건강, 한국전력,
삼성에스디에스가 거기 있다.

금융비용은 이자비용의 상위 개념이라 이자보상배율이 실제보다 낮게
나온다. 품질 필터에서는 보수적인 방향이라 쓰되, proxy_fields에 남겨
판정 근거가 그 사실을 말할 수 있게 한다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 2: 발행주식수 수집 — `stockTotqySttus.json`

**왜:** `shares_outstanding`이 196건 전부 결측이라 시가총액·PER·PBR이 전부 NULL이다(설계서 §9.2). 전용 API가 있고 파라미터가 재무제표 API와 **똑같다.**

**실제 응답에서 확인한 것 (설계서 §17.4):** `se` 필드로 **보통주 / 우선주 / 합계 / 비고** 4행이 온다. 삼성전자 2024 기준 보통주 59.7억 주, 합계 67.9억 주. **합계를 쓰면 시가총액이 13.8% 과대평가된다.** 005930은 보통주 티커이므로 보통주 행을 써야 한다(우선주 005935는 별도 티커다). `비고` 행의 수량은 `-`다.

**Files:**
- Modify: `src/stockscout/sources/dart.py`
- Modify: `src/stockscout/normalize/kr_mapping.py`
- Modify: `src/stockscout/pipeline/collect.py`
- Modify: `src/stockscout/pipeline/normalize.py`
- Test: `tests/test_kr_mapping.py`, `tests/test_dart_statements.py`

- [ ] **Step 1: 정규화 함수의 실패하는 테스트를 쓴다**

`tests/test_kr_mapping.py` 맨 끝에 붙인다.

```python
def share_row(se, istc, tesstk):
    return {"se": se, "istc_totqy": istc, "tesstk_co": tesstk,
            "distb_stock_co": "0", "corp_cls": "Y"}


def test_shares_uses_common_stock_not_the_total():
    """합계를 쓰면 시가총액이 부풀려진다.

    삼성전자 2024 실측: 보통주 59.7억 주, 합계 67.9억 주. 13.8% 차이다.
    005930은 보통주 티커이고 우선주는 005935라는 별도 티커다.
    """
    payload = {"list": [
        share_row("보통주", "5,969,782,550", "29,700,000"),
        share_row("우선주", "822,886,700", "4,050,000"),
        share_row("합계", "6,792,669,250", "33,750,000"),
        share_row("비고", "-", "-"),
    ]}

    shares, treasury = parse_share_counts(payload)

    assert shares == 5_969_782_550.0
    assert treasury == 29_700_000.0


def test_shares_falls_back_to_total_when_common_row_is_absent():
    """se 표기가 회사마다 다를 수 있다. 합계라도 있으면 NULL보다 낫다."""
    payload = {"list": [share_row("합계", "1,000,000", "10,000")]}

    assert parse_share_counts(payload) == (1_000_000.0, 10_000.0)


def test_shares_ignores_the_remarks_row():
    payload = {"list": [share_row("비고", "-", "-")]}

    assert parse_share_counts(payload) == (None, None)


def test_shares_returns_none_for_empty_payload():
    assert parse_share_counts({}) == (None, None)
    assert parse_share_counts({"list": []}) == (None, None)
```

import에 `parse_share_counts`를 더한다.

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_kr_mapping.py`
Expected: FAIL — `ImportError: cannot import name 'parse_share_counts'`

- [ ] **Step 3: 정규화 함수를 만든다**

`src/stockscout/normalize/kr_mapping.py` 맨 끝에 더한다.

```python
# stockTotqySttus.json의 se(구분) 값. 보통주를 먼저 찾고, 없으면 합계를 쓴다.
# 합계는 우선주를 포함하므로 보통주 티커의 시가총액을 부풀린다 —
# 삼성전자 2024 기준 13.8%다. 그래도 NULL보다는 낫다 (설계서 §17.4).
SHARE_ROW_PRIORITY = ("보통주", "합계")


def parse_share_counts(payload: dict) -> tuple[float | None, float | None]:
    """주식의 총수 현황 → (발행주식총수, 자기주식수).

    se가 보통주/우선주/합계/비고 4행으로 온다. 비고 행의 수량은 '-'라
    _parse_amount가 None을 돌려주므로 자연히 걸러진다.
    """
    rows = payload.get("list", []) or []
    by_se: dict[str, dict] = {}
    for row in rows:
        se = (row.get("se") or "").strip().replace(" ", "")
        by_se.setdefault(se, row)

    for se in SHARE_ROW_PRIORITY:
        row = by_se.get(se)
        if row is None:
            continue
        issued = _parse_amount(row.get("istc_totqy"))
        if issued is None:
            continue
        return issued, _parse_amount(row.get("tesstk_co"))
    return None, None
```

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_kr_mapping.py`
Expected: PASS

- [ ] **Step 5: DART 소스의 상태 처리를 공통화한다**

`src/stockscout/sources/dart.py`의 `_fetch_one_year`(143행)를 연다. 본문에서 **엔드포인트와 파라미터만** 밖으로 빼고 나머지(상태 코드 7종 처리)는 그대로 둔다.

```python
    @retry_on_transient(attempts=3)
    def _fetch_one_year(self, corp_code: str, year: int) -> dict | None:
        """한 회계연도의 연결 재무제표 원본. 데이터가 없으면 None."""
        return self._fetch_json(
            "fnlttSinglAcntAll.json", corp_code, year, {"fs_div": "CFS"}
        )

    @retry_on_transient(attempts=3)
    def _fetch_shares_one_year(self, corp_code: str, year: int) -> dict | None:
        """한 회계연도의 주식의 총수 현황. 데이터가 없으면 None."""
        return self._fetch_json("stockTotqySttus.json", corp_code, year, {})
```

그리고 기존 `_fetch_one_year`의 본문(`self.bucket.wait()`부터 상태 처리 끝까지)을 그대로 옮긴 `_fetch_json`을 만든다. **docstring의 상태 코드 설명은 `_fetch_json`으로 옮긴다** — 그 지식이 사는 곳이 거기다.

```python
    def _fetch_json(
        self, endpoint: str, corp_code: str, year: int, extra: dict[str, str]
    ) -> dict | None:
        """DART JSON 엔드포인트 하나를 호출하고 status 7종을 해석한다.

        DART의 모든 공시 API가 crtfc_key·corp_code·bsns_year·reprt_code를
        공유하고 status 코드 체계도 같다. 엔드포인트마다 이 판단을 복사하면
        한 곳만 고쳐지는 일이 생긴다.

        (아래에 기존 _fetch_one_year의 status 설명 주석을 그대로 옮긴다:
         000 정상 / 013 데이터 없음 → None / 010·100·020 → FatalSourceError로
         배치 중단 / 800 → DartServiceUnavailable로 재시도 / 900·미지정 →
         SourceError로 이 종목만 실패)
        """
        self.bucket.wait()
        self.quota.consume()
        resp = self._client.get(
            f"{BASE_URL}/{endpoint}",
            params={
                "crtfc_key": self.api_key,
                "corp_code": corp_code,
                "bsns_year": str(year),
                "reprt_code": REPORT_CODE_ANNUAL,
                **extra,
            },
        )
        resp.raise_for_status()
        ...  # 기존 status 처리를 그대로 옮긴다
```

**`retry_on_transient`을 `_fetch_json`에 붙이지 마라.** 이미 호출자 두 곳에 붙어 있어 중첩되면 재시도가 3회가 아니라 9회가 된다.

- [ ] **Step 6: `fetch_shares`를 더한다**

상수는 **모듈 최상단**(`REPORT_CODE_ANNUAL` 옆)에, 메서드는 `fetch_annual` 바로 아래 **클래스 안**에 붙인다. `fetch_annual`과 구조가 같고 호출하는 내부 메서드와 `source` 값만 다르다.

```python
# 모듈 최상단
SOURCE_SHARES = "dart_shares"
```

```python
    # DartSource 클래스 안, fetch_annual 아래
    def fetch_shares(self, ticker: str, years: list[int]) -> list[RawStatement]:
        """주식의 총수 현황을 연도별로 받는다.

        RawStatement를 재사용하고 source만 달리한다. raw_financials의
        기본키가 (ticker, fiscal_year, source)라 테이블을 늘리지 않아도 된다.
        """
        self.ensure_corp_codes()
        stock_code = self._stock_code(ticker)
        corp_code = self.corp_codes.get(stock_code)
        if not corp_code:
            raise PermanentSourceError(f"{ticker}의 corp_code를 찾을 수 없습니다")

        statements: list[RawStatement] = []
        for year in years:
            payload = self._fetch_shares_one_year(corp_code, year)
            if payload is None:
                continue
            statements.append(RawStatement(
                ticker=ticker, fiscal_year=year,
                source=SOURCE_SHARES, payload=payload,
            ))
        return statements
```

- [ ] **Step 7: 소스 테스트를 쓴다**

`tests/test_dart_statements.py`에 붙인다. 이 파일의 기존 테스트가 httpx를 어떻게 흉내 내는지 먼저 읽고 같은 방식을 써라.

```python
def test_fetch_shares_tags_statements_with_its_own_source():
    """source가 다르면 같은 (ticker, year)라도 재무제표를 덮어쓰지 않는다."""
    source = make_source(responses={
        "stockTotqySttus.json": {"status": "000", "list": [
            {"se": "보통주", "istc_totqy": "100", "tesstk_co": "1"},
        ]},
    })

    (stmt,) = source.fetch_shares("005930.KS", [2024])

    assert stmt.source == "dart_shares"
    assert stmt.fiscal_year == 2024
    assert stmt.payload["list"][0]["se"] == "보통주"


def test_fetch_shares_skips_years_with_no_data():
    source = make_source(responses={
        "stockTotqySttus.json": {"status": "013", "message": "조회된 데이타가 없습니다."},
    })

    assert source.fetch_shares("005930.KS", [2021, 2022]) == []
```

`make_source`는 이 파일에 이미 있는 헬퍼일 수도 있고 없을 수도 있다. 없으면 기존 테스트가 쓰는 방식(`respx`·`httpx.MockTransport`·monkeypatch 중 무엇이든)을 그대로 따라 엔드포인트별 응답을 흉내 내는 헬퍼를 만들어라. **엔드포인트 이름으로 응답을 고를 수 있어야 한다** — Task 3에서 감사의견 엔드포인트가 하나 더 붙는다.

- [ ] **Step 8: 수집 파이프라인에 배선한다**

`src/stockscout/pipeline/collect.py`에 범용 헬퍼를 더한다. Task 3의 감사의견도 이것을 그대로 쓴다.

```python
def _collect_optional(
    repo: Repository,
    ticker: str,
    source: FundamentalSource,
    method: str,
    raw_source: str,
    years: list[int],
    force: bool,
) -> int:
    """소스가 선택적으로 제공하는 공시를 수집한다 (주식수·감사의견).

    메서드가 없으면 0을 돌려준다. DART에만 있는 엔드포인트라서, 앞으로
    붙일 EDGAR 소스는 이 메서드를 갖지 않는다 — 프로토콜을 넓혀 모든
    소스가 빈 구현을 갖게 하는 것보다 낫다.
    """
    fetch = getattr(source, method, None)
    if fetch is None:
        return 0
    if not years:
        return 0
    wanted = list(years)
    if not force:
        wanted = [y for y in years
                  if y not in repo.existing_statement_years(ticker, raw_source)]
    if not wanted:
        return 0

    statements = fetch(ticker, wanted)
    if statements:
        repo.save_raw_statements(statements)
    return len(statements)
```

`collect_market`의 재무제표 try 블록 **뒤**, 주가 try 블록 **앞**에 넣는다.

```python
        # 재무제표와 별개의 try. 주식수 수집이 실패해도 재무제표와 주가는
        # 남아야 한다 — 계획 A의 결함 C2가 정확히 이 실수였다(두 수집을 한
        # try에 묶었더니 DART 실패가 주가 5년치를 통째로 날렸다).
        if not aborted:
            try:
                summary.shares += _collect_optional(
                    repo, ticker, fundamentals, "fetch_shares",
                    SOURCE_SHARES, years, force,
                )
            except ABORTING_ERRORS as exc:
                repo.record_error(ticker, SOURCE_SHARES, str(exc))
                summary.abort_reason = str(exc)
                aborted = True
            except Exception as exc:  # 종목 단위 격리 (설계서 §13)
                repo.record_error(ticker, SOURCE_SHARES,
                                  f"{type(exc).__name__}: {exc}")
                ticker_failed = True
```

`CollectSummary`에 `shares: int = 0`을 더하고, `render()`의 `재무제표 ... · 주가 ...봉 저장` 줄에 `· 주식수 {self.shares}건`을 끼워 넣는다. `SOURCE_SHARES`는 `stockscout.sources.dart`에서 import한다.

- [ ] **Step 9: 정규화 파이프라인에서 병합한다**

`src/stockscout/normalize/kr_mapping.py`에 적용 함수를 더한다.

```python
def apply_share_counts(fin: Financials, payload: dict) -> None:
    """주식수 원본을 이미 만들어진 Financials 위에 얹는다."""
    issued, treasury = parse_share_counts(payload)
    if issued is not None:
        fin.shares_outstanding = issued
    if treasury is not None:
        fin.shares_treasury = treasury
```

`src/stockscout/pipeline/normalize.py`에 보강 소스 표를 더한다.

```python
from stockscout.normalize.kr_mapping import apply_share_counts, normalize_dart_statement

# 재무제표 위에 얹는 보강 소스: (원본 source, 적용 함수).
# 재무제표가 있는 연도에만 얹는다 — 주식수만 있는 연도에 Financials 행을
# 만들면 필수 항목이 전부 비어 DATA_INSUFFICIENT가 될 뿐이다. 금융
# 7종목의 2021·2022년이 정확히 그 모양이다(DART에 재무제표가 없다).
ENRICHERS: dict[str, list[tuple[str, object]]] = {
    "KR": [("dart_shares", apply_share_counts)],
}
```

`normalize_market`의 `records = [normalizer(s) for s in statements]` **뒤**, `repo.save_financials(records)` **앞**에 넣는다.

```python
            by_year = {r.fiscal_year: r for r in records}
            for enrich_source, apply in ENRICHERS.get(market, []):
                for raw in repo.get_raw_statements(ticker, source=enrich_source):
                    target = by_year.get(raw.fiscal_year)
                    if target is not None:
                        apply(target, raw.payload)
```

같은 `try` 안이라 보강이 터져도 종목 단위로 격리된다.

- [ ] **Step 10: 실제로 수집한다**

```bash
.venv/Scripts/stockscout.exe collect --market KR
.venv/Scripts/stockscout.exe normalize --market KR
```

DART 호출이 종목당 5회 늘어 210건 추가된다(일일 한도 20,000건이라 여유가 크다). 몇 분 걸리므로 백그라운드로 돌려라.

Expected: `normalize` 출력의 `결측 상위:`에서 `shares_outstanding`이 **196건에서 크게 줄어든다.**

- [ ] **Step 11: 실제 값을 눈으로 확인한다**

```bash
.venv/Scripts/python.exe -c "import sqlite3; c=sqlite3.connect('data/stockscout.db'); [print(r) for r in c.execute('SELECT ticker, fiscal_year, shares_outstanding, shares_treasury FROM financials WHERE ticker=\"005930.KS\" ORDER BY fiscal_year')]"
```

Expected: 2024년 행의 `shares_outstanding`이 **5969782550.0** 근처여야 한다. 6792669250.0이 나오면 합계 행을 쓴 것이니 `parse_share_counts`를 고쳐라.

- [ ] **Step 12: 커밋**

```bash
git add -A
git commit -m "feat: 발행주식수를 수집해 시가총액·PER·PBR을 살린다

stockTotqySttus.json은 재무제표 API와 파라미터가 같아 상태 처리
코드를 그대로 재사용한다. _fetch_json으로 공통화했다.

se 필드로 보통주/우선주/합계/비고 4행이 온다. 보통주를 쓴다 -
합계는 우선주를 포함해서, 삼성전자 기준 시가총액이 13.8%
부풀려진다. 005930은 보통주 티커이고 우선주는 005935다.

RawStatement를 재사용하고 source만 dart_shares로 달리했다.
raw_financials의 기본키가 (ticker, fiscal_year, source)라
테이블을 늘리지 않아도 된다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 3: 감사의견 수집 — `accnutAdtorNmNdAdtOpinion.json`

**왜:** 설계서 §8.1의 `ADVERSE_AUDIT_OPINION` 필터가 아직 없다. 감사의견 비적정은 우량주 판정에서 가장 강한 결격 신호다 — 회계법인이 재무제표를 믿을 수 없다고 말한 것이므로, 그 재무제표로 계산한 점수도 믿을 수 없다.

**실제 응답에서 확인한 것 (설계서 §17.4):**
- `bsns_year`가 연도가 아니다. `"제56기\n(당기)"` 형태이고 **줄바꿈이 박혀 있다.** 연도로 파싱하면 깨진다
- 기수당 2행씩 **중복**으로 온다 (3기 → 6행)
- `adt_opinion`은 `"적정의견"` 같은 문자열이다

**Files:**
- Modify: `src/stockscout/sources/dart.py`
- Modify: `src/stockscout/normalize/kr_mapping.py`
- Modify: `src/stockscout/pipeline/collect.py`
- Modify: `src/stockscout/pipeline/normalize.py`
- Test: `tests/test_kr_mapping.py`, `tests/test_dart_statements.py`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_kr_mapping.py` 맨 끝에 붙인다.

```python
def audit_row(bsns_year, opinion, adtor="삼정회계법인"):
    return {"bsns_year": bsns_year, "adt_opinion": opinion, "adtor": adtor}


def test_audit_opinion_takes_the_current_period():
    """bsns_year가 연도가 아니라 '제56기\\n(당기)'다. 줄바꿈이 박혀 있다."""
    payload = {"list": [
        audit_row("제56기\n(당기)", "적정의견"),
        audit_row("제56기\n(당기)", "적정의견"),
        audit_row("제55기\n(전기)", "한정의견"),
    ]}

    assert parse_audit_opinion(payload) == "적정의견"


def test_audit_opinion_falls_back_to_the_first_row_without_a_period_marker():
    """(당기) 표기가 없는 회사가 있을 수 있다. 첫 행이 최신이다."""
    payload = {"list": [audit_row("2024", "적정"), audit_row("2023", "적정")]}

    assert parse_audit_opinion(payload) == "적정"


def test_audit_opinion_returns_none_when_absent():
    assert parse_audit_opinion({}) is None
    assert parse_audit_opinion({"list": []}) is None
    assert parse_audit_opinion({"list": [audit_row("제56기\n(당기)", "")]}) is None


def test_adverse_opinion_detection():
    """'적정'이 들어 있으면 통과. 그 외는 전부 결격이다."""
    assert is_adverse_opinion("적정의견") is False
    assert is_adverse_opinion("적정") is False
    assert is_adverse_opinion("한정의견") is True
    assert is_adverse_opinion("부적정의견") is True
    assert is_adverse_opinion("의견거절") is True


def test_unknown_opinion_is_not_treated_as_adverse():
    """모르는 값으로 종목을 떨어뜨리지 않는다.

    DART 문서가 값 목록을 명시하지 않는다. 처음 보는 문자열을 결격으로
    처리하면, 표기가 조금 다른 회사가 통째로 사라지고 아무도 이유를
    모른다. 결격 판정은 아는 단어에만 건다.
    """
    assert is_adverse_opinion(None) is False
    assert is_adverse_opinion("") is False
    assert is_adverse_opinion("검토의견 없음") is False
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_kr_mapping.py`
Expected: FAIL — `ImportError: cannot import name 'parse_audit_opinion'`

- [ ] **Step 3: 결격 판정을 `models.py`에 둔다**

**`is_adverse_opinion`은 `kr_mapping.py`가 아니라 `src/stockscout/models.py`에 넣는다.** Task 4의 `apply_financial_filters`가 이 함수를 쓰는데, 계층 규칙(§5)상 `analysis`는 `normalize`를 import할 수 없다. 감사의견이 결격인지는 DART의 지식이 아니라 도메인의 지식이므로 models가 맞는 자리다. DART 응답 **구조**를 아는 `parse_audit_opinion`은 `kr_mapping.py`에 남는다.

`src/stockscout/models.py`의 `Financials` **앞**에 넣는다.

```python
# 감사의견 결격 판정에 쓰는 단어. 설계서 §8.1이 "적정이 아니면 제외"라고
# 하지만, 모르는 문자열을 결격으로 처리하면 표기가 조금 다른 회사가
# 통째로 사라지고 아무도 이유를 모른다. 아는 단어에만 건다.
ADVERSE_OPINION_WORDS = ("한정", "부적정", "의견거절", "의견 거절")


def is_adverse_opinion(opinion: str | None) -> bool:
    """감사의견이 결격인가. 모르는 값은 결격으로 보지 않는다."""
    if not opinion:
        return False
    text = opinion.replace(" ", "")
    return any(word.replace(" ", "") in text for word in ADVERSE_OPINION_WORDS)
```

- [ ] **Step 3b: 응답 파서를 만든다**

`src/stockscout/normalize/kr_mapping.py` 맨 끝에 더한다.

```python
def parse_audit_opinion(payload: dict) -> str | None:
    """회계감사인의 명칭 및 감사의견 → 최근 사업연도의 감사의견 원문.

    bsns_year가 연도가 아니라 '제56기\\n(당기)' 형태이고 줄바꿈이 박혀
    있다. 연도로 파싱하지 않고 '(당기)' 표기를 찾는다. 기수당 2행씩
    중복으로 오므로 첫 매치만 쓴다.
    """
    rows = payload.get("list", []) or []
    for row in rows:
        marker = (row.get("bsns_year") or "").replace(" ", "")
        if "(당기)" in marker:
            return (row.get("adt_opinion") or "").strip() or None
    for row in rows:
        opinion = (row.get("adt_opinion") or "").strip()
        if opinion:
            return opinion
    return None
```

**"적정이 없으면 결격"으로 짜지 않는 것이 요점이다.** DART 문서가 값 목록을 명시하지 않으므로, 그렇게 짜면 표기가 조금 다른 회사가 전부 결격이 되어 통째로 사라지고 아무도 이유를 모른다. 아는 결격 단어에만 건다.

Step 1의 테스트 import를 그에 맞춘다.

```python
from stockscout.models import is_adverse_opinion
from stockscout.normalize.kr_mapping import parse_audit_opinion
```

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_kr_mapping.py`
Expected: PASS

- [ ] **Step 5: `fetch_audit`을 더한다**

`src/stockscout/sources/dart.py`에 Task 2의 `fetch_shares`와 같은 모양으로 붙인다. 상수는 모듈 최상단, 메서드는 클래스 안이다.

```python
# 모듈 최상단
SOURCE_AUDIT = "dart_audit"
```

```python
    # DartSource 클래스 안
    @retry_on_transient(attempts=3)
    def _fetch_audit_one_year(self, corp_code: str, year: int) -> dict | None:
        """한 회계연도의 감사인·감사의견. 데이터가 없으면 None."""
        return self._fetch_json(
            "accnutAdtorNmNdAdtOpinion.json", corp_code, year, {}
        )

    def fetch_audit(self, ticker: str, years: list[int]) -> list[RawStatement]:
        """감사인의 명칭 및 감사의견을 연도별로 받는다."""
        self.ensure_corp_codes()
        stock_code = self._stock_code(ticker)
        corp_code = self.corp_codes.get(stock_code)
        if not corp_code:
            raise PermanentSourceError(f"{ticker}의 corp_code를 찾을 수 없습니다")

        statements: list[RawStatement] = []
        for year in years:
            payload = self._fetch_audit_one_year(corp_code, year)
            if payload is None:
                continue
            statements.append(RawStatement(
                ticker=ticker, fiscal_year=year,
                source=SOURCE_AUDIT, payload=payload,
            ))
        return statements
```

- [ ] **Step 6: 소스 테스트를 쓴다**

`tests/test_dart_statements.py`에 붙인다. Task 2 Step 7에서 만든 엔드포인트별 응답 헬퍼를 쓴다.

```python
def test_fetch_audit_tags_statements_with_its_own_source():
    source = make_source(responses={
        "accnutAdtorNmNdAdtOpinion.json": {"status": "000", "list": [
            {"bsns_year": "제56기\n(당기)", "adt_opinion": "적정의견",
             "adtor": "삼정회계법인"},
        ]},
    })

    (stmt,) = source.fetch_audit("005930.KS", [2024])

    assert stmt.source == "dart_audit"
    assert stmt.payload["list"][0]["adt_opinion"] == "적정의견"
```

- [ ] **Step 7: 수집·정규화에 배선한다**

Task 2가 만든 `_collect_optional`을 그대로 쓴다. `collect_market`의 주식수 블록 바로 뒤에 붙인다.

```python
        if not aborted:
            try:
                summary.audits += _collect_optional(
                    repo, ticker, fundamentals, "fetch_audit",
                    SOURCE_AUDIT, years, force,
                )
            except ABORTING_ERRORS as exc:
                repo.record_error(ticker, SOURCE_AUDIT, str(exc))
                summary.abort_reason = str(exc)
                aborted = True
            except Exception as exc:  # 종목 단위 격리 (설계서 §13)
                repo.record_error(ticker, SOURCE_AUDIT,
                                  f"{type(exc).__name__}: {exc}")
                ticker_failed = True
```

`CollectSummary`에 `audits: int = 0`을 더하고 `render()`에 `· 감사의견 {self.audits}건`을 끼운다.

`src/stockscout/normalize/kr_mapping.py`에 적용 함수를 더한다.

```python
def apply_audit_opinion(fin: Financials, payload: dict) -> None:
    opinion = parse_audit_opinion(payload)
    if opinion is not None:
        fin.audit_opinion = opinion
```

`src/stockscout/pipeline/normalize.py`의 `ENRICHERS`에 한 줄 더한다.

```python
ENRICHERS: dict[str, list[tuple[str, object]]] = {
    "KR": [
        ("dart_shares", apply_share_counts),
        ("dart_audit", apply_audit_opinion),
    ],
}
```

- [ ] **Step 8: 실제로 수집한다**

```bash
.venv/Scripts/stockscout.exe collect --market KR
.venv/Scripts/stockscout.exe normalize --market KR
```

- [ ] **Step 9: 실제 값 분포를 확인한다**

```bash
.venv/Scripts/python.exe -c "import sqlite3,io,sys; sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8'); c=sqlite3.connect('data/stockscout.db'); [print(r) for r in c.execute('SELECT audit_opinion, COUNT(*) FROM financials GROUP BY audit_opinion ORDER BY 2 DESC')]"
```

Expected: 대부분 `적정의견`이다. **처음 보는 문자열이 나오면 `ADVERSE_OPINION_WORDS`가 그것을 놓치는지 확인하고, 실제로 결격을 뜻하는 표기면 목록에 더해라.** 42종목 KOSPI 대형주라 비적정이 하나도 없을 가능성이 높다 — 그것이 정상이며, 필터가 아무도 걸러내지 않아도 버그가 아니다.

- [ ] **Step 10: 커밋**

```bash
git add -A
git commit -m "feat: 감사의견을 수집한다

회계법인이 재무제표를 믿을 수 없다고 말했다면 그 재무제표로 계산한
점수도 믿을 수 없다. 설계서 §8.1이 가장 강한 결격 신호로 꼽는다.

응답에 함정이 둘 있다. bsns_year가 연도가 아니라 '제56기\\n(당기)'이고
줄바꿈이 박혀 있다. 그리고 기수당 2행씩 중복으로 온다.

모르는 의견 문자열은 결격으로 보지 않는다. DART 문서가 값 목록을
명시하지 않는데, 처음 보는 표기를 결격으로 처리하면 회사가 통째로
사라지고 아무도 이유를 모른다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 4: 재무 하드필터

**왜:** 설계서 §8.1의 9개 필터 중 지금 구현된 것은 `FINANCIAL_SECTOR`와 `INSUFFICIENT_HISTORY`(주가 기준) 둘뿐이다. 나머지 일곱을 더한다.

**Files:**
- Modify: `src/stockscout/analysis/filters.py`
- Test: `tests/test_financial_filters.py` (신규)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_financial_filters.py`를 만든다.

```python
"""재무 하드필터. 설계서 §8.1."""

import pytest

from stockscout.analysis.filters import (
    ADVERSE_AUDIT_OPINION,
    CAPITAL_IMPAIRED,
    CONSECUTIVE_LOSS,
    DATA_INSUFFICIENT,
    HIGH_DEBT,
    INSUFFICIENT_HISTORY,
    INTEREST_UNCOVERED,
    REVENUE_COLLAPSE,
    apply_financial_filters,
)
from stockscout.config import HardFilterConfig
from stockscout.models import Financials


def fin(year, **kwargs):
    """필수 4항목이 채워진 정상 재무 한 해. 바꿀 것만 kwargs로 준다."""
    base = dict(
        ticker="005930.KS", fiscal_year=year, currency="KRW",
        revenue=1_000.0, operating_income=100.0, net_income=80.0,
        total_assets=2_000.0, total_liabilities=500.0, total_equity=1_000.0,
        current_assets=800.0, current_liabilities=400.0,
        interest_expense=10.0, operating_cash_flow=120.0, capex=30.0,
        audit_opinion="적정의견",
    )
    base.update(kwargs)
    return Financials(**base)


def five_years(**kwargs):
    return [fin(y, **kwargs) for y in range(2021, 2026)]


CONFIG = HardFilterConfig()


def test_healthy_company_passes_everything():
    outcome = apply_financial_filters(five_years(), CONFIG)

    assert outcome.failures == []
    assert outcome.passed is True


def test_capital_impairment_is_caught():
    years = five_years()
    years[-1] = fin(2025, total_equity=-10.0)

    assert CAPITAL_IMPAIRED in apply_financial_filters(years, CONFIG).failures


def test_three_consecutive_operating_losses_are_caught():
    years = five_years()
    for i in (-3, -2, -1):
        years[i] = fin(years[i].fiscal_year, operating_income=-5.0)

    assert CONSECUTIVE_LOSS in apply_financial_filters(years, CONFIG).failures


def test_two_consecutive_losses_are_not_enough():
    """설정이 3년이다. 2년 적자로 떨어뜨리면 경기 순환 기업이 전멸한다."""
    years = five_years()
    for i in (-2, -1):
        years[i] = fin(years[i].fiscal_year, operating_income=-5.0)

    assert CONSECUTIVE_LOSS not in apply_financial_filters(years, CONFIG).failures


def test_high_debt_is_caught():
    years = five_years()
    years[-1] = fin(2025, total_liabilities=2_100.0, total_equity=1_000.0)  # 210%

    assert HIGH_DEBT in apply_financial_filters(years, CONFIG).failures


def test_debt_exactly_at_the_threshold_passes():
    """200%는 '초과'가 아니다. 경계에서 한 종목이 갈린다."""
    years = five_years()
    years[-1] = fin(2025, total_liabilities=2_000.0, total_equity=1_000.0)  # 200%

    assert HIGH_DEBT not in apply_financial_filters(years, CONFIG).failures


def test_interest_uncovered_for_two_years_is_caught():
    years = five_years()
    for i in (-2, -1):
        years[i] = fin(years[i].fiscal_year, operating_income=5.0, interest_expense=10.0)

    assert INTEREST_UNCOVERED in apply_financial_filters(years, CONFIG).failures


def test_missing_interest_expense_passes_the_coverage_filter():
    """설계서 §8.1: interest_expense가 NULL이면 통과.

    이자비용을 못 구했다는 것은 부채 부담이 크다는 뜻이 아니다.
    계정과목 매핑이 실패한 것뿐이다. 모르는 것으로 떨어뜨리지 않는다.
    """
    years = five_years()
    for i in (-2, -1):
        years[i] = fin(years[i].fiscal_year, operating_income=5.0, interest_expense=None)

    assert INTEREST_UNCOVERED not in apply_financial_filters(years, CONFIG).failures


def test_zero_interest_expense_passes_the_coverage_filter():
    """이자비용 0은 무차입이라는 뜻이다. 가장 건전한 상태다."""
    years = five_years()
    for i in (-2, -1):
        years[i] = fin(years[i].fiscal_year, operating_income=5.0, interest_expense=0.0)

    assert INTEREST_UNCOVERED not in apply_financial_filters(years, CONFIG).failures


def test_revenue_collapse_is_caught():
    years = five_years()
    years[-4] = fin(years[-4].fiscal_year, revenue=1_000.0)
    years[-1] = fin(years[-1].fiscal_year, revenue=400.0)   # -60%

    assert REVENUE_COLLAPSE in apply_financial_filters(years, CONFIG).failures


def test_revenue_halved_exactly_is_caught():
    """-50%는 임계치 '이하'다 (max_revenue_decline_3y: -0.5)."""
    years = five_years()
    years[-4] = fin(years[-4].fiscal_year, revenue=1_000.0)
    years[-1] = fin(years[-1].fiscal_year, revenue=500.0)

    assert REVENUE_COLLAPSE in apply_financial_filters(years, CONFIG).failures


def test_fewer_than_five_years_is_insufficient_history():
    assert INSUFFICIENT_HISTORY in apply_financial_filters(
        [fin(y) for y in (2023, 2024, 2025)], CONFIG
    ).failures


def test_missing_required_field_is_data_insufficient():
    years = five_years()
    years[-1] = fin(2025, revenue=None)

    assert DATA_INSUFFICIENT in apply_financial_filters(years, CONFIG).failures


def test_adverse_audit_opinion_is_caught():
    years = five_years()
    years[-1] = fin(2025, audit_opinion="의견거절")

    assert ADVERSE_AUDIT_OPINION in apply_financial_filters(years, CONFIG).failures


def test_missing_audit_opinion_passes():
    """수집이 안 된 것과 비적정은 다르다."""
    years = five_years()
    years[-1] = fin(2025, audit_opinion=None)

    assert ADVERSE_AUDIT_OPINION not in apply_financial_filters(years, CONFIG).failures


def test_empty_financials_is_insufficient_not_a_crash():
    outcome = apply_financial_filters([], CONFIG)

    assert INSUFFICIENT_HISTORY in outcome.failures
    assert DATA_INSUFFICIENT in outcome.failures


def test_all_failures_are_collected_not_just_the_first():
    """화면이 '왜 제외됐나'를 한 번에 보여줘야 한다.

    사유가 하나만 나오면 사용자가 고치고 다시 돌렸을 때 또 다른 사유로
    걸린다. FilterOutcome이 목록인 이유다.
    """
    years = five_years()
    years[-1] = fin(2025, total_equity=-10.0, total_liabilities=5_000.0,
                    audit_opinion="의견거절")

    failures = apply_financial_filters(years, CONFIG).failures

    assert CAPITAL_IMPAIRED in failures
    assert ADVERSE_AUDIT_OPINION in failures
    assert len(failures) >= 2


def test_financials_may_arrive_unsorted():
    """Repository는 정렬해 주지만, 순수 함수가 그것에 기대면 안 된다."""
    years = list(reversed(five_years()))
    years[0] = fin(2025, total_equity=-10.0)   # 뒤집힌 목록의 첫 원소가 최근 연도

    assert CAPITAL_IMPAIRED in apply_financial_filters(years, CONFIG).failures
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_financial_filters.py`
Expected: FAIL — `ImportError: cannot import name 'apply_financial_filters'`

- [ ] **Step 3: 구현한다**

`src/stockscout/analysis/filters.py`에 더한다. 파일 맨 위 import에 `Financials`와 `HardFilterConfig`를 더한다.

```python
from stockscout.config import HardFilterConfig
from stockscout.models import FilterOutcome, Financials, Security, is_adverse_opinion
```

`is_adverse_opinion`은 Task 3에서 `models.py`에 넣었다. `normalize/kr_mapping.py`에서 import하면 계층 규칙(§5 — `sources`·`normalize`·`analysis`·`store`는 서로를 import하지 않는다)이 깨진다.

```python
CAPITAL_IMPAIRED = "CAPITAL_IMPAIRED"
CONSECUTIVE_LOSS = "CONSECUTIVE_LOSS"
HIGH_DEBT = "HIGH_DEBT"
INTEREST_UNCOVERED = "INTEREST_UNCOVERED"
REVENUE_COLLAPSE = "REVENUE_COLLAPSE"
DATA_INSUFFICIENT = "DATA_INSUFFICIENT"
ADVERSE_AUDIT_OPINION = "ADVERSE_AUDIT_OPINION"

# 매출 급감을 재는 간격. revenue[최근] 대 revenue[3년전]이므로 목록에서
# 4개 연도가 필요하다 (설계서 §8.1).
REVENUE_LOOKBACK_YEARS = 3


def apply_financial_filters(
    financials: list[Financials], config: HardFilterConfig
) -> FilterOutcome:
    """재무제표 기반 하드필터를 적용한다. 설계서 §8.1.

    첫 실패에서 멈추지 않고 모든 사유를 모은다 — apply_price_filters와
    같은 이유다.

    "모르는 것으로 떨어뜨리지 않는다"가 이 함수 전체를 관통하는 규칙이다.
    이자비용이 NULL이면 이자보상배율 필터를 건너뛰고, 감사의견이 NULL이면
    감사의견 필터를 건너뛴다. 계정과목 매핑 실패가 사업 부실로 둔갑하면
    안 된다. 결측 자체는 DATA_INSUFFICIENT가 필수 4항목에 대해서만 잡는다.
    """
    failures: list[str] = []

    # Repository는 연도 오름차순으로 주지만 그것에 기대지 않는다.
    years = sorted(financials, key=lambda f: f.fiscal_year)

    if len(years) < config.min_history_years:
        failures.append(INSUFFICIENT_HISTORY)

    if not years or not years[-1].has_required:
        failures.append(DATA_INSUFFICIENT)

    if not years:
        return FilterOutcome(failures=failures)

    latest = years[-1]

    if latest.total_equity is not None and latest.total_equity <= 0:
        failures.append(CAPITAL_IMPAIRED)

    loss_window = years[-config.consecutive_operating_loss_years:]
    if (
        len(loss_window) == config.consecutive_operating_loss_years
        and all(
            f.operating_income is not None and f.operating_income < 0
            for f in loss_window
        )
    ):
        failures.append(CONSECUTIVE_LOSS)

    # debt_ratio는 자본이 0 이하면 None이다. 그 경우는 CAPITAL_IMPAIRED가
    # 이미 잡았으므로 여기서 또 떨어뜨리지 않는다.
    if latest.debt_ratio is not None and latest.debt_ratio > config.max_debt_ratio:
        failures.append(HIGH_DEBT)

    coverage_window = years[-config.interest_coverage_years:]
    coverages = [
        f.interest_coverage
        for f in coverage_window
        if f.interest_expense not in (None, 0) and f.interest_coverage is not None
    ]
    if (
        len(coverages) == config.interest_coverage_years
        and all(c < config.min_interest_coverage for c in coverages)
    ):
        failures.append(INTEREST_UNCOVERED)

    if len(years) > REVENUE_LOOKBACK_YEARS:
        base = years[-1 - REVENUE_LOOKBACK_YEARS].revenue
        now = latest.revenue
        if base is not None and base > 0 and now is not None:
            if now / base - 1 <= config.max_revenue_decline_3y:
                failures.append(REVENUE_COLLAPSE)

    if is_adverse_opinion(latest.audit_opinion):
        failures.append(ADVERSE_AUDIT_OPINION)

    return FilterOutcome(failures=failures)
```

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_financial_filters.py`
Expected: PASS — `19 passed`

- [ ] **Step 5: 계층 규칙이 깨지지 않았는지 확인한다**

```bash
grep -rn "from stockscout" src/stockscout/analysis/
```

Expected: `stockscout.models`와 `stockscout.config`, 그리고 `analysis` 내부만 나와야 한다. `normalize`·`sources`·`store`·`pipeline`이 보이면 Step 3의 이동을 안 한 것이다.

- [ ] **Step 6: 변이 테스트 3건**

각각 변이를 넣고 **지목된 테스트가 실패하는지** 확인한 뒤 되돌려라. 실패하지 않으면 그 테스트가 무의미하므로 테스트를 고쳐라.

| 변이 | 실패해야 할 테스트 |
|---|---|
| `latest.debt_ratio > config.max_debt_ratio` → `>=` | `test_debt_exactly_at_the_threshold_passes` |
| `f.interest_expense not in (None, 0)` → `True` | `test_missing_interest_expense_passes_the_coverage_filter` |
| `years = sorted(...)` → `years = financials` | `test_financials_may_arrive_unsorted` |

변이 후 `git diff -- src/`로 원복을 확인해라.

- [ ] **Step 7: 커밋**

```bash
git add -A
git commit -m "feat: 재무 하드필터 일곱 개

설계서 §8.1의 자본잠식, 연속 영업적자, 과다 부채, 이자 미충당,
매출 급감, 필수항목 결측, 감사의견 비적정.

모르는 것으로 떨어뜨리지 않는 것이 이 함수를 관통하는 규칙이다.
이자비용이 NULL이면 이자보상배율 필터를 건너뛰고, 감사의견이
NULL이면 감사의견 필터를 건너뛴다. 계정과목 매핑 실패가 사업
부실로 둔갑하면 안 된다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 5: 점수 모델과 구간 평가

**왜:** 설계서 §8.3이 "지표별 `{원값, 획득점수, 만점, 상태}`를 남겨 화면에서 근거를 보여준다"고 못박는다. 총점만 저장하면 §11.2의 점수 분해 막대를 그릴 수 없다.

**Files:**
- Modify: `src/stockscout/models.py`
- Create: `src/stockscout/analysis/scoring.py`
- Test: `tests/test_scoring_bands.py` (신규)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_scoring_bands.py`를 만든다.

```python
"""구간 평가와 점수 모델. 설계서 §8.2·§8.3."""

from stockscout.analysis.scoring import band_score
from stockscout.config import MetricConfig
from stockscout.models import AxisScore, MetricScore, ScoreCard, STATUS_MISSING, STATUS_OK

ROE = MetricConfig(points=10, bands=[(0.15, 10), (0.10, 7), (0.05, 4), (0.0, 1)])
DEBT = MetricConfig(points=10, bands=[(50, 10), (100, 7), (150, 4), (200, 1)],
                    lower_is_better=True)


def test_higher_is_better_takes_the_first_band_the_value_clears():
    assert band_score(0.20, ROE) == 10
    assert band_score(0.15, ROE) == 10      # 경계는 포함이다
    assert band_score(0.12, ROE) == 7
    assert band_score(0.0, ROE) == 1


def test_higher_is_better_scores_zero_below_every_band():
    assert band_score(-0.05, ROE) == 0


def test_lower_is_better_flips_the_comparison():
    assert band_score(30, DEBT) == 10
    assert band_score(50, DEBT) == 10       # 경계는 포함이다
    assert band_score(80, DEBT) == 7
    assert band_score(200, DEBT) == 1


def test_lower_is_better_scores_zero_above_every_band():
    assert band_score(250, DEBT) == 0


def test_infinity_takes_the_top_band():
    """이자비용 0이면 interest_coverage가 inf다. 무차입은 가장 건전하다."""
    coverage = MetricConfig(points=10, bands=[(10, 10), (5, 7), (3, 4), (1, 2)])

    assert band_score(float("inf"), coverage) == 10


def test_metric_with_no_bands_scores_zero():
    """bands가 빈 지표(op_margin_trend 등)는 전용 계산을 쓴다.

    band_score에 실수로 넘기면 0점이 나온다. 조용히 만점을 주는 것보다
    낫다 - 0점은 눈에 띄고 만점은 안 띈다.
    """
    assert band_score(5.0, MetricConfig(points=5)) == 0


def test_axis_points_scale_earned_over_denominator_to_25():
    axis = AxisScore(
        axis="profitability", metrics=[], earned=15.0, denominator=20.0,
        status=STATUS_OK,
    )

    assert axis.points == 18.75


def test_axis_points_are_zero_when_the_denominator_is_zero():
    axis = AxisScore(axis="cashflow", metrics=[], earned=0.0, denominator=0.0,
                     status=STATUS_OK)

    assert axis.points == 0.0


def test_scorecard_total_is_global_not_an_average_of_axes():
    """설계서 §8.3: (획득 합계 / 유효만점 합계) x 100.

    축 점수를 평균내면 안 된다. 유효만점이 작은 축이 과대 대표된다.
    """
    card = ScoreCard(axes=[
        AxisScore(axis="a", metrics=[], earned=20.0, denominator=25.0, status=STATUS_OK),
        AxisScore(axis="b", metrics=[], earned=5.0, denominator=10.0, status=STATUS_OK),
    ])

    assert card.total == 25.0 / 35.0 * 100


def test_scorecard_total_is_zero_when_nothing_is_measurable():
    assert ScoreCard(axes=[]).total == 0.0


def test_metric_score_carries_its_raw_value_for_the_screen():
    m = MetricScore(metric="roe_avg3", value=0.123, points=7.0, max_points=10.0,
                    status=STATUS_OK)

    assert m.value == 0.123
    assert m.status == STATUS_OK


def test_missing_metric_keeps_its_max_points_for_the_screen():
    """유효만점 계산에서는 빠지지만, 화면은 '10점 중 측정 불가'를 보여야 한다."""
    m = MetricScore(metric="fcf_positive_years", value=None, points=0.0,
                    max_points=5.0, status=STATUS_MISSING)

    assert m.max_points == 5.0
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_scoring_bands.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'stockscout.analysis.scoring'`

- [ ] **Step 3: 모델을 더한다**

`src/stockscout/models.py`의 `AnalysisResult` **앞**에 넣는다.

```python
# 지표 상태 (설계서 §8.2·§8.3)
STATUS_OK = "OK"
STATUS_MISSING = "MISSING"                            # 데이터가 없어 계산 불가
STATUS_BASE_NEGATIVE = "BASE_NEGATIVE"                # CAGR 기저가 0 이하
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
    # 이 축의 배점 합. 설정에서 온다 - 상수 AXIS_MAX를 쓰지 않는 이유는
    # 사용자가 YAML에서 배점을 바꿀 수 있기 때문이다.
    max_points: float = AXIS_MAX

    @property
    def points(self) -> float:
        """화면 표시용 0~배점 점수."""
        if self.denominator <= 0:
            return 0.0
        return self.earned / self.denominator * self.max_points


class ScoreCard(BaseModel):
    axes: list[AxisScore]

    @property
    def total(self) -> float:
        """설계서 §8.3: (획득점수 합계 / 유효만점 합계) x 100."""
        denominator = sum(a.denominator for a in self.axes)
        if denominator <= 0:
            return 0.0
        return sum(a.earned for a in self.axes) / denominator * 100
```

- [ ] **Step 4: `band_score`를 만든다**

`src/stockscout/analysis/scoring.py`를 만든다.

```python
"""4축 스코어링. 설계서 §8.2·§8.3.

순수 함수다. 네트워크도 DB도 모른다. 배점과 구간은 전부 설정(config)에서
오고 이 파일에는 숫자가 없다 - 사용자가 YAML만 고쳐 기준을 바꿀 수 있어야
한다.
"""

from __future__ import annotations

from stockscout.config import MetricConfig

def band_score(value: float, metric: MetricConfig) -> float:
    """구간표에서 점수를 찾는다. 위에서부터 첫 매치를 취한다.

    bands가 비어 있으면 0을 돌려준다. 전용 계산이 필요한 지표
    (op_margin_trend, growth_consistency)를 실수로 여기에 넘겼을 때
    조용히 만점이 나가는 것보다 0점이 낫다 - 0점은 눈에 띈다.
    """
    for threshold, points in metric.bands:
        if metric.lower_is_better:
            if value <= threshold:
                return points
        elif value >= threshold:
            return points
    return 0.0
```

- [ ] **Step 5: 테스트가 통과하는지 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_scoring_bands.py`
Expected: PASS — `12 passed`

- [ ] **Step 6: 커밋**

```bash
git add -A
git commit -m "feat: 점수 모델과 구간 평가

설계서 §8.3이 지표별 {원값, 획득점수, 만점, 상태}를 요구한다.
총점만 저장하면 §11.2의 점수 분해 막대를 그릴 수 없다.

AxisScore가 earned/denominator를 그대로 들고 다닌다. 총점은 축
점수의 평균이 아니라 전체 획득을 전체 유효만점으로 나눈 값이라,
축에서 25점으로 환산해 버리면 그 계산을 할 수 없다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 6: 지표 12개

**왜:** 설계서 §8.2의 4축 × 3지표다. 각 지표는 `list[Financials]`(연도 오름차순)를 받아 `MetricScore` 하나를 낸다.

**Files:**
- Modify: `src/stockscout/analysis/scoring.py`
- Test: `tests/test_scoring_metrics.py` (신규)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_scoring_metrics.py`를 만든다. `fin`·`five_years` 헬퍼는 `tests/test_financial_filters.py`에서 그대로 복사해 온다 (두 파일이 독립적으로 읽히는 편이 낫다).

```python
"""지표 12개. 설계서 §8.2."""

import math

from stockscout.analysis.scoring import (
    metric_current_ratio,
    metric_debt_ratio,
    metric_fcf_positive_years,
    metric_growth_consistency,
    metric_interest_coverage,
    metric_ocf_positive_years,
    metric_ocf_to_op_income,
    metric_op_income_cagr3,
    metric_op_margin_avg3,
    metric_op_margin_trend,
    metric_revenue_cagr3,
    metric_roe_avg3,
)
from stockscout.config import MetricConfig
from stockscout.models import (
    Financials,
    STATUS_BASE_NEGATIVE,
    STATUS_DENOMINATOR_NONPOSITIVE,
    STATUS_MISSING,
    STATUS_OK,
)


def fin(year, **kwargs):
    base = dict(
        ticker="005930.KS", fiscal_year=year, currency="KRW",
        revenue=1_000.0, operating_income=100.0, net_income=80.0,
        total_assets=2_000.0, total_liabilities=500.0, total_equity=1_000.0,
        current_assets=800.0, current_liabilities=400.0,
        interest_expense=10.0, operating_cash_flow=120.0, capex=30.0,
    )
    base.update(kwargs)
    return Financials(**base)


def five_years(**kwargs):
    return [fin(y, **kwargs) for y in range(2021, 2026)]


TEN = MetricConfig(points=10, bands=[(0.15, 10), (0.10, 7), (0.05, 4), (0.0, 1)])
FIVE = MetricConfig(points=5)


# --- 수익성 ---------------------------------------------------------

def test_roe_avg3_averages_the_last_three_years():
    years = five_years()
    for i, ni in zip((-3, -2, -1), (100.0, 200.0, 300.0)):
        years[i] = fin(years[i].fiscal_year, net_income=ni, total_equity=1_000.0)

    score = metric_roe_avg3(years, TEN)

    assert math.isclose(score.value, 0.20)     # (0.1 + 0.2 + 0.3) / 3
    assert score.points == 10
    assert score.status == STATUS_OK


def test_roe_avg3_needs_at_least_two_years():
    years = [fin(2025, net_income=None)] * 1

    assert metric_roe_avg3(years, TEN).status == STATUS_MISSING


def test_roe_avg3_ignores_years_whose_roe_is_none():
    years = five_years()
    years[-1] = fin(2025, net_income=None)

    assert metric_roe_avg3(years, TEN).status == STATUS_OK


def test_op_margin_trend_rewards_a_rising_margin():
    """기울기 단위는 연당 %p다. 0.5%p/년 초과면 만점."""
    years = five_years()
    for i, rev, op in zip((-3, -2, -1), (1000.0, 1000.0, 1000.0), (50.0, 100.0, 150.0)):
        years[i] = fin(years[i].fiscal_year, revenue=rev, operating_income=op)

    score = metric_op_margin_trend(years, FIVE)

    assert score.value > 0.5      # 5%p/년
    assert score.points == 5


def test_op_margin_trend_penalises_a_falling_margin():
    years = five_years()
    for i, op in zip((-3, -2, -1), (150.0, 100.0, 50.0)):
        years[i] = fin(years[i].fiscal_year, revenue=1000.0, operating_income=op)

    score = metric_op_margin_trend(years, FIVE)

    assert score.value < -0.5
    assert score.points == 0


def test_op_margin_trend_gives_partial_credit_for_flat():
    years = five_years()   # 전 연도 동일 마진

    score = metric_op_margin_trend(years, FIVE)

    assert math.isclose(score.value, 0.0, abs_tol=1e-9)
    assert score.points == 2


def test_op_margin_trend_needs_three_years():
    assert metric_op_margin_trend([fin(2025)], FIVE).status == STATUS_MISSING


# --- 성장성 ---------------------------------------------------------

def test_revenue_cagr3_uses_the_year_three_back():
    years = five_years()
    years[-4] = fin(years[-4].fiscal_year, revenue=1_000.0)
    years[-1] = fin(years[-1].fiscal_year, revenue=1_331.0)   # 10%/년 x 3

    score = metric_revenue_cagr3(years, TEN)

    assert math.isclose(score.value, 0.10, abs_tol=1e-9)


def test_revenue_cagr3_needs_four_years():
    assert metric_revenue_cagr3([fin(y) for y in (2023, 2024, 2025)],
                                TEN).status == STATUS_MISSING


def test_op_income_cagr3_records_base_negative_and_scores_zero():
    """설계서 §8.2: 기저가 음수면 CAGR이 정의되지 않는다.

    0점 처리하되 유효만점에는 남긴다 - 3년 전 적자는 데이터 공백이
    아니라 사업 사실이다.
    """
    years = five_years()
    years[-4] = fin(years[-4].fiscal_year, operating_income=-50.0)

    score = metric_op_income_cagr3(years, TEN)

    assert score.status == STATUS_BASE_NEGATIVE
    assert score.points == 0
    assert score.max_points == 10


def test_growth_consistency_counts_rising_years():
    years = [fin(2021, revenue=100.0), fin(2022, revenue=110.0),
             fin(2023, revenue=120.0), fin(2024, revenue=115.0),
             fin(2025, revenue=130.0)]

    score = metric_growth_consistency(years, FIVE)

    assert math.isclose(score.value, 3 / 4)
    assert math.isclose(score.points, 3.75)


def test_growth_consistency_needs_three_comparisons():
    years = [fin(2024, revenue=100.0), fin(2025, revenue=110.0)]

    assert metric_growth_consistency(years, FIVE).status == STATUS_MISSING


# --- 안정성 ---------------------------------------------------------

def test_debt_ratio_uses_the_latest_year():
    years = five_years()
    years[-1] = fin(2025, total_liabilities=400.0, total_equity=1_000.0)   # 40%
    cfg = MetricConfig(points=10, bands=[(50, 10), (100, 7)], lower_is_better=True)

    score = metric_debt_ratio(years, cfg)

    assert math.isclose(score.value, 40.0)
    assert score.points == 10


def test_current_ratio_is_a_ratio_not_a_percentage():
    """설정의 구간이 [2.0, 1.5, 1.0]이다. 200이 아니라 2.0이다."""
    years = five_years()
    years[-1] = fin(2025, current_assets=900.0, current_liabilities=400.0)
    cfg = MetricConfig(points=5, bands=[(2.0, 5), (1.5, 3.5), (1.0, 2)])

    score = metric_current_ratio(years, cfg)

    assert math.isclose(score.value, 2.25)
    assert score.points == 5


def test_interest_coverage_scores_full_when_there_is_no_debt():
    years = five_years()
    years[-1] = fin(2025, interest_expense=0.0)
    cfg = MetricConfig(points=10, bands=[(10, 10), (5, 7), (3, 4), (1, 2)])

    score = metric_interest_coverage(years, cfg)

    assert score.points == 10
    assert score.status == STATUS_OK


def test_interest_coverage_is_missing_when_the_expense_is_unknown():
    years = five_years()
    years[-1] = fin(2025, interest_expense=None)
    cfg = MetricConfig(points=10, bands=[(10, 10)])

    assert metric_interest_coverage(years, cfg).status == STATUS_MISSING


# --- 현금흐름 -------------------------------------------------------

def test_ocf_positive_years_counts_consecutively_from_the_latest():
    years = five_years()
    years[-3] = fin(years[-3].fiscal_year, operating_cash_flow=-10.0)
    cfg = MetricConfig(points=10, bands=[(5, 10), (4, 8), (3, 5), (2, 3)])

    score = metric_ocf_positive_years(years, cfg)

    assert score.value == 2       # 최근 2년만 연속 흑자
    assert score.points == 3


def test_ocf_positive_years_is_missing_when_no_year_has_data():
    years = five_years(operating_cash_flow=None)

    assert metric_ocf_positive_years(
        years, MetricConfig(points=10, bands=[(5, 10)])
    ).status == STATUS_MISSING


def test_ocf_to_op_income_uses_three_year_totals():
    years = five_years()
    for i in (-3, -2, -1):
        years[i] = fin(years[i].fiscal_year, operating_cash_flow=120.0,
                       operating_income=100.0)
    cfg = MetricConfig(points=10, bands=[(1.2, 10), (1.0, 8), (0.8, 5), (0.5, 2)])

    score = metric_ocf_to_op_income(years, cfg)

    assert math.isclose(score.value, 1.2)
    assert score.points == 10


def test_ocf_to_op_income_records_a_nonpositive_denominator():
    """설계서 §8.2: 3년 누계 영업이익이 0 이하면 비율이 무의미하다."""
    years = five_years()
    for i in (-3, -2, -1):
        years[i] = fin(years[i].fiscal_year, operating_income=-100.0)
    cfg = MetricConfig(points=10, bands=[(1.2, 10)])

    score = metric_ocf_to_op_income(years, cfg)

    assert score.status == STATUS_DENOMINATOR_NONPOSITIVE
    assert score.points == 0
    assert score.max_points == 10


def test_fcf_positive_years_subtracts_capex():
    years = five_years()
    for i in (-2, -1):
        years[i] = fin(years[i].fiscal_year, operating_cash_flow=100.0, capex=150.0)
    cfg = MetricConfig(points=5, bands=[(3, 5), (2, 3), (1, 1)])

    score = metric_fcf_positive_years(years, cfg)

    assert score.value == 0
    assert score.points == 0


def test_fcf_positive_years_is_missing_without_capex():
    years = five_years(capex=None)

    assert metric_fcf_positive_years(
        years, MetricConfig(points=5, bands=[(3, 5)])
    ).status == STATUS_MISSING
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_scoring_metrics.py`
Expected: FAIL — `ImportError`

- [ ] **Step 3: 구현한다**

`src/stockscout/analysis/scoring.py`에 더한다.

```python
from stockscout.models import (
    AXIS_MAX,
    AxisScore,
    Financials,
    MetricScore,
    ScoreCard,
    STATUS_BASE_NEGATIVE,
    STATUS_DENOMINATOR_NONPOSITIVE,
    STATUS_MISSING,
    STATUS_OK,
)

CAGR_YEARS = 3               # 설계서 §8.2의 "3년 CAGR"
AVG_YEARS = 3                # "3년 평균"
TREND_YEARS = 3              # "최근 3년 추세"
CONSISTENCY_COMPARISONS = 4  # "최근 4년 중 증가한 해"
STREAK_YEARS = 5             # 흑자 연속년수를 세는 최대 구간
MIN_AVG_YEARS = 2            # 평균을 낼 최소 표본
MIN_CONSISTENCY_COMPARISONS = 3
TREND_FLAT_BAND = 0.5        # %p/년. 이 안쪽은 보합으로 본다
TREND_FLAT_POINTS_RATIO = 0.4  # 보합은 배점의 40% (5점 중 2점)


def _missing(name: str, cfg: MetricConfig) -> MetricScore:
    return MetricScore(metric=name, value=None, points=0.0,
                       max_points=cfg.points, status=STATUS_MISSING)


def _scored(name: str, value: float, cfg: MetricConfig,
            points: float | None = None, status: str = STATUS_OK) -> MetricScore:
    return MetricScore(
        metric=name, value=value,
        points=band_score(value, cfg) if points is None else points,
        max_points=cfg.points, status=status,
    )


def _latest(years: list[Financials]) -> Financials | None:
    return years[-1] if years else None


# --- 수익성 ---------------------------------------------------------

def metric_roe_avg3(years: list[Financials], cfg: MetricConfig) -> MetricScore:
    values = [f.roe for f in years[-AVG_YEARS:] if f.roe is not None]
    if len(values) < MIN_AVG_YEARS:
        return _missing("roe_avg3", cfg)
    return _scored("roe_avg3", sum(values) / len(values), cfg)


def metric_op_margin_avg3(years: list[Financials], cfg: MetricConfig) -> MetricScore:
    values = [f.operating_margin for f in years[-AVG_YEARS:]
              if f.operating_margin is not None]
    if len(values) < MIN_AVG_YEARS:
        return _missing("op_margin_avg3", cfg)
    return _scored("op_margin_avg3", sum(values) / len(values), cfg)


def metric_op_margin_trend(years: list[Financials], cfg: MetricConfig) -> MetricScore:
    """최근 3년 영업이익률의 선형회귀 기울기 (연당 %p).

    bands를 쓰지 않는다. 설계서 §8.2가 보합 구간을 따로 정의하기 때문에
    구간표로 표현되지 않는다.
    """
    margins = [f.operating_margin for f in years[-TREND_YEARS:]]
    if len(margins) < TREND_YEARS or any(m is None for m in margins):
        return _missing("op_margin_trend", cfg)

    n = len(margins)
    mean_x = (n - 1) / 2
    mean_y = sum(margins) / n
    numerator = sum((i - mean_x) * (m - mean_y) for i, m in enumerate(margins))
    denominator = sum((i - mean_x) ** 2 for i in range(n))
    slope_pp = numerator / denominator * 100   # 비율 -> %p

    if slope_pp > TREND_FLAT_BAND:
        points = cfg.points
    elif slope_pp >= -TREND_FLAT_BAND:
        points = cfg.points * TREND_FLAT_POINTS_RATIO
    else:
        points = 0.0
    return _scored("op_margin_trend", slope_pp, cfg, points=points)


# --- 성장성 ---------------------------------------------------------

def _cagr(years: list[Financials], field: str, name: str,
          cfg: MetricConfig) -> MetricScore:
    if len(years) <= CAGR_YEARS:
        return _missing(name, cfg)
    base = getattr(years[-1 - CAGR_YEARS], field)
    now = getattr(years[-1], field)
    if base is None or now is None:
        return _missing(name, cfg)
    if base <= 0:
        # 기저가 음수·0이면 CAGR이 정의되지 않는다. 0점 처리하되 유효만점에는
        # 남긴다 - 3년 전 적자는 데이터 공백이 아니라 사업 사실이다.
        return MetricScore(metric=name, value=None, points=0.0,
                           max_points=cfg.points, status=STATUS_BASE_NEGATIVE)
    return _scored(name, (now / base) ** (1 / CAGR_YEARS) - 1, cfg)


def metric_revenue_cagr3(years: list[Financials], cfg: MetricConfig) -> MetricScore:
    return _cagr(years, "revenue", "revenue_cagr3", cfg)


def metric_op_income_cagr3(years: list[Financials], cfg: MetricConfig) -> MetricScore:
    return _cagr(years, "operating_income", "op_income_cagr3", cfg)


def metric_growth_consistency(years: list[Financials],
                              cfg: MetricConfig) -> MetricScore:
    """최근 4년 중 매출이 전년 대비 증가한 해의 비율.

    설계서 §8.2는 "증가한 해의 수 x 1.25"라고 쓰지만, 비교 가능한 해가
    4년보다 적을 수 있으므로 비율로 계산해 배점을 곱한다. 4년이 전부
    있으면 결과가 같다.
    """
    window = years[-(CONSISTENCY_COMPARISONS + 1):]
    pairs = [
        (prev.revenue, curr.revenue)
        for prev, curr in zip(window, window[1:])
        if prev.revenue is not None and curr.revenue is not None
    ]
    if len(pairs) < MIN_CONSISTENCY_COMPARISONS:
        return _missing("growth_consistency", cfg)
    ratio = sum(1 for prev, curr in pairs if curr > prev) / len(pairs)
    return _scored("growth_consistency", ratio, cfg, points=ratio * cfg.points)


# --- 안정성 ---------------------------------------------------------

def metric_debt_ratio(years: list[Financials], cfg: MetricConfig) -> MetricScore:
    latest = _latest(years)
    if latest is None or latest.debt_ratio is None:
        return _missing("debt_ratio", cfg)
    return _scored("debt_ratio", latest.debt_ratio, cfg)


def metric_current_ratio(years: list[Financials], cfg: MetricConfig) -> MetricScore:
    latest = _latest(years)
    if latest is None or latest.current_ratio is None:
        return _missing("current_ratio", cfg)
    return _scored("current_ratio", latest.current_ratio, cfg)


def metric_interest_coverage(years: list[Financials],
                             cfg: MetricConfig) -> MetricScore:
    latest = _latest(years)
    if latest is None or latest.interest_coverage is None:
        return _missing("interest_coverage", cfg)
    # interest_coverage는 이자비용이 0이면 inf다. band_score가 첫 구간을
    # 집으므로 자연히 만점이 된다.
    return _scored("interest_coverage", latest.interest_coverage, cfg)


# --- 현금흐름 -------------------------------------------------------

def _positive_streak(values: list[float | None]) -> int:
    """뒤에서부터 연속으로 0보다 큰 값의 개수. None을 만나면 멈춘다."""
    streak = 0
    for value in reversed(values):
        if value is None or value <= 0:
            break
        streak += 1
    return streak


def metric_ocf_positive_years(years: list[Financials],
                              cfg: MetricConfig) -> MetricScore:
    window = years[-STREAK_YEARS:]
    if all(f.operating_cash_flow is None for f in window):
        return _missing("ocf_positive_years", cfg)
    streak = _positive_streak([f.operating_cash_flow for f in window])
    return _scored("ocf_positive_years", float(streak), cfg)


def metric_ocf_to_op_income(years: list[Financials],
                            cfg: MetricConfig) -> MetricScore:
    window = years[-AVG_YEARS:]
    ocf = [f.operating_cash_flow for f in window]
    op = [f.operating_income for f in window]
    if len(window) < AVG_YEARS or any(v is None for v in ocf + op):
        return _missing("ocf_to_op_income", cfg)
    denominator = sum(op)
    if denominator <= 0:
        return MetricScore(metric="ocf_to_op_income", value=None, points=0.0,
                           max_points=cfg.points,
                           status=STATUS_DENOMINATOR_NONPOSITIVE)
    return _scored("ocf_to_op_income", sum(ocf) / denominator, cfg)


def metric_fcf_positive_years(years: list[Financials],
                              cfg: MetricConfig) -> MetricScore:
    window = years[-STREAK_YEARS:]
    if all(f.free_cash_flow is None for f in window):
        return _missing("fcf_positive_years", cfg)
    streak = _positive_streak([f.free_cash_flow for f in window])
    return _scored("fcf_positive_years", float(streak), cfg)
```

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_scoring_metrics.py`
Expected: PASS — `21 passed`

- [ ] **Step 5: 변이 테스트 3건**

| 변이 | 실패해야 할 테스트 |
|---|---|
| `_cagr`의 `years[-1 - CAGR_YEARS]` → `years[-CAGR_YEARS]` | `test_revenue_cagr3_uses_the_year_three_back` |
| `_positive_streak`의 `break` → `continue` | `test_ocf_positive_years_counts_consecutively_from_the_latest` |
| `if base <= 0` → `if base < 0` | (새 테스트가 필요하다. 기저가 정확히 0일 때 `BASE_NEGATIVE`가 나오는지 보는 테스트를 추가해라) |

세 번째는 기존 테스트가 잡지 못한다. 아래를 추가하고 나서 변이를 넣어라.

```python
def test_cagr_base_of_exactly_zero_is_base_negative():
    """0으로 나누면 터진다. 음수만 막으면 이 줄에서 ZeroDivisionError가 난다."""
    years = five_years()
    years[-4] = fin(years[-4].fiscal_year, revenue=0.0)

    assert metric_revenue_cagr3(years, TEN).status == STATUS_BASE_NEGATIVE
```

변이 후 `git diff -- src/`로 원복을 확인해라.

- [ ] **Step 6: 커밋**

```bash
git add -A
git commit -m "feat: 4축 스코어링 지표 12개

설계서 §8.2. 배점과 구간은 전부 설정에서 오고 이 파일에는 숫자가
없다 - 사용자가 YAML만 고쳐 기준을 바꿀 수 있어야 한다.

계산 불가와 사업 사실을 구분한다. 계정과목을 못 찾은 것은 MISSING이라
유효만점에서 빠지고, 3년 전이 적자라 CAGR이 정의되지 않는 것은
BASE_NEGATIVE라 0점이되 만점에는 남는다. 전자는 데이터 공백이고
후자는 사업 사실이다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 7: 축 조립과 환산 — §8.3의 하한이 실제로 무는지

**왜:** 이 계획에서 가장 틀리기 쉬운 곳이다. 설계서 §8.3에는 규칙이 둘 있고 서로 반대 방향으로 당긴다.

1. **환산** — 못 구한 지표는 획득점수와 유효만점 **양쪽에서** 뺀다. 데이터 공백이 사업 부실로 둔갑하지 않게
2. **축 하한** — 한 축의 유효만점이 배점의 절반 미만이면 그 축을 0점 처리한다. "볼 수 없다"가 면제가 되지 않게

**하한이 실제로 물려면 그 축의 배점을 분모에 그대로 남겨야 한다.** 분모에서도 빼면 0점 처리가 아무 효과가 없다 — 0/0이라 총점이 그대로다. 이것이 Step 1의 핵심 테스트다.

**Files:**
- Modify: `src/stockscout/analysis/scoring.py`
- Test: `tests/test_scoring_axes.py` (신규)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_scoring_axes.py`를 만든다.

```python
"""축 조립과 §8.3 환산."""

import math

import pytest

from stockscout.analysis.scoring import score_security
from stockscout.config import load_config
from stockscout.models import (
    AXIS_UNMEASURABLE,
    Financials,
    STATUS_MISSING,
    STATUS_OK,
)

CONFIG = load_config().scoring   # 실제 config/settings.yaml을 쓴다


def fin(year, **kwargs):
    base = dict(
        ticker="005930.KS", fiscal_year=year, currency="KRW",
        revenue=1_000.0, operating_income=100.0, net_income=80.0,
        total_assets=2_000.0, total_liabilities=500.0, total_equity=1_000.0,
        current_assets=800.0, current_liabilities=400.0,
        interest_expense=10.0, operating_cash_flow=120.0, capex=30.0,
    )
    base.update(kwargs)
    return Financials(**base)


def five_years(**kwargs):
    return [fin(y, **kwargs) for y in range(2021, 2026)]


def axis(card, name):
    return next(a for a in card.axes if a.axis == name)


def test_a_complete_company_scores_out_of_a_hundred():
    card = score_security(five_years(), CONFIG)

    assert len(card.axes) == 4
    assert all(a.status == STATUS_OK for a in card.axes)
    assert math.isclose(sum(a.denominator for a in card.axes), 100.0)
    assert 0 <= card.total <= 100


def test_one_missing_metric_shrinks_the_denominator():
    """설계서 §8.3: capex 결측이면 FCF 지표(5점)가 유효만점에서 빠진다."""
    card = score_security(five_years(capex=None), CONFIG)

    cashflow = axis(card, "cashflow")

    assert cashflow.denominator == 20.0          # 25 - 5
    assert cashflow.status == STATUS_OK
    fcf = next(m for m in cashflow.metrics if m.metric == "fcf_positive_years")
    assert fcf.status == STATUS_MISSING
    assert fcf.max_points == 5.0                 # 화면은 "5점 중 측정 불가"를 보여야 한다


def test_an_axis_that_loses_most_of_its_points_is_zeroed_not_prorated():
    """§8.3의 축 하한. OCF가 없으면 현금흐름 25점 중 20점이 사라진다.

    남는 것은 FCF 5점뿐이라 유효만점이 배점의 절반 미만이다. 환산하면
    80점 만점으로 후하게 채점되고, "현금흐름을 볼 수 없다"가 감점이
    아니라 면제가 된다.
    """
    card = score_security(five_years(operating_cash_flow=None), CONFIG)

    cashflow = axis(card, "cashflow")

    assert cashflow.status == AXIS_UNMEASURABLE
    assert cashflow.earned == 0.0
    assert cashflow.points == 0.0


def test_a_zeroed_axis_keeps_its_full_weight_in_the_denominator():
    """하한이 실제로 무는 곳이다.

    분모에서도 25점을 빼면 0점 처리가 아무 효과가 없다 - 총점이
    그대로다. 이 테스트가 그것을 막는다.
    """
    complete = score_security(five_years(), CONFIG)
    no_ocf = score_security(five_years(operating_cash_flow=None), CONFIG)

    assert axis(no_ocf, "cashflow").denominator == 25.0
    assert no_ocf.total < complete.total


def test_the_floor_bites_exactly_at_half():
    """절반은 살아남는다. '미만'이지 '이하'가 아니다.

    현금흐름 축에서 OCF÷영업이익(10점)과 FCF(5점)를 잃으면 10점이
    남는다. 25의 절반은 12.5이므로 10 < 12.5 - 하한에 걸린다.
    반대로 OCF÷영업이익(10점)만 잃으면 15점이 남아 살아남는다.
    """
    lost_ten = score_security(five_years(capex=None), CONFIG)
    assert axis(lost_ten, "cashflow").status == STATUS_OK      # 20점 남음

    # 유동비율(5점)만 잃은 안정성 축은 20점이 남아 살아남는다
    no_current = score_security(five_years(current_assets=None), CONFIG)
    assert axis(no_current, "stability").status == STATUS_OK


def test_base_negative_keeps_its_points_in_the_denominator():
    """0점 처리와 측정 불가는 다르다.

    3년 전 영업적자는 데이터 공백이 아니라 사업 사실이다. 유효만점에서
    빼주면 적자 기업이 오히려 후하게 채점된다.
    """
    years = five_years()
    years[-4] = fin(years[-4].fiscal_year, operating_income=-50.0)

    growth = axis(score_security(years, CONFIG), "growth")

    assert growth.denominator == 25.0
    assert growth.status == STATUS_OK


def test_total_is_zero_when_there_are_no_financials():
    card = score_security([], CONFIG)

    assert card.total == 0.0
    assert all(a.status == AXIS_UNMEASURABLE for a in card.axes)


def test_unknown_metric_in_config_is_an_error_not_a_silent_zero():
    """설정 오타로 지표가 조용히 사라지면 아무도 모른다."""
    broken = {"profitability": {"roe_avg9": CONFIG["profitability"]["roe_avg3"]}}

    with pytest.raises(ValueError, match="roe_avg9"):
        score_security(five_years(), broken)


def test_unknown_axis_in_config_is_an_error():
    broken = {"vibes": {"roe_avg3": CONFIG["profitability"]["roe_avg3"]}}

    with pytest.raises(ValueError, match="vibes"):
        score_security(five_years(), broken)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_scoring_axes.py`
Expected: FAIL — `ImportError: cannot import name 'score_security'`

- [ ] **Step 3: 구현한다**

`src/stockscout/analysis/scoring.py` 맨 끝에 더한다.

```python
# 축 이름 -> 지표 이름 -> 계산 함수. 설정의 scoring 섹션 구조와 맞물린다.
METRIC_FUNCTIONS = {
    "profitability": {
        "roe_avg3": metric_roe_avg3,
        "op_margin_avg3": metric_op_margin_avg3,
        "op_margin_trend": metric_op_margin_trend,
    },
    "growth": {
        "revenue_cagr3": metric_revenue_cagr3,
        "op_income_cagr3": metric_op_income_cagr3,
        "growth_consistency": metric_growth_consistency,
    },
    "stability": {
        "debt_ratio": metric_debt_ratio,
        "current_ratio": metric_current_ratio,
        "interest_coverage": metric_interest_coverage,
    },
    "cashflow": {
        "ocf_positive_years": metric_ocf_positive_years,
        "ocf_to_op_income": metric_ocf_to_op_income,
        "fcf_positive_years": metric_fcf_positive_years,
    },
}

# 축 하한 (설계서 §8.3). 유효만점이 배점의 이 비율 미만이면 축을 0점 처리한다.
AXIS_FLOOR_RATIO = 0.5


def score_security(
    financials: list[Financials],
    scoring: dict[str, dict[str, MetricConfig]],
) -> ScoreCard:
    """재무 목록을 4축 100점으로 채점한다. 설계서 §8.2·§8.3.

    두 규칙이 서로 반대로 당긴다.

    환산: 못 구한 지표는 획득점수와 유효만점 양쪽에서 뺀다. 계정과목
    매핑 실패가 사업 부실로 둔갑하지 않게 하려는 것이다.

    축 하한: 한 축의 유효만점이 배점의 절반 미만이면 환산하지 않고 그
    축을 0점 처리한다. 이때 **배점 전액을 분모에 남긴다** - 분모에서도
    빼면 0점 처리가 아무 효과가 없어진다(0/0이라 총점이 그대로다).
    "볼 수 없다"가 면제가 아니라 감점이 되어야 한다.
    """
    years = sorted(financials, key=lambda f: f.fiscal_year)

    axes: list[AxisScore] = []
    for axis_name, metrics_config in scoring.items():
        if axis_name not in METRIC_FUNCTIONS:
            raise ValueError(
                f"설정의 scoring에 모르는 축이 있습니다: {axis_name} "
                f"(가능: {sorted(METRIC_FUNCTIONS)})"
            )
        known = METRIC_FUNCTIONS[axis_name]

        scores: list[MetricScore] = []
        for metric_name, cfg in metrics_config.items():
            if metric_name not in known:
                raise ValueError(
                    f"설정의 scoring.{axis_name}에 모르는 지표가 있습니다: "
                    f"{metric_name} (가능: {sorted(known)})"
                )
            scores.append(known[metric_name](years, cfg))

        axis_max = sum(m.max_points for m in scores)
        measurable = [m for m in scores if m.status != STATUS_MISSING]
        effective_max = sum(m.max_points for m in measurable)

        if effective_max < axis_max * AXIS_FLOOR_RATIO:
            axes.append(AxisScore(
                axis=axis_name, metrics=scores,
                earned=0.0, denominator=axis_max, max_points=axis_max,
                status=AXIS_UNMEASURABLE,
            ))
        else:
            axes.append(AxisScore(
                axis=axis_name, metrics=scores,
                earned=sum(m.points for m in measurable),
                denominator=effective_max, max_points=axis_max,
                status=STATUS_OK,
            ))

    return ScoreCard(axes=axes)
```

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_scoring_axes.py`
Expected: PASS — `10 passed`

`test_total_is_zero_when_there_are_no_financials`가 실패하면 빈 목록에서 `axis_max`가 0이 되어 `0 < 0 * 0.5`가 거짓이 되는 경우다. 그때는 `effective_max < axis_max * AXIS_FLOOR_RATIO` 대신 `axis_max <= 0 or effective_max < axis_max * AXIS_FLOOR_RATIO`로 고쳐라. 다만 지표 12개가 전부 MISSING이면 `axis_max`는 25로 남으므로 이 경로는 정상적으로 하한에 걸린다 — 먼저 실제로 실패하는지 확인하고 고쳐라.

- [ ] **Step 5: 변이 테스트 — 이 계획에서 가장 중요한 하나**

`denominator=axis_max`를 `denominator=effective_max`로 바꿔라 (축 하한 분기 쪽).

Run: `.venv/Scripts/python.exe -m pytest tests/test_scoring_axes.py::test_a_zeroed_axis_keeps_its_full_weight_in_the_denominator`
Expected: **FAIL**

이 변이가 통과하면 §8.3의 하한이 아무 일도 하지 않는 것이고, 현금흐름을 못 구한 기업이 그대로 후하게 채점된다. 통과했다면 테스트를 고쳐라.

두 번째 변이: `AXIS_FLOOR_RATIO = 0.5` → `0.0`.
Expected: `test_an_axis_that_loses_most_of_its_points_is_zeroed_not_prorated`가 **FAIL**

둘 다 확인 후 되돌리고 `git diff -- src/`로 원복을 확인해라.

- [ ] **Step 6: 커밋**

```bash
git add -A
git commit -m "feat: 축 조립과 §8.3 환산

두 규칙이 서로 반대로 당긴다. 환산은 못 구한 지표를 획득과 만점
양쪽에서 빼서 데이터 공백이 사업 부실로 둔갑하지 않게 한다. 축
하한은 한 축이 통째로 사라진 기업이 오히려 후하게 채점되는 것을
막는다.

하한이 실제로 물려면 0점 처리한 축의 배점을 분모에 그대로 남겨야
한다. 분모에서도 빼면 0/0이라 총점이 그대로다 - 규칙이 있으나
마나가 된다. 테스트가 그것을 고정한다.

설정에 모르는 축·지표 이름이 있으면 ValueError를 던진다. 오타로
지표가 조용히 사라지면 아무도 모른다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 8: 등급 판정 — ★★/★

**왜:** 설계서 §9.3. 점수와 하락이 **둘 다** 있어야 등급이 나온다. 아무리 많이 떨어져도 우량주가 아니면 등급이 없다.

**Files:**
- Create: `src/stockscout/analysis/grading.py`
- Test: `tests/test_grading.py` (신규)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_grading.py`를 만든다.

```python
"""등급 판정. 설계서 §9.3."""

from stockscout.analysis.grading import GRADE_NONE, GRADE_STRONG, GRADE_WATCH, grade_signal
from stockscout.config import SignalConfig
from stockscout.models import PriceStats

CONFIG = SignalConfig()   # min_score=70, 두 임계치 모두 -0.40


def stats(drop_avg, drop_high):
    return PriceStats(
        price_current=100.0,
        avg_price={1: 150.0, 3: 160.0, 5: 170.0},
        high_5y=200.0,
        drop_vs_avg={1: -0.33, 3: -0.375, 5: drop_avg},
        drop_vs_high=drop_high,
    )


def test_both_thresholds_and_a_good_score_gives_two_stars():
    grade, reason = grade_signal(
        stats(-0.45, -0.50), score_total=80.0, passed_filters=True,
        config=CONFIG, primary_window=5,
    )

    assert grade == GRADE_STRONG
    assert "평균" in reason and "고점" in reason


def test_only_one_threshold_gives_one_star():
    grade, _ = grade_signal(
        stats(-0.30, -0.50), score_total=80.0, passed_filters=True,
        config=CONFIG, primary_window=5,
    )

    assert grade == GRADE_WATCH


def test_a_falling_stock_that_is_not_quality_gets_no_grade():
    """이 계획의 시금석이다.

    LG화학은 5년 평균 대비 -41.3%로 현재 1위지만 영업이익률이 11.8%에서
    2.6%로 무너졌고 작년 ROE는 -2.1%다. 41% 하락에는 이유가 있었다.
    많이 떨어졌다는 것만으로 등급이 나오면 이 제품은 위험하다.
    """
    grade, reason = grade_signal(
        stats(-0.45, -0.50), score_total=42.0, passed_filters=True,
        config=CONFIG, primary_window=5,
    )

    assert grade == GRADE_NONE
    assert "70" in reason        # 왜 안 되는지 숫자로 말해야 한다


def test_an_excluded_stock_gets_no_grade_even_with_a_high_score():
    grade, _ = grade_signal(
        stats(-0.45, -0.50), score_total=95.0, passed_filters=False,
        config=CONFIG, primary_window=5,
    )

    assert grade == GRADE_NONE


def test_no_score_yet_gives_no_grade():
    """점수가 없는 동안 등급을 표시하면 품질을 확인한 것처럼 보인다."""
    grade, reason = grade_signal(
        stats(-0.45, -0.50), score_total=None, passed_filters=True,
        config=CONFIG, primary_window=5,
    )

    assert grade == GRADE_NONE
    assert "점수" in reason


def test_a_quality_stock_that_has_not_fallen_gets_no_grade():
    grade, _ = grade_signal(
        stats(0.10, -0.05), score_total=90.0, passed_filters=True,
        config=CONFIG, primary_window=5,
    )

    assert grade == GRADE_NONE


def test_the_primary_window_selects_which_drop_is_used():
    """설정이 primary_window를 1로 바꾸면 1년 평균 대비를 본다."""
    s = PriceStats(
        price_current=100.0, avg_price={1: 200.0, 5: 105.0}, high_5y=300.0,
        drop_vs_avg={1: -0.50, 5: -0.05}, drop_vs_high=-0.10,
    )

    one_year, _ = grade_signal(s, 80.0, True, CONFIG, primary_window=1)
    five_year, _ = grade_signal(s, 80.0, True, CONFIG, primary_window=5)

    assert one_year == GRADE_WATCH     # 1년 평균 대비 -50%가 임계치를 넘는다
    assert five_year == GRADE_NONE     # 5년 평균 대비는 -5%뿐이다


def test_a_null_drop_does_not_count_as_a_hit():
    """커버리지 미달 창은 None이다 (설계서 §9.1). 등급 판정에서 제외한다."""
    s = PriceStats(
        price_current=100.0, avg_price={5: None}, high_5y=200.0,
        drop_vs_avg={5: None}, drop_vs_high=-0.50,
    )

    grade, _ = grade_signal(s, 80.0, True, CONFIG, primary_window=5)

    assert grade == GRADE_WATCH        # 고점 대비만으로 별 하나


def test_exactly_at_the_threshold_is_a_hit():
    """-40%는 '이하'다. 경계에서 종목이 갈린다."""
    grade, _ = grade_signal(
        stats(-0.40, -0.40), 80.0, True, CONFIG, primary_window=5,
    )

    assert grade == GRADE_STRONG


def test_reason_contains_the_actual_numbers():
    """화면이 계산식을 그대로 보여준다 (설계서 §11.2)."""
    _, reason = grade_signal(
        stats(-0.45, -0.50), 80.0, True, CONFIG, primary_window=5,
    )

    assert "-45.0%" in reason
    assert "-50.0%" in reason
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_grading.py`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 구현한다**

`src/stockscout/analysis/grading.py`를 만든다.

```python
"""등급 판정. 설계서 §9.3.

점수(품질)와 하락(가격)을 함께 본다. scoring.py가 가격을 모르고
valuation.py가 점수를 모르므로, 둘을 합치는 자리를 따로 둔다.

기호를 쓰지 않는다. 이 문자열은 웹으로도 CLI로도 가고, 한국어 Windows
콘솔은 cp949라 별표 같은 문자를 출력하면 UnicodeEncodeError로 죽는다.
등급 값은 STRONG/WATCH/NONE이고 별은 화면이 붙인다.
"""

from __future__ import annotations

from stockscout.config import SignalConfig
from stockscout.models import PriceStats

GRADE_STRONG = "STRONG"   # 화면에서 별 둘
GRADE_WATCH = "WATCH"     # 화면에서 별 하나
GRADE_NONE = "NONE"


def grade_signal(
    stats: PriceStats,
    score_total: float | None,
    passed_filters: bool,
    config: SignalConfig,
    primary_window: int,
) -> tuple[str, str]:
    """(등급, 판정 근거 문장)을 낸다.

    품질이 먼저다. 아무리 많이 떨어져도 우량주가 아니면 등급이 없다 -
    많이 떨어진 데에는 대개 이유가 있기 때문이다.
    """
    if not passed_filters:
        return GRADE_NONE, "하드필터에 걸려 등급을 매기지 않습니다"
    if score_total is None:
        return GRADE_NONE, "우량주 점수가 없어 등급을 매기지 않습니다"
    if score_total < config.min_score:
        return GRADE_NONE, (
            f"우량주 점수 {score_total:.0f}점이 기준 "
            f"{config.min_score:.0f}점에 못 미칩니다"
        )

    drop_avg = stats.drop_vs_avg.get(primary_window)
    avg_hit = drop_avg is not None and drop_avg <= config.drop_vs_avg_threshold
    high_hit = stats.drop_vs_high <= config.drop_vs_high_threshold

    parts = []
    if drop_avg is None:
        parts.append(f"{primary_window}년 평균 대비: 데이터 부족으로 판정 제외")
    else:
        parts.append(
            f"{primary_window}년 평균 대비 {drop_avg * 100:.1f}% "
            f"{'<=' if avg_hit else '>'} {config.drop_vs_avg_threshold * 100:.0f}%"
        )
    parts.append(
        f"5년 고점 대비 {stats.drop_vs_high * 100:.1f}% "
        f"{'<=' if high_hit else '>'} {config.drop_vs_high_threshold * 100:.0f}%"
    )
    reason = f"우량주 점수 {score_total:.0f}점 · " + " · ".join(parts)

    if avg_hit and high_hit:
        return GRADE_STRONG, reason
    if avg_hit or high_hit:
        return GRADE_WATCH, reason
    return GRADE_NONE, reason
```

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_grading.py`
Expected: PASS — `10 passed`

- [ ] **Step 5: 변이 테스트 2건**

| 변이 | 실패해야 할 테스트 |
|---|---|
| `if score_total < config.min_score` 블록 삭제 | `test_a_falling_stock_that_is_not_quality_gets_no_grade` |
| `drop_avg <= threshold` → `<` | `test_exactly_at_the_threshold_is_a_hit` |

확인 후 되돌리고 `git diff -- src/`로 원복을 확인해라.

- [ ] **Step 6: 커밋**

```bash
git add -A
git commit -m "feat: ★★/★ 등급 판정

설계서 §9.3. 품질이 먼저다 - 아무리 많이 떨어져도 우량주가 아니면
등급이 없다. 많이 떨어진 데에는 대개 이유가 있다.

LG화학이 그 예다. 5년 평균 대비 -41.3%로 현재 1위지만 영업이익률이
11.8%에서 2.6%로 무너졌고 작년 ROE는 -2.1%다. 테스트가 이 경우를
고정한다.

등급 값은 STRONG/WATCH/NONE이고 별은 화면이 붙인다. 이 문자열이
CLI로도 가는데 한국어 Windows 콘솔은 cp949라 별표를 출력하면 죽는다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 9: EPS·BPS는 유통주식수로

**왜:** 설계서 §9.2가 용도별로 다른 주식수를 쓰라고 한다.

- **시가총액** — 발행주식총수(`shares_outstanding`). 지수 산출도 상장주식수 기준이다
- **EPS·BPS** — 자기주식은 배당도 의결권도 없으므로 유통주식수(`shares_outstanding - shares_treasury`)가 더 정확하다

삼성전자 2024 보통주 기준 자기주식이 2,970만 주(0.5%)라 차이가 작지만, 자사주를 많이 사들인 회사에서는 유의미해진다.

`compute_valuation`은 지금 발행주식총수 하나로 셋을 다 계산한다.

**Files:**
- Modify: `src/stockscout/analysis/valuation.py`
- Test: `tests/test_valuation.py` (기존 파일. `grep -rln "compute_valuation" tests/`로 찾아라)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_market_cap_uses_issued_shares_but_eps_uses_free_float():
    """설계서 §9.2: 자기주식은 배당도 의결권도 없다."""
    latest = Financials(
        ticker="005930.KS", fiscal_year=2024, currency="KRW",
        revenue=1.0, operating_income=1.0,
        net_income=1_000.0, total_equity=10_000.0, total_liabilities=1.0,
        shares_outstanding=1_000.0, shares_treasury=200.0,
    )

    val = compute_valuation(price=50.0, latest=latest)

    assert val.market_cap == 50_000.0             # 50 x 1,000 (발행)
    # EPS = 1,000 / 800 = 1.25 -> PER = 50 / 1.25 = 40
    assert math.isclose(val.per, 40.0)
    # BPS = 10,000 / 800 = 12.5 -> PBR = 50 / 12.5 = 4
    assert math.isclose(val.pbr, 4.0)


def test_missing_treasury_falls_back_to_issued_shares():
    """자기주식 수집이 실패해도 PER·PBR을 포기하지 않는다.

    자기주식이 없는 회사와 못 구한 회사를 구분할 방법이 없지만, 대개
    자기주식 비중은 작다. NULL로 만드는 쪽이 손해가 크다.
    """
    latest = Financials(
        ticker="000660.KS", fiscal_year=2024, currency="KRW",
        revenue=1.0, operating_income=1.0,
        net_income=1_000.0, total_equity=10_000.0, total_liabilities=1.0,
        shares_outstanding=1_000.0, shares_treasury=None,
    )

    val = compute_valuation(price=50.0, latest=latest)

    assert math.isclose(val.per, 50.0)            # EPS = 1.0


def test_treasury_larger_than_issued_falls_back_to_issued():
    """말이 안 되는 데이터로 0이나 음수로 나누지 않는다."""
    latest = Financials(
        ticker="000000.KS", fiscal_year=2024, currency="KRW",
        revenue=1.0, operating_income=1.0,
        net_income=1_000.0, total_equity=10_000.0, total_liabilities=1.0,
        shares_outstanding=1_000.0, shares_treasury=1_000.0,
    )

    val = compute_valuation(price=50.0, latest=latest)

    assert val.market_cap == 50_000.0
    assert math.isclose(val.per, 50.0)
```

`import math`가 파일에 없으면 더한다.

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_valuation.py`
Expected: FAIL — `test_market_cap_uses_issued_shares_but_eps_uses_free_float`에서 `per`가 50.0으로 나온다 (유통주식수를 안 쓴다)

- [ ] **Step 3: 구현한다**

`compute_valuation`의 본문에서 `shares` 한 개를 두 개로 나눈다.

```python
    issued = latest.shares_outstanding
    if issued <= 0:
        return Valuation()

    # 시가총액은 발행주식총수, EPS·BPS는 유통주식수를 쓴다 (설계서 §9.2).
    # 자기주식은 배당도 의결권도 없으므로 주당 지표의 분모에서 빼는 것이
    # 맞다. 자기주식을 못 구했거나 값이 이상하면 발행주식총수로 돌아간다 -
    # 자사주 비중은 대개 작아서, NULL로 만드는 쪽이 손해가 크다.
    free_float = issued
    if latest.shares_treasury is not None:
        remaining = issued - latest.shares_treasury
        if remaining > 0:
            free_float = remaining

    market_cap = price * issued

    per = None
    if latest.net_income is not None:
        eps = latest.net_income / free_float
        if eps > 0:
            per = price / eps

    pbr = None
    if latest.total_equity is not None:
        bps = latest.total_equity / free_float
        if bps > 0:
            pbr = price / bps
```

docstring의 "발행주식수를 주지 않으므로 항상 None이 된다"는 문장은 **더 이상 사실이 아니다.** Task 2가 그것을 수집한다. 고쳐라.

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_valuation.py`
Expected: PASS

- [ ] **Step 5: 변이 테스트**

`market_cap = price * issued`를 `price * free_float`로 바꿔라.
Expected: `test_market_cap_uses_issued_shares_but_eps_uses_free_float`가 **FAIL**

되돌리고 `git diff -- src/`로 확인해라.

- [ ] **Step 6: 커밋**

```bash
git add -A
git commit -m "feat: EPS·BPS를 유통주식수로 계산한다

설계서 §9.2. 시가총액은 발행주식총수를 쓰지만 주당 지표는 자기주식을
빼야 한다 - 자기주식은 배당도 의결권도 없다.

자기주식을 못 구했거나 값이 이상하면 발행주식총수로 돌아간다. 자사주
비중은 대개 작아서, PER·PBR을 NULL로 만드는 쪽이 손해가 크다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 10: analyze 배선

**왜:** 순수 함수 세 개(`apply_financial_filters`·`score_security`·`grade_signal`)가 준비됐다. `analyze_market`이 호출하고 결과를 스냅샷에 넣어야 화면이 살아난다.

**Files:**
- Modify: `src/stockscout/pipeline/analyze.py`
- Modify: `src/stockscout/store/repository.py` (필요하면)
- Test: `tests/test_pipeline_analyze.py` (기존 파일)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

기존 `tests/test_pipeline_analyze.py`를 읽고 그 파일의 fixture를 그대로 쓴다. 42종목 실제 DB가 아니라 임시 DB에 소수 종목을 넣는 방식일 것이다.

```python
def test_analyze_scores_a_passing_security(repo, config):
    """재무가 있으면 점수가 채워지고 스냅샷에 저장된다."""
    _seed_security_with_prices_and_financials(repo, "005930.KS")

    summary = analyze_market(repo, "KR", config)
    (row,) = repo.get_analysis_results(summary.snapshot_id)

    assert row["score_total"] is not None
    assert row["score_profit"] is not None
    assert row["score_detail"] is not None


def test_analyze_does_not_score_an_excluded_security(repo, config):
    """설계서 §8.1: 하드필터에 걸리면 점수를 계산하지 않는다.

    점수를 매겨두면 화면에서 '제외됐지만 85점'이라는 모순된 표시가 난다.
    """
    _seed_security_with_prices_and_financials(repo, "105560.KS", is_financial=True)

    summary = analyze_market(repo, "KR", config)
    (row,) = repo.get_analysis_results(summary.snapshot_id)

    assert row["passed_filters"] == 0
    assert row["score_total"] is None
    assert row["signal_grade"] == "NONE"


def test_analyze_merges_price_and_financial_failures_without_duplicates(repo, config):
    """두 필터가 모두 INSUFFICIENT_HISTORY를 낼 수 있다."""
    _seed_security_with_short_history(repo, "000000.KS")

    summary = analyze_market(repo, "KR", config)
    (row,) = repo.get_analysis_results(summary.snapshot_id)
    failures = row["filter_failures"].split(",")

    assert failures.count("INSUFFICIENT_HISTORY") == 1


def test_analyze_records_the_grade(repo, config):
    _seed_security_with_prices_and_financials(repo, "005930.KS")

    summary = analyze_market(repo, "KR", config)
    (row,) = repo.get_analysis_results(summary.snapshot_id)

    assert row["signal_grade"] in ("STRONG", "WATCH", "NONE")
    assert row["signal_reason"]
```

`_seed_*` 헬퍼는 기존 파일에 있는 것을 재사용하거나, 없으면 기존 테스트가 데이터를 넣는 방식을 그대로 따라 만들어라. **재무 5개년을 넣어야 `INSUFFICIENT_HISTORY`에 걸리지 않는다.**

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_pipeline_analyze.py`
Expected: FAIL — `score_total`이 None이다

- [ ] **Step 3: 구현한다**

`src/stockscout/pipeline/analyze.py`의 `analyze_market` 안, `results.append(...)` **앞**을 바꾼다.

```python
            financials = repo.get_financials(security.ticker)
            valuation = compute_valuation(
                stats.price_current, financials[-1] if financials else None
            )

            # 가격 필터와 재무 필터가 모두 INSUFFICIENT_HISTORY를 낼 수 있다.
            # dict.fromkeys로 순서를 지키면서 중복만 없앤다 - 화면이 같은
            # 사유를 두 번 보여주면 안 된다.
            financial_outcome = apply_financial_filters(
                financials, config.hard_filters
            )
            outcome = FilterOutcome(failures=list(dict.fromkeys(
                outcome.failures + financial_outcome.failures
            )))

            # 하드필터에 걸린 종목은 점수를 계산하지 않는다 (설계서 §8.1).
            # 매겨두면 화면에 "제외됐지만 85점"이라는 모순된 표시가 난다.
            card = score_security(financials, config.scoring) if outcome.passed else None
            grade, reason = grade_signal(
                stats,
                score_total=card.total if card else None,
                passed_filters=outcome.passed,
                config=config.signal,
                primary_window=config.lookback.primary_window,
            )
        except Exception as exc:
            ...

        results.append(AnalysisResult(
            ticker=security.ticker, filters=outcome, stats=stats,
            valuation=valuation,
            score_total=card.total if card else None,
            score_profit=_axis_points(card, "profitability"),
            score_growth=_axis_points(card, "growth"),
            score_stability=_axis_points(card, "stability"),
            score_cashflow=_axis_points(card, "cashflow"),
            score_detail=card.model_dump_json() if card else None,
            signal_grade=grade,
            signal_reason=reason,
        ))
```

파일 맨 끝에 헬퍼를 더한다.

```python
def _axis_points(card: ScoreCard | None, axis: str) -> float | None:
    """축 점수 0~25. 카드가 없거나 그 축이 없으면 None."""
    if card is None:
        return None
    for entry in card.axes:
        if entry.axis == axis:
            return entry.points
    return None
```

import를 더한다.

```python
from stockscout.analysis.filters import apply_financial_filters, apply_price_filters
from stockscout.analysis.grading import grade_signal
from stockscout.analysis.scoring import score_security
from stockscout.models import AnalysisResult, FilterOutcome, ScoreCard, Snapshot
```

- [ ] **Step 4: `score_detail`이 저장·조회되는지 확인한다**

`Repository.save_snapshot`의 `_result_row`(292행)가 `score_detail`을 이미 쓰고 있을 것이다. `AnalysisResult.score_detail`이 `str | None`이므로 `card.model_dump_json()`이 그대로 들어간다. **컬럼 타입이 TEXT라 JSON 문자열이 맞다.**

`get_analysis_results`는 dict를 그대로 돌려주므로 `score_detail`은 문자열이다. 파싱은 화면(Task 12)이 한다.

- [ ] **Step 5: 테스트가 통과하는지 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_pipeline_analyze.py`
Expected: PASS

- [ ] **Step 6: 전체 스위트**

Run: `.venv/Scripts/python.exe -m pytest`
Expected: 실패 0건

`tests/test_web_integration.py::test_scores_are_absent_until_plan_c`가 **실패하면 정상이다.** 계획 D의 인계 노트가 예고한 그대로다. 지우지 말고 방향을 뒤집어라.

```python
def test_scores_are_present(snapshot):
    """계획 C가 점수를 채웠다. has_scores가 화면 전체의 스위치다.

    이 값이 true가 되는 순간 우량주 점수 슬라이더, 등급 칩, 점수 KPI가
    전부 켜진다 (계획 D가 그렇게 만들어 두었다).
    """
    assert snapshot["has_scores"] is True
    assert snapshot["has_valuation"] is True
```

- [ ] **Step 7: 실제 데이터로 돌린다**

```bash
.venv/Scripts/stockscout.exe analyze --market KR
```

Expected: `42종목 분석 · 통과 N · 제외 M`. 재무 필터가 붙었으므로 **통과 수가 34보다 줄어들 수 있다.** 줄었다면 무엇에 걸렸는지 확인해라.

```bash
.venv/Scripts/python.exe -c "import sqlite3,io,sys; sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8'); c=sqlite3.connect('data/stockscout.db'); sid=c.execute('SELECT MAX(snapshot_id) FROM snapshots').fetchone()[0]; [print(r) for r in c.execute('SELECT filter_failures, COUNT(*) FROM analysis_results WHERE snapshot_id=? GROUP BY filter_failures ORDER BY 2 DESC', (sid,))]"
```

- [ ] **Step 8: 커밋**

```bash
git add -A
git commit -m "feat: analyze가 재무 필터·점수·등급을 채운다

하드필터에 걸린 종목은 점수를 계산하지 않는다. 매겨두면 화면에
'제외됐지만 85점'이라는 모순된 표시가 난다.

가격 필터와 재무 필터가 모두 INSUFFICIENT_HISTORY를 낼 수 있어
dict.fromkeys로 순서를 지키며 중복을 없앤다. 화면이 같은 사유를 두
번 보여주면 안 된다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 11: 판정 근거에 점수 산출을 더한다

**왜:** 설계서 §11.2가 "하드필터 통과 내역, **점수 산출 근거**, 신호 판정식을 실제 숫자로 노출"하라고 한다. 지금 근거는 가격 계산식만 보여준다.

그리고 §17.3의 결정에 따라, 이자보상배율을 **금융비용으로 계산했다면 그 사실이 근거에 나와야 한다.**

**Files:**
- Modify: `src/stockscout/web/api.py`
- Modify: `src/stockscout/web/schemas.py` (필요하면)
- Test: `tests/test_web_securities.py`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_web_securities.py`에 붙인다. 이 파일의 기존 fixture가 임시 DB에 스냅샷을 넣는 방식을 그대로 쓴다.

```python
def test_rationale_shows_the_score_and_the_grade(client, seeded):
    body = client.get("/api/securities/005930.KS").json()
    joined = "\n".join(body["rationale"])

    assert "우량주 점수" in joined
    assert "수익성" in joined and "현금흐름" in joined


def test_rationale_names_an_unmeasurable_axis(client, db):
    """§8.3의 축 하한에 걸린 종목은 왜 0점인지 말해야 한다.

    "현금흐름 0.0 / 25"만 보여주면 사업이 나쁜 것처럼 읽힌다. 실제로는
    볼 수 없었던 것이다.
    """
    _seed_result_with_axis_status(db, "000000.KS", axis="cashflow",
                                  status="AXIS_UNMEASURABLE")

    body = client.get("/api/securities/000000.KS").json()

    assert any("측정" in line for line in body["rationale"])


def test_rationale_discloses_a_proxy_interest_expense(client, db):
    """설계서 §17.3: 금융비용으로 이자보상배율을 계산했으면 그렇게 말한다."""
    _seed_result_with_proxy_field(db, "051900.KS", "interest_expense")

    body = client.get("/api/securities/051900.KS").json()

    assert any("금융비용" in line for line in body["rationale"])


def test_rationale_has_no_score_lines_for_an_excluded_security(client, seeded):
    """제외된 종목은 점수가 없다. 0점이라고 쓰면 사업 평가로 읽힌다."""
    body = client.get("/api/securities/105560.KS").json()
    joined = "\n".join(body["rationale"])

    assert "[제외]" in joined
    assert "수익성" not in joined
```

두 시드 헬퍼는 이 파일의 기존 헬퍼가 쓰는 fixture(임시 DB 연결)를 그대로 받아 아래 값을 넣는다.

```python
UNMEASURABLE_DETAIL = json.dumps({"axes": [
    {"axis": "profitability", "metrics": [], "earned": 20.0,
     "denominator": 25.0, "max_points": 25.0, "status": "OK"},
    {"axis": "growth", "metrics": [], "earned": 15.0,
     "denominator": 25.0, "max_points": 25.0, "status": "OK"},
    {"axis": "stability", "metrics": [], "earned": 18.0,
     "denominator": 25.0, "max_points": 25.0, "status": "OK"},
    {"axis": "cashflow", "metrics": [], "earned": 0.0,
     "denominator": 25.0, "max_points": 25.0, "status": "AXIS_UNMEASURABLE"},
]})
```

`_seed_result_with_axis_status`는 `analysis_results`의 그 종목 행에 `score_detail = UNMEASURABLE_DETAIL`과 `score_total = 53.0`, `passed_filters = 1`을 넣는다.

`_seed_result_with_proxy_field`는 `financials` 테이블의 그 종목 최근 연도 행에 `proxy_fields = '["interest_expense"]'`를 넣는다. 그 종목의 `analysis_results` 행도 있어야 상세 API가 404를 내지 않는다.

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_web_securities.py`
Expected: FAIL

- [ ] **Step 3: 구현한다**

`src/stockscout/web/api.py`의 `_rationale`(293행)을 고친다. 지금 시그니처는 `_rationale(row, failures)`다. 재무를 받아야 하므로 `_rationale(row, failures, financials)`로 바꾸고 호출부(193행 부근)도 함께 고친다.

기존 가격 계산식 줄들 **뒤**, 마지막 줄 앞에 더한다.

```python
AXIS_LABELS = {
    "profitability": "수익성",
    "growth": "성장성",
    "stability": "안정성",
    "cashflow": "현금흐름",
}

# score_detail에 기록되는 축 상태. 설계서 §8.3.
AXIS_UNMEASURABLE = "AXIS_UNMEASURABLE"


def _score_lines(row: dict) -> list[str]:
    """점수 산출 근거. 축별 점수와, 못 잰 축이 있으면 그 사실.

    점수가 없으면 빈 목록을 돌려준다 - 제외된 종목에 "수익성 0점"을
    쓰면 사업 평가로 읽힌다. 실제로는 채점하지 않은 것이다.
    """
    if row.get("score_total") is None or not row.get("score_detail"):
        return []

    try:
        card = json.loads(row["score_detail"])
    except (TypeError, ValueError):
        return []

    lines = [f"우량주 점수 {row['score_total']:.0f}점 / 100"]
    for axis in card.get("axes", []):
        label = AXIS_LABELS.get(axis.get("axis"), axis.get("axis", ""))
        earned = axis.get("earned", 0.0)
        denominator = axis.get("denominator", 0.0)
        scaled = 0.0 if denominator <= 0 else earned / denominator * axis.get(
            "max_points", 25.0
        )
        if axis.get("status") == AXIS_UNMEASURABLE:
            lines.append(
                f"  {label} 0.0점 - 지표를 절반도 못 구해 측정 불가로 0점 처리"
            )
        else:
            lines.append(
                f"  {label} {scaled:.1f}점 "
                f"(획득 {earned:.1f} / 유효만점 {denominator:.1f})"
            )
    return lines


def _proxy_lines(financials: list) -> list[str]:
    """대체 계정을 쓴 항목을 밝힌다 (설계서 §17.3).

    이자보상배율이 금융비용으로 계산됐다면 사용자가 그것을 알아야 한다.
    금융비용은 이자비용에 환차손·파생손실이 더해진 상위 개념이라 배율이
    실제보다 낮게 나온다.
    """
    if not financials:
        return []
    latest = financials[-1]
    if "interest_expense" not in (getattr(latest, "proxy_fields", None) or []):
        return []
    return [
        "이자비용을 찾지 못해 금융비용으로 대신했습니다. "
        "이자보상배율이 실제보다 낮게 계산됩니다"
    ]
```

`_rationale` 본문 끝에서 합친다.

```python
    lines.extend(_score_lines(row))
    lines.extend(_proxy_lines(financials))
    if row.get("signal_reason"):
        lines.append(f"신호 판정: {row['signal_reason']}")
    return lines
```

`import json`이 `api.py`에 없으면 맨 위에 더한다.

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_web_securities.py`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add -A
git commit -m "feat: 판정 근거에 점수 산출과 대체 계정 사용을 드러낸다

설계서 §11.2가 점수 산출 근거를 실제 숫자로 요구한다.

축 하한에 걸린 축은 왜 0점인지 말한다. '현금흐름 0.0 / 25'만
보여주면 사업이 나쁜 것처럼 읽히는데, 실제로는 볼 수 없었던 것이다.

이자보상배율을 금융비용으로 계산했으면 그렇게 말한다 (§17.3).
금융비용은 이자비용의 상위 개념이라 배율이 실제보다 낮게 나온다.

제외된 종목에는 점수 줄을 쓰지 않는다. 0점이라고 쓰면 사업 평가로
읽힌다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 12: 화면 — 점수 분해 4축 막대

**왜:** 설계서 §11.2의 4번 항목이다. 계획 D가 "점수 구조가 계획 C에서 정해지므로 그때 붙인다"며 미뤄둔 것이다.

나머지(점수 슬라이더·등급 칩·점수 KPI·재무 추이표)는 계획 D가 이미 만들어 뒀고 `has_scores`가 true가 되는 순간 저절로 켜진다. **새로 만드는 것은 막대 하나뿐이다.**

**Files:**
- Modify: `src/stockscout/web/static/app.js`
- Modify: `src/stockscout/web/static/style.css`
- Test: `tests/test_web_static.py`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_web_static.py`에 붙인다. 이 파일은 app.js를 문자열로 읽어 검사한다. **기존 테스트가 함수 본문을 어떻게 잘라내는지 먼저 읽어라** — `js.split("drawChart")[1]` 같은 방식은 호출부를 집어 엉뚱한 것을 검사한다(계획 D에서 실제로 그랬다). `"function scoreBars"`처럼 정의부에 앵커를 걸어라.

```python
def test_app_js_renders_a_score_breakdown():
    js = APP_JS.read_text(encoding="utf-8")

    assert "function scoreBars" in js
    assert "점수 분해" in js


def test_score_bars_scale_by_max_points_not_a_hardcoded_25():
    """배점은 설정에서 온다. 사용자가 YAML을 고치면 막대도 따라야 한다."""
    body = _function_body(js_text(), "scoreBars")

    assert "max_points" in body
    assert "/ 25" not in body


def test_score_bars_label_an_unmeasurable_axis():
    body = _function_body(js_text(), "scoreBars")

    assert "AXIS_UNMEASURABLE" in body
    assert "측정" in body


def test_detail_skips_the_breakdown_when_there_is_no_score():
    body = _function_body(js_text(), "renderDetail")

    assert "score_detail" in body
```

`_function_body(js, name)`가 없으면 만든다. `"function <name>"`부터 시작해 중괄호 깊이가 0으로 돌아오는 지점까지 자르는 함수다. **`split`으로 대충 자르지 마라** — 계획 D에서 그 방식이 호출부의 꼬리(`'(prices);\n}\n\n'`)를 집어 테스트가 무의미해진 적이 있다.

```python
def _function_body(js: str, name: str) -> str:
    start = js.index(f"function {name}")
    depth = 0
    for i in range(js.index("{", start), len(js)):
        if js[i] == "{":
            depth += 1
        elif js[i] == "}":
            depth -= 1
            if depth == 0:
                return js[start:i + 1]
    raise AssertionError(f"{name}의 본문을 찾지 못했습니다")
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_web_static.py`
Expected: FAIL

- [ ] **Step 3: 구현한다**

`src/stockscout/web/static/app.js`의 `renderDetail`에서 **KPI 다음, 판정 근거 앞**에 넣는다.

```javascript
  // 점수 분해 (설계서 §11.2)
  if (d.score_detail) {
    panel.appendChild(sectionTitle("점수 분해"));
    const bars = document.createElement("div");
    bars.className = "score-bars";
    bars.innerHTML = scoreBars(d.score_detail);
    panel.appendChild(bars);
  }
```

파일 아래쪽 헬퍼들 옆에 더한다.

```javascript
const AXIS_LABELS = {
  profitability: "수익성",
  growth: "성장성",
  stability: "안정성",
  cashflow: "현금흐름",
};

const METRIC_LABELS = {
  roe_avg3: "ROE 3년 평균",
  op_margin_avg3: "영업이익률 3년 평균",
  op_margin_trend: "영업이익률 추세",
  revenue_cagr3: "매출 CAGR 3년",
  op_income_cagr3: "영업이익 CAGR 3년",
  growth_consistency: "성장 일관성",
  debt_ratio: "부채비율",
  current_ratio: "유동비율",
  interest_coverage: "이자보상배율",
  ocf_positive_years: "OCF 흑자 연속",
  ocf_to_op_income: "OCF / 영업이익",
  fcf_positive_years: "FCF 흑자 연속",
};

const METRIC_STATUS_LABELS = {
  MISSING: "데이터 없음",
  BASE_NEGATIVE: "3년 전 기저가 적자라 계산 불가",
  DENOMINATOR_NONPOSITIVE: "3년 누계 영업이익이 0 이하",
};

function scoreBars(detailJson) {
  let card;
  try {
    card = typeof detailJson === "string" ? JSON.parse(detailJson) : detailJson;
  } catch (e) {
    return "";
  }
  if (!card || !card.axes) return "";

  return card.axes.map((axis) => {
    const label = AXIS_LABELS[axis.axis] ?? axis.axis;
    const max = axis.max_points ?? 0;
    const unmeasurable = axis.status === "AXIS_UNMEASURABLE";
    const scaled = unmeasurable || axis.denominator <= 0
      ? 0
      : (axis.earned / axis.denominator) * max;
    const width = max > 0 ? (scaled / max) * 100 : 0;
    const note = unmeasurable
      ? `<span class="axis-note">지표를 절반도 못 구해 측정 불가</span>`
      : "";

    const rows = (axis.metrics ?? []).map((m) => {
      const name = METRIC_LABELS[m.metric] ?? m.metric;
      const status = METRIC_STATUS_LABELS[m.status];
      const value = status
        ? `<i>${escapeHtml(status)}</i>`
        : `${m.points.toFixed(1)} / ${m.max_points.toFixed(0)}`;
      return `<div class="metric-row"><span>${escapeHtml(name)}</span>${value}</div>`;
    }).join("");

    return `<details class="axis${unmeasurable ? " unmeasurable" : ""}">
      <summary>
        <span class="axis-name">${escapeHtml(label)}</span>
        <span class="axis-track"><span class="axis-fill" style="width:${width.toFixed(1)}%"></span></span>
        <span class="axis-score">${scaled.toFixed(1)} / ${max.toFixed(0)}</span>
        ${note}
      </summary>
      ${rows}
    </details>`;
  }).join("");
}
```

`<details>`를 쓰는 이유: 설계서 §11.2가 "막대 클릭 시 지표별 상세 펼침"을 요구한다. 브라우저가 그 동작을 공짜로 준다 — 자바스크립트로 토글을 만들 이유가 없다.

- [ ] **Step 4: API가 `score_detail`을 내려보내는지 확인한다**

`src/stockscout/web/schemas.py`의 상세 응답 모델에 `score_detail: str | None = None`이 있는지 확인하고, 없으면 더한다. `api.py`의 상세 빌더가 `row["score_detail"]`을 채우는지도 확인해라 — 계획 D가 이미 `score_detail=row["score_detail"]`를 넣어 뒀을 것이다(194행).

- [ ] **Step 5: CSS를 더한다**

`src/stockscout/web/static/style.css` 맨 끝에 붙인다. 기존 색상 변수와 간격 규칙을 먼저 읽고 같은 값을 써라.

```css
.score-bars .axis { padding: 6px 0; border-bottom: 1px solid #eef0f3; }
.score-bars summary { display: flex; align-items: center; gap: 10px;
                      cursor: pointer; list-style: none; }
.score-bars summary::-webkit-details-marker { display: none; }
.axis-name  { width: 68px; font-size: 13px; }
.axis-track { flex: 1; height: 10px; background: #eef0f3; border-radius: 5px; }
.axis-fill  { display: block; height: 100%; background: #2f6fd0; border-radius: 5px; }
.axis-score { width: 78px; text-align: right; font-size: 13px; color: #556; }
.axis-note  { font-size: 12px; color: #98a; }
.axis.unmeasurable .axis-fill { background: #c9ced6; }
.metric-row { display: flex; justify-content: space-between;
              padding: 3px 0 3px 78px; font-size: 12px; color: #667; }
.metric-row i { color: #98a; font-style: normal; }
```

- [ ] **Step 6: 테스트가 통과하는지 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_web_static.py`
Expected: PASS

- [ ] **Step 7: 커밋**

```bash
git add -A
git commit -m "feat: 점수 분해 4축 막대

설계서 §11.2의 마지막 항목. 계획 D가 점수 구조를 몰라 미뤄둔 것이다.

details/summary를 쓴다. '막대 클릭 시 지표별 상세 펼침'을 브라우저가
공짜로 준다.

배점을 25로 박지 않고 max_points를 쓴다. 사용자가 YAML에서 배점을
고치면 막대도 따라야 한다.

축 하한에 걸린 축은 회색 막대와 '측정 불가' 표시를 단다. 파란 0점
막대는 사업이 나쁜 것처럼 읽힌다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 13: 실제 데이터 관통 검증

**왜:** 앞선 열두 과제는 전부 임시 데이터로 검증했다. 42종목 실제 데이터에서 무엇이 나오는지는 돌려봐야 안다.

**Files:**
- Modify: `tests/test_web_integration.py`
- Test: `tests/test_integration_scoring.py` (신규)

- [ ] **Step 1: 전 과정을 다시 돌린다**

```bash
.venv/Scripts/stockscout.exe collect --market KR
.venv/Scripts/stockscout.exe normalize --market KR
.venv/Scripts/stockscout.exe analyze --market KR
```

- [ ] **Step 2: 결과를 눈으로 본다**

```bash
.venv/Scripts/python.exe -c "import sqlite3,io,sys; sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8'); c=sqlite3.connect('data/stockscout.db'); c.row_factory=sqlite3.Row; sid=c.execute('SELECT MAX(snapshot_id) FROM snapshots').fetchone()[0]; rows=c.execute('SELECT a.ticker,s.name,a.score_total,a.score_profit,a.score_growth,a.score_stability,a.score_cashflow,a.signal_grade,a.drop_vs_avg_5y FROM analysis_results a JOIN securities s ON s.ticker=a.ticker WHERE a.snapshot_id=? AND a.score_total IS NOT NULL ORDER BY a.score_total DESC', (sid,)).fetchall(); print(f\"{'종목':<16}{'총점':>6}{'수익':>6}{'성장':>6}{'안정':>6}{'현금':>6}{'등급':>8}{'5년평균대비':>12}\"); [print(f\"{r['name']:<16}{r['score_total']:>6.0f}{r['score_profit']:>6.1f}{r['score_growth']:>6.1f}{r['score_stability']:>6.1f}{r['score_cashflow']:>6.1f}{r['signal_grade']:>8}{r['drop_vs_avg_5y']*100:>11.1f}%\") for r in rows]"
```

**확인할 것:**

1. **LG화학이 70점 미만인가.** 영업이익률 11.8% → 2.6%, ROE -2.1%다. 70점을 넘으면 스코어링이 수익성 악화를 못 잡는 것이니 `metric_op_margin_trend`와 `metric_roe_avg3`의 실제 값을 찍어 확인해라
2. **★★가 0건이어도 정상이다.** 설계서 §9.5가 그렇게 예고한다. 우량주 필터까지 통과하면서 5년 평균 대비 -40%인 종목은 드물다
3. **점수가 전 종목 비슷하면 의심해라.** 구간표가 안 먹거나 전부 같은 구간에 몰린 것이다
4. **`AXIS_UNMEASURABLE`이 몇 종목에 뜨는지 세어라.** 많으면 매핑이 부족한 것이다

```bash
.venv/Scripts/python.exe -c "import sqlite3,json; c=sqlite3.connect('data/stockscout.db'); sid=c.execute('SELECT MAX(snapshot_id) FROM snapshots').fetchone()[0]; n=0; [n:=n+1 for (d,) in c.execute('SELECT score_detail FROM analysis_results WHERE snapshot_id=? AND score_detail IS NOT NULL',(sid,)) if 'AXIS_UNMEASURABLE' in d]; print('축 측정불가 종목:', n)"
```

- [ ] **Step 3: 통합 테스트를 쓴다**

`tests/test_integration_scoring.py`를 만든다. `tests/test_web_integration.py`의 skipif 패턴을 그대로 쓴다 — DB가 없거나 스냅샷이 없으면 모듈 전체를 skip한다.

```python
"""실제 DB에 대한 스코어링 검증.

DB가 없거나 점수가 없으면 모듈 전체가 skip된다.
"""

import json
from pathlib import Path

import pytest

from stockscout.store.db import DEFAULT_DB_PATH, connect
from stockscout.store.repository import Repository

pytestmark = pytest.mark.skipif(
    not Path(DEFAULT_DB_PATH).exists(),
    reason="stockscout analyze 를 먼저 실행해야 한다",
)


@pytest.fixture
def rows():
    with connect(DEFAULT_DB_PATH) as conn:
        repo = Repository(conn)
        snap = repo.get_latest_snapshot()
        if snap is None:
            pytest.skip("스냅샷이 없다")
        data = repo.get_analysis_results(snap.snapshot_id)
    scored = [r for r in data if r["score_total"] is not None]
    if not scored:
        pytest.skip("점수가 계산된 종목이 없다")
    return data


def test_every_passing_security_is_scored(rows):
    for row in rows:
        if row["passed_filters"]:
            assert row["score_total"] is not None, row["ticker"]


def test_no_excluded_security_carries_a_score(rows):
    """제외됐는데 점수가 있으면 화면에 모순된 표시가 난다."""
    for row in rows:
        if not row["passed_filters"]:
            assert row["score_total"] is None, row["ticker"]


def test_scores_stay_inside_zero_and_a_hundred(rows):
    for row in rows:
        if row["score_total"] is not None:
            assert 0 <= row["score_total"] <= 100, row["ticker"]


def test_axis_scores_stay_inside_zero_and_twentyfive(rows):
    for row in rows:
        for axis in ("profit", "growth", "stability", "cashflow"):
            value = row[f"score_{axis}"]
            if value is not None:
                assert 0 <= value <= 25, (row["ticker"], axis)


def test_score_detail_is_parseable_and_has_four_axes(rows):
    for row in rows:
        if row["score_detail"] is None:
            continue
        card = json.loads(row["score_detail"])
        assert len(card["axes"]) == 4, row["ticker"]
        assert {a["axis"] for a in card["axes"]} == {
            "profitability", "growth", "stability", "cashflow"
        }


def test_scores_are_not_all_identical(rows):
    """전 종목이 같은 점수면 구간표가 안 먹은 것이다."""
    scores = {r["score_total"] for r in rows if r["score_total"] is not None}

    assert len(scores) > 3, f"점수가 {len(scores)}종류뿐이다: {sorted(scores)}"


def test_every_row_has_a_grade(rows):
    for row in rows:
        assert row["signal_grade"] in ("STRONG", "WATCH", "NONE"), row["ticker"]


def test_a_graded_security_is_both_quality_and_fallen(rows):
    """등급이 붙었으면 점수 기준과 하락 기준을 둘 다 넘었어야 한다."""
    for row in rows:
        if row["signal_grade"] in ("STRONG", "WATCH"):
            assert row["passed_filters"]
            assert row["score_total"] >= 70


def test_two_stars_require_both_thresholds(rows):
    for row in rows:
        if row["signal_grade"] == "STRONG":
            assert row["drop_vs_avg_5y"] <= -0.40, row["ticker"]
            assert row["drop_vs_high"] <= -0.40, row["ticker"]
```

- [ ] **Step 4: 돌린다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_integration_scoring.py`
Expected: PASS

- [ ] **Step 5: 브라우저로 본다**

```bash
.venv/Scripts/stockscout.exe serve --port 8799
```

확인할 것:

1. 우량주 점수 슬라이더가 **켜져 있다** (비활성 설명 문구가 사라졌다)
2. 등급 칩(★★ / ★)이 눌린다
3. 종목을 고르면 **점수 분해 4축 막대**가 보인다
4. 막대를 누르면 지표별 상세가 펼쳐진다
5. 점수 KPI에 숫자가 있다
6. 재무 추이 자리에 DART 안내 대신 **5년표**가 있다
7. 판정 근거에 점수 산출 줄이 있다
8. 점수 슬라이더를 움직였을 때 **네트워크 요청이 0건**이다 (설계서 §6.1)

**스크린샷을 찍어 보고한다.** 8번은 개발자 도구 네트워크 탭으로 확인한다.

- [ ] **Step 6: 전체 스위트**

Run: `.venv/Scripts/python.exe -m pytest`
Expected: 실패 0건

- [ ] **Step 7: 커밋**

```bash
git add -A
git commit -m "test: 실제 42종목 데이터로 스코어링을 검증한다

제외된 종목에 점수가 없는지, 점수가 0~100 안에 있는지, 전 종목이
같은 점수가 아닌지(구간표가 안 먹은 신호다), 등급이 붙은 종목이
품질과 하락을 둘 다 넘었는지를 본다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## 완료 기준

- [ ] `interest_expense` 결측이 129건에서 10건 안팎으로 줄었다
- [ ] 금융비용으로 대체한 종목의 판정 근거가 그 사실을 말한다
- [ ] 시가총액·PER·PBR이 채워지고 `has_valuation`이 true다
- [ ] 감사의견이 수집되고, 비적정이면 제외된다
- [ ] 하드필터 9종이 전부 동작한다
- [ ] 4축 100점이 계산되고 `has_scores`가 true다
- [ ] **한 축이 통째로 측정 불가인 종목의 총점이 낮아진다** (§8.3 하한이 실제로 문다)
- [ ] ★★/★ 등급이 매겨지고, 점수 기준에 못 미치면 아무리 떨어져도 등급이 없다
- [ ] **LG화학이 70점 미만이다** (영업이익률 11.8% → 2.6%, ROE -2.1%)
- [ ] 화면에 점수 분해 4축 막대가 보이고 클릭하면 지표별로 펼쳐진다
- [ ] 점수 슬라이더가 **서버 왕복 없이** 즉시 반영된다
- [ ] `analysis/`가 `normalize`·`sources`·`store`·`pipeline`을 import하지 않는다
- [ ] `pytest` 전체 통과

## 다음 계획

**계획 E — 미국 주식 (S&P 500).** DART와 무관하므로 지금도 착수할 수 있다.

- 유니버스: S&P 500 구성종목
- 재무: SEC EDGAR `companyfacts` API (무료, 인증 불필요, User-Agent 필수)
- 주가: yfinance
- 계층 구조상 `sources/edgar.py`와 `normalize/us_mapping.py`를 더하면 `analysis/`는 손대지 않아도 된다. 설계서 §7.1이 "이 아래로는 어떤 코드도 시장을 구분하지 않는다"고 정한 것이 여기서 값을 한다

**계획 A에서 넘어와 아직 안 한 것:**

- I3 — pykrx 재시도·속도 제한 (`sources/ratelimit.py`의 `TRANSIENT`에 `requests` 예외가 없다)
- I5 — 설정 기반 소스 선택 (`config.sources`가 아직 배선되지 않았다)
- `Repository.trading_dates`가 시장을 구분하지 않는다 — 미국 종목이 들어오면 한국 거래일 달력에 섞인다. **계획 E가 반드시 고쳐야 한다**
