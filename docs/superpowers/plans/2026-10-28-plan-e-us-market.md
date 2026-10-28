# 계획 E — 미국 시장 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 미국 우량주 약 40종목을 SEC EDGAR 재무와 yfinance 주가로 한국과 같은 파이프라인에 태우고, 화면에서 KR | US 탭으로 본다.

**Architecture:** 소스 두 개(`sources/edgar.py`·`sources/yahoo.py`)와 매핑 하나(`normalize/us_mapping.py`)를 더하고, 스냅샷을 시장별로 나눈다. **`analysis/`는 한 줄도 바꾸지 않는다** — 설계서 §7.1·§15가 "그것이 이 설계가 맞았는지에 대한 검증"이라고 정했고, 이 계획의 마지막 Task가 diff로 확인한다.

**Tech Stack:** Python 3.14 · httpx · yfinance · pydantic 2 · SQLite · FastAPI · pytest

---

## 시작 전 상태

`master` 기준(`2b79357` 이후). 계획 A·B·C·D가 병합돼 있고 테스트 636개가 통과한다. DB에는 한국 42종목과 스냅샷 #8(통과 31 · 제외 11)이 있다. 미국 데이터는 없다.

### 반드시 먼저 읽을 것

**설계서 §18** (`docs/superpowers/specs/2026-09-10-stock-screener-design.md`). 특히 §18.2 — 실제 SEC 응답을 받아 보고 알게 된 다섯 가지가 이 계획의 Task 5·7을 정한다.

1. 10-K 안에 분기 값이 섞여 있다 → 기간 350~380일만 연간으로 본다
2. `fy`는 공시의 회계연도다 → 기간은 `end`로 식별한다
3. 태그가 해마다 바뀐다 → 우선순위는 연도마다 적용한다
4. 감사의견은 XBRL에 없다 → None, 필터는 건너뛴다
5. 미국의 `CommonStockSharesOutstanding`은 유통주식수다

### 설계서 §18.3에서 바꾸는 한 가지

§18.3은 "원본 전체가 아니라 매핑 후보 태그만 담는다"고 적었다. 실측해 보니 AAPL 한 해의 연간 값 전체가 **10KB**다(태그 약 200개). 40종목 × 5년이면 2MB다. 후보만 담으면 매핑에 태그를 더할 때마다 SEC에서 다시 받아야 한다. **연간 값 전체를 담는다.** 한국도 DART 응답 전체를 담는다. Task 5의 커밋에서 §18.3의 그 문장을 고친다.

---

## 이 계획의 핵심 결정 세 가지

### 1. 연간 값은 기간으로 고른다 — `form`과 `fy`를 믿지 않는다

AAPL의 `Revenues`에는 `form=10-K`이면서 기간이 2018-07-01 ~ 2018-09-29(한 분기)인 항목이 있다. `form`만 보면 분기 매출 629억 달러가 연간 매출로 들어가 매출이 4분의 1로 줄어든 것처럼 보인다. 매출 급감 필터(`REVENUE_COLLAPSE`)가 그 회사를 떨어뜨린다.

`fy`도 믿을 수 없다. 2025년 10-K에는 2023·2024년 비교 수치가 모두 `fy=2025`로 들어 있다.

그래서 **흐름 항목은 기간이 350~380일인 것만**, 회계연도는 **`end`의 연도**로 정한다. 시점 항목(재무상태표)은 그 회계연도 흐름 기간의 `end`와 같은 날짜의 값을 쓴다. 같은 (태그, 기간)이 여러 번 공시되면 `filed`가 가장 늦은 것(정정 공시)을 쓴다.

### 2. 스냅샷을 시장별로 나눈다

지금 `get_latest_snapshot()`은 시장을 모른다. 미국을 분석하면 화면이 미국 40종목만 보여주고 한국 결과가 사라진다. `snapshots.market`을 더하고, API가 `?market=`을 받고, 화면에 탭을 둔다.

기존 행과 `market`을 넘기지 않는 기존 코드는 `KR`로 본다. `Snapshot.market`의 기본값이 `"KR"`인 이유다 — 이 기본값 덕분에 기존 테스트 수십 개가 그대로 통과한다.

### 3. 미국 이자비용에는 대체값을 두지 않는다

한국은 이자비용이 없으면 금융비용으로 대신했다(설계서 §17.3). 미국은 `InterestExpense` → `InterestExpenseNonoperating` → `InterestExpenseDebt` 중 첫 매치만 쓰고, 없으면 None이다. AAPL은 2024년부터 셋 다 없다. 이자보상배율 하드필터는 §8.1대로 건너뛰고, 점수에서는 그 지표가 MISSING이 되어 안정성 축의 유효만점이 줄어든다(§8.3). 둘 다 이미 있는 규칙이다 — `analysis/`를 고칠 일이 없다.

---

## 파일 구조

| 파일 | 책임 | 상태 |
|---|---|---|
| `src/stockscout/config.py` | 시장별 비밀값 요구, SEC User-Agent 검증 | 수정 |
| `config/.env`, `config/.env.example` | `SEC_USER_AGENT` | 수정 |
| `src/stockscout/store/schema.sql` | `snapshots.market` | 수정 |
| `src/stockscout/store/db.py` | `snapshots.market` 마이그레이션 | 수정 |
| `src/stockscout/models.py` | `Snapshot.market` | 수정 |
| `src/stockscout/store/repository.py` | 시장별 최신 스냅샷 | 수정 |
| `src/stockscout/pipeline/analyze.py` | 스냅샷에 market 기록 | 수정 |
| `src/stockscout/web/api.py`, `web/schemas.py` | `?market=`, 티커의 시장으로 스냅샷 찾기 | 수정 |
| `src/stockscout/sources/universe.py` | 시장별 티커 표기, YAML의 종목명 | 수정 |
| `config/universe.yaml` | `US:` 40종목 | 수정 |
| `src/stockscout/sources/edgar.py` | 연간 값 추출(순수 함수) + SEC HTTP | **신규** |
| `src/stockscout/normalize/us_mapping.py` | US-GAAP 태그 → Financials | **신규** |
| `src/stockscout/pipeline/normalize.py` | `NORMALIZERS["US"]` 등록 | 수정 |
| `src/stockscout/sources/yahoo.py` | yfinance 수정종가 | **신규** |
| `pyproject.toml` | `yfinance` 의존성 | 수정 |
| `src/stockscout/cli.py` | 시장별 소스 조립, 통화별 출력 | 수정 |
| `src/stockscout/web/static/*` | KR \| US 탭, USD 표시 | 수정 |

`edgar.py`에 추출 함수를 함께 두는 이유: 연간 값 고르기는 **SEC 응답 형식**에 대한 지식이지 계정 해석이 아니다. `normalize/`는 "어떤 태그가 매출인가"만 안다. DART도 같다 — `dart.py`가 응답을 연도별로 받아 오고, `kr_mapping.py`가 계정을 해석한다.

### 이 계획에서 빼는 것

- **S&P 500 전체.** 40종목으로 매핑 적중률을 실측하고 고친 뒤 넓힌다(설계서 §18.1)
- **감사의견.** XBRL에 없다. 미국 상장사는 비적정 의견으로는 10-K를 낼 수 없어(SEC 규정 S-X 2-02) 필터의 실익도 작다
- **배당.** 한국과 같은 이유로 점수에 넣지 않는다(§8.2)
- **티커 표기 변환.** SEC(`company_tickers.json`)와 Yahoo가 둘 다 `BRK-B`처럼 하이픈을 쓴다. DB도 하이픈으로 둔다

---

## 실행 규칙

- 인터프리터는 `.venv/Scripts/python.exe`다. 시스템 python이 아니다
- 테스트는 `.venv/Scripts/python.exe -m pytest ...`. `pyproject.toml`에 `addopts = "-q"`가 있으므로 `-q`를 **더 주지 마라**
- 콘솔이 cp949다. `✓` `⚠` `★` `—` 를 `print`하면 `UnicodeEncodeError`가 난다. 소스 문자열에 담는 것은 파일이 UTF-8이라 괜찮다
- `filterwarnings = ["error", ...]`다. 새 경고가 뜨면 테스트가 실패한다
- **SEC에는 반드시 `SEC_USER_AGENT`를 붙여 요청한다.** 없으면 403이다. 단위 테스트는 `httpx.MockTransport`로 네트워크를 쓰지 않는다
- 매 Task 끝에 커밋한다. 커밋 메시지 마지막 줄은 `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`

---

## Task 1: 비밀값 — 시장별로 필요한 것만 요구한다

**왜:** `load_secrets`는 DART 키가 없으면 무조건 죽는다. 미국만 수집하는데 DART 키를 요구하면 안 된다. 반대로 미국은 연락처가 담긴 `SEC_USER_AGENT`가 없으면 SEC가 403을 낸다(실측). 지금 기본값 `stockscout/0.1`은 연락처가 없어 정책 위반이다.

**Files:**
- Modify: `src/stockscout/config.py:112-147`
- Modify: `config/.env`, `config/.env.example`
- Test: `tests/test_config.py`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_config.py` 끝에 붙인다.

```python
# --- 시장별 비밀값 (계획 E) -------------------------------------------


def test_us_does_not_require_a_dart_key(tmp_path, monkeypatch):
    """미국만 수집하는데 DART 키가 없다고 죽으면 안 된다."""
    monkeypatch.delenv("DART_API_KEY", raising=False)
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    env = tmp_path / ".env"
    env.write_text("SEC_USER_AGENT=stockscout me@example.com\n", encoding="utf-8")

    secrets = load_secrets(env, market="US")

    assert secrets.sec_user_agent == "stockscout me@example.com"
    assert secrets.dart_api_key is None


def test_us_requires_a_contact_in_the_user_agent(tmp_path, monkeypatch):
    """SEC는 연락처 없는 User-Agent를 403으로 막는다 (설계서 §18.2)."""
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    env = tmp_path / ".env"
    env.write_text("SEC_USER_AGENT=stockscout/0.1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="SEC_USER_AGENT"):
        load_secrets(env, market="US")


def test_us_requires_a_user_agent_at_all(tmp_path, monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    env = tmp_path / ".env"
    env.write_text("DART_API_KEY=abc\n", encoding="utf-8")

    with pytest.raises(ValueError, match="SEC_USER_AGENT"):
        load_secrets(env, market="US")


def test_kr_is_still_the_default_market(tmp_path, monkeypatch):
    """기존 호출(load_secrets(env))은 지금처럼 DART 키를 요구한다."""
    monkeypatch.delenv("DART_API_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text("SEC_USER_AGENT=stockscout me@example.com\n", encoding="utf-8")

    with pytest.raises(ValueError, match="DART_API_KEY"):
        load_secrets(env)


def test_unknown_market_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="XX"):
        load_secrets(tmp_path / ".env", market="XX")
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_config.py`
Expected: FAIL — `TypeError: load_secrets() got an unexpected keyword argument 'market'`

- [ ] **Step 3: 구현한다**

`src/stockscout/config.py`의 `Secrets`와 `load_secrets`를 바꾼다.

```python
class Secrets(BaseModel):
    """시장마다 필요한 비밀값이 다르다. 그 시장에 필요 없는 값은 None일 수 있다."""

    dart_api_key: str | None = None
    sec_user_agent: str | None = None


SECRETS_MARKETS = ("KR", "US")


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
```

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_config.py tests/test_cli.py`
Expected: PASS. `test_env_var_overrides_dotenv_file`이 `sec_user_agent == "agent-from-file"`을 확인하는데, 기본 시장이 KR이라 연락처 검사를 하지 않으므로 그대로 통과한다.

- [ ] **Step 5: `.env`에 연락처를 넣는다**

`config/.env`(커밋되지 않는다 — `.gitignore`에 있다)에 한 줄을 더한다. 사용자가 Git 이메일을 쓰기로 승인했다(설계서 §18.2).

```
SEC_USER_AGENT=stockscout rusipul@gmail.com
```

`config/.env.example`의 SEC 줄을 고친다.

```
# SEC EDGAR는 연락처(이메일)가 담긴 User-Agent를 요구한다. 없으면 403이다
SEC_USER_AGENT=stockscout your@email.com
```

- [ ] **Step 6: 커밋**

```bash
git add src/stockscout/config.py config/.env.example tests/test_config.py
git commit -m "feat: 비밀값을 시장별로 필요한 것만 요구한다

미국만 수집하는데 DART 키가 없다고 죽으면 안 된다. 반대로 미국은
연락처가 담긴 SEC_USER_AGENT가 없으면 SEC가 403을 낸다(실측). 기본값
'stockscout/0.1'은 연락처가 없어 정책 위반이었다 - 기본값을 없애고,
'@'가 없으면 무엇을 넣으라고 말하며 멈춘다.

load_secrets의 기본 시장은 KR이라 기존 호출은 그대로다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 2: 스냅샷을 시장별로 나눈다

**왜:** `get_latest_snapshot()`이 시장을 모른다. 미국을 분석하는 순간 화면에서 한국이 사라진다.

**Files:**
- Modify: `src/stockscout/store/schema.sql:58-68`
- Modify: `src/stockscout/store/db.py`
- Modify: `src/stockscout/models.py` (`Snapshot`)
- Modify: `src/stockscout/store/repository.py` (`save_snapshot`, `get_latest_snapshot`)
- Modify: `src/stockscout/pipeline/analyze.py` (`Snapshot(...)` 생성부)
- Test: `tests/test_repository_snapshots.py`, `tests/test_db.py`, `tests/test_pipeline_analyze.py`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_repository_snapshots.py` 끝에 붙인다. 이 파일의 `repo` fixture를 쓴다.

```python
# --- 시장별 스냅샷 (계획 E) -------------------------------------------


def _snap(market, run_at):
    from stockscout.models import Snapshot

    return Snapshot(run_at=run_at, as_of="2026-09-10", config_hash="h",
                    config_json="{}", market=market)


def test_latest_snapshot_is_scoped_to_market(repo):
    """미국을 나중에 분석해도 한국의 최신 스냅샷은 한국 것이어야 한다."""
    kr = repo.save_snapshot(_snap("KR", "2026-09-11T00:00:00+00:00"), [])
    us = repo.save_snapshot(_snap("US", "2026-09-12T00:00:00+00:00"), [])

    assert repo.get_latest_snapshot(market="KR").snapshot_id == kr
    assert repo.get_latest_snapshot(market="US").snapshot_id == us
    assert repo.get_latest_snapshot(market="KR").market == "KR"


def test_latest_snapshot_without_market_is_the_newest_of_all(repo):
    repo.save_snapshot(_snap("KR", "2026-09-11T00:00:00+00:00"), [])
    us = repo.save_snapshot(_snap("US", "2026-09-12T00:00:00+00:00"), [])

    assert repo.get_latest_snapshot().snapshot_id == us


def test_latest_snapshot_is_none_for_a_market_never_analyzed(repo):
    repo.save_snapshot(_snap("KR", "2026-09-11T00:00:00+00:00"), [])

    assert repo.get_latest_snapshot(market="US") is None


def test_snapshot_market_defaults_to_kr():
    """market을 넘기지 않는 기존 코드는 한국 스냅샷을 만든다."""
    from stockscout.models import Snapshot

    snap = Snapshot(run_at="x", config_hash="h", config_json="{}")

    assert snap.market == "KR"
```

`tests/test_db.py` 끝에 붙인다.

```python
def test_init_db_adds_snapshot_market_to_an_old_database(tmp_path):
    """계획 E 이전 DB의 스냅샷은 전부 한국 것이다. KR로 채워져야 한다."""
    import sqlite3

    from stockscout.store.db import init_db

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE snapshots (snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "run_at TEXT NOT NULL, config_hash TEXT NOT NULL, "
        "config_json TEXT NOT NULL, note TEXT, as_of TEXT)"
    )
    conn.execute("INSERT INTO snapshots (run_at, config_hash, config_json) "
                 "VALUES ('2026-09-11', 'h', '{}')")
    conn.commit()

    init_db(conn)

    (market,) = conn.execute("SELECT market FROM snapshots").fetchone()
    assert market == "KR"
    conn.close()
```

`tests/test_pipeline_analyze.py`의 `test_analyze_as_of_is_scoped_to_market` 뒤에 붙인다.

```python
def test_analyze_records_the_market_on_the_snapshot(repo):
    seed(repo, "AAPL", market="US")

    summary = analyze_market(repo, market="US", config=cfg())

    assert repo.get_latest_snapshot(market="US").snapshot_id == summary.snapshot_id
    assert repo.get_latest_snapshot(market="KR") is None
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_repository_snapshots.py tests/test_db.py tests/test_pipeline_analyze.py`
Expected: FAIL — `Snapshot`에 `market`이 없다

- [ ] **Step 3: 구현한다**

`src/stockscout/store/schema.sql`의 `snapshots`에 컬럼을 더한다(`as_of` 다음 줄).

```sql
  as_of       TEXT,  -- 이 스냅샷이 고정한 가격 기준일. run_at(실행 시각)과는
                      -- 별개다: 수집을 며칠 건너뛰면 run_at은 흘러가도
                      -- as_of는 그대로일 수 있다. 기존 DB는 store/db.py의
                      -- init_db()가 ALTER TABLE로 얹어주므로 NULL 허용이다.
  market      TEXT NOT NULL DEFAULT 'KR'  -- 시장마다 따로 분석·저장한다 (설계서 §18.5)
```

`as_of TEXT` 뒤에 쉼표를 붙이는 것을 잊지 마라.

`src/stockscout/store/db.py`의 `init_db`에 호출을 더하고 함수를 만든다.

```python
def init_db(conn: sqlite3.Connection) -> None:
    """스키마를 적용한다. 여러 번 호출해도 안전하다(CREATE TABLE IF NOT EXISTS)."""
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    _add_snapshots_as_of_column_if_missing(conn)
    _add_financials_columns_if_missing(conn)
    _add_snapshots_market_column_if_missing(conn)
    conn.commit()


def _add_snapshots_market_column_if_missing(conn: sqlite3.Connection) -> None:
    """계획 E가 더한 컬럼을 기존 DB에 얹는다.

    계획 E 이전 스냅샷은 전부 한국 것이므로 DEFAULT 'KR'이 그대로 맞는
    값이다. SQLite는 NOT NULL 컬럼도 DEFAULT가 있으면 ADD COLUMN을 허용하고
    기존 행을 그 값으로 채운다.
    """
    columns = {row[1] for row in conn.execute("PRAGMA table_info(snapshots)")}
    if "market" not in columns:
        conn.execute(
            "ALTER TABLE snapshots ADD COLUMN market TEXT NOT NULL DEFAULT 'KR'"
        )
```

`src/stockscout/models.py`의 `Snapshot`에 필드를 더한다(`note` 다음).

```python
    note: str | None = None
    # 시장마다 기준일·통화·수집 시점이 달라 따로 저장한다 (설계서 §18.5).
    # 기본값이 KR인 이유: 계획 E 이전 코드와 스냅샷은 전부 한국 것이다.
    market: str = "KR"
```

`src/stockscout/store/repository.py`의 `save_snapshot` INSERT를 바꾼다.

```python
            cur = self.conn.execute(
                "INSERT INTO snapshots "
                "(run_at, as_of, config_hash, config_json, note, market) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (snapshot.run_at, snapshot.as_of, snapshot.config_hash,
                 snapshot.config_json, snapshot.note, snapshot.market),
            )
```

`get_latest_snapshot`을 바꾼다.

```python
    def get_latest_snapshot(self, market: str | None = None) -> Snapshot | None:
        """가장 최근 스냅샷. market을 넘기면 그 시장의 것만 본다.

        화면과 API는 항상 market을 넘긴다 - 미국을 나중에 분석했다고
        한국 화면이 미국 결과를 보여주면 안 된다. market을 생략하면 전
        시장에서 가장 최근 것이다 (latest_price_date_any와 같은 규칙).
        """
        if market:
            row = self.conn.execute(
                "SELECT * FROM snapshots WHERE market = ? "
                "ORDER BY snapshot_id DESC LIMIT 1",
                (market,),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM snapshots ORDER BY snapshot_id DESC LIMIT 1"
            ).fetchone()
        if not row:
            return None
        return Snapshot(
            snapshot_id=row["snapshot_id"], run_at=row["run_at"],
            as_of=row["as_of"],
            config_hash=row["config_hash"], config_json=row["config_json"],
            note=row["note"], market=row["market"],
        )
```

`src/stockscout/pipeline/analyze.py`의 `Snapshot(...)` 생성부에 `market=market,`을 더한다(`note=note,` 다음).

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

Run: `.venv/Scripts/python.exe -m pytest`
Expected: PASS. 실DB 통합 테스트도 통과해야 한다 — 실DB는 다음 `init_db` 호출에서 마이그레이션된다.

- [ ] **Step 5: 실DB 마이그레이션을 확인한다**

```bash
.venv/Scripts/python.exe -c "import sqlite3; from stockscout.store.db import init_db; c=sqlite3.connect('data/stockscout.db'); init_db(c); print(c.execute('SELECT market, COUNT(*) FROM snapshots GROUP BY market').fetchall())"
```

Expected: `[('KR', 8)]`

- [ ] **Step 6: 커밋**

```bash
git add src/stockscout/store/schema.sql src/stockscout/store/db.py src/stockscout/models.py src/stockscout/store/repository.py src/stockscout/pipeline/analyze.py tests/test_repository_snapshots.py tests/test_db.py tests/test_pipeline_analyze.py
git commit -m "feat: 스냅샷을 시장별로 나눈다

get_latest_snapshot이 시장을 몰라, 미국을 분석하는 순간 화면에서 한국이
사라졌다. 시장마다 기준일·통화·수집 시점이 달라 한 스냅샷에 섞을
이유가 없다 (설계서 §18.5).

기존 스냅샷은 전부 한국 것이라 마이그레이션이 KR로 채운다.
Snapshot.market의 기본값도 KR이라 기존 코드와 테스트는 그대로다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 3: API가 시장을 받는다

**왜:** 설계서 §11.3이 이미 `?market=`을 적어 두었다. 상세·차트는 티커로 찾으므로 그 티커의 시장에서 최신 스냅샷을 고른다.

**Files:**
- Modify: `src/stockscout/web/api.py`
- Modify: `src/stockscout/web/schemas.py` (`SnapshotInfo`)
- Test: `tests/test_web_markets.py` (신규)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_web_markets.py`를 만든다.

```python
"""시장별 스냅샷을 API가 구분하는지. 설계서 §18.5."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from stockscout.models import (
    AnalysisResult,
    FilterOutcome,
    PriceBar,
    PriceStats,
    Security,
    Snapshot,
    Valuation,
)
from stockscout.store.db import connect, init_db
from stockscout.store.repository import Repository
from stockscout.web.api import create_app


def result(ticker, price):
    return AnalysisResult(
        ticker=ticker, filters=FilterOutcome(failures=[]),
        stats=PriceStats(price_current=price,
                         avg_price={1: price, 3: price, 5: price},
                         high_5y=price, drop_vs_avg={1: 0.0, 3: 0.0, 5: 0.0},
                         drop_vs_high=0.0),
        valuation=Valuation(),
    )


@pytest.fixture
def db(tmp_path):
    """한국을 먼저, 미국을 나중에 분석한 DB. 최신 스냅샷은 미국이다."""
    path = tmp_path / "t.db"
    with connect(path) as conn:
        init_db(conn)
        repo = Repository(conn)
        repo.upsert_security(Security(ticker="005930.KS", market="KR",
                                      name="삼성전자", currency="KRW"))
        repo.upsert_security(Security(ticker="AAPL", market="US",
                                      name="Apple", currency="USD"))
        repo.save_price_bars(
            [PriceBar(ticker="AAPL", date=date(2026, 9, 10), close=230.5)],
            source="test")
        repo.save_snapshot(
            Snapshot(run_at="2026-09-11T00:00:00+00:00", as_of="2026-09-10",
                     config_hash="h", config_json="{}", market="KR"),
            [result("005930.KS", 60000.0)])
        repo.save_snapshot(
            Snapshot(run_at="2026-09-12T00:00:00+00:00", as_of="2026-09-10",
                     config_hash="h", config_json="{}", market="US"),
            [result("AAPL", 230.5)])
    return path


@pytest.fixture
def client(db):
    return TestClient(create_app(db_path=db))


def test_snapshot_defaults_to_kr_even_when_us_is_newer(client):
    body = client.get("/api/snapshot").json()

    assert body["market"] == "KR"
    assert body["total"] == 1


def test_snapshot_can_be_asked_for_us(client):
    body = client.get("/api/snapshot", params={"market": "US"}).json()

    assert body["market"] == "US"


def test_securities_list_only_that_market(client):
    kr = client.get("/api/securities").json()
    us = client.get("/api/securities", params={"market": "US"}).json()

    assert [r["ticker"] for r in kr] == ["005930.KS"]
    assert [r["ticker"] for r in us] == ["AAPL"]


def test_detail_finds_a_us_ticker_in_the_us_snapshot(client):
    """상세는 티커로 찾는다. 그 티커의 시장에서 최신 스냅샷을 골라야 한다."""
    body = client.get("/api/securities/AAPL").json()

    assert body["price_current"] == 230.5
    assert body["currency"] == "USD"


def test_detail_still_finds_a_kr_ticker_after_us_was_analyzed(client):
    response = client.get("/api/securities/005930.KS")

    assert response.status_code == 200


def test_prices_find_a_us_ticker(client):
    response = client.get("/api/securities/AAPL/prices")

    assert response.status_code == 200
    assert response.json()["price_current"] == 230.5


def test_missing_market_snapshot_says_which_command_to_run(tmp_path):
    path = tmp_path / "kr-only.db"
    with connect(path) as conn:
        init_db(conn)
        Repository(conn).save_snapshot(
            Snapshot(run_at="x", config_hash="h", config_json="{}"), [])

    response = TestClient(create_app(db_path=path)).get(
        "/api/snapshot", params={"market": "US"})

    assert response.status_code == 404
    assert "analyze --market US" in response.json()["detail"]


def test_unknown_market_is_rejected(client):
    assert client.get("/api/snapshot", params={"market": "JP"}).status_code == 422
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_web_markets.py`
Expected: FAIL — `market`이 응답에 없다

- [ ] **Step 3: 구현한다**

`src/stockscout/web/schemas.py`의 `SnapshotInfo`에 필드를 더한다(`snapshot_id` 다음).

```python
    snapshot_id: int
    market: str        # 'KR' | 'US'. 화면이 통화 표시와 탭을 고른다
```

`src/stockscout/web/api.py`에서:

1. 파일 위쪽 상수 근처에 더한다.

```python
Market = Literal["KR", "US"]


def _no_snapshot(market: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail=(f"{market} 분석 결과가 없습니다. "
                f"stockscout analyze --market {market} 를 먼저 실행하세요."),
    )


def _snapshot_for_ticker(repo: Repository, ticker: str):
    """티커의 시장에서 최신 스냅샷과 그 종목의 행을 찾는다.

    상세·차트 API는 티커만 받는다. 시장을 모르고 전체 최신 스냅샷을 보면
    미국을 나중에 분석한 순간 한국 종목이 전부 404가 된다.
    """
    master = repo.get_security(ticker)
    market = master.market if master else "KR"
    snap = repo.get_latest_snapshot(market=market)
    if snap is None:
        raise _no_snapshot(market)
    row = next(
        (r for r in repo.get_analysis_results(snap.snapshot_id)
         if r["ticker"] == ticker),
        None,
    )
    if row is None:
        raise HTTPException(status_code=404,
                            detail=f"{ticker} 는 최신 스냅샷에 없습니다.")
    return master, snap, row
```

2. `get_snapshot`이 `market: Market = "KR"`을 받고, `repo.get_latest_snapshot(market=market)`을 쓰고, 없으면 `raise _no_snapshot(market)`, `SnapshotInfo(...)`에 `market=snap.market,`을 넘긴다.

3. `list_securities`에 `market: Market = "KR"` 인자를 더하고(`q` 앞), 같은 방식으로 `repo.get_latest_snapshot(market=market)`·`_no_snapshot(market)`을 쓴다. `masters`는 `repo.list_securities(market=market, in_universe_only=False)`로 좁힌다.

4. `get_security`의 `build` 앞부분(스냅샷·행·master를 찾는 부분)을 한 줄로 바꾼다.

```python
        def build(repo: Repository) -> SecurityDetail:
            master, snap, row = _snapshot_for_ticker(repo, ticker)
            failures = json.loads(row["filter_failures"] or "[]")
            financials = repo.get_financials(ticker)
```

5. `get_prices`의 `build` 앞부분도 같다.

```python
        def build(repo: Repository) -> PriceSeries:
            _, snap, row = _snapshot_for_ticker(repo, ticker)
            as_of = date.fromisoformat(snap.as_of) if snap.as_of else None
```

`test_404_detail_names_the_command_to_run`(기존)은 `"analyze" in detail`을 확인하므로 새 문구로도 통과한다.

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_web_markets.py tests/test_web_snapshot.py tests/test_web_securities.py tests/test_web_detail.py tests/test_web_prices.py tests/test_web_integration.py`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add src/stockscout/web/api.py src/stockscout/web/schemas.py tests/test_web_markets.py
git commit -m "feat: API가 시장별 스냅샷을 구분한다

/api/snapshot과 /api/securities가 ?market=(기본 KR)을 받는다. 설계서
§11.3이 이미 적어 둔 형태다.

상세·차트는 티커만 받으므로 그 티커의 시장에서 최신 스냅샷을 찾는다.
전체 최신을 보면 미국을 분석한 순간 한국 종목이 전부 404가 된다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 4: 유니버스 — 미국 40종목

**왜:** `YamlUniverse`는 모든 코드에 `.KS`를 붙이고 종목명을 pykrx로 찾는다. 미국 티커는 그대로 두고, 종목명은 YAML에 적는다(pykrx는 미국 종목을 모른다).

**Files:**
- Modify: `src/stockscout/sources/universe.py`
- Modify: `config/universe.yaml`
- Test: `tests/test_universe_yaml.py`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_universe_yaml.py` 끝에 붙인다.

```python
US_YAML = """
US:
  exchange: US
  currency: USD
  tickers:
    - AAPL
    - brk-b
    - JPM
  names:
    AAPL: Apple
    BRK-B: Berkshire Hathaway
  is_financial:
    - BRK-B
    - JPM
"""


def test_us_tickers_keep_their_symbol_without_a_suffix(tmp_path):
    path = _write_universe(tmp_path, US_YAML)

    secs = YamlUniverse(path=path, market="US").fetch()

    assert [s.ticker for s in secs] == ["AAPL", "BRK-B", "JPM"]
    assert all(s.currency == "USD" and s.market == "US" for s in secs)


def test_us_names_come_from_the_yaml(tmp_path):
    """pykrx는 미국 종목을 모른다. 이름이 없으면 티커를 이름으로 쓴다."""
    path = _write_universe(tmp_path, US_YAML)

    secs = {s.ticker: s for s in YamlUniverse(path=path, market="US").fetch()}

    assert secs["AAPL"].name == "Apple"
    assert secs["BRK-B"].name == "Berkshire Hathaway"
    assert secs["JPM"].name == "JPM"


def test_us_name_lookup_never_calls_pykrx(tmp_path):
    path = _write_universe(tmp_path, US_YAML)

    def explode(code):
        raise AssertionError("미국 종목에 pykrx 이름 조회를 쓰면 안 된다")

    YamlUniverse(path=path, market="US", name_fn=explode).fetch()


def test_us_financial_flags_match_after_upper_casing(tmp_path):
    path = _write_universe(tmp_path, US_YAML)

    secs = {s.ticker: s for s in YamlUniverse(path=path, market="US").fetch()}

    assert secs["BRK-B"].is_financial is True
    assert secs["JPM"].is_financial is True
    assert secs["AAPL"].is_financial is False


def test_kr_names_in_yaml_override_pykrx(tmp_path):
    """names는 시장과 무관하게 먼저 본다. 조회 실패를 피하고 싶을 때 쓴다."""
    path = _write_universe(tmp_path, SAMPLE_YAML + '  names:\n    "005930": 삼성전자\n')

    secs = YamlUniverse(path=path, market="KR",
                        name_fn=lambda code: f"pykrx-{code}").fetch()

    assert secs[0].name == "삼성전자"
    assert secs[1].name == "pykrx-000660"


def test_the_real_universe_has_a_us_section():
    secs = YamlUniverse(market="US").fetch()

    assert 35 <= len(secs) <= 45
    assert any(s.is_financial for s in secs)
    assert all("." not in s.ticker for s in secs)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_universe_yaml.py`
Expected: FAIL — 티커가 `AAPL.KS`가 된다

- [ ] **Step 3: 구현한다**

`src/stockscout/sources/universe.py`의 `fetch` 본문에서 티커·이름·금융 판정 부분을 바꾼다.

```python
        section = raw[self.market]
        exchange = section.get("exchange")
        currency = section.get("currency")
        tickers: list[str] = [str(t) for t in section.get("tickers", [])]
        names: dict[str, str] = {
            self._symbol(str(k)): v for k, v in (section.get("names") or {}).items()
        }
        financial = {self._symbol(str(t)) for t in section.get("is_financial", [])}

        securities: list[Security] = []
        for code in tickers:
            symbol = self._symbol(code)
            securities.append(
                Security(
                    ticker=self._ticker(code),
                    market=self.market,
                    name=names.get(symbol) or self._lookup_name(code),
                    exchange=exchange,
                    currency=currency,
                    is_financial=symbol in financial,
                    in_universe=True,
                )
            )
        return securities

    def _symbol(self, code: str) -> str:
        """YAML에 적힌 코드를 비교용으로 맞춘다. 미국 티커는 대문자다."""
        return code if self.market == "KR" else code.upper()

    def _ticker(self, code: str) -> str:
        """DB의 ticker. 한국은 거래소 접미사를 붙이고 미국은 그대로 둔다.

        미국은 SEC(company_tickers.json)와 Yahoo가 둘 다 'BRK-B'처럼 하이픈을
        쓴다. 변환하지 않는 것이 두 소스를 함께 맞추는 방법이다.
        """
        return to_ticker(code) if self.market == "KR" else code.upper()

    def _lookup_name(self, code: str) -> str:
        """YAML에 이름이 없을 때. pykrx는 한국 종목만 안다."""
        if self.market != "KR":
            return self._symbol(code)
        try:
            return self._name(code)
        except Exception:
            # 손으로 고른 목록이라 조회 실패는 일시적 네트워크 문제일 뿐,
            # 종목이 잘못된 게 아니다. KrxUniverse와 달리 건너뛰지 않는다.
            return code
```

- [ ] **Step 4: `config/universe.yaml`에 미국 섹션을 더한다**

파일 끝에 붙인다. 업종을 섞고, 은행·보험 4종목은 `is_financial`로 표시해 필터를 관통시킨다. 에스티로더(EL)·인텔(INTC)·나이키(NKE)는 최근 크게 빠진 종목이라 등급 판정이 실제로 갈리는지 보기 위해 넣는다.

```yaml

US:
  exchange: US
  currency: USD
  # 티커는 SEC·Yahoo 표기 그대로다 (BRK-B처럼 하이픈). pykrx가 미국 종목을
  # 모르므로 이름은 여기 적는다.
  tickers:
    - AAPL
    - MSFT
    - NVDA
    - GOOGL
    - META
    - AMZN
    - INTC
    - CSCO
    - ORCL
    - ADBE
    - JNJ
    - PFE
    - MRK
    - UNH
    - ABT
    - KO
    - PEP
    - PG
    - WMT
    - MCD
    - NKE
    - HD
    - SBUX
    - TGT
    - EL
    - CAT
    - BA
    - MMM
    - UPS
    - HON
    - GE
    - XOM
    - CVX
    - VZ
    - T
    - DIS
    - JPM
    - BAC
    - GS
    - BRK-B
  names:
    AAPL: Apple
    MSFT: Microsoft
    NVDA: NVIDIA
    GOOGL: Alphabet
    META: Meta Platforms
    AMZN: Amazon
    INTC: Intel
    CSCO: Cisco
    ORCL: Oracle
    ADBE: Adobe
    JNJ: Johnson & Johnson
    PFE: Pfizer
    MRK: Merck
    UNH: UnitedHealth
    ABT: Abbott
    KO: Coca-Cola
    PEP: PepsiCo
    PG: Procter & Gamble
    WMT: Walmart
    MCD: McDonald's
    NKE: Nike
    HD: Home Depot
    SBUX: Starbucks
    TGT: Target
    EL: Estee Lauder
    CAT: Caterpillar
    BA: Boeing
    MMM: 3M
    UPS: UPS
    HON: Honeywell
    GE: GE Aerospace
    XOM: Exxon Mobil
    CVX: Chevron
    VZ: Verizon
    T: AT&T
    DIS: Disney
    JPM: JPMorgan Chase
    BAC: Bank of America
    GS: Goldman Sachs
    BRK-B: Berkshire Hathaway
  is_financial:
    - JPM
    - BAC
    - GS
    - BRK-B
```

- [ ] **Step 5: 테스트가 통과하는지 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_universe_yaml.py`
Expected: PASS

- [ ] **Step 6: 커밋**

```bash
git add src/stockscout/sources/universe.py config/universe.yaml tests/test_universe_yaml.py
git commit -m "feat: 유니버스에 미국 40종목

미국 티커는 접미사 없이 그대로 둔다. SEC와 Yahoo가 둘 다 BRK-B처럼
하이픈을 써서, 변환하지 않는 것이 두 소스를 함께 맞추는 방법이다.

pykrx는 미국 종목을 모르므로 이름은 YAML의 names에 적는다. names는
한국에도 쓸 수 있고 pykrx 조회보다 먼저 본다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 5: EDGAR 연간 값 추출 — 순수 함수

**왜:** 이 계획에서 가장 틀리기 쉬운 곳이다. "핵심 결정 1"을 보라. 네트워크 없이 검증할 수 있게 HTTP와 분리한다.

**Files:**
- Create: `src/stockscout/sources/edgar.py`
- Test: `tests/test_edgar_annual.py` (신규)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_edgar_annual.py`를 만든다. companyfacts의 모양을 손으로 만든다 — 각 테스트가 무엇을 보는지 한눈에 보여야 한다.

```python
"""companyfacts에서 연간 값을 고른다. 설계서 §18.2·§18.3."""

from stockscout.sources.edgar import annual_facts


def fact(val, end, start=None, form="10-K", filed="2025-11-01", fy=2025):
    entry = {"val": val, "end": end, "form": form, "filed": filed, "fy": fy,
             "fp": "FY"}
    if start:
        entry["start"] = start
    return entry


def facts(us_gaap=None, dei=None):
    def wrap(tags, unit):
        return {tag: {"units": {unit(tag): entries}} for tag, entries in tags.items()}

    unit = lambda tag: "shares" if "Shares" in tag else "USD"
    return {"facts": {"us-gaap": wrap(us_gaap or {}, unit),
                      "dei": wrap(dei or {}, unit)}}


def test_an_annual_flow_lands_in_the_year_of_its_end_date():
    cf = facts({"Revenues": [fact(100, "2025-09-27", "2024-09-29")]})

    years = annual_facts(cf)

    assert years[2025]["facts"]["Revenues"] == 100
    assert years[2025]["period_end"] == "2025-09-27"


def test_a_quarter_inside_a_10k_is_not_annual():
    """실측: AAPL의 Revenues에 form=10-K이면서 한 분기짜리 항목이 있다."""
    cf = facts({"Revenues": [
        fact(400, "2025-09-27", "2024-09-29"),
        fact(100, "2025-09-27", "2025-06-29"),   # 4분기만
    ]})

    assert annual_facts(cf)[2025]["facts"]["Revenues"] == 400


def test_a_10q_is_never_used():
    cf = facts({"Revenues": [
        fact(400, "2025-09-27", "2024-09-29"),
        fact(999, "2025-09-27", "2024-09-29", form="10-Q"),
    ]})

    assert annual_facts(cf)[2025]["facts"]["Revenues"] == 400


def test_comparatives_are_assigned_by_period_not_by_fy():
    """2025년 10-K에 든 2024년 비교 수치는 fy=2025지만 2024년 값이다."""
    cf = facts({"Revenues": [
        fact(400, "2025-09-27", "2024-09-29", fy=2025),
        fact(350, "2024-09-28", "2023-10-01", fy=2025),
    ]})

    years = annual_facts(cf)

    assert years[2025]["facts"]["Revenues"] == 400
    assert years[2024]["facts"]["Revenues"] == 350


def test_a_restatement_wins_over_the_original():
    cf = facts({"Revenues": [
        fact(350, "2024-09-28", "2023-10-01", filed="2024-11-01"),
        fact(355, "2024-09-28", "2023-10-01", filed="2025-11-01"),   # 정정
    ]})

    assert annual_facts(cf)[2024]["facts"]["Revenues"] == 355


def test_a_balance_sheet_value_is_taken_at_the_fiscal_year_end():
    cf = facts({
        "Revenues": [fact(400, "2025-09-27", "2024-09-29")],
        "Assets": [fact(900, "2025-09-27"), fact(800, "2025-06-28")],
    })

    assert annual_facts(cf)[2025]["facts"]["Assets"] == 900


def test_a_cover_page_share_count_dated_after_year_end_is_kept():
    """dei:EntityCommonStockSharesOutstanding은 표지 날짜(결산 후 몇 주)로 온다."""
    cf = facts(
        {"Revenues": [fact(400, "2025-09-27", "2024-09-29")]},
        dei={"EntityCommonStockSharesOutstanding": [
            fact(15, "2025-10-17"),
            fact(99, "2026-04-17", form="10-Q"),
        ]},
    )

    assert annual_facts(cf)[2025]["facts"]["EntityCommonStockSharesOutstanding"] == 15


def test_a_january_year_end_belongs_to_the_year_it_ends_in():
    """월마트는 2025-01-31에 끝나는 해를 FY2025라 부른다. end의 연도와 같다."""
    cf = facts({"Revenues": [fact(680, "2025-01-31", "2024-02-01")]})

    assert set(annual_facts(cf)) == {2025}


def test_no_annual_flow_means_no_years():
    """연간 흐름 항목이 하나도 없으면 회계연도 말을 알 수 없다."""
    cf = facts({"Assets": [fact(900, "2025-09-27")]})

    assert annual_facts(cf) == {}


def test_other_units_are_ignored():
    cf = {"facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [fact(400, "2025-09-27", "2024-09-29")]}},
        "EarningsPerShareBasic": {"units": {"USD/shares": [
            fact(6.1, "2025-09-27", "2024-09-29")]}},
    }}}

    assert "EarningsPerShareBasic" not in annual_facts(cf)[2025]["facts"]
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_edgar_annual.py`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 구현한다**

`src/stockscout/sources/edgar.py`를 만든다. HTTP 부분은 Task 6에서 같은 파일에 더한다.

```python
"""SEC EDGAR XBRL companyfacts. https://www.sec.gov/edgar/sec-api-documentation

응답에서 연간 값을 골라 회계연도별로 나눈다. 태그 해석(어떤 태그가
매출인가)은 normalize/us_mapping.py의 몫이다 - DART의 dart.py와
kr_mapping.py의 분담과 같다.
"""

from __future__ import annotations

from datetime import date

ANNUAL_FORMS = {"10-K", "10-K/A"}

# 흐름 항목(손익·현금흐름)을 연간으로 볼 기간. 52/53주 회계연도가 있어
# 365일 정확히가 아니다(AAPL 2023 = 371일). 분기(약 91일)와는 넉넉히 떨어진다.
MIN_ANNUAL_DAYS = 350
MAX_ANNUAL_DAYS = 380

# 표지(dei:Entity...) 값은 결산일이 아니라 표지 작성일로 온다. 결산 후
# 이 일수 안의 10-K 표지 값을 그 회계연도 것으로 본다. 10-K 제출 기한이
# 결산 후 최장 90일이다.
COVER_WINDOW_DAYS = 120

# 금액과 주식수만 본다. 'USD/shares'(EPS) 같은 비율 단위는 쓰지 않는다.
UNITS = ("USD", "shares")
NAMESPACES = ("us-gaap", "dei")


def _date(text: str) -> date:
    return date.fromisoformat(text)


def _is_annual_period(entry: dict) -> bool:
    start = entry.get("start")
    if not start:
        return False
    days = (_date(entry["end"]) - _date(start)).days
    return MIN_ANNUAL_DAYS <= days <= MAX_ANNUAL_DAYS


def _annual_entries(companyfacts: dict) -> dict[str, list[dict]]:
    """태그 -> 10-K 계열 항목. 태그마다 첫 번째로 있는 단위 하나만 본다."""
    out: dict[str, list[dict]] = {}
    for namespace in NAMESPACES:
        for tag, body in companyfacts.get("facts", {}).get(namespace, {}).items():
            units = body.get("units", {})
            for unit in UNITS:
                if unit in units:
                    out[tag] = [e for e in units[unit]
                                if e.get("form") in ANNUAL_FORMS]
                    break
    return out


def _fiscal_year_ends(entries: dict[str, list[dict]]) -> dict[int, date]:
    """회계연도 -> 결산일. 연간 흐름 항목의 end에서 얻는다.

    fy를 쓰지 않는다. 10-K의 전기 비교 수치가 그 공시의 fy를 달고 오기
    때문이다(설계서 §18.2). 한 해에 결산일이 둘이면(결산월 변경) 늦은 쪽.
    """
    ends: dict[int, date] = {}
    for items in entries.values():
        for entry in items:
            if _is_annual_period(entry):
                end = _date(entry["end"])
                if end > ends.get(end.year, date.min):
                    ends[end.year] = end
    return ends


def _matches(tag: str, entry: dict, year_end: date) -> bool:
    end = _date(entry["end"])
    if entry.get("start"):
        return end == year_end and _is_annual_period(entry)
    if tag.startswith("Entity"):
        return 0 <= (end - year_end).days <= COVER_WINDOW_DAYS
    return end == year_end


def annual_facts(companyfacts: dict) -> dict[int, dict]:
    """companyfacts -> {회계연도: {"period_end": ISO 날짜, "facts": {태그: 값}}}.

    - 흐름 항목: 10-K 계열이고 기간이 350~380일인 것. 10-K 안에 분기 값이
      섞여 있다(실측: AAPL Revenues 2018-07-01 ~ 2018-09-29)
    - 시점 항목: 그 회계연도 결산일과 같은 날짜
    - 표지 항목(dei:Entity...): 결산 후 COVER_WINDOW_DAYS 안
    - 같은 (태그, 기간)이 여러 번 공시되면 filed가 가장 늦은 값(정정 공시)
    - 회계연도 = 결산일의 연도. 월마트 2025-01-31 -> 2025, 회사의 호칭과 같다
    """
    entries = _annual_entries(companyfacts)
    out: dict[int, dict] = {}
    for year, year_end in _fiscal_year_ends(entries).items():
        picked: dict[str, float] = {}
        for tag, items in entries.items():
            best: dict | None = None
            for entry in items:
                if not _matches(tag, entry, year_end):
                    continue
                if best is None or entry.get("filed", "") > best.get("filed", ""):
                    best = entry
            if best is not None:
                picked[tag] = best["val"]
        out[year] = {"period_end": year_end.isoformat(), "facts": picked}
    return out
```

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_edgar_annual.py`
Expected: PASS — `10 passed`

- [ ] **Step 5: 변이 테스트 3건**

| 변이 | 실패해야 할 테스트 |
|---|---|
| `_matches`의 `and _is_annual_period(entry)` 삭제 | `test_a_quarter_inside_a_10k_is_not_annual` |
| `entry.get("filed", "") > best.get(...)` → `<` | `test_a_restatement_wins_over_the_original` |
| `ends[end.year] = end`의 `end.year` → `entry["fy"]` (int 변환 포함) | `test_comparatives_are_assigned_by_period_not_by_fy` |

변이마다 파일을 백업해 두고 되돌린다. 마지막에 `git diff --stat -- src/`로 변이가 남지 않았는지 확인한다.

- [ ] **Step 6: 설계서 §18.3의 저장 범위를 고친다**

`docs/superpowers/specs/2026-09-10-stock-screener-design.md` §18.3의 이 문장을

```
  `raw_financials`에 `source='edgar'`로 저장한다. 원본 전체가 아니라
  **매핑 후보 태그만** 담는다(전체는 회사당 수백 KB다)
```

이렇게 바꾼다.

```
  `raw_financials`에 `source='edgar'`로 저장한다. 원본 전체(회사당 수백
  KB, 분기·비교 수치 포함)가 아니라 **연간 값 전체**를 담는다 - AAPL 한
  해가 10KB(태그 약 200개)다. 후보 태그만 담으면 매핑에 태그를 더할
  때마다 SEC에서 다시 받아야 한다
```

- [ ] **Step 7: 커밋**

```bash
git add src/stockscout/sources/edgar.py tests/test_edgar_annual.py docs/superpowers/specs/2026-09-10-stock-screener-design.md
git commit -m "feat: companyfacts에서 연간 값을 고른다

form과 fy를 믿지 않는다. AAPL의 10-K에는 한 분기짜리 매출이 섞여
있고(그대로 쓰면 매출이 4분의 1로 줄어 매출 급감 필터에 걸린다),
10-K의 전기 비교 수치는 그 공시의 fy를 달고 온다.

흐름 항목은 기간 350~380일만, 회계연도는 결산일의 연도로, 정정
공시는 filed가 늦은 쪽으로 정한다.

연간 값 전체를 저장하기로 설계서 §18.3을 고친다. AAPL 한 해가
10KB라, 후보 태그만 담아 매핑을 고칠 때마다 다시 받을 이유가 없다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 6: EDGAR HTTP — `EdgarSource`

**왜:** `FundamentalSource` 프로토콜을 구현한다. 회사당 호출 1회로 전 연도를 받아 Task 5의 함수로 나눈다.

**Files:**
- Modify: `src/stockscout/sources/edgar.py`
- Test: `tests/test_edgar_source.py` (신규)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_edgar_source.py`를 만든다. DART 테스트(`tests/test_dart_statements.py`)와 같은 `httpx.MockTransport` 패턴이다.

```python
"""SEC EDGAR HTTP. 네트워크 없이 MockTransport로 검증한다."""

import httpx
import pytest

from stockscout.sources.base import FatalSourceError, PermanentSourceError
from stockscout.sources.edgar import EdgarSource, parse_company_tickers

UA = "stockscout test@example.com"

TICKERS = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 1067983, "ticker": "BRK-B", "title": "BERKSHIRE HATHAWAY INC"},
}

FACTS = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
    {"val": 400, "start": "2024-09-29", "end": "2025-09-27", "form": "10-K",
     "filed": "2025-10-31", "fy": 2025},
    {"val": 350, "start": "2023-10-01", "end": "2024-09-28", "form": "10-K",
     "filed": "2025-10-31", "fy": 2025},
]}}}}}


def make_source(handler):
    transport = httpx.MockTransport(handler)
    return EdgarSource(UA, rate_per_sec=1000.0,
                       client=httpx.Client(transport=transport))


def routes(facts_status=200, facts_body=FACTS, seen=None):
    def handler(request):
        if seen is not None:
            seen.append(request)
        if request.url.path.endswith("company_tickers.json"):
            return httpx.Response(200, json=TICKERS)
        if "companyfacts" in request.url.path:
            return httpx.Response(facts_status, json=facts_body)
        return httpx.Response(404)
    return handler


def test_parse_company_tickers_maps_ticker_to_cik():
    assert parse_company_tickers(TICKERS) == {"AAPL": 320193, "BRK-B": 1067983}


def test_fetch_annual_returns_only_the_requested_years():
    src = make_source(routes())

    stmts = src.fetch_annual("AAPL", [2025, 2023])

    assert [s.fiscal_year for s in stmts] == [2025]
    assert stmts[0].source == "edgar"
    assert stmts[0].payload["facts"]["Revenues"] == 400


def test_the_cik_is_zero_padded_in_the_url():
    seen = []
    make_source(routes(seen=seen)).fetch_annual("AAPL", [2025])

    assert any("CIK0000320193.json" in str(r.url) for r in seen)


def test_every_request_carries_the_contact_user_agent():
    """SEC는 연락처 없는 요청을 403으로 막는다 (설계서 §18.2)."""
    seen = []
    make_source(routes(seen=seen)).fetch_annual("AAPL", [2025])

    assert seen and all(r.headers["User-Agent"] == UA for r in seen)


def test_the_ticker_list_is_fetched_once_per_run():
    seen = []
    src = make_source(routes(seen=seen))

    src.fetch_annual("AAPL", [2025])
    src.fetch_annual("BRK-B", [2025])

    assert sum("company_tickers" in str(r.url) for r in seen) == 1


def test_unknown_ticker_is_a_permanent_failure():
    with pytest.raises(PermanentSourceError, match="ZZZZ"):
        make_source(routes()).fetch_annual("ZZZZ", [2025])


def test_missing_companyfacts_is_a_permanent_failure():
    with pytest.raises(PermanentSourceError):
        make_source(routes(facts_status=404)).fetch_annual("AAPL", [2025])


def test_403_stops_the_batch():
    """User-Agent가 막힌 것이다. 남은 종목도 전부 403이다."""
    with pytest.raises(FatalSourceError, match="SEC_USER_AGENT"):
        make_source(routes(facts_status=403)).fetch_annual("AAPL", [2025])


def test_429_is_retried_then_raised():
    calls = []

    def handler(request):
        if request.url.path.endswith("company_tickers.json"):
            return httpx.Response(200, json=TICKERS)
        calls.append(request)
        return httpx.Response(429)

    src = make_source(handler)
    src._retry_delay = 0   # 테스트에서 백오프를 기다리지 않는다

    with pytest.raises(ConnectionError):
        src.fetch_annual("AAPL", [2025])
    assert len(calls) == 3
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_edgar_source.py`
Expected: FAIL — `ImportError: cannot import name 'EdgarSource'`

- [ ] **Step 3: 구현한다**

`src/stockscout/sources/edgar.py` 위쪽 import를 더하고 파일 끝에 붙인다.

```python
import time

import httpx

from stockscout.models import RawStatement
from stockscout.sources.base import FatalSourceError, PermanentSourceError
from stockscout.sources.ratelimit import TokenBucket
```

```python
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

# 재시도할 상태. 429(속도 제한)와 5xx는 정의상 일시적이다.
RETRY_STATUSES = {429, 500, 502, 503, 504}
RETRY_ATTEMPTS = 3


class SecTemporarilyUnavailable(ConnectionError):
    """SEC가 지금 응답을 내주지 못한다 (429·5xx).

    ConnectionError를 상속하는 이유는 dart.DartServiceUnavailable과 같다 -
    '지금은 연결해 데이터를 받을 수 없다'는 뜻이고, 다 써도 실패하면
    네트워크 장애와 똑같이 그 종목만 실패한다.
    """


def parse_company_tickers(data: dict) -> dict[str, int]:
    """company_tickers.json -> {티커: CIK}. 티커는 대문자, SEC 표기(BRK-B) 그대로."""
    return {row["ticker"].upper(): int(row["cik_str"]) for row in data.values()}


class EdgarSource:
    """FundamentalSource 프로토콜 구현.

    DART와 달리 회사당 호출 1회로 전 연도가 온다. 받은 것을 연도별로
    나눠 요청한 연도만 돌려준다.
    """

    name = "edgar"

    def __init__(
        self,
        user_agent: str,
        rate_per_sec: float = 5.0,
        client: httpx.Client | None = None,
    ) -> None:
        # SEC 한도는 초당 10회다. 절반으로 둔다.
        self._headers = {"User-Agent": user_agent,
                         "Accept-Encoding": "gzip, deflate"}
        self.bucket = TokenBucket(rate_per_sec)
        self._client = client or httpx.Client(timeout=30)
        self._retry_delay = 1.0
        self.ciks: dict[str, int] = {}

    def fetch_annual(self, ticker: str, years: list[int]) -> list[RawStatement]:
        if not self.ciks:
            self.ciks = parse_company_tickers(self._get_json(TICKERS_URL))
        cik = self.ciks.get(ticker.upper())
        if cik is None:
            raise PermanentSourceError(f"SEC 티커 목록에 없습니다: {ticker}")

        by_year = annual_facts(self._get_json(FACTS_URL.format(cik=cik)))
        return [
            RawStatement(ticker=ticker, fiscal_year=year, source=self.name,
                         payload=by_year[year])
            for year in years if year in by_year
        ]

    def _get_json(self, url: str) -> dict:
        """GET 하나. 429·5xx만 지수 백오프로 재시도한다.

        retry_on_transient를 쓰지 않는 이유: 그 데코레이터는 TRANSIENT
        예외만 보는데, 여기서는 HTTP 상태를 보고 판단해야 한다. 상태를
        SecTemporarilyUnavailable로 바꿔 던지고 같은 횟수만 재시도한다.
        """
        last: Exception | None = None
        for attempt in range(RETRY_ATTEMPTS):
            self.bucket.wait()
            resp = self._client.get(url, headers=self._headers)
            if resp.status_code in RETRY_STATUSES:
                last = SecTemporarilyUnavailable(
                    f"SEC가 일시적으로 응답하지 않습니다 ({resp.status_code}): {url}")
                if attempt < RETRY_ATTEMPTS - 1 and self._retry_delay > 0:
                    time.sleep(self._retry_delay * (2 ** attempt))
                continue
            if resp.status_code == 403:
                # 이 종목이 아니라 요청 자체가 막혔다. 남은 종목도 전부 403이다.
                raise FatalSourceError(
                    "SEC가 요청을 거부했습니다(403). config/.env의 "
                    "SEC_USER_AGENT에 연락처(이메일)가 들어 있는지 확인하세요."
                )
            if resp.status_code == 404:
                raise PermanentSourceError(f"SEC에 데이터가 없습니다(404): {url}")
            resp.raise_for_status()
            return resp.json()
        assert last is not None
        raise last
```

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_edgar_source.py tests/test_edgar_annual.py`
Expected: PASS

- [ ] **Step 5: 실제 SEC로 한 번 확인하고 픽스처를 만든다**

Task 7이 쓸 픽스처다. AAPL FY2025의 연간 값(10KB)을 저장한다.

```bash
.venv/Scripts/python.exe -c "import json; from stockscout.config import load_secrets; from stockscout.sources.edgar import EdgarSource; s=EdgarSource(load_secrets(market='US').sec_user_agent); (st,)=s.fetch_annual('AAPL',[2025]); json.dump(st.payload, open('tests/fixtures/edgar_aapl_2025.json','w'), indent=1, sort_keys=True); print(st.payload['period_end'], len(st.payload['facts']))"
```

Expected: `2025-09-27 182` 안팎 (태그 수는 공시에 따라 조금 다를 수 있다)

- [ ] **Step 6: 커밋**

```bash
git add src/stockscout/sources/edgar.py tests/test_edgar_source.py tests/fixtures/edgar_aapl_2025.json
git commit -m "feat: SEC EDGAR 재무 소스

FundamentalSource를 구현한다. 회사당 companyfacts 1회로 전 연도를 받아
요청한 연도만 돌려준다. 티커 -> CIK 목록은 실행당 한 번 받는다.

403은 배치를 멈춘다 - 종목이 아니라 User-Agent가 막힌 것이라 남은
종목도 전부 403이다. 429·5xx는 지수 백오프로 3회까지 재시도한다.

실제 AAPL FY2025 연간 값을 픽스처로 남긴다(Task 7이 쓴다).

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 7: US-GAAP 매핑 — `us_mapping.py`

**왜:** 태그를 `Financials` 필드로 옮긴다. 우선순위는 **연도마다** 적용된다 — `normalize_edgar_statement`가 한 해의 payload만 받으므로 저절로 그렇게 된다.

**Files:**
- Create: `src/stockscout/normalize/us_mapping.py`
- Modify: `src/stockscout/pipeline/normalize.py` (`NORMALIZERS`)
- Test: `tests/test_us_mapping.py` (신규)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_us_mapping.py`를 만든다.

```python
"""US-GAAP 태그 -> Financials. 설계서 §18.4."""

import json
from pathlib import Path

from stockscout.models import RawStatement
from stockscout.normalize.us_mapping import normalize_edgar_statement

FIXTURE = Path(__file__).parent / "fixtures" / "edgar_aapl_2025.json"


def stmt(facts, year=2025):
    return RawStatement(ticker="AAPL", fiscal_year=year, source="edgar",
                        payload={"period_end": f"{year}-09-27", "facts": facts})


def test_real_aapl_2025_maps_the_required_fields():
    """실제 SEC 응답. 값은 AAPL 10-K(2025-09-27)와 대조했다."""
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    fin = normalize_edgar_statement(
        RawStatement(ticker="AAPL", fiscal_year=2025, source="edgar", payload=payload))

    assert fin.currency == "USD"
    assert fin.revenue == 416_161_000_000
    assert fin.operating_income == 133_050_000_000
    assert fin.total_liabilities == 285_508_000_000
    assert fin.capex == 12_715_000_000
    assert fin.has_required


def test_real_aapl_2025_has_no_interest_expense():
    """AAPL은 2024년부터 이자비용을 따로 보고하지 않는다. 대체값을 두지 않는다."""
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    fin = normalize_edgar_statement(
        RawStatement(ticker="AAPL", fiscal_year=2025, source="edgar", payload=payload))

    assert fin.interest_expense is None
    assert fin.proxy_fields == []


def test_revenue_tag_priority_is_applied_per_year():
    """AAPL은 Revenues에서 RevenueFromContract...로 옮겼다. 해마다 있는 쪽을 쓴다."""
    old = normalize_edgar_statement(stmt({"Revenues": 260.0}, year=2019))
    new = normalize_edgar_statement(stmt(
        {"RevenueFromContractWithCustomerExcludingAssessedTax": 416.0}))

    assert old.revenue == 260.0
    assert new.revenue == 416.0


def test_the_first_tag_in_priority_wins():
    fin = normalize_edgar_statement(stmt({
        "RevenueFromContractWithCustomerExcludingAssessedTax": 416.0,
        "Revenues": 999.0,
    }))

    assert fin.revenue == 416.0


def test_equity_includes_noncontrolling_interest_like_kr():
    """한국은 자본총계(비지배지분 포함)를 쓴다. 같은 개념을 먼저 본다."""
    fin = normalize_edgar_statement(stmt({
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest": 110.0,
        "StockholdersEquity": 100.0,
    }))

    assert fin.total_equity == 110.0


def test_liabilities_are_derived_when_not_reported():
    """총부채를 따로 내지 않는 회사가 있다. 부채와자본총계 - 자본총계는 항등식이다."""
    fin = normalize_edgar_statement(stmt({
        "LiabilitiesAndStockholdersEquity": 1000.0,
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest": 300.0,
    }))

    assert fin.total_liabilities == 700.0
    assert fin.proxy_fields == []    # 항등식이라 대체값이 아니다


def test_reported_liabilities_win_over_the_derivation():
    fin = normalize_edgar_statement(stmt({
        "Liabilities": 650.0,
        "LiabilitiesAndStockholdersEquity": 1000.0,
        "StockholdersEquity": 300.0,
    }))

    assert fin.total_liabilities == 650.0


def test_capex_and_interest_are_stored_as_positive_sizes():
    fin = normalize_edgar_statement(stmt({
        "PaymentsToAcquirePropertyPlantAndEquipment": -30.0,
        "InterestExpense": -5.0,
    }))

    assert fin.capex == 30.0
    assert fin.interest_expense == 5.0


def test_shares_fall_back_to_the_cover_page():
    fin = normalize_edgar_statement(stmt({"EntityCommonStockSharesOutstanding": 15.0}))

    assert fin.shares_outstanding == 15.0
    assert fin.shares_treasury is None      # 미국 유통주식수는 이미 자기주식을 뺐다
    assert fin.audit_opinion is None        # XBRL에 없다


def test_an_empty_payload_is_all_missing_not_an_error():
    fin = normalize_edgar_statement(stmt({}))

    assert not fin.has_required
```

`tests/test_pipeline_normalize.py` 끝에 붙인다. 이 파일의 fixture 이름이 `repo`인지 먼저 확인하고 맞춰라.

```python
def test_us_market_is_normalized_from_edgar(repo):
    from stockscout.models import RawStatement, Security
    from stockscout.pipeline.normalize import normalize_market

    repo.upsert_security(Security(ticker="AAPL", market="US", name="Apple",
                                  currency="USD"))
    repo.save_raw_statements([RawStatement(
        ticker="AAPL", fiscal_year=2025, source="edgar",
        payload={"period_end": "2025-09-27", "facts": {
            "Revenues": 400.0, "OperatingIncomeLoss": 100.0,
            "Liabilities": 300.0, "StockholdersEquity": 200.0}})])

    summary = normalize_market(repo, market="US")

    (fin,) = repo.get_financials("AAPL")
    assert summary.normalized == 1
    assert fin.revenue == 400.0
    assert fin.currency == "USD"
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_us_mapping.py tests/test_pipeline_normalize.py`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 구현한다**

`src/stockscout/normalize/us_mapping.py`를 만든다.

```python
"""US-GAAP 태그 -> 공통 Financials 매핑. 설계서 §7.2·§18.4.

payload는 sources/edgar.py의 annual_facts가 고른 한 해의 연간 값이다.
한 해씩 받으므로 태그 우선순위는 저절로 연도마다 적용된다 - AAPL처럼
회사가 중간에 태그를 바꿔도 해마다 있는 쪽을 쓴다.
"""

from __future__ import annotations

from stockscout.models import Financials, RawStatement

# 필드 -> 태그 우선순위. 위에서부터 첫 매치를 취한다.
#
# 한국(kr_mapping.ACCOUNT_MAP)과 달리 단계를 나누지 않는다. 한국은 이자비용이
# 없으면 금융비용으로 대신했지만(§17.3), 미국에는 그런 대체값을 두지 않는다
# (계획 E 핵심 결정 3). 여기 있는 동의어는 전부 정확한 값이다.
#
# 자본·순이익은 비지배지분 포함을 먼저 본다. 한국이 자본총계·당기순이익
# (ifrs-full_Equity·ProfitLoss, 둘 다 비지배지분 포함)을 쓰므로 같은 개념을
# 맞춘다. ROE의 분자와 분모가 같은 범위여야 한다.
TAG_MAP: dict[str, list[str]] = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
    ],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["ProfitLoss", "NetIncomeLoss"],
    "total_assets": ["Assets"],
    "total_liabilities": ["Liabilities"],
    "total_equity": [
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        "StockholdersEquity",
    ],
    "current_assets": ["AssetsCurrent"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "interest_expense": [
        "InterestExpense",
        "InterestExpenseNonoperating",
        "InterestExpenseDebt",
    ],
    "operating_cash_flow": [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ],
    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
    ],
    # 미국의 발행주식수는 자기주식을 뺀 유통주식수다(§18.2). 재무상태표
    # 값이 없으면 10-K 표지 값을 쓴다.
    "shares_outstanding": [
        "CommonStockSharesOutstanding",
        "EntityCommonStockSharesOutstanding",
    ],
}

# 양수 크기로 저장하는 필드 (kr_mapping.ABS_FIELDS와 같다)
ABS_FIELDS = {"capex", "interest_expense"}

LIABILITIES_AND_EQUITY = "LiabilitiesAndStockholdersEquity"


def pick(facts: dict[str, float], tags: list[str]) -> float | None:
    for tag in tags:
        value = facts.get(tag)
        if value is not None:
            return float(value)
    return None


def normalize_edgar_statement(stmt: RawStatement) -> Financials:
    """EDGAR 연간 값 하나를 공통 스키마로 옮긴다. 순수 함수다."""
    facts: dict[str, float] = stmt.payload.get("facts", {}) or {}

    values: dict[str, float | None] = {}
    for field, tags in TAG_MAP.items():
        amount = pick(facts, tags)
        if amount is not None and field in ABS_FIELDS:
            amount = abs(amount)
        values[field] = amount

    # 총부채를 따로 보고하지 않는 회사가 있다. 부채와자본총계 - 자본총계는
    # 회계 항등식이라 대체값이 아니므로 proxy_fields에 남기지 않는다.
    if values["total_liabilities"] is None:
        total = facts.get(LIABILITIES_AND_EQUITY)
        if total is not None and values["total_equity"] is not None:
            values["total_liabilities"] = float(total) - values["total_equity"]

    return Financials(
        ticker=stmt.ticker,
        fiscal_year=stmt.fiscal_year,
        currency="USD",
        **values,
    )
```

`src/stockscout/pipeline/normalize.py`에서 import를 더하고 `NORMALIZERS`를 바꾼다. 위쪽 주석의 "계획 D에서 ... 추가한다"는 이제 사실이므로 지운다.

```python
from stockscout.normalize.us_mapping import normalize_edgar_statement
```

```python
# 시장별 (원본 source 식별자, 정규화 함수). raw_financials는 (ticker, fiscal_year,
# source)로 dart/edgar를 함께 담으므로, 정규화기가 자기 것만 골라 보게
# source를 함께 등록한다.
NORMALIZERS = {
    "KR": ("dart", normalize_dart_statement),
    "US": ("edgar", normalize_edgar_statement),
}
```

`ENRICHERS`에는 US를 더하지 않는다. 주식수는 EDGAR 재무 안에 있고 감사의견은 없다.

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_us_mapping.py tests/test_pipeline_normalize.py`
Expected: PASS. `test_real_aapl_2025_maps_the_required_fields`가 실패하면 픽스처 값을 직접 열어 어느 태그가 왔는지 보고, 테스트의 숫자가 아니라 **매핑을** 고쳐라 — 숫자는 실제 10-K다.

- [ ] **Step 5: 커밋**

```bash
git add src/stockscout/normalize/us_mapping.py src/stockscout/pipeline/normalize.py tests/test_us_mapping.py tests/test_pipeline_normalize.py
git commit -m "feat: US-GAAP 태그 매핑

필드마다 태그 우선순위를 두고 첫 매치를 취한다. 정규화가 한 해씩
받으므로 우선순위는 저절로 연도마다 적용된다 - AAPL처럼 중간에 태그를
바꿔도 해마다 있는 쪽을 쓴다.

자본·순이익은 한국과 같이 비지배지분 포함을 먼저 본다. 이자비용에는
대체값을 두지 않는다(AAPL은 2024년부터 셋 다 없다 - None이고 필터는
건너뛴다). 총부채가 없으면 부채와자본총계 - 자본총계로 구한다. 항등식이라
대체값으로 표시하지 않는다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 8: yfinance 주가 — `YahooPriceSource`

**왜:** `PriceSource` 프로토콜을 구현한다. `KrxPriceSource`와 같은 모양으로 함수를 주입받아 네트워크 없이 검증한다.

**Files:**
- Create: `src/stockscout/sources/yahoo.py`
- Modify: `pyproject.toml`
- Test: `tests/test_yahoo.py` (신규)

- [ ] **Step 1: yfinance를 설치하고 버전을 고정한다**

```bash
.venv/Scripts/python.exe -m pip install "yfinance==1.7.0"
.venv/Scripts/python.exe -c "import yfinance as yf; df=yf.Ticker('AAPL').history(period='5d', auto_adjust=True); print(df[['Close','Volume']].tail(2)); print(df.index.tz)"
```

Expected: 최근 2거래일 종가·거래량과 `America/New_York`. 설치가 Python 3.14에서 실패하면 멈추고 보고하라 — 다른 버전을 임의로 고르지 마라.

`pyproject.toml`의 `dependencies`에 더한다(`pykrx` 다음).

```toml
    "yfinance==1.7.0",
```

`pykrx`와 달리 정확한 버전으로 고정한다. 비공식 경로라 상류가 응답 형식을 바꾸면 조용히 깨진다(설계서 §2).

- [ ] **Step 2: 실패하는 테스트를 쓴다**

`tests/test_yahoo.py`를 만든다.

```python
"""yfinance 어댑터. 함수를 주입해 네트워크 없이 검증한다 (tests/test_krx.py와 같다)."""

from datetime import date

import pandas as pd

from stockscout.sources.yahoo import YahooPriceSource


def frame(rows):
    index = pd.DatetimeIndex([pd.Timestamp(d, tz="America/New_York") for d, _, _ in rows])
    return pd.DataFrame({"Close": [c for _, c, _ in rows],
                         "Volume": [v for _, _, v in rows]}, index=index)


def test_fetch_daily_returns_bars_in_date_order():
    calls = []

    def history(symbol, start, end):
        calls.append((symbol, start, end))
        return frame([("2026-09-09", 230.0, 100), ("2026-09-10", 231.5, 120)])

    bars = YahooPriceSource(history_fn=history).fetch_daily(
        "AAPL", date(2026, 9, 1), date(2026, 9, 10))

    assert [(b.date, b.close, b.volume) for b in bars] == [
        (date(2026, 9, 9), 230.0, 100), (date(2026, 9, 10), 231.5, 120)]
    assert all(b.ticker == "AAPL" for b in bars)


def test_end_is_inclusive():
    """yfinance의 end는 미포함이다. 하루를 더해 넘겨야 마지막 날이 들어온다."""
    calls = []

    def history(symbol, start, end):
        calls.append((start, end))
        return frame([])

    YahooPriceSource(history_fn=history).fetch_daily(
        "AAPL", date(2026, 9, 1), date(2026, 9, 10))

    assert calls == [(date(2026, 9, 1), date(2026, 9, 11))]


def test_the_date_is_the_exchange_date_not_utc():
    """뉴욕 장 마감은 UTC로 다음 날일 수 있다. 거래소 현지 날짜를 쓴다."""
    index = pd.DatetimeIndex([pd.Timestamp("2026-09-10 00:00", tz="America/New_York")])
    df = pd.DataFrame({"Close": [231.5], "Volume": [1]}, index=index)

    (bar,) = YahooPriceSource(history_fn=lambda *a: df).fetch_daily(
        "AAPL", date(2026, 9, 10), date(2026, 9, 10))

    assert bar.date == date(2026, 9, 10)


def test_missing_and_nonpositive_closes_are_skipped():
    df = frame([("2026-09-09", float("nan"), 0), ("2026-09-10", 0.0, 0),
                ("2026-09-11", 232.0, 5)])

    bars = YahooPriceSource(history_fn=lambda *a: df).fetch_daily(
        "AAPL", date(2026, 9, 9), date(2026, 9, 11))

    assert [b.close for b in bars] == [232.0]


def test_empty_frame_is_no_bars():
    assert YahooPriceSource(history_fn=lambda *a: pd.DataFrame()).fetch_daily(
        "AAPL", date(2026, 9, 1), date(2026, 9, 10)) == []


def test_source_name_is_recorded_on_raw_prices():
    assert YahooPriceSource(history_fn=lambda *a: None).name == "yfinance"
```

- [ ] **Step 3: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_yahoo.py`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 4: 구현한다**

`src/stockscout/sources/yahoo.py`를 만든다.

```python
"""yfinance 어댑터. 미국 주식의 수정종가를 받는다.

yfinance 함수를 주입받아 테스트에서 네트워크 없이 검증할 수 있게 한다
(sources/krx.py와 같은 모양이다).
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Callable

import pandas as pd

from stockscout.models import PriceBar

CLOSE_COLUMN = "Close"
VOLUME_COLUMN = "Volume"


def _default_history_fn(symbol: str, start: date, end_exclusive: date) -> pd.DataFrame:
    import yfinance as yf

    # auto_adjust=True를 명시한다. 설계서 §9.1은 수정주가를 필수로 요구하고,
    # 기본값에 기대면 상류가 그것을 뒤집는 날 하락률이 조용히 틀어진다
    # (sources/krx.py의 adjusted=True와 같은 이유).
    return yf.Ticker(symbol).history(
        start=start.isoformat(), end=end_exclusive.isoformat(),
        auto_adjust=True, actions=False,
    )


class YahooPriceSource:
    """PriceSource 프로토콜 구현."""

    name = "yfinance"

    def __init__(
        self, history_fn: Callable[[str, date, date], pd.DataFrame] | None = None
    ) -> None:
        self._history = history_fn or _default_history_fn

    def fetch_daily(self, ticker: str, start: date, end: date) -> list[PriceBar]:
        # yfinance의 end는 미포함이다. 하루를 더해 마지막 날까지 받는다.
        df = self._history(ticker, start, end + timedelta(days=1))
        if df is None or df.empty:
            return []

        bars: list[PriceBar] = []
        for idx, row in df.sort_index().iterrows():
            close = float(row[CLOSE_COLUMN])
            if math.isnan(close) or close <= 0:
                continue  # 거래정지·결측. 평균을 오염시킨다
            volume = row.get(VOLUME_COLUMN)
            bars.append(PriceBar(
                ticker=ticker,
                # 인덱스는 거래소 시간대(America/New_York)다. UTC로 바꾸지 않고
                # 그 현지 날짜를 쓴다.
                date=pd.Timestamp(idx).date(),
                close=close,
                volume=int(volume) if volume is not None and not pd.isna(volume) else None,
            ))
        return bars
```

- [ ] **Step 5: 테스트가 통과하는지 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_yahoo.py`
Expected: PASS

- [ ] **Step 6: 커밋**

```bash
git add src/stockscout/sources/yahoo.py pyproject.toml tests/test_yahoo.py
git commit -m "feat: yfinance 미국 주가 소스

PriceSource를 구현한다. auto_adjust=True를 명시한다 - 수정주가는 설계서
§9.1의 필수 조건이고 기본값에 기대지 않는다(krx.py의 adjusted=True와
같은 이유).

yfinance의 end는 미포함이라 하루를 더해 넘긴다. 날짜는 거래소 현지
날짜다. 비공식 경로라 버전을 정확히 고정한다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 9: CLI — 시장별 소스 조립

**왜:** `collect`가 `KrxPriceSource`와 `DartSource`를 박아 쓴다. `--market US`면 EDGAR와 yfinance를 쓴다. `analyze`의 순위표는 원 단위 정수로 찍는데 달러는 센트까지 보여야 한다.

**Files:**
- Modify: `src/stockscout/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_cli.py` 끝에 붙인다.

```python
# --- 시장별 소스 (계획 E) ---------------------------------------------


def test_build_fundamentals_uses_edgar_for_us(monkeypatch):
    from stockscout.cli import build_fundamentals
    from stockscout.sources.edgar import EdgarSource

    monkeypatch.delenv("DART_API_KEY", raising=False)
    monkeypatch.setenv("SEC_USER_AGENT", "stockscout me@example.com")

    source, years = build_fundamentals(skip=False, years=[2025], market="US")

    assert isinstance(source, EdgarSource)
    assert years == [2025]


def test_build_fundamentals_skips_us_without_a_contact(tmp_path, monkeypatch):
    from stockscout.cli import SkippedFundamentals, build_fundamentals

    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    monkeypatch.chdir(tmp_path)

    source, years = build_fundamentals(skip=False, years=[2025], market="US")

    assert isinstance(source, SkippedFundamentals)
    assert years == []


def test_build_prices_picks_the_market_source():
    from stockscout.cli import build_prices
    from stockscout.sources.krx import KrxPriceSource
    from stockscout.sources.yahoo import YahooPriceSource

    assert isinstance(build_prices("KR"), KrxPriceSource)
    assert isinstance(build_prices("US"), YahooPriceSource)


def test_render_ranking_shows_cents_for_us(repo):
    from stockscout.cli import render_ranking
    from stockscout.models import (
        AnalysisResult, FilterOutcome, PriceStats, Security, Snapshot, Valuation)

    repo.upsert_security(Security(ticker="AAPL", market="US", name="Apple",
                                  currency="USD"))
    sid = repo.save_snapshot(
        Snapshot(run_at="x", config_hash="h", config_json="{}", market="US"),
        [AnalysisResult(
            ticker="AAPL", filters=FilterOutcome(failures=[]),
            stats=PriceStats(price_current=230.55, avg_price={5: 250.25},
                             high_5y=260.0, drop_vs_avg={5: -0.08},
                             drop_vs_high=-0.11),
            valuation=Valuation())])

    text = render_ranking(repo, sid, top=5, window=5, market="US")

    assert "230.55" in text and "250.25" in text
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli.py`
Expected: FAIL — `build_fundamentals() got an unexpected keyword argument 'market'`

- [ ] **Step 3: 구현한다**

`src/stockscout/cli.py`:

1. import를 더한다.

```python
from stockscout.sources.edgar import EdgarSource
from stockscout.sources.yahoo import YahooPriceSource
```

2. `build_fundamentals`를 바꾼다.

```python
def build_fundamentals(skip: bool, years: list[int], market: str = "KR"):
    """재무 소스와 수집할 회계연도를 정한다.

    주가는 재무 소스의 비밀값이 필요 없다. 비밀값 하나가 없다고 수집
    전체를 막을 이유가 없으므로, 없으면 재무만 건너뛰고 주가는 받는다.
    수집은 증분이라, 나중에 값을 넣고 다시 돌리면 재무만 채워진다.
    """
    if skip:
        print("재무제표를 건너뜁니다 (--skip-financials). 주가만 수집합니다.")
        return SkippedFundamentals(), []
    try:
        secrets = load_secrets(market=market)
    except ValueError as exc:
        print("재무제표를 건너뜁니다. 주가만 수집합니다.", file=sys.stderr)
        print(f"  이유: {exc}", file=sys.stderr)
        return SkippedFundamentals(), []
    if market == "US":
        return EdgarSource(secrets.sec_user_agent), years
    return DartSource(secrets.dart_api_key), years


def build_prices(market: str):
    """시장의 주가 소스. 한국 pykrx, 미국 yfinance (설계서 §5.1)."""
    return YahooPriceSource() if market == "US" else KrxPriceSource()
```

3. `main`의 `collect` 분기에서 `build_fundamentals(...)`에 `market=args.market,`을 더하고, `prices=KrxPriceSource(),`를 `prices=build_prices(args.market),`로 바꾼다.

4. `render_ranking`에 `market: str = "KR"` 인자를 더하고 금액 형식을 고른다.

```python
def render_ranking(repo, snapshot_id: int, top: int, window: int,
                   market: str = "KR") -> str:
    """하락률 상위 종목표. ASCII 기호만 쓴다 (콘솔이 cp949다).

    달러는 센트까지 보인다. 20달러짜리 종목을 정수로 찍으면 평균과 현재가가
    같아 보인다.
    """
    money = ",.2f" if market == "US" else ",.0f"
```

본문의 두 금액 `f"{r['price_current']:>12,.0f}"`, `f"{r[f'avg_price_{window}y']:>12,.0f}"`를 각각 `f"{r['price_current']:>12{money}}"`, `f"{r[f'avg_price_{window}y']:>12{money}}"`로 바꾼다.

5. `main`의 `analyze` 분기에서 `render_ranking(...)` 호출에 `market=args.market`을 더한다.

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli.py`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add src/stockscout/cli.py tests/test_cli.py
git commit -m "feat: collect·analyze가 시장별 소스와 통화를 쓴다

--market US면 재무는 EDGAR, 주가는 yfinance다. SEC 연락처가 없으면
DART 키가 없을 때와 같이 재무만 건너뛰고 주가는 받는다.

순위표의 달러는 센트까지 보인다. 20달러짜리 종목을 정수로 찍으면
평균과 현재가가 같아 보인다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 10: 화면 — KR | US 탭과 달러 표시

**왜:** 설계서 §18.5. 시장마다 스냅샷이 따로라 화면도 한 번에 한 시장을 본다. 아직 분석하지 않은 시장을 고르면 페이지 전체를 지우지 말고 무엇을 실행하라고 말해야 한다(지금 `init`은 스냅샷이 없으면 `document.body`를 통째로 덮는다).

**Files:**
- Modify: `src/stockscout/web/static/index.html`
- Modify: `src/stockscout/web/static/app.js`
- Modify: `src/stockscout/web/static/style.css`
- Test: `tests/test_web_static.py`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_web_static.py` 끝에 붙인다. 이 파일의 `js_text()`·`_function_body()` 헬퍼(계획 C Task 12)를 쓴다.

```python
# --- 계획 E: 시장 탭 --------------------------------------------------


def test_index_has_market_tabs():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert 'id="market-tabs"' in html


def test_every_api_list_call_carries_the_market():
    js = js_text()

    assert "market: state.market" in _function_body(js, "reload")
    assert "/api/snapshot?market=" in _function_body(js, "loadMarket")


def test_money_shows_dollars_with_cents_for_us():
    """원화 formatter로 달러를 찍으면 $230.55가 231이 된다."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node 가 없어 금액 형식 실행 검사를 건너뛴다")

    script = (
        'const state = { market: "US" };\n'
        f"{_function_body(js_text(), 'money')}\n"
        "console.log(money(230.55)); state.market = 'KR'; console.log(money(60000));"
    )
    out = subprocess.run([node, "-e", script], capture_output=True, text=True)

    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["$230.55", "60,000"]


def test_no_amount_is_formatted_as_won_directly():
    """금액은 전부 money()를 거친다. 원화 전용 formatter가 남아 있으면 미국 화면이 원화로 찍힌다."""
    assert "won(" not in js_text()


def test_a_market_without_a_snapshot_does_not_wipe_the_page():
    """미국을 아직 분석하지 않았으면 탭은 남기고 무엇을 실행할지 말한다."""
    body = _function_body(js_text(), "loadMarket")

    assert "document.body.innerHTML" not in body
    assert "catch" in body


def test_the_chosen_market_survives_a_reload():
    js = js_text()

    assert "stockscout.market" in _function_body(js, "loadPrefs")
    assert "stockscout.market" in _function_body(js, "savePrefs")
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_web_static.py`
Expected: FAIL

- [ ] **Step 3: 구현한다 — `index.html`**

`#topbar`의 브랜드 다음에 탭 자리를 둔다.

```html
  <header id="topbar">
    <span class="brand">stockscout</span>
    <nav id="market-tabs"></nav>
    <span id="snapshot-info" class="muted">불러오는 중…</span>
  </header>
```

- [ ] **Step 4: 구현한다 — `app.js`**

1. `state`에 `market: "KR",`을 더한다(`rows` 앞).

2. `won` 정의 바로 아래에 `money`를 둔다.

```javascript
// 금액은 전부 이 함수를 거친다. 시장마다 통화가 다르다 (설계서 §18.5).
// 달러는 센트까지, 원은 정수로.
function money(v) {
  if (v === null || v === undefined) return "—";
  if (state.market === "US") {
    return "$" + v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  return v.toLocaleString("ko-KR", { maximumFractionDigits: 0 });
}
```

그리고 파일의 나머지 `won(` 호출(현재가 KPI, 재무 추이표 세 줄, 차트 기준선 라벨, 차트 y축)을 **전부** `money(`로 바꾸고, `const won = ...` 정의를 지운다 — 쓰는 곳이 없어진다.

3. `loadPrefs`·`savePrefs`에 시장을 더한다.

```javascript
    const market = localStorage.getItem("stockscout.market");
    if (market === "KR" || market === "US") state.market = market;
```

```javascript
    localStorage.setItem("stockscout.market", state.market);
```

4. 탭을 그린다(`renderSnapshotInfo` 앞).

```javascript
const MARKETS = [["KR", "한국"], ["US", "미국"]];

function renderMarketTabs() {
  const nav = $("market-tabs");
  nav.innerHTML = "";
  for (const [key, label] of MARKETS) {
    const b = document.createElement("button");
    b.className = "tab" + (state.market === key ? " on" : "");
    b.textContent = label;
    b.onclick = async () => {
      if (state.market === key) return;
      state.market = key;
      savePrefs();
      await loadMarket();
    };
    nav.appendChild(b);
  }
}
```

5. `reload`가 시장을 넘기고, 스냅샷이 없으면 아무것도 하지 않는다.

```javascript
async function reload() {
  if (!state.snapshot) return;   // 이 시장은 아직 분석하지 않았다
  const q = $("search").value.trim();
  const sort = $("sort").value;
  const params = new URLSearchParams({ market: state.market, sort });
  if (q) params.set("q", q);
  state.rows = await getJSON(`/api/securities?${params}`);
  renderHitCount();
  renderList();
}
```

6. `init`을 `loadMarket`과 나눈다.

```javascript
// 시장을 바꿀 때마다 부른다. 스냅샷이 없으면 페이지를 지우지 않는다 -
// 탭은 남아 있어야 다른 시장으로 돌아갈 수 있다.
async function loadMarket() {
  renderMarketTabs();
  state.selected = null;
  state.rows = [];
  $("detail-panel").innerHTML = `<p class="placeholder">왼쪽에서 종목을 선택하세요.</p>`;
  try {
    state.snapshot = await getJSON(`/api/snapshot?market=${state.market}`);
  } catch (err) {
    state.snapshot = null;
    $("snapshot-info").textContent = err.message;
    $("hit-count").textContent = "";
    $("chips").innerHTML = "";
    $("stock-list").innerHTML = "";
    return;
  }
  renderSnapshotInfo();
  renderScoreBlock();
  renderChips();
  await reload();
}

async function init() {
  loadPrefs();
  wireControls();
  await loadMarket();
}
```

기존 `init`의 `document.body.innerHTML = ...` 분기는 없어진다.

7. 재무 추이가 비었을 때의 안내(`DART_GUIDE`)는 한국 전용이다. `renderDetail`의 그 분기를 시장으로 가른다.

```javascript
  if (!d.financials.length) {
    const box2 = document.createElement("div");
    box2.innerHTML = state.market === "US" ? EDGAR_GUIDE : DART_GUIDE;
    panel.appendChild(box2);
  }
```

`DART_GUIDE` 정의 아래에 더한다.

```javascript
const EDGAR_GUIDE = `<div class="empty-state">
  <b>재무 데이터가 아직 없습니다</b>
  config/.env 의 SEC_USER_AGENT 에 연락처(이메일)를 넣고 실행하세요.
  <pre>stockscout collect --market US
stockscout normalize --market US
stockscout analyze --market US</pre>
</div>`;
```

- [ ] **Step 5: 구현한다 — `style.css`**

파일 끝에 붙인다. 상단바가 어두운 배경이라 칩(`.chip`)과 반대 색을 쓴다.

```css
/* 시장 탭 (설계서 §18.5) */
#market-tabs { display: flex; gap: 4px; }
#market-tabs .tab {
  border: 1px solid #4a5568;
  background: transparent;
  color: #a0aec0;
  border-radius: 4px;
  padding: 2px 10px;
  font-size: 12px;
  cursor: pointer;
}
#market-tabs .tab.on { background: #e2e8f0; color: #2d3748; border-color: #e2e8f0; }
```

- [ ] **Step 6: 테스트가 통과하는지 확인한다**

Run: `.venv/Scripts/python.exe -m pytest tests/test_web_static.py`
Expected: PASS. `test_index_has_an_element_for_every_id_app_js_looks_up`가 `market-tabs`를 찾으므로 Step 3이 빠지면 실패한다.

- [ ] **Step 7: 커밋**

```bash
git add src/stockscout/web/static/index.html src/stockscout/web/static/app.js src/stockscout/web/static/style.css tests/test_web_static.py
git commit -m "feat: 화면에 KR | US 탭과 달러 표시

시장마다 스냅샷이 따로라 화면도 한 번에 한 시장을 본다. 고른 탭은
localStorage에 남는다.

금액은 전부 money()를 거친다. 원화 formatter로 달러를 찍으면 $230.55가
231이 된다. node로 실제 실행해 확인한다.

아직 분석하지 않은 시장을 고르면 페이지를 지우지 않고 무엇을 실행할지
말한다. 전에는 스냅샷이 없으면 body를 통째로 덮어 탭까지 사라졌다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 11: 실제 데이터 — 수집·정규화·적중률

**왜:** 한국도 실제 데이터를 받고 나서야 §17이 나왔다. 손으로 고른 태그 목록이 40종목에서 얼마나 맞는지는 돌려봐야 안다.

**Files:**
- Modify: `src/stockscout/normalize/us_mapping.py` (적중률에 따라)
- Modify: `docs/superpowers/specs/2026-09-10-stock-screener-design.md` (§18.4에 실측 표)

- [ ] **Step 1: 유니버스와 수집**

```bash
.venv/Scripts/stockscout.exe universe --market US
.venv/Scripts/stockscout.exe collect --market US
```

Expected: `유니버스 갱신 완료: 40종목`, 수집은 `대상 40종목 · 성공 40 · 실패 0`. 수집은 EDGAR 41회(티커 목록 1 + 회사 40)와 yfinance 40회라 몇 분 안에 끝난다.

실패가 있으면 `.venv/Scripts/python.exe -c "import sqlite3; c=sqlite3.connect('data/stockscout.db'); [print(r) for r in c.execute(\"SELECT ticker, source, message FROM collect_errors ORDER BY id DESC LIMIT 10\")]"`로 원인을 본다.

- [ ] **Step 2: 정규화**

```bash
.venv/Scripts/stockscout.exe normalize --market US
```

Expected: `40종목 · 200개 연도 정규화` 안팎과 결측 상위 목록.

- [ ] **Step 3: 필드별 적중률을 잰다**

```bash
.venv/Scripts/python.exe -c "import sqlite3,io,sys; sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8'); c=sqlite3.connect('data/stockscout.db'); f=['revenue','operating_income','net_income','total_assets','total_liabilities','total_equity','current_assets','current_liabilities','interest_expense','operating_cash_flow','capex','shares_outstanding']; n=c.execute(\"SELECT COUNT(*) FROM financials WHERE currency='USD'\").fetchone()[0]; print('행', n); [print(f'{x:<22}', c.execute(f\"SELECT COUNT({x}) FROM financials WHERE currency='USD'\").fetchone()[0]) for x in f]"
```

결측이 있는 필드마다 **어느 종목의 어느 해인지** 찾고, 그 종목의 `raw_financials` payload에서 실제로 어떤 태그가 왔는지 본다.

```bash
.venv/Scripts/python.exe -c "import sqlite3,json,io,sys; sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8'); c=sqlite3.connect('data/stockscout.db'); p=json.loads(c.execute(\"SELECT payload FROM raw_financials WHERE ticker=? AND fiscal_year=? AND source='edgar'\", (sys.argv[1], int(sys.argv[2]))).fetchone()[0]); [print(k, v) for k, v in sorted(p['facts'].items()) if any(w in k for w in sys.argv[3].split(','))]" JPM 2025 Revenue,Interest
```

(마지막 인자: 종목, 연도, 태그 이름에 들어갈 단어들)

**판단 규칙:**

1. 같은 뜻의 다른 태그면 `TAG_MAP`의 그 필드 목록에 더한다. 테스트를 하나 더해 고정한다(`tests/test_us_mapping.py`, 그 태그로만 된 payload가 그 필드를 채우는지)
2. **뜻이 다른 태그는 넣지 않는다.** 예: 은행의 `InterestExpense`는 예금 이자라 영업비용이다 — 은행은 `is_financial`로 이미 제외되므로 고칠 일이 없다. `OperatingIncomeLoss`가 없는 회사(은행·보험 다수)도 마찬가지다
3. 금융 4종목을 제외하고도 필수 4항목(`revenue`·`operating_income`·`total_equity`·`total_liabilities`)이 빠지는 종목이 있으면 그 종목을 보고서에 이름으로 남긴다. 억지로 채우지 않는다 — `DATA_INSUFFICIENT`로 제외되는 것이 맞는 동작이다(§7.3)

매핑을 고쳤으면 `normalize --market US`를 다시 돌려 Step 3을 반복한다(수집은 다시 할 필요가 없다 — 연간 값 전체를 저장했다).

- [ ] **Step 4: 설계서 §18.4에 실측을 남긴다**

§18.4 끝의 "구체적인 목록은 실측 후 확정하고, 적중률을 §17.2처럼 이 절에 남긴다"를 실측 표로 바꾼다. §17.2와 같은 형식이다.

```markdown
**실측 (2026-10-XX, 40종목 · N개 연도)**

| 필드 | 채워짐 | 결측 | 결측의 정체 |
|---|---|---|---|
| revenue | … | … | … |
| … | | | |

매핑에 더한 태그: …(무엇을, 어느 종목 때문에)
일부러 넣지 않은 것: …(왜)
```

`N`, 날짜, 숫자는 Step 3의 실제 값으로 채운다.

- [ ] **Step 5: 전체 스위트**

Run: `.venv/Scripts/python.exe -m pytest`
Expected: 실패 0건

- [ ] **Step 6: 커밋**

```bash
git add src/stockscout/normalize/us_mapping.py tests/test_us_mapping.py docs/superpowers/specs/2026-09-10-stock-screener-design.md
git commit -m "docs: 미국 40종목의 태그 적중률을 설계서 §18.4로 남긴다

(Step 3의 결과로 본문을 쓴다: 무엇이 결측이었고, 어떤 태그를 더했고,
무엇을 일부러 넣지 않았는지)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 12: 실제 데이터 분석 관통 — `analysis/`가 그대로인가

**왜:** 이 계획의 존재 이유다. 설계서 §15: "②계층 위쪽은 손대지 않아야 한다 — 그것이 이 설계가 맞았는지에 대한 검증이다."

**Files:**
- Test: `tests/test_integration_us.py` (신규)

- [ ] **Step 1: 분석한다**

```bash
.venv/Scripts/stockscout.exe analyze --market US
```

Expected: `40종목 분석 · 통과 N · 제외 M`. 금융 4종목은 반드시 제외(`FINANCIAL_SECTOR`)다.

- [ ] **Step 2: 결과를 눈으로 본다**

```bash
.venv/Scripts/python.exe -c "import sqlite3,io,sys,json; sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8'); c=sqlite3.connect('data/stockscout.db'); c.row_factory=sqlite3.Row; sid=c.execute(\"SELECT MAX(snapshot_id) FROM snapshots WHERE market='US'\").fetchone()[0]; [print(f\"{r['ticker']:<6} {r['score_total'] or 0:5.1f} {r['signal_grade']:<6} {(r['drop_vs_avg_5y'] or 0)*100:6.1f}% {r['filter_failures']}\") for r in c.execute('SELECT * FROM analysis_results WHERE snapshot_id=? ORDER BY score_total DESC', (sid,))]"
```

**확인할 것:**

1. **금융 4종목(JPM·BAC·GS·BRK-B)이 제외됐는가**
2. **점수가 전 종목 비슷하지 않은가** — 한국은 20.5~93.5에 28종류였다
3. **측정 불가 축(`AXIS_UNMEASURABLE`)이 몇 종목인가.** 많으면 Task 11로 돌아가 매핑을 본다
4. **한국 스냅샷이 그대로인가** — 아래 Step 3의 `test_the_kr_snapshot_is_still_the_kr_default`가 보고, 화면은 Step 5에서 본다

- [ ] **Step 3: 통합 테스트를 쓴다**

`tests/test_integration_us.py`를 만든다. 실DB가 없거나 미국 스냅샷이 없으면 skip한다.

```python
"""실제 DB의 미국 스냅샷 검증. 미국을 분석하기 전에는 skip된다."""

import json
from pathlib import Path

import pytest

from stockscout.store.db import DEFAULT_DB_PATH, connect
from stockscout.store.repository import Repository

pytestmark = pytest.mark.skipif(
    not Path(DEFAULT_DB_PATH).exists(),
    reason="stockscout analyze --market US 를 먼저 실행해야 한다",
)

FINANCIALS = {"JPM", "BAC", "GS", "BRK-B"}


@pytest.fixture
def rows():
    with connect(DEFAULT_DB_PATH) as conn:
        repo = Repository(conn)
        snap = repo.get_latest_snapshot(market="US")
        if snap is None:
            pytest.skip("미국 스냅샷이 없다")
        return repo.get_analysis_results(snap.snapshot_id)


def test_every_us_security_has_a_row(rows):
    with connect(DEFAULT_DB_PATH) as conn:
        expected = {s.ticker for s in Repository(conn).list_securities(market="US")}

    assert {r["ticker"] for r in rows} == expected


def test_no_kr_ticker_leaks_into_the_us_snapshot(rows):
    assert not any(r["ticker"].endswith(".KS") for r in rows)


def test_financials_are_excluded(rows):
    for row in rows:
        if row["ticker"] in FINANCIALS:
            assert "FINANCIAL_SECTOR" in json.loads(row["filter_failures"])


def test_most_non_financials_are_scored(rows):
    """매핑이 크게 비면 대부분이 DATA_INSUFFICIENT로 떨어진다."""
    others = [r for r in rows if r["ticker"] not in FINANCIALS]
    scored = [r for r in others if r["score_total"] is not None]

    assert len(scored) >= len(others) * 0.8, \
        [(r["ticker"], r["filter_failures"]) for r in others if r["score_total"] is None]


def test_us_scores_spread_out(rows):
    scores = {round(r["score_total"], 1) for r in rows if r["score_total"] is not None}

    assert len(scores) > 10, sorted(scores)


def test_market_cap_is_in_dollars(rows):
    """AAPL 시가총액은 조 단위 달러다. 원으로 섞이면 1000배가 넘게 커진다."""
    aapl = next(r for r in rows if r["ticker"] == "AAPL")

    assert 1e12 < aapl["market_cap"] < 1e13


def test_the_kr_snapshot_is_still_the_kr_default():
    """미국을 분석한 뒤에도 한국의 최신 스냅샷은 한국 것이다."""
    with connect(DEFAULT_DB_PATH) as conn:
        kr = Repository(conn).get_latest_snapshot(market="KR")

    assert kr is not None and kr.market == "KR"
```

Run: `.venv/Scripts/python.exe -m pytest tests/test_integration_us.py`
Expected: PASS. `test_market_cap_is_in_dollars`의 범위는 AAPL 주가 × 유통주식수(약 148억 주)다 — 주가가 크게 달라졌으면 범위를 보고 판단하라.

- [ ] **Step 4: `analysis/`가 그대로인지 확인한다**

```bash
git diff 2b79357 -- src/stockscout/analysis/
```

Expected: **출력 없음.** 한 줄이라도 나오면 멈추고 보고하라 — 설계서 §7.1의 시장 분리가 어딘가에서 새고 있다는 뜻이다.

계층 규칙도 확인한다.

```bash
grep -rn "^from stockscout\.\(normalize\|sources\|store\|pipeline\)" src/stockscout/analysis/ || echo "계층 위반 없음"
```

- [ ] **Step 5: 브라우저로 본다**

```bash
.venv/Scripts/stockscout.exe serve --port 8799
```

1. 처음 열면 KR 탭이고 42종목이 보인다
2. US 탭을 누르면 40종목, 현재가가 `$123.45` 형식이다
3. 새로고침하면 마지막에 고른 탭이 유지된다
4. 미국 종목 상세의 차트 y축·재무 추이가 달러다
5. KR로 돌아가면 한국 종목이 그대로다

- [ ] **Step 6: 전체 스위트**

Run: `.venv/Scripts/python.exe -m pytest`
Expected: 실패 0건

- [ ] **Step 7: 커밋**

```bash
git add tests/test_integration_us.py
git commit -m "test: 실제 미국 40종목으로 파이프라인을 관통한다

analysis/를 한 줄도 바꾸지 않고 미국이 같은 화면에 합류했다. 설계서
§15가 '그것이 이 설계가 맞았는지에 대한 검증'이라고 정한 조건이다.

금융 4종목이 제외되는지, 대부분이 채점되는지, 점수가 퍼지는지,
시가총액이 달러인지, 미국을 분석한 뒤에도 한국 스냅샷이 한국 것인지
본다.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## 완료 기준

- [ ] `stockscout collect/normalize/analyze --market US`가 40종목을 끝까지 처리한다
- [ ] 10-K 안의 분기 값이 연간 값으로 들어가지 않는다 (Task 5 테스트)
- [ ] 미국만 수집할 때 DART 키를 요구하지 않고, SEC 연락처가 없으면 무엇을 넣으라고 말한다
- [ ] 금융 4종목이 `FINANCIAL_SECTOR`로 제외된다
- [ ] 비금융 종목의 80% 이상이 채점된다
- [ ] 설계서 §18.4에 필드별 적중률 실측 표가 있다
- [ ] 미국을 분석해도 한국 화면과 API 기본값이 한국이다
- [ ] 화면에 KR | US 탭이 있고 달러가 `$1,234.56`으로 보인다
- [ ] **`git diff 2b79357 -- src/stockscout/analysis/`가 비어 있다**
- [ ] `pytest` 전체 통과

## 다음 계획

**계획 F — S&P 500 전체.** 40종목에서 매핑이 검증되면 구성종목 목록 소스(위키피디아 표 또는 SEC 공시)를 붙이고 500종목으로 넓힌다. 첫 수집이 EDGAR 501회 + yfinance 500회라 진행률 표시가 필요해진다.

**계획 A에서 넘어와 아직 안 한 것:**

- I3 — pykrx·yfinance 재시도 (`sources/ratelimit.py`의 `TRANSIENT`에 `requests` 예외가 없다. yfinance도 내부적으로 requests 계열을 쓴다)
- I5 — 설정 기반 소스 선택 (`config.sources`가 아직 배선되지 않았다. 이 계획은 시장으로 소스를 고른다)
