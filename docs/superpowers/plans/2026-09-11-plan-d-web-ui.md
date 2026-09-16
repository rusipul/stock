# 계획 D — 웹 UI

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `stockscout serve` 로 브라우저를 열면 좌측에서 종목을 고르고 우측에서 그 종목의 주가 차트와 하락률 판정 근거를 볼 수 있다.

**Architecture:** 설계서 §11의 좌우 분할 단일 화면. FastAPI가 저장된 스냅샷을 **읽기만** 하고, 프론트는 빌드 스텝 없는 정적 HTML + vanilla JS + Chart.js(CDN)다. 임계치 슬라이더는 서버 왕복 없이 클라이언트에서 즉시 재판정한다(설계서 §6.1).

**Tech Stack:** FastAPI / uvicorn / 정적 HTML·CSS·vanilla JS / Chart.js 4 (CDN) / pytest + httpx TestClient. **빌드 도구 없음.**

**참조 문서:** 설계서 `docs/superpowers/specs/2026-09-10-stock-screener-design.md` §4.2(기술 스택) §6.1(재계산 불필요) §9(하락 신호) §11(웹 UI) §12(CLI)

---

## 시작 전 상태

계획 A·B가 `master`에 병합돼 있다. 테스트 295개 통과, 2개 skip.

`data/stockscout.db` 실제 데이터:

| | 내용 |
|---|---|
| `securities` | 42행 (KOSPI 대표종목, 7개 `is_financial=1`) |
| `raw_prices` | 51,231행 · 2021-09-10 ~ 2026-09-10 |
| `snapshots` | 최신 1건 · `as_of=2026-09-10` |
| `analysis_results` | 42행 |
| `financials` · `raw_financials` | **0행** — DART 키 대기 |
| `watchlist` | 0행 |

### 지금 채워진 컬럼과 빈 컬럼

`analysis_results` 42행에서 실측한 결과다.

**채워짐:** `ticker` `passed_filters` `filter_failures` `price_current` `avg_price_1y/3y/5y` `high_5y` `drop_vs_avg_1y/3y/5y` `drop_vs_high`

**비어 있음:** `score_total` `score_profit` `score_growth` `score_stability` `score_cashflow` `score_detail` `market_cap` `per` `pbr` `signal_grade` `signal_reason`

---

## 이 계획의 핵심 결정 두 가지

### 1. 점수가 없는 동안 등급을 표시하지 않는다

설계서 §9.3의 ★★는 **"우량주 점수 통과 **AND** 하락 임계치 통과"** 를 뜻한다. 점수가 없는 상태에서 가격만으로 ★★를 매기면, **품질을 검증한 것처럼 보이게 만드는 거짓말**이 된다. 이 제품은 "싸다"와 "좋다"를 구분하는 것이 존재 이유이므로 그 구분을 화면에서 흐리면 안 된다.

그래서 §11.1의 등급 필터 칩(★★/★)은 점수가 없는 동안 **비활성**이고, 대신 `passed_filters` 기준 **통과/제외** 칩을 쓴다. `passed_filters`는 진짜 값이다 — 가격 기반 하드필터 2종(`FINANCIAL_SECTOR`·`INSUFFICIENT_HISTORY`)의 판정 결과다.

우량주 점수 슬라이더도 같은 이유로 비활성이며, 왜 비활성인지와 무엇을 하면 켜지는지를 화면이 말한다.

### 2. 빈 패널은 임시 자리표시자가 아니라 제품 상태다

재무를 수집하지 않은 사용자는 누구든 이 화면을 본다. 계획 C가 오면 사라질 임시 화면이 아니라, **DART 키 없이 이 도구를 쓰는 사람이 계속 보게 될 상태**다.

그래서 빈 패널은 회색 박스가 아니라 다음을 말한다.

```
재무 데이터가 없습니다

DART 공시에서 재무제표를 수집하면 우량주 점수와 재무 추이가 여기에
표시됩니다. 지금은 주가 기준 하락률만 계산됩니다.

  1. https://opendart.fss.or.kr 에서 인증키를 발급받습니다
  2. config/.env 의 DART_API_KEY= 뒤에 붙여넣습니다
  3. stockscout collect --market KR
     stockscout normalize --market KR
     stockscout analyze --market KR
```

**"준비 중"이나 "곧 지원"이라고 쓰지 않는다.** 사용자가 직접 할 수 있는 일이고, 그 방법을 정확히 적는 것이 맞다.

---

## 파일 구조

| 파일 | 책임 |
|---|---|
| `src/stockscout/web/__init__.py` | 빈 패키지 마커 |
| `src/stockscout/web/api.py` | FastAPI 앱과 라우트. 저장된 스냅샷을 읽기만 한다 |
| `src/stockscout/web/schemas.py` | 응답 모델(pydantic). API 계약을 한곳에 모은다 |
| `src/stockscout/web/static/index.html` | 좌우 분할 레이아웃 |
| `src/stockscout/web/static/style.css` | 전체 스타일 |
| `src/stockscout/web/static/app.js` | 데이터 로드·필터·정렬·차트 |
| `src/stockscout/cli.py` (수정) | `serve` 하위명령 |
| `pyproject.toml` (수정) | `fastapi`·`uvicorn` 의존성, 정적 파일 패키지 데이터 |

**계층 규칙:** `web/`은 `store/`와 `models/`를 읽는다. `analysis/`·`sources/`를 import하지 않는다 — 웹은 계산하지 않고 저장된 것을 보여줄 뿐이다(설계서 §4). `pipeline/`도 import하지 않는다. 설계서 §11.3의 `POST /api/refresh`는 이 계획에서 **구현하지 않는다**(아래 참조).

```
        cli
       ↙   ↘
  pipeline  web
   ↙  ↓  ↘   ↓
sources normalize analysis store
   ↘   ↘  ↓  ↙   ↙
        models
```

### 설계서 §11.3에서 빼는 것

`POST /api/refresh` 와 `GET /api/refresh/status` 를 구현하지 않는다.

수집은 42종목 × pykrx 호출로 수 분이 걸리고, DART가 붙으면 일일 한도까지 얽힌다. 이를 웹에서 백그라운드로 돌리려면 작업 큐·진행률 추적·중복 실행 방지가 필요한데, 그것들은 화면 하나를 위해 들이기엔 큰 기계장치다. CLI가 이미 그 일을 하고 요약까지 출력한다.

빈 상태 패널이 실행할 명령을 그대로 보여주므로 사용자가 막히지도 않는다. 나중에 정말 필요해지면 그때 붙인다.

---

## 실행 중 발견된 변경 (2026-09-11)

### 쿼리 파라미터에 `Literal[1, 3, 5]`를 쓰면 안 된다

Task 4의 차트 창 파라미터를 `window: Literal[1, 3, 5] = 5`로 선언했더니
**1·3·5가 전부 422로 거부됐다.** 세 값 중 하나가 아니라 셋 다 막힌다.

쿼리 문자열은 pydantic에 문자열로 들어오고, `Literal`은 정수 리터럴과
문자열 `"1"`을 같은 값으로 보지 않는다. `int`였다면 강제 변환이 일어나지만
`Literal`은 변환 대상이 아니라 비교 대상이다. 세 방식을 나란히 실측했다.

| 선언 | 런타임 | OpenAPI 스키마 |
|---|---|---|
| `Literal[1, 3, 5]` | 전부 422 | 값 3개가 나온다 |
| `int` + 수동 검증 | 동작 | 제약이 안 보인다 |
| `IntEnum` | 동작 | 값 3개가 나온다 |

`IntEnum`만 런타임과 스키마 양쪽에서 맞다. `ChartWindow(IntEnum)`을
`web/api.py`에 두고 이유를 docstring에 적었다.

### `serve()`가 바인드에 실패해도 성공을 보고했다

포트 8765가 이전 세션 서버에 점유된 상태로 `stockscout serve`를 띄웠더니
`http://127.0.0.1:8765 에서 실행 중입니다`가 먼저 찍히고 uvicorn이 뒤이어
종료했다. `print`가 `uvicorn.run()` **앞**에 있기 때문이다. 브라우저를
열어보기 전까지는 서버가 떠 있다고 믿게 된다.

소켓을 `serve()`가 직접 바인드한 뒤 출력하도록 바꿨다. 겸사겸사 uvicorn이
켜는 `SO_REUSEADDR`을 끊었다 — 윈도우에서 그 옵션은 "이미 쓰는 주소에도
붙어라"는 뜻이라, 남의 서버 위에 얹히고도 성공할 수 있다.

부수적으로 함께 고친 것:

- 브라우저가 자동 요청하던 `/favicon.ico` 404 → 인라인 data URI로 요청 자체를 제거
- 파이프로 넘길 때 블록 버퍼링에 걸려 주소 줄이 종료 시점까지 안 보이던 문제 (`flush=True`)
- `.gitignore`가 `data/*.db-journal`만 막고 WAL 모드의 `-shm`/`-wal`을 흘리던 문제

### `python -m stockscout`는 동작하지 않는다

`__main__.py`가 없다. 진입점은 콘솔 스크립트(`.venv/Scripts/stockscout.exe`)
하나다. 문서와 안내 문구는 전부 `stockscout ...` 형태를 쓴다.

### 실제 브라우저 관통 검증 (Task 8 Step 3)

Chromium으로 9개 항목을 전부 확인했다. **콘솔 에러 0건, 실패 요청 0건.**

| # | 항목 | 결과 |
|---|---|---|
| 1 | 상단 정보 | `기준일 2026-09-10 · 42종목 · 통과 34 · 제외 8` |
| 2 | 슬라이더 -40%→-20% | 3→4종목, **네트워크 요청 0건** |
| 3 | `임계치 이하` 칩 | 3종목 |
| 4 | 차트 기준선 | 1,393,000 / 500,559 / 319,391 / 268,180 |
| 5 | 판정 근거 | `295,000 / 500,559 - 1 = -41.1%` |
| 6 | 재무 추이 자리 | DART 인증키 발급 + 3단계 명령 |
| 7 | KB금융 | `제외` 뱃지 + `FINANCIAL_SECTOR` |
| 8 | 별 → 새로고침 | 유지됨 |
| 9 | 검색 `삼성` | 8종목 (기대 목록과 일치) |

2번이 설계서 §6.1의 핵심이다. 슬라이더를 움직여도 요청이 한 건도 나가지
않는다 — 등급 재계산이 전부 클라이언트에서 일어난다.

### 테스트 수

Task 8 Step 4의 `356 passed`는 계획을 쓸 때의 추정치였다. 실제는
**407 passed, 2 skipped**이다. skip 2건은 DART 응답 픽스처가 없어서 멈춰
있는 것으로, 인증키가 나오면 풀린다.

### 계획 C가 알아야 할 것

1. **`has_scores`·`has_valuation`이 화면 전체의 스위치다.** 점수를 채우면
   우량주 점수 슬라이더, ★★/★ 등급 칩, 점수 KPI, 재무 추이 5년표가 전부
   자동으로 살아난다. 화면 쪽 추가 작업이 필요 없다. 다만 설계서 §11.2의
   「점수 분해」 4축 막대는 새로 붙여야 한다.

2. **판정 근거 문장은 `web/api.py`의 모듈 수준 `_rationale(row, failures)`가
   만든다.** 점수 산출 줄은 여기에 추가한다.

3. **`tests/test_web_integration.py`는 지금의 실제 DB 상태를 하드코딩한다.**
   계획 C가 성공하면 `test_scores_are_absent_until_dart_key`와
   `test_financials_are_empty_everywhere`가 **의도대로 실패한다.** 그때
   지우지 말고 반대 방향 단언으로 바꿔라. 이 두 테스트는 "아직 없다"가 아니라
   "없다는 사실이 화면에 정직하게 드러난다"를 지키는 장치다.

4. **등급 재계산에 서버 왕복을 들이지 마라.** 점수 기준이 추가돼도 `app.js`의
   `hits(row)` 안에서 끝나야 한다 (설계서 §6.1).

---

## Task 1: FastAPI 앱 골격과 serve 명령

**Files:**
- Create: `src/stockscout/web/__init__.py`
- Create: `src/stockscout/web/schemas.py`
- Create: `src/stockscout/web/api.py`
- Modify: `src/stockscout/cli.py`
- Modify: `pyproject.toml`
- Test: `tests/test_web_snapshot.py`

- [ ] **Step 1: 의존성을 추가한다**

`pyproject.toml`의 `dependencies`에 추가:

```toml
    "fastapi>=0.115",
    "uvicorn>=0.32",
```

`[tool.setuptools.package-data]`를 다음으로 교체한다:

```toml
[tool.setuptools.package-data]
stockscout = ["store/*.sql", "web/static/*"]
```

설치:

```bash
C:/Users/USER/DEV/주식투자/.venv/Scripts/python.exe -m pip install -e ".[dev]" -q
```

- [ ] **Step 2: 실패하는 테스트를 작성한다**

`tests/test_web_snapshot.py`:

```python
import pytest
from fastapi.testclient import TestClient

from stockscout.models import (
    AnalysisResult,
    FilterOutcome,
    PriceStats,
    Security,
    Snapshot,
    Valuation,
)
from stockscout.store.db import connect, init_db
from stockscout.store.repository import Repository
from stockscout.web.api import create_app


def stats(current=41300.0, avg5=71400.0, high=88800.0):
    return PriceStats(
        price_current=current,
        avg_price={1: 55000.0, 3: 64200.0, 5: avg5},
        high_5y=high,
        drop_vs_avg={1: -0.249, 3: -0.357, 5: current / avg5 - 1},
        drop_vs_high=current / high - 1,
    )


def result(ticker="005930.KS", failures=None, **kw):
    return AnalysisResult(
        ticker=ticker,
        filters=FilterOutcome(failures=failures or []),
        stats=stats(**kw),
        valuation=Valuation(),
    )


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "t.db"
    with connect(path) as conn:
        init_db(conn)
        repo = Repository(conn)
        repo.upsert_security(Security(
            ticker="005930.KS", market="KR", name="삼성전자",
            exchange="KOSPI", currency="KRW"))
        repo.upsert_security(Security(
            ticker="105560.KS", market="KR", name="KB금융",
            exchange="KOSPI", currency="KRW", is_financial=True))
        repo.save_snapshot(
            Snapshot(run_at="2026-09-11T02:00:00+00:00", as_of="2026-09-10",
                     config_hash="h1", config_json='{"signal": {"min_score": 70}}',
                     note="테스트"),
            [result(), result("105560.KS", failures=["FINANCIAL_SECTOR"])],
        )
    return path


@pytest.fixture
def client(db):
    return TestClient(create_app(db_path=db))


def test_snapshot_returns_metadata(client):
    body = client.get("/api/snapshot").json()

    assert body["snapshot_id"] == 1
    assert body["as_of"] == "2026-09-10"
    assert body["note"] == "테스트"


def test_snapshot_reports_counts(client):
    body = client.get("/api/snapshot").json()

    assert body["total"] == 2
    assert body["passed"] == 1
    assert body["excluded"] == 1


def test_snapshot_reports_whether_scores_exist(client):
    """점수가 없으면 프론트가 등급 UI를 비활성화해야 한다."""
    body = client.get("/api/snapshot").json()

    assert body["has_scores"] is False
    assert body["has_valuation"] is False


def test_snapshot_404_when_no_snapshot(tmp_path):
    path = tmp_path / "empty.db"
    with connect(path) as conn:
        init_db(conn)
    client = TestClient(create_app(db_path=path))

    response = client.get("/api/snapshot")

    assert response.status_code == 404
    assert "분석" in response.json()["detail"]


def test_index_is_served(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
```

- [ ] **Step 3: 테스트가 실패하는지 확인한다**

Run: `C:/Users/USER/DEV/주식투자/.venv/Scripts/python.exe -m pytest tests/test_web_snapshot.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'stockscout.web'`

- [ ] **Step 4: 응답 모델을 작성한다**

`src/stockscout/web/schemas.py`:

```python
"""API 응답 모델. 계약을 한곳에 모은다.

저장 계층의 원본 행을 그대로 내보내지 않는 이유: 컬럼 이름이 바뀌면
프론트가 조용히 깨진다. 여기서 한 번 형태를 고정하면 변경이 컴파일
에러처럼 드러난다.
"""

from __future__ import annotations

from pydantic import BaseModel


class SnapshotInfo(BaseModel):
    snapshot_id: int
    run_at: str
    as_of: str | None
    config_hash: str
    note: str | None

    total: int
    passed: int
    excluded: int

    # 프론트가 등급·점수 UI를 켤지 결정하는 근거.
    # 점수가 없는데 등급을 보여주면 품질을 검증한 것처럼 보이는 거짓말이 된다.
    has_scores: bool

    # 시가총액·PER·PBR을 계산할 수 있었는가.
    # financials 테이블이 채워졌는지와 다른 질문이다 — 발행주식수는
    # 재무제표 API가 아니라 stockTotqySttus.json 에서 온다.
    has_valuation: bool


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

    score_total: float | None
    market_cap: float | None
    per: float | None
    pbr: float | None

    in_watchlist: bool
```

- [ ] **Step 5: 앱을 구현한다**

`src/stockscout/web/api.py`:

```python
"""FastAPI 앱. 저장된 스냅샷을 읽기만 한다.

계산하지 않는다 — analysis/·sources/·pipeline/을 import하지 않는 이유다.
화면에 보이는 모든 숫자는 analyze가 이미 계산해 스냅샷에 넣은 것이다.
그래야 "화면의 숫자가 어느 실행에서 나왔는가"가 항상 명확하다.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from stockscout.store.db import DEFAULT_DB_PATH, connect
from stockscout.store.repository import Repository
from stockscout.web.schemas import SnapshotInfo

STATIC_DIR = Path(__file__).parent / "static"


def create_app(db_path: Path | str = DEFAULT_DB_PATH) -> FastAPI:
    """앱을 만든다. db_path를 주입받아 테스트가 임시 DB를 쓸 수 있게 한다."""
    app = FastAPI(title="stockscout")
    db_path = Path(db_path)

    def read(fn):
        """요청마다 연결을 열고 닫는다.

        SQLite 연결은 스레드를 넘나들 수 없고, 이 앱은 읽기 전용이라
        연결 풀링이 필요할 만큼 무겁지 않다.
        """
        with connect(db_path) as conn:
            return fn(Repository(conn))

    @app.get("/api/snapshot", response_model=SnapshotInfo)
    def get_snapshot() -> SnapshotInfo:
        def build(repo: Repository) -> SnapshotInfo:
            snap = repo.get_latest_snapshot()
            if snap is None:
                raise HTTPException(
                    status_code=404,
                    detail="분석 결과가 없습니다. stockscout analyze 를 먼저 실행하세요.",
                )
            rows = repo.get_analysis_results(snap.snapshot_id)
            return SnapshotInfo(
                snapshot_id=snap.snapshot_id,
                run_at=snap.run_at,
                as_of=snap.as_of,
                config_hash=snap.config_hash,
                note=snap.note,
                total=len(rows),
                passed=sum(1 for r in rows if r["passed_filters"]),
                excluded=sum(1 for r in rows if not r["passed_filters"]),
                has_scores=any(r["score_total"] is not None for r in rows),
                has_valuation=any(r["per"] is not None for r in rows),
            )

        return read(build)

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
```

`src/stockscout/web/__init__.py`: 빈 파일로 생성한다.

`src/stockscout/web/static/index.html` — 이 Task에서는 최소 형태로 만들고 Task 6·7에서 채운다:

```html
<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>stockscout</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
  <div id="app">로딩 중…</div>
  <script src="/static/app.js"></script>
</body>
</html>
```

`src/stockscout/web/static/style.css` 와 `src/stockscout/web/static/app.js` 는 빈 파일로 생성한다 (Task 6·7에서 채운다). `index.html`이 참조하므로 없으면 404가 난다.

- [ ] **Step 6: 테스트가 통과하는지 확인한다**

Run: `C:/Users/USER/DEV/주식투자/.venv/Scripts/python.exe -m pytest tests/test_web_snapshot.py -v`
Expected: PASS — `5 passed`

- [ ] **Step 7: serve 명령을 배선한다**

`src/stockscout/cli.py`의 `build_parser()` 안, `p_ana` 블록 뒤에 추가:

```python
    p_srv = sub.add_parser("serve", help="브라우저에서 결과를 본다")
    p_srv.add_argument("--host", default="127.0.0.1")
    p_srv.add_argument("--port", type=int, default=8000)
```

`main()` 안, `analyze` 분기 뒤에 추가:

```python
        elif args.command == "serve":
            serve(args.db, host=args.host, port=args.port)
```

`cli.py`의 `render_ranking` 앞에 추가:

```python
def serve(db_path: str, host: str, port: int) -> None:
    """개발용 서버를 띄운다.

    uvicorn과 web 모듈을 함수 안에서 import한다. 다른 하위명령은
    FastAPI가 필요 없으므로, 최상단에서 import하면 collect 한 번
    돌리는 데도 웹 스택 전체가 로드된다.
    """
    import uvicorn

    from stockscout.web.api import create_app

    print(f"http://{host}:{port} 에서 실행 중입니다. 종료하려면 Ctrl+C.")
    uvicorn.run(create_app(db_path), host=host, port=port, log_level="warning")
```

- [ ] **Step 8: 파서 테스트를 추가한다**

`tests/test_cli.py` 맨 아래에 추가:

```python
def test_parser_serve_command():
    args = build_parser().parse_args(["serve"])

    assert args.command == "serve"
    assert args.host == "127.0.0.1"
    assert args.port == 8000


def test_parser_serve_accepts_host_and_port():
    args = build_parser().parse_args(["serve", "--host", "0.0.0.0", "--port", "9000"])

    assert args.host == "0.0.0.0"
    assert args.port == 9000
```

- [ ] **Step 9: 실제로 서버가 뜨는지 확인한다**

```bash
C:/Users/USER/DEV/주식투자/.venv/Scripts/python.exe -c "
from fastapi.testclient import TestClient
from stockscout.web.api import create_app
c = TestClient(create_app('data/stockscout.db'))
s = c.get('/api/snapshot').json()
print('snapshot', s['snapshot_id'], 'as_of', s['as_of'])
print('total', s['total'], 'passed', s['passed'], 'excluded', s['excluded'])
print('has_scores', s['has_scores'], 'has_valuation', s['has_valuation'])
print('index', c.get('/').status_code)
"
```

Expected: `total 42 passed 34 excluded 8`, `has_scores False has_valuation False`, `index 200`

- [ ] **Step 10: 전체 스위트를 돌리고 커밋한다**

Run: `C:/Users/USER/DEV/주식투자/.venv/Scripts/python.exe -m pytest`
Expected: `302 passed, 2 skipped`

```bash
git add src/stockscout/web/ src/stockscout/cli.py pyproject.toml tests/test_web_snapshot.py tests/test_cli.py
git commit -m "feat: FastAPI 앱 골격과 serve 명령"
```

---

## Task 2: 종목 목록 API

**Files:**
- Modify: `src/stockscout/web/api.py`
- Test: `tests/test_web_securities.py`

- [ ] **Step 1: 실패하는 테스트를 작성한다**

`tests/test_web_securities.py`:

```python
import pytest
from fastapi.testclient import TestClient

from stockscout.models import (
    AnalysisResult,
    FilterOutcome,
    PriceStats,
    Security,
    Snapshot,
    Valuation,
)
from stockscout.store.db import connect, init_db
from stockscout.store.repository import Repository
from stockscout.web.api import create_app


def result(ticker, current, avg5, failures=None):
    return AnalysisResult(
        ticker=ticker,
        filters=FilterOutcome(failures=failures or []),
        stats=PriceStats(
            price_current=current,
            avg_price={1: avg5, 3: avg5, 5: avg5},
            high_5y=avg5 * 2,
            drop_vs_avg={1: current / avg5 - 1, 3: current / avg5 - 1,
                         5: current / avg5 - 1},
            drop_vs_high=current / (avg5 * 2) - 1,
        ),
        valuation=Valuation(),
    )


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "t.db"
    with connect(path) as conn:
        init_db(conn)
        repo = Repository(conn)
        for ticker, name, fin in [
            ("005930.KS", "삼성전자", False),
            ("035720.KS", "카카오", False),
            ("105560.KS", "KB금융", True),
        ]:
            repo.upsert_security(Security(
                ticker=ticker, market="KR", name=name, exchange="KOSPI",
                currency="KRW", is_financial=fin))
        repo.save_snapshot(
            Snapshot(run_at="2026-09-11T02:00:00+00:00", as_of="2026-09-10",
                     config_hash="h1", config_json="{}"),
            [
                result("005930.KS", 100.0, 100.0),    # 평균대비 0%
                result("035720.KS", 60.0, 100.0),     # 평균대비 -40%
                result("105560.KS", 80.0, 100.0, failures=["FINANCIAL_SECTOR"]),
            ],
        )
    return path


@pytest.fixture
def client(db):
    return TestClient(create_app(db_path=db))


def test_returns_every_security_in_the_snapshot(client):
    rows = client.get("/api/securities").json()

    assert {r["ticker"] for r in rows} == {"005930.KS", "035720.KS", "105560.KS"}


def test_row_carries_name_and_exchange_from_master(client):
    rows = {r["ticker"]: r for r in client.get("/api/securities").json()}

    assert rows["005930.KS"]["name"] == "삼성전자"
    assert rows["005930.KS"]["exchange"] == "KOSPI"
    assert rows["105560.KS"]["is_financial"] is True


def test_row_carries_every_metric_the_slider_needs(client):
    """프론트가 서버 왕복 없이 재판정하려면 지표가 전부 있어야 한다."""
    row = next(r for r in client.get("/api/securities").json()
               if r["ticker"] == "035720.KS")

    assert row["price_current"] == 60.0
    assert row["avg_price_5y"] == 100.0
    assert row["drop_vs_avg_5y"] == pytest.approx(-0.4)
    assert row["drop_vs_high"] == pytest.approx(-0.7)
    assert row["high_5y"] == 200.0


def test_filter_failures_are_parsed_into_a_list(client):
    row = next(r for r in client.get("/api/securities").json()
               if r["ticker"] == "105560.KS")

    assert row["passed_filters"] is False
    assert row["filter_failures"] == ["FINANCIAL_SECTOR"]


def test_empty_score_columns_are_null_not_zero(client):
    """0점과 '점수 없음'은 다르다. 프론트가 구분해야 한다."""
    row = client.get("/api/securities").json()[0]

    assert row["score_total"] is None
    assert row["per"] is None
    assert row["market_cap"] is None


def test_search_matches_name_and_ticker(client):
    by_name = client.get("/api/securities", params={"q": "카카오"}).json()
    by_ticker = client.get("/api/securities", params={"q": "035720"}).json()

    assert [r["ticker"] for r in by_name] == ["035720.KS"]
    assert [r["ticker"] for r in by_ticker] == ["035720.KS"]


def test_search_is_case_insensitive_for_ascii(client):
    rows = client.get("/api/securities", params={"q": "ks"}).json()

    assert len(rows) == 3


def test_sort_by_drop_puts_most_fallen_first(client):
    rows = client.get("/api/securities", params={"sort": "drop"}).json()

    assert rows[0]["ticker"] == "035720.KS"   # -40%


def test_sort_by_name_is_alphabetical(client):
    rows = client.get("/api/securities", params={"sort": "name"}).json()

    assert [r["name"] for r in rows] == ["KB금융", "삼성전자", "카카오"]


def test_unknown_sort_is_rejected(client):
    response = client.get("/api/securities", params={"sort": "bogus"})

    assert response.status_code == 422


def test_watchlist_membership_is_reported(client, db):
    with connect(db) as conn:
        conn.execute("INSERT INTO watchlist (ticker, added_at) "
                     "VALUES ('005930.KS', '2026-09-11')")
        conn.commit()

    rows = {r["ticker"]: r for r in client.get("/api/securities").json()}

    assert rows["005930.KS"]["in_watchlist"] is True
    assert rows["035720.KS"]["in_watchlist"] is False


def test_404_when_no_snapshot(tmp_path):
    path = tmp_path / "empty.db"
    with connect(path) as conn:
        init_db(conn)

    response = TestClient(create_app(db_path=path)).get("/api/securities")

    assert response.status_code == 404
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

Run: `C:/Users/USER/DEV/주식투자/.venv/Scripts/python.exe -m pytest tests/test_web_securities.py -v`
Expected: FAIL — 404 (라우트가 없다)

- [ ] **Step 3: 구현한다**

`src/stockscout/web/api.py`의 import에 추가:

```python
from typing import Literal

from fastapi import Query

from stockscout.web.schemas import SecurityRow, SnapshotInfo
```

`index()` 앞에 라우트를 추가한다:

```python
    @app.get("/api/securities", response_model=list[SecurityRow])
    def list_securities(
        q: str | None = None,
        sort: Literal["drop", "name", "ticker"] = "drop",
    ) -> list[SecurityRow]:
        """스냅샷의 전 종목을 한 번에 내려보낸다.

        페이지네이션을 두지 않는 이유: 유니버스가 수백 종목이고, 프론트가
        슬라이더로 즉시 재판정하려면 어차피 전부 필요하다(설계서 §6.1).
        서버 왕복을 없애는 것이 이 화면의 핵심 반응성이다.
        """
        def build(repo: Repository) -> list[SecurityRow]:
            snap = repo.get_latest_snapshot()
            if snap is None:
                raise HTTPException(
                    status_code=404,
                    detail="분석 결과가 없습니다. stockscout analyze 를 먼저 실행하세요.",
                )
            masters = {s.ticker: s for s in repo.list_securities(in_universe_only=False)}
            watched = repo.watchlist_tickers()

            rows = []
            for r in repo.get_analysis_results(snap.snapshot_id):
                master = masters.get(r["ticker"])
                rows.append(SecurityRow(
                    ticker=r["ticker"],
                    name=master.name if master else r["ticker"],
                    exchange=master.exchange if master else None,
                    is_financial=bool(master.is_financial) if master else False,
                    passed_filters=bool(r["passed_filters"]),
                    filter_failures=json.loads(r["filter_failures"] or "[]"),
                    price_current=r["price_current"],
                    avg_price_1y=r["avg_price_1y"],
                    avg_price_3y=r["avg_price_3y"],
                    avg_price_5y=r["avg_price_5y"],
                    high_5y=r["high_5y"],
                    drop_vs_avg_1y=r["drop_vs_avg_1y"],
                    drop_vs_avg_3y=r["drop_vs_avg_3y"],
                    drop_vs_avg_5y=r["drop_vs_avg_5y"],
                    drop_vs_high=r["drop_vs_high"],
                    score_total=r["score_total"],
                    market_cap=r["market_cap"],
                    per=r["per"],
                    pbr=r["pbr"],
                    in_watchlist=r["ticker"] in watched,
                ))

            if q:
                needle = q.lower()
                rows = [x for x in rows
                        if needle in x.name.lower() or needle in x.ticker.lower()]

            if sort == "drop":
                # 하락률이 없는 종목(커버리지 미달)은 항상 뒤로 보낸다.
                rows.sort(key=lambda x: (x.drop_vs_avg_5y is None,
                                         x.drop_vs_avg_5y or 0.0))
            elif sort == "name":
                rows.sort(key=lambda x: x.name)
            else:
                rows.sort(key=lambda x: x.ticker)

            return rows

        return read(build)
```

- [ ] **Step 4: Repository에 관심종목 조회를 추가한다**

`src/stockscout/store/repository.py`의 `record_error` 앞에 추가:

```python
    # --- 관심종목 -----------------------------------------------------

    def watchlist_tickers(self) -> set[str]:
        rows = self.conn.execute("SELECT ticker FROM watchlist").fetchall()
        return {r["ticker"] for r in rows}
```

- [ ] **Step 5: 테스트가 통과하는지 확인한다**

Run: `C:/Users/USER/DEV/주식투자/.venv/Scripts/python.exe -m pytest tests/test_web_securities.py -v`
Expected: PASS — `12 passed`

- [ ] **Step 6: 실제 데이터로 확인한다**

```bash
C:/Users/USER/DEV/주식투자/.venv/Scripts/python.exe -c "
from fastapi.testclient import TestClient
from stockscout.web.api import create_app
c = TestClient(create_app('data/stockscout.db'))
rows = c.get('/api/securities', params={'sort':'drop'}).json()
print('rows', len(rows))
for r in rows[:4]:
    print(f\"  {r['ticker']:12} drop5={r['drop_vs_avg_5y']*100:6.1f}%  passed={r['passed_filters']}\")
"
```

Expected: 42행, 상위 4개가 `051900.KS`(LG생활건강) `035720.KS`(카카오) `097950.KS`(CJ제일제당) `051910.KS`(LG화학) 순

- [ ] **Step 7: 전체 스위트를 돌리고 커밋한다**

Expected: `314 passed, 2 skipped`

```bash
git add src/stockscout/web/api.py src/stockscout/store/repository.py tests/test_web_securities.py
git commit -m "feat: 종목 목록 API"
```

---

## Task 3: 종목 상세 API

**Files:**
- Modify: `src/stockscout/web/schemas.py`
- Modify: `src/stockscout/web/api.py`
- Test: `tests/test_web_detail.py`

- [ ] **Step 1: 실패하는 테스트를 작성한다**

`tests/test_web_detail.py`:

```python
import pytest
from fastapi.testclient import TestClient

from stockscout.models import (
    AnalysisResult,
    FilterOutcome,
    Financials,
    PriceStats,
    Security,
    Snapshot,
    Valuation,
)
from stockscout.store.db import connect, init_db
from stockscout.store.repository import Repository
from stockscout.web.api import create_app


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "t.db"
    with connect(path) as conn:
        init_db(conn)
        repo = Repository(conn)
        repo.upsert_security(Security(
            ticker="005930.KS", market="KR", name="삼성전자",
            exchange="KOSPI", currency="KRW"))
        repo.upsert_security(Security(
            ticker="105560.KS", market="KR", name="KB금융",
            exchange="KOSPI", currency="KRW", is_financial=True))
        repo.save_snapshot(
            Snapshot(run_at="2026-09-11T02:00:00+00:00", as_of="2026-09-10",
                     config_hash="h1", config_json="{}"),
            [
                AnalysisResult(
                    ticker="005930.KS", filters=FilterOutcome(failures=[]),
                    stats=PriceStats(
                        price_current=60.0,
                        avg_price={1: 80.0, 3: 90.0, 5: 100.0},
                        high_5y=200.0,
                        drop_vs_avg={1: -0.25, 3: -1/3, 5: -0.4},
                        drop_vs_high=-0.7),
                    valuation=Valuation()),
                AnalysisResult(
                    ticker="105560.KS",
                    filters=FilterOutcome(failures=["FINANCIAL_SECTOR"]),
                    stats=PriceStats(
                        price_current=100.0, avg_price={1: 100.0, 3: 100.0, 5: 100.0},
                        high_5y=100.0, drop_vs_avg={1: 0.0, 3: 0.0, 5: 0.0},
                        drop_vs_high=0.0),
                    valuation=Valuation()),
            ],
        )
    return path


@pytest.fixture
def client(db):
    return TestClient(create_app(db_path=db))


def test_detail_returns_master_and_metrics(client):
    body = client.get("/api/securities/005930.KS").json()

    assert body["name"] == "삼성전자"
    assert body["price_current"] == 60.0
    assert body["drop_vs_avg_5y"] == pytest.approx(-0.4)


def test_detail_includes_computation_lines(client):
    """설계서 §11.2: 판정 근거를 실제 숫자로 노출한다."""
    body = client.get("/api/securities/005930.KS").json()

    lines = body["rationale"]
    joined = " ".join(lines)
    assert "60" in joined and "100" in joined and "-40.0%" in joined


def test_rationale_names_each_filter_failure(client):
    body = client.get("/api/securities/105560.KS").json()

    assert any("FINANCIAL_SECTOR" in line for line in body["rationale"])
    assert body["passed_filters"] is False


def test_financials_are_empty_until_collected(client):
    body = client.get("/api/securities/005930.KS").json()

    assert body["financials"] == []


def test_financials_are_returned_when_present(client, db):
    with connect(db) as conn:
        Repository(conn).save_financials([
            Financials(ticker="005930.KS", fiscal_year=2025, currency="KRW",
                       revenue=300.0, operating_income=30.0,
                       total_equity=200.0, total_liabilities=60.0,
                       net_income=20.0, operating_cash_flow=40.0),
        ])

    body = client.get("/api/securities/005930.KS").json()

    assert len(body["financials"]) == 1
    row = body["financials"][0]
    assert row["fiscal_year"] == 2025
    assert row["revenue"] == 300.0
    assert row["operating_margin"] == pytest.approx(0.1)
    assert row["debt_ratio"] == pytest.approx(30.0)


def test_score_breakdown_is_null_until_scored(client):
    body = client.get("/api/securities/005930.KS").json()

    assert body["score_total"] is None
    assert body["score_detail"] is None


def test_unknown_ticker_is_404(client):
    response = client.get("/api/securities/NOPE.KS")

    assert response.status_code == 404
    assert "NOPE.KS" in response.json()["detail"]
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

Expected: FAIL — 404 (라우트가 없다)

- [ ] **Step 3: 응답 모델을 추가한다**

`src/stockscout/web/schemas.py` 맨 아래에 추가:

```python
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
```

- [ ] **Step 4: 라우트를 구현한다**

`src/stockscout/web/api.py`의 import에 `SecurityDetail`·`FinancialRow`를 더하고, `index()` 앞에 추가:

```python
    @app.get("/api/securities/{ticker}", response_model=SecurityDetail)
    def get_security(ticker: str) -> SecurityDetail:
        def build(repo: Repository) -> SecurityDetail:
            snap = repo.get_latest_snapshot()
            if snap is None:
                raise HTTPException(status_code=404, detail="분석 결과가 없습니다.")
            row = next(
                (r for r in repo.get_analysis_results(snap.snapshot_id)
                 if r["ticker"] == ticker),
                None,
            )
            if row is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"{ticker} 는 최신 스냅샷에 없습니다.",
                )
            master = repo.get_security(ticker)
            failures = json.loads(row["filter_failures"] or "[]")

            return SecurityDetail(
                ticker=ticker,
                name=master.name if master else ticker,
                exchange=master.exchange if master else None,
                is_financial=bool(master.is_financial) if master else False,
                currency=master.currency if master else "KRW",
                passed_filters=bool(row["passed_filters"]),
                filter_failures=failures,
                price_current=row["price_current"],
                avg_price_1y=row["avg_price_1y"],
                avg_price_3y=row["avg_price_3y"],
                avg_price_5y=row["avg_price_5y"],
                high_5y=row["high_5y"],
                drop_vs_avg_1y=row["drop_vs_avg_1y"],
                drop_vs_avg_3y=row["drop_vs_avg_3y"],
                drop_vs_avg_5y=row["drop_vs_avg_5y"],
                drop_vs_high=row["drop_vs_high"],
                score_total=row["score_total"],
                market_cap=row["market_cap"],
                per=row["per"],
                pbr=row["pbr"],
                in_watchlist=ticker in repo.watchlist_tickers(),
                rationale=_rationale(row, failures),
                score_detail=row["score_detail"],
                financials=[
                    FinancialRow(
                        fiscal_year=f.fiscal_year,
                        revenue=f.revenue,
                        operating_income=f.operating_income,
                        net_income=f.net_income,
                        operating_margin=f.operating_margin,
                        roe=f.roe,
                        debt_ratio=f.debt_ratio,
                        operating_cash_flow=f.operating_cash_flow,
                    )
                    for f in repo.get_financials(ticker)
                ],
            )

        return read(build)
```

`create_app` 밖, 모듈 수준에 헬퍼를 추가한다:

```python
def _rationale(row: dict, failures: list[str]) -> list[str]:
    """판정 근거를 실제 숫자가 담긴 문장으로 만든다 (설계서 §11.2).

    점수만 던지는 화면은 실제 투자 판단에 쓸 수 없다. 계산식을 그대로
    보여줘야 사용자가 검증할 수 있다.

    기호를 쓰지 않는다. 이 문자열은 웹으로 가지만 CLI로도 갈 수 있고,
    한국어 Windows 콘솔은 cp949라 체크표시 같은 문자를 출력하면 죽는다.
    """
    lines: list[str] = []

    if failures:
        lines.append(f"[제외] 하드필터 {len(failures)}건에 걸렸습니다: "
                     + ", ".join(failures))
    else:
        lines.append("[통과] 가격 기반 하드필터를 모두 통과했습니다 "
                     "(금융업 아님, 5년 이력 충족)")

    for years in (1, 3, 5):
        avg = row[f"avg_price_{years}y"]
        drop = row[f"drop_vs_avg_{years}y"]
        if avg is None or drop is None:
            lines.append(f"{years}년 평균 대비: 데이터 커버리지 미달로 계산하지 않음")
            continue
        lines.append(
            f"{years}년 평균 대비  {row['price_current']:,.0f}"
            f" / {avg:,.0f} - 1 = {drop * 100:.1f}%"
        )

    lines.append(
        f"5년 고점 대비  {row['price_current']:,.0f}"
        f" / {row['high_5y']:,.0f} - 1 = {row['drop_vs_high'] * 100:.1f}%"
    )

    if row["score_total"] is None:
        lines.append("우량주 점수: 재무 데이터가 없어 계산되지 않았습니다")

    return lines
```

- [ ] **Step 5: 테스트가 통과하는지 확인한다**

Expected: PASS — `7 passed`

- [ ] **Step 6: 실제 데이터로 확인한다**

```bash
PYTHONUTF8=1 C:/Users/USER/DEV/주식투자/.venv/Scripts/python.exe -c "
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from fastapi.testclient import TestClient
from stockscout.web.api import create_app
c = TestClient(create_app('data/stockscout.db'))
b = c.get('/api/securities/051900.KS').json()
print(b['name'], b['ticker'])
for line in b['rationale']:
    print(' ', line)
"
```

Expected: LG생활건강의 판정 근거가 실제 숫자로 출력된다 (`5년 평균 대비  295,000 / 500,559 - 1 = -41.1%` 등)

- [ ] **Step 7: 전체 스위트를 돌리고 커밋한다**

Expected: `321 passed, 2 skipped`

```bash
git add src/stockscout/web/ tests/test_web_detail.py
git commit -m "feat: 종목 상세 API와 판정 근거"
```

---

## Task 4: 차트 시계열 API

**Files:**
- Modify: `src/stockscout/web/schemas.py`
- Modify: `src/stockscout/web/api.py`
- Test: `tests/test_web_prices.py`

- [ ] **Step 1: 실패하는 테스트를 작성한다**

`tests/test_web_prices.py`:

```python
from datetime import date, timedelta

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


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "t.db"
    with connect(path) as conn:
        init_db(conn)
        repo = Repository(conn)
        repo.upsert_security(Security(
            ticker="005930.KS", market="KR", name="삼성전자",
            exchange="KOSPI", currency="KRW"))
        bars, d = [], date(2021, 9, 10)
        while d <= date(2026, 9, 10):
            bars.append(PriceBar(ticker="005930.KS", date=d, close=100.0))
            d += timedelta(days=7)     # 주 1봉이면 충분하다
        repo.save_price_bars(bars, source="pykrx")
        repo.save_snapshot(
            Snapshot(run_at="2026-09-11T02:00:00+00:00", as_of="2026-09-10",
                     config_hash="h1", config_json="{}"),
            [AnalysisResult(
                ticker="005930.KS", filters=FilterOutcome(failures=[]),
                stats=PriceStats(
                    price_current=100.0,
                    avg_price={1: 110.0, 3: 120.0, 5: 130.0},
                    high_5y=200.0,
                    drop_vs_avg={1: -0.09, 3: -0.17, 5: -0.23},
                    drop_vs_high=-0.5),
                valuation=Valuation())],
        )
    return path


@pytest.fixture
def client(db):
    return TestClient(create_app(db_path=db))


def test_returns_series_ascending_by_date(client):
    body = client.get("/api/securities/005930.KS/prices").json()

    dates = [p["date"] for p in body["series"]]
    assert dates == sorted(dates)
    assert len(dates) > 200


def test_series_points_carry_date_and_close(client):
    point = client.get("/api/securities/005930.KS/prices").json()["series"][0]

    assert point["date"] == "2021-09-10"
    assert point["close"] == 100.0


def test_reference_lines_come_from_the_snapshot(client):
    """차트의 평균선은 프론트가 다시 계산하지 않는다.

    같은 평균을 두 곳에서 계산하면 반올림이나 창 정의가 갈린다.
    analyze가 계산한 값을 그대로 쓴다.
    """
    body = client.get("/api/securities/005930.KS/prices").json()

    assert body["avg_price_1y"] == 110.0
    assert body["avg_price_3y"] == 120.0
    assert body["avg_price_5y"] == 130.0
    assert body["high_5y"] == 200.0
    assert body["price_current"] == 100.0


def test_years_parameter_narrows_the_window(client):
    five = client.get("/api/securities/005930.KS/prices").json()
    one = client.get("/api/securities/005930.KS/prices",
                     params={"years": 1}).json()

    assert len(one["series"]) < len(five["series"])
    assert one["series"][0]["date"] > five["series"][0]["date"]


def test_window_is_anchored_to_snapshot_as_of_not_today(client):
    """기준일은 스냅샷의 as_of다. 오늘로 잡으면 화면과 숫자가 어긋난다."""
    body = client.get("/api/securities/005930.KS/prices").json()

    assert body["as_of"] == "2026-09-10"
    assert body["series"][-1]["date"] <= "2026-09-10"


def test_unsupported_years_is_rejected(client):
    response = client.get("/api/securities/005930.KS/prices",
                          params={"years": 2})

    assert response.status_code == 422


def test_ticker_in_master_but_absent_from_snapshot_is_404(client, db):
    """이름 주의 — 이 테스트는 "주가 없음"이 아니라 "스냅샷에 없음" 분기를 탄다.

    EMPTY.KS 를 securities 에만 넣고 스냅샷에는 넣지 않으므로, 404가
    앞쪽 분기에서 난다. 진짜 no-bars 분기는 아래 테스트가 덮는다.
    """
    with connect(db) as conn:
        Repository(conn).upsert_security(Security(
            ticker="EMPTY.KS", market="KR", name="빈종목", currency="KRW"))

    response = client.get("/api/securities/EMPTY.KS/prices")

    assert response.status_code == 404


def test_unknown_ticker_is_404(client):
    assert client.get("/api/securities/NOPE.KS/prices").status_code == 404
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

Expected: FAIL — 404

- [ ] **Step 3: 응답 모델을 추가한다**

`src/stockscout/web/schemas.py` 맨 아래에 추가:

```python
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
```

- [ ] **Step 4: 라우트를 구현한다**

`src/stockscout/web/api.py`의 import에 `PricePoint`·`PriceSeries`를 더하고, `index()` 앞에 추가:

```python
    @app.get("/api/securities/{ticker}/prices", response_model=PriceSeries)
    def get_prices(
        ticker: str,
        years: ChartWindow = ChartWindow.FIVE,
    ) -> PriceSeries:
        def build(repo: Repository) -> PriceSeries:
            snap = repo.get_latest_snapshot()
            if snap is None:
                raise HTTPException(status_code=404, detail="분석 결과가 없습니다.")
            row = next(
                (r for r in repo.get_analysis_results(snap.snapshot_id)
                 if r["ticker"] == ticker),
                None,
            )
            if row is None:
                raise HTTPException(
                    status_code=404, detail=f"{ticker} 는 최신 스냅샷에 없습니다.")

            as_of = date.fromisoformat(snap.as_of) if snap.as_of else None
            start = _window_start(as_of, years) if as_of else None
            bars = repo.get_price_bars(ticker, start=start, end=as_of)
            if not bars:
                raise HTTPException(
                    status_code=404, detail=f"{ticker} 의 주가 데이터가 없습니다.")

            return PriceSeries(
                ticker=ticker,
                as_of=snap.as_of,
                series=[PricePoint(date=b.date.isoformat(), close=b.close)
                        for b in bars],
                price_current=row["price_current"],
                avg_price_1y=row["avg_price_1y"],
                avg_price_3y=row["avg_price_3y"],
                avg_price_5y=row["avg_price_5y"],
                high_5y=row["high_5y"],
            )

        return read(build)
```

모듈 상단 import에 추가:

```python
from datetime import date
from enum import IntEnum
```

`STATIC_DIR` 아래에 구간 열거형을 둔다:

```python
class ChartWindow(IntEnum):
    """차트에 그릴 구간(년). 스키마가 허용값을 스스로 문서화한다.

    Literal[1, 3, 5]를 쓰면 안 된다. 이 FastAPI/pydantic 조합에서
    쿼리스트링 "1"이 int 리터럴 1로 캐스팅되지 않아 1·3·5가 전부
    422로 거부된다(실측 확인). 정수 Literal만 그렇고, sort 파라미터의
    Literal["drop", "name", "ticker"]처럼 문자열 Literal은 정상이다.
    IntEnum은 정상 캐스팅되고 OpenAPI 스키마에도 허용값이 남는다.
    """

    ONE = 1
    THREE = 3
    FIVE = 5
```

`_rationale` 옆에 헬퍼를 추가한다:

```python
def _window_start(as_of: date, years: int) -> date:
    """as_of로부터 years년 전. 달력 기준이다.

    analysis.valuation.window_start와 같은 계산이지만 여기서 다시 쓴다.
    web/은 analysis/를 import하지 않는다 — 웹은 계산 계층이 아니라
    저장된 것을 보여주는 계층이고, 이 함수는 차트 구간을 자르기 위한
    표시용 계산이지 판정에 쓰이는 값이 아니다.
    """
    try:
        return as_of.replace(year=as_of.year - years)
    except ValueError:  # 2월 29일
        return as_of.replace(year=as_of.year - years, day=28)
```

- [ ] **Step 5: 테스트가 통과하는지 확인한다**

Expected: PASS — `8 passed`

- [ ] **Step 6: 실제 데이터로 확인한다**

```bash
C:/Users/USER/DEV/주식투자/.venv/Scripts/python.exe -c "
from fastapi.testclient import TestClient
from stockscout.web.api import create_app
c = TestClient(create_app('data/stockscout.db'))
b = c.get('/api/securities/005930.KS/prices').json()
print('points', len(b['series']), b['series'][0]['date'], '~', b['series'][-1]['date'])
print('avg5', b['avg_price_5y'], 'high5', b['high_5y'], 'current', b['price_current'])
"
```

Expected: `points 1222 2021-09-10 ~ 2026-09-10`, `avg5 91987.56... high5 362500.0 current 269000.0`

- [ ] **Step 7: 전체 스위트를 돌리고 커밋한다**

Expected: `329 passed, 2 skipped`

```bash
git add src/stockscout/web/ tests/test_web_prices.py
git commit -m "feat: 차트 시계열 API"
```

---

## Task 5: 관심종목 API

**Files:**
- Modify: `src/stockscout/store/repository.py`
- Modify: `src/stockscout/web/api.py`
- Test: `tests/test_web_watchlist.py`

- [ ] **Step 1: 실패하는 테스트를 작성한다**

`tests/test_web_watchlist.py`:

```python
import pytest
from fastapi.testclient import TestClient

from stockscout.models import Security
from stockscout.store.db import connect, init_db
from stockscout.store.repository import Repository
from stockscout.web.api import create_app


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "t.db"
    with connect(path) as conn:
        init_db(conn)
        repo = Repository(conn)
        for ticker, name in [("005930.KS", "삼성전자"), ("035720.KS", "카카오")]:
            repo.upsert_security(Security(
                ticker=ticker, market="KR", name=name, currency="KRW"))
    return path


@pytest.fixture
def client(db):
    return TestClient(create_app(db_path=db))


def test_watchlist_starts_empty(client):
    assert client.get("/api/watchlist").json() == []


def test_add_then_list(client):
    client.post("/api/watchlist/005930.KS")

    assert client.get("/api/watchlist").json() == ["005930.KS"]


def test_add_is_idempotent(client):
    client.post("/api/watchlist/005930.KS")
    client.post("/api/watchlist/005930.KS")

    assert client.get("/api/watchlist").json() == ["005930.KS"]


def test_delete_removes(client):
    client.post("/api/watchlist/005930.KS")
    client.delete("/api/watchlist/005930.KS")

    assert client.get("/api/watchlist").json() == []


def test_delete_missing_is_not_an_error(client):
    """이미 지워진 것을 또 지우는 것은 실패가 아니다."""
    response = client.delete("/api/watchlist/005930.KS")

    assert response.status_code == 204


def test_add_unknown_ticker_is_404(client):
    response = client.post("/api/watchlist/NOPE.KS")

    assert response.status_code == 404
    assert "NOPE.KS" in response.json()["detail"]


def test_list_is_sorted(client):
    client.post("/api/watchlist/035720.KS")
    client.post("/api/watchlist/005930.KS")

    assert client.get("/api/watchlist").json() == ["005930.KS", "035720.KS"]
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

Expected: FAIL — 404

- [ ] **Step 3: Repository에 쓰기를 추가한다**

`src/stockscout/store/repository.py`의 `watchlist_tickers` 뒤에 추가:

```python
    def add_to_watchlist(self, ticker: str, memo: str | None = None) -> None:
        """이미 있으면 아무것도 하지 않는다. 별 토글은 멱등해야 한다."""
        self.conn.execute(
            "INSERT INTO watchlist (ticker, added_at, memo) VALUES (?, ?, ?) "
            "ON CONFLICT(ticker) DO NOTHING",
            (ticker, _now(), memo),
        )
        self.conn.commit()

    def remove_from_watchlist(self, ticker: str) -> None:
        self.conn.execute("DELETE FROM watchlist WHERE ticker = ?", (ticker,))
        self.conn.commit()
```

- [ ] **Step 4: 라우트를 구현한다**

`src/stockscout/web/api.py`의 import에 추가:

```python
from fastapi import Response
```

`index()` 앞에 추가:

```python
    @app.get("/api/watchlist", response_model=list[str])
    def get_watchlist() -> list[str]:
        return read(lambda repo: sorted(repo.watchlist_tickers()))

    @app.post("/api/watchlist/{ticker}", status_code=204)
    def add_watchlist(ticker: str) -> Response:
        def run(repo: Repository) -> None:
            if repo.get_security(ticker) is None:
                raise HTTPException(
                    status_code=404, detail=f"{ticker} 는 종목 목록에 없습니다.")
            repo.add_to_watchlist(ticker)

        read(run)
        return Response(status_code=204)

    @app.delete("/api/watchlist/{ticker}", status_code=204)
    def remove_watchlist(ticker: str) -> Response:
        read(lambda repo: repo.remove_from_watchlist(ticker))
        return Response(status_code=204)
```

- [ ] **Step 5: 테스트가 통과하는지 확인하고 커밋한다**

Expected: PASS — `7 passed`, 전체 `336 passed, 2 skipped`

```bash
git add src/stockscout/web/api.py src/stockscout/store/repository.py tests/test_web_watchlist.py
git commit -m "feat: 관심종목 API"
```

---

## Task 6: 화면 — 레이아웃과 좌측 패널

**Files:**
- Rewrite: `src/stockscout/web/static/index.html`
- Write: `src/stockscout/web/static/style.css`
- Write: `src/stockscout/web/static/app.js`
- Test: `tests/test_web_static.py`

- [ ] **Step 1: 실패하는 테스트를 작성한다**

정적 파일은 브라우저에서 확인하지만, **깨진 채로 커밋되는 것은 막을 수 있다.**

`tests/test_web_static.py`:

```python
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stockscout.store.db import connect, init_db
from stockscout.web.api import STATIC_DIR, create_app


@pytest.fixture
def client(tmp_path):
    path = tmp_path / "t.db"
    with connect(path) as conn:
        init_db(conn)
    return TestClient(create_app(db_path=path))


def test_static_files_exist():
    for name in ("index.html", "style.css", "app.js"):
        assert (STATIC_DIR / name).exists(), f"{name} 이 없습니다"


def test_index_references_its_assets():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert "/static/style.css" in html
    assert "/static/app.js" in html


def test_assets_are_served(client):
    for path in ("/static/style.css", "/static/app.js"):
        assert client.get(path).status_code == 200, path


def test_chartjs_is_pinned_to_an_exact_version():
    """CDN 스크립트에 버전을 고정한다.

    latest를 쓰면 어느 날 갑자기 차트가 깨지고, 그때 원인을 찾기
    어렵다. 빌드 스텝이 없으므로 lockfile도 없다.
    """
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert "chart.js" in html.lower()
    assert "latest" not in html.lower()


def test_index_has_the_two_panels():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert 'id="list-panel"' in html
    assert 'id="detail-panel"' in html


def test_javascript_parses():
    """실제로 파싱해서 문법 오류를 잡는다.

    괄호 균형 세기 같은 대용품은 문자열 안의 괄호에 속고, 정작 빠진
    쉼표 같은 진짜 문법 오류는 못 잡는다. 이 프로젝트에는 빌드 스텝이
    없어서 문법 오류가 브라우저에서만 드러나므로, 여기서 막는다.

    node가 없는 환경에서는 skip한다 — 이 저장소는 node를 요구하지 않는다.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node 가 없어 JS 문법 검사를 건너뛴다")

    result = subprocess.run(
        [node, "--check", str(STATIC_DIR / "app.js")],
        capture_output=True, text=True,
    )

    assert result.returncode == 0, result.stderr
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

Expected: FAIL — `style.css`·`app.js` 가 비어 있어 `test_index_references_its_assets` 등이 실패

- [ ] **Step 3: `index.html`을 작성한다**

```html
<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>stockscout</title>
  <link rel="stylesheet" href="/static/style.css">
  <script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
</head>
<body>
  <header id="topbar">
    <span class="brand">stockscout</span>
    <span id="snapshot-info" class="muted">불러오는 중…</span>
  </header>

  <main id="layout">
    <section id="list-panel">
      <div id="controls">
        <input id="search" type="search" placeholder="종목명 또는 코드 검색" autocomplete="off">

        <div class="slider-row">
          <label for="drop-threshold">하락 임계치</label>
          <output id="drop-value">-40%</output>
        </div>
        <input id="drop-threshold" type="range" min="0" max="80" step="1" value="40">

        <div id="score-slider-block" class="disabled-block">
          <div class="slider-row">
            <label>우량주 점수 최소</label>
            <output id="score-value">—</output>
          </div>
          <input id="score-threshold" type="range" min="0" max="100" step="5" value="70" disabled>
          <p class="hint" id="score-hint"></p>
        </div>

        <p id="hit-count" class="hit"></p>

        <div id="chips"></div>
        <select id="sort">
          <option value="drop">하락률순</option>
          <option value="name">이름순</option>
          <option value="ticker">코드순</option>
        </select>
      </div>

      <ul id="stock-list"></ul>
    </section>

    <section id="detail-panel">
      <p class="placeholder">왼쪽에서 종목을 선택하세요.</p>
    </section>
  </main>

  <script src="/static/app.js"></script>
</body>
</html>
```

- [ ] **Step 4: `style.css`를 작성한다**

```css
:root {
  --bg: #ffffff;
  --panel: #f7fafc;
  --border: #e2e8f0;
  --text: #2d3748;
  --muted: #718096;
  --accent: #2b6cb0;
  --down: #c53030;
  --up: #2f855a;
}

* { box-sizing: border-box; }

body {
  margin: 0;
  font-family: "Malgun Gothic", "맑은 고딕", system-ui, sans-serif;
  font-size: 14px;
  color: var(--text);
  background: var(--bg);
}

#topbar {
  display: flex;
  align-items: baseline;
  gap: 16px;
  padding: 10px 16px;
  background: #2d3748;
  color: #e2e8f0;
}
#topbar .brand { font-weight: 700; letter-spacing: .02em; }
.muted { color: var(--muted); font-size: 12px; }
#topbar .muted { color: #a0aec0; }

#layout {
  display: flex;
  height: calc(100vh - 41px);
}

#list-panel {
  width: 380px;
  min-width: 320px;
  border-right: 1px solid var(--border);
  background: var(--panel);
  display: flex;
  flex-direction: column;
}

#controls { padding: 12px; border-bottom: 1px solid var(--border); }

#search, #sort {
  width: 100%;
  padding: 6px 8px;
  border: 1px solid var(--border);
  border-radius: 4px;
  font: inherit;
  background: #fff;
}

.slider-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin: 12px 0 4px;
  font-size: 12px;
  color: var(--muted);
}
.slider-row output { color: var(--accent); font-weight: 700; font-size: 13px; }

input[type=range] { width: 100%; }

.disabled-block { opacity: .55; }
.hint {
  margin: 4px 0 0;
  font-size: 11px;
  line-height: 1.5;
  color: var(--muted);
}

.hit {
  margin: 10px 0;
  padding: 6px 8px;
  background: #ebf8ff;
  border: 1px solid #bee3f8;
  border-radius: 4px;
  font-size: 12px;
  color: #2c5282;
  text-align: center;
}

#chips { display: flex; gap: 4px; flex-wrap: wrap; margin-bottom: 8px; }
.chip {
  padding: 3px 10px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: #fff;
  font-size: 12px;
  cursor: pointer;
}
.chip.on { background: #2d3748; color: #fff; border-color: #2d3748; }
.chip[disabled] { opacity: .4; cursor: not-allowed; }

#stock-list { flex: 1; overflow-y: auto; margin: 0; padding: 0; list-style: none; }
#stock-list li {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 12px;
  border-bottom: 1px solid var(--border);
  background: #fff;
  cursor: pointer;
}
#stock-list li:hover { background: #edf2f7; }
#stock-list li.selected { background: var(--accent); color: #fff; }
#stock-list li.excluded { opacity: .5; }
#stock-list .nm { flex: 1; font-weight: 600; }
#stock-list .dp { font-variant-numeric: tabular-nums; }
#stock-list .star { width: 16px; text-align: center; color: #d69e2e; }
.neg { color: var(--down); }
.pos { color: var(--up); }
#stock-list li.selected .neg,
#stock-list li.selected .pos { color: #fff; }

#detail-panel { flex: 1; overflow-y: auto; padding: 16px 20px; }
.placeholder { color: var(--muted); margin-top: 40px; text-align: center; }
```

- [ ] **Step 5: `app.js`의 좌측 패널 부분을 작성한다**

```javascript
"use strict";

const state = {
  rows: [],
  snapshot: null,
  selected: null,
  chip: "all",
  dropThreshold: -0.40,
  chart: null,
};

const $ = (id) => document.getElementById(id);
const pct = (v) => (v === null || v === undefined) ? "—" : (v * 100).toFixed(1) + "%";
const won = (v) => (v === null || v === undefined) ? "—" : v.toLocaleString("ko-KR", { maximumFractionDigits: 0 });
const signClass = (v) => (v === null || v === undefined) ? "" : (v < 0 ? "neg" : "pos");

// 슬라이더 값은 다음 방문에 복원한다. 설정 파일 기본값은 건드리지 않는다 (설계서 §11.1).
function loadPrefs() {
  try {
    const saved = localStorage.getItem("stockscout.dropThreshold");
    if (saved !== null) state.dropThreshold = parseFloat(saved);
  } catch (e) { /* 시크릿 모드 등에서 막힐 수 있다 */ }
}

function savePrefs() {
  try {
    localStorage.setItem("stockscout.dropThreshold", String(state.dropThreshold));
  } catch (e) { /* 저장 실패는 기능을 막지 않는다 */ }
}

async function getJSON(url) {
  const res = await fetch(url);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `${res.status} ${url}`);
  }
  return res.json();
}

// --- 판정 ------------------------------------------------------------
//
// 임계치는 지표를 바꾸지 않고 등급만 나눈다. 그래서 서버 왕복 없이
// 여기서 즉시 재판정한다 (설계서 §6.1).
//
// 점수가 없는 동안 ★★/★ 등급은 매기지 않는다. 가격만으로 등급을
// 매기면 품질을 검증한 것처럼 보이는 거짓말이 된다 (설계서 §9.3).

function hits(row) {
  const d = row.drop_vs_avg_5y;
  return d !== null && d <= state.dropThreshold;
}

function visibleRows() {
  let rows = state.rows;
  if (state.chip === "passed") rows = rows.filter((r) => r.passed_filters);
  else if (state.chip === "excluded") rows = rows.filter((r) => !r.passed_filters);
  else if (state.chip === "watch") rows = rows.filter((r) => r.in_watchlist);
  else if (state.chip === "hit") rows = rows.filter((r) => r.passed_filters && hits(r));
  return rows;
}

// --- 렌더 ------------------------------------------------------------

function renderSnapshotInfo() {
  const s = state.snapshot;
  const parts = [`기준일 ${s.as_of ?? "?"}`, `${s.total}종목`,
                 `통과 ${s.passed}`, `제외 ${s.excluded}`];
  if (s.note) parts.push(s.note);
  $("snapshot-info").textContent = parts.join(" · ");
}

function renderScoreBlock() {
  const hasScores = state.snapshot.has_scores;
  $("score-threshold").disabled = !hasScores;
  $("score-slider-block").classList.toggle("disabled-block", !hasScores);
  $("score-value").textContent = hasScores ? "70점" : "—";
  $("score-hint").textContent = hasScores
    ? ""
    : "재무 데이터가 없어 우량주 점수를 계산하지 못했습니다. "
      + "DART 인증키를 넣고 collect · normalize · analyze 를 실행하면 켜집니다.";
}

function renderChips() {
  const hasScores = state.snapshot.has_scores;
  const defs = [
    ["all", "전체", false],
    ["hit", `임계치 이하`, false],
    ["passed", "통과", false],
    ["excluded", "제외", false],
    ["watch", "관심", false],
  ];
  $("chips").innerHTML = "";
  for (const [key, label, needsScores] of defs) {
    const b = document.createElement("button");
    b.className = "chip" + (state.chip === key ? " on" : "");
    b.textContent = label;
    if (needsScores && !hasScores) b.disabled = true;
    else b.onclick = () => { state.chip = key; renderChips(); renderList(); };
    $("chips").appendChild(b);
  }
}

function renderHitCount() {
  const passing = state.rows.filter((r) => r.passed_filters);
  const n = passing.filter(hits).length;
  $("hit-count").textContent =
    `현재 기준 -> ${n}종목 해당 / 통과 ${passing.length}종목`;
}

function renderList() {
  const ul = $("stock-list");
  ul.innerHTML = "";
  for (const row of visibleRows()) {
    const li = document.createElement("li");
    li.className = (row.ticker === state.selected ? "selected " : "")
      + (row.passed_filters ? "" : "excluded");
    li.onclick = () => selectTicker(row.ticker);

    const star = document.createElement("span");
    star.className = "star";
    star.textContent = row.in_watchlist ? "★" : "☆";
    star.title = "관심종목";
    star.onclick = (e) => { e.stopPropagation(); toggleWatch(row); };

    const nm = document.createElement("span");
    nm.className = "nm";
    nm.textContent = row.name;

    const dp = document.createElement("span");
    dp.className = "dp " + signClass(row.drop_vs_avg_5y);
    dp.textContent = pct(row.drop_vs_avg_5y);

    li.append(star, nm, dp);
    ul.appendChild(li);
  }
  if (!ul.children.length) {
    const li = document.createElement("li");
    li.textContent = "해당하는 종목이 없습니다.";
    li.style.color = "var(--muted)";
    li.style.cursor = "default";
    ul.appendChild(li);
  }
}

async function toggleWatch(row) {
  const method = row.in_watchlist ? "DELETE" : "POST";
  await fetch(`/api/watchlist/${encodeURIComponent(row.ticker)}`, { method });
  row.in_watchlist = !row.in_watchlist;
  renderList();
}

// --- 초기화 ----------------------------------------------------------

function wireControls() {
  const slider = $("drop-threshold");
  slider.value = String(Math.round(-state.dropThreshold * 100));
  $("drop-value").textContent = `-${slider.value}%`;

  slider.oninput = () => {
    state.dropThreshold = -Number(slider.value) / 100;
    $("drop-value").textContent = `-${slider.value}%`;
    savePrefs();
    renderHitCount();
    if (state.chip === "hit") renderList();
  };

  $("search").oninput = async () => { await reload(); };
  $("sort").onchange = async () => { await reload(); };
}

async function reload() {
  const q = $("search").value.trim();
  const sort = $("sort").value;
  const params = new URLSearchParams({ sort });
  if (q) params.set("q", q);
  state.rows = await getJSON(`/api/securities?${params}`);
  renderHitCount();
  renderList();
}

async function init() {
  loadPrefs();
  try {
    state.snapshot = await getJSON("/api/snapshot");
  } catch (err) {
    document.body.innerHTML =
      `<p style="padding:40px;text-align:center;color:#718096">${err.message}</p>`;
    return;
  }
  renderSnapshotInfo();
  renderScoreBlock();
  renderChips();
  wireControls();
  await reload();
}

init();
```

- [ ] **Step 6: 테스트가 통과하는지 확인한다**

Run: `C:/Users/USER/DEV/주식투자/.venv/Scripts/python.exe -m pytest tests/test_web_static.py -v`
Expected: PASS — `6 passed`. 전체 스위트는 `342 passed, 2 skipped`

- [ ] **Step 7: 브라우저에서 확인한다**

```bash
C:/Users/USER/DEV/주식투자/.venv/Scripts/stockscout.exe serve
```

브라우저에서 http://127.0.0.1:8000 을 연다. 확인할 것:

- 상단에 `기준일 2026-09-10 · 42종목 · 통과 34 · 제외 8`
- 좌측 목록에 42종목, 하락률순으로 LG생활건강이 맨 위
- 하락 임계치 슬라이더를 움직이면 `현재 기준 -> N종목 해당` 이 **즉시** 바뀐다 (네트워크 탭에 요청이 없어야 한다)
- 우량주 점수 슬라이더는 비활성이고, 왜 비활성인지 안내가 보인다
- 검색창에 `카카오` 입력 시 1종목만 남는다
- 별(☆)을 누르면 채워지고, 새로고침해도 유지된다

**슬라이더를 움직일 때 네트워크 요청이 발생하면 §6.1 위반이다.** 확인하고 보고한다.

- [ ] **Step 8: 커밋한다**

```bash
git add src/stockscout/web/static/ tests/test_web_static.py
git commit -m "feat: 화면 레이아웃과 좌측 패널"
```

---

## Task 7: 화면 — 우측 상세 패널

**Files:**
- Modify: `src/stockscout/web/static/app.js`
- Modify: `src/stockscout/web/static/style.css`
- Test: `tests/test_web_static.py`

- [ ] **Step 1: 실패하는 테스트를 추가한다**

`tests/test_web_static.py` 맨 아래에 추가:

```python
def test_app_js_renders_the_detail_panel():
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    for needed in ("renderDetail", "drawChart", "rationale", "financials"):
        assert needed in js, f"{needed} 이 app.js 에 없습니다"


def test_app_js_explains_missing_financials_with_commands():
    """빈 패널은 '준비 중'이 아니라 사용자가 할 수 있는 일을 말한다."""
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "opendart.fss.or.kr" in js
    assert "stockscout collect" in js
    assert "준비 중" not in js


def test_app_js_uses_server_computed_reference_lines():
    """차트 기준선을 프론트에서 다시 계산하지 않는다.

    같은 평균을 두 곳에서 계산하면 창 정의나 반올림이 갈려
    차트와 판정 근거가 어긋난다.
    """
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "avg_price_5y" in js
    assert "reduce" not in js.split("drawChart")[1].split("function")[0]
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

Expected: FAIL — `renderDetail` 이 없다

- [ ] **Step 3: CSS를 보강한다**

`style.css` 맨 아래에 추가:

```css
.detail-header {
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 14px;
}
.detail-header h2 { margin: 0; font-size: 19px; }
.detail-header .code { color: var(--muted); font-size: 13px; }
.detail-header .star { cursor: pointer; color: #d69e2e; font-size: 17px; }

.badge {
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 11px;
  font-weight: 700;
}
.badge.pass { background: #c6f6d5; color: #22543d; }
.badge.fail { background: #fed7d7; color: #822727; }

#chart-box { position: relative; height: 260px; margin-bottom: 16px; }

.kpis { display: flex; gap: 8px; margin-bottom: 18px; }
.kpi {
  flex: 1;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 9px;
  text-align: center;
  background: #fff;
}
.kpi b { display: block; font-size: 18px; line-height: 1.25; }
.kpi span { font-size: 11px; color: var(--muted); }
.kpi.empty b { color: var(--muted); font-weight: 400; }

.section-title {
  font-size: 12px;
  font-weight: 700;
  color: var(--muted);
  letter-spacing: .04em;
  margin: 18px 0 7px;
}

.rationale {
  background: var(--panel);
  border-left: 3px solid var(--accent);
  border-radius: 0 4px 4px 0;
  padding: 10px 12px;
  font-size: 12.5px;
  line-height: 1.85;
  font-variant-numeric: tabular-nums;
}
.rationale div:first-child { font-weight: 700; }

.empty-state {
  border: 1px dashed var(--border);
  border-radius: 6px;
  padding: 16px;
  background: var(--panel);
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--muted);
}
.empty-state b { color: var(--text); display: block; margin-bottom: 6px; }
.empty-state pre {
  margin: 8px 0 0;
  padding: 8px 10px;
  background: #2d3748;
  color: #9ae6b4;
  border-radius: 4px;
  font-size: 11.5px;
  overflow-x: auto;
}

table.fin { width: 100%; border-collapse: collapse; font-size: 12px; }
table.fin th, table.fin td {
  padding: 5px 7px;
  text-align: right;
  border-bottom: 1px solid var(--border);
  font-variant-numeric: tabular-nums;
}
table.fin th { background: var(--panel); color: var(--muted); font-weight: 600; }
table.fin th:first-child, table.fin td:first-child { text-align: left; }
```

- [ ] **Step 4: `app.js`에 우측 패널을 추가한다**

`app.js`의 `// --- 초기화 ---` 블록 **앞에** 추가:

```javascript
// --- 우측 상세 패널 --------------------------------------------------

const DART_GUIDE = `<div class="empty-state">
  <b>재무 데이터가 없습니다</b>
  DART 공시에서 재무제표를 수집하면 우량주 점수와 재무 추이가 여기에
  표시됩니다. 지금은 주가 기준 하락률만 계산됩니다.
  <pre>1. https://opendart.fss.or.kr 에서 인증키 발급
2. config/.env 의 DART_API_KEY= 뒤에 붙여넣기
3. stockscout collect --market KR
   stockscout normalize --market KR
   stockscout analyze --market KR</pre>
</div>`;

async function selectTicker(ticker) {
  state.selected = ticker;
  renderList();
  const panel = $("detail-panel");
  panel.innerHTML = `<p class="placeholder">불러오는 중…</p>`;
  try {
    const [detail, prices] = await Promise.all([
      getJSON(`/api/securities/${encodeURIComponent(ticker)}`),
      getJSON(`/api/securities/${encodeURIComponent(ticker)}/prices`),
    ]);
    renderDetail(detail, prices);
  } catch (err) {
    panel.innerHTML = `<p class="placeholder">${err.message}</p>`;
  }
}

function renderDetail(d, prices) {
  const panel = $("detail-panel");
  panel.innerHTML = "";

  // 헤더
  const header = document.createElement("div");
  header.className = "detail-header";
  header.innerHTML = `
    <h2>${d.name}</h2>
    <span class="code">${d.ticker} · ${d.exchange ?? ""}</span>
    <span class="badge ${d.passed_filters ? "pass" : "fail"}">
      ${d.passed_filters ? "통과" : "제외"}
    </span>
    <span style="flex:1"></span>
    <span class="star" id="detail-star">${d.in_watchlist ? "★" : "☆"}</span>`;
  panel.appendChild(header);
  header.querySelector("#detail-star").onclick = async () => {
    const row = state.rows.find((r) => r.ticker === d.ticker);
    if (row) { await toggleWatch(row); d.in_watchlist = row.in_watchlist; }
    header.querySelector("#detail-star").textContent = d.in_watchlist ? "★" : "☆";
  };

  // 차트
  const box = document.createElement("div");
  box.id = "chart-box";
  box.innerHTML = `<canvas id="price-chart"></canvas>`;
  panel.appendChild(box);

  // KPI
  const kpis = document.createElement("div");
  kpis.className = "kpis";
  kpis.innerHTML = [
    kpi(won(d.price_current), "현재가"),
    kpi(pct(d.drop_vs_avg_5y), "5년평균 대비", signClass(d.drop_vs_avg_5y)),
    kpi(pct(d.drop_vs_high), "5년고점 대비", signClass(d.drop_vs_high)),
    d.score_total === null
      ? kpi("—", "우량주 점수", "", true)
      : kpi(d.score_total.toFixed(0), "우량주 점수"),
  ].join("");
  panel.appendChild(kpis);

  // 판정 근거
  panel.appendChild(sectionTitle("판정 근거"));
  const rat = document.createElement("div");
  rat.className = "rationale";
  rat.innerHTML = d.rationale.map((line) => `<div>${escapeHtml(line)}</div>`).join("");
  panel.appendChild(rat);

  // 재무 추이
  panel.appendChild(sectionTitle("재무 추이"));
  if (!d.financials.length) {
    const box2 = document.createElement("div");
    box2.innerHTML = DART_GUIDE;
    panel.appendChild(box2);
  } else {
    panel.appendChild(financialsTable(d.financials));
  }

  drawChart(prices);
}

function kpi(value, label, cls = "", empty = false) {
  return `<div class="kpi${empty ? " empty" : ""}">
    <b class="${cls}">${value}</b><span>${label}</span></div>`;
}

function sectionTitle(text) {
  const el = document.createElement("div");
  el.className = "section-title";
  el.textContent = text;
  return el;
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function financialsTable(rows) {
  const table = document.createElement("table");
  table.className = "fin";
  const years = rows.map((r) => r.fiscal_year);
  const line = (label, pick) =>
    `<tr><td>${label}</td>` + rows.map((r) => `<td>${pick(r)}</td>`).join("") + "</tr>";

  table.innerHTML =
    `<tr><th>항목</th>${years.map((y) => `<th>${y}</th>`).join("")}</tr>` +
    line("매출", (r) => won(r.revenue)) +
    line("영업이익", (r) => won(r.operating_income)) +
    line("영업이익률", (r) => pct(r.operating_margin)) +
    line("ROE", (r) => pct(r.roe)) +
    line("부채비율", (r) => r.debt_ratio === null ? "—" : r.debt_ratio.toFixed(1) + "%") +
    line("영업현금흐름", (r) => won(r.operating_cash_flow));
  return table;
}

function drawChart(p) {
  if (state.chart) state.chart.destroy();

  // 기준선은 서버가 준 값을 그대로 쓴다. 여기서 다시 평균을 내면
  // 창 정의나 반올림이 갈려 차트와 판정 근거가 어긋난다.
  const refs = [
    ["5년 고점", p.high_5y, "#e53e3e"],
    ["5년 평균", p.avg_price_5y, "#805ad5"],
    ["3년 평균", p.avg_price_3y, "#38a169"],
    ["1년 평균", p.avg_price_1y, "#dd6b20"],
  ].filter(([, v]) => v !== null && v !== undefined);

  const labels = p.series.map((x) => x.date);
  const datasets = [{
    label: "종가",
    data: p.series.map((x) => x.close),
    borderColor: "#2b6cb0",
    borderWidth: 1.4,
    pointRadius: 0,
    tension: 0,
  }];
  for (const [label, value, color] of refs) {
    datasets.push({
      label: `${label} ${won(value)}`,
      data: labels.map(() => value),
      borderColor: color,
      borderWidth: 1,
      borderDash: [5, 4],
      pointRadius: 0,
    });
  }

  state.chart = new Chart($("price-chart"), {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { labels: { boxWidth: 12, font: { size: 11 } } } },
      scales: {
        x: { ticks: { maxTicksLimit: 8, font: { size: 10 } }, grid: { display: false } },
        y: { ticks: { font: { size: 10 }, callback: (v) => won(v) } },
      },
    },
  });
}
```

- [ ] **Step 5: 테스트가 통과하는지 확인한다**

Expected: PASS — `9 passed`. 전체 스위트는 `345 passed, 2 skipped`

- [ ] **Step 6: 브라우저에서 확인한다**

`stockscout serve` 후 확인할 것:

- LG생활건강을 클릭하면 우측에 차트가 그려지고, 5년 평균선(500,559)이 현재가(295,000) 위로 한참 떨어져 보인다
- 판정 근거에 `5년 평균 대비  295,000 / 500,559 - 1 = -41.1%` 가 실제 숫자로 나온다
- 우량주 점수 KPI가 `—` 이고 회색이다
- 재무 추이 자리에 DART 안내가 나오고, 명령이 복사 가능한 형태로 보인다
- KB금융을 클릭하면 헤더에 `제외` 뱃지가 뜨고 판정 근거 첫 줄이 `[제외] 하드필터 1건에 걸렸습니다: FINANCIAL_SECTOR`
- 종목을 여러 번 바꿔도 차트가 겹치지 않는다 (`chart.destroy()` 확인)

**스크린샷을 찍어 보고한다.**

- [ ] **Step 7: 커밋한다**

```bash
git add src/stockscout/web/static/ tests/test_web_static.py
git commit -m "feat: 우측 상세 패널과 주가 차트"
```

---

## Task 8: 실제 브라우저 관통 검증

**Files:**
- Create: `tests/test_web_integration.py`

- [ ] **Step 1: 실제 DB에 대한 통합 테스트를 작성한다**

`tests/test_web_integration.py`:

```python
"""실제 DB에 대한 웹 API 검증.

DB가 없거나 스냅샷이 없으면 모듈 전체가 skip된다.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stockscout.store.db import DEFAULT_DB_PATH
from stockscout.web.api import create_app

pytestmark = pytest.mark.skipif(
    not Path(DEFAULT_DB_PATH).exists(),
    reason="stockscout analyze 를 먼저 실행해야 한다",
)


@pytest.fixture
def client():
    return TestClient(create_app(DEFAULT_DB_PATH))


@pytest.fixture
def snapshot(client):
    res = client.get("/api/snapshot")
    if res.status_code == 404:
        pytest.skip("스냅샷이 없다")
    return res.json()


def test_snapshot_matches_cli_counts(snapshot):
    assert snapshot["total"] == 42
    assert snapshot["passed"] == 34
    assert snapshot["excluded"] == 8


def test_scores_are_absent_until_dart_key(snapshot):
    assert snapshot["has_scores"] is False
    assert snapshot["has_valuation"] is False


def test_list_returns_every_security(client, snapshot):
    rows = client.get("/api/securities").json()

    assert len(rows) == snapshot["total"]


def test_list_is_sorted_most_fallen_first(client, snapshot):
    rows = client.get("/api/securities", params={"sort": "drop"}).json()
    drops = [r["drop_vs_avg_5y"] for r in rows if r["drop_vs_avg_5y"] is not None]

    assert drops == sorted(drops)


def test_every_row_can_be_fetched_in_detail(client, snapshot):
    """목록에 있는 종목은 전부 상세를 열 수 있어야 한다."""
    for row in client.get("/api/securities").json():
        res = client.get(f"/api/securities/{row['ticker']}")
        assert res.status_code == 200, row["ticker"]


def test_every_passing_row_has_a_chart(client, snapshot):
    for row in client.get("/api/securities").json():
        if not row["passed_filters"]:
            continue
        body = client.get(f"/api/securities/{row['ticker']}/prices").json()
        assert len(body["series"]) > 1000, row["ticker"]


def test_chart_reference_lines_match_the_list_row(client, snapshot):
    """차트 기준선과 목록 지표가 같은 스냅샷에서 나와야 한다."""
    row = client.get("/api/securities").json()[0]
    chart = client.get(f"/api/securities/{row['ticker']}/prices").json()

    assert chart["avg_price_5y"] == row["avg_price_5y"]
    assert chart["high_5y"] == row["high_5y"]
    assert chart["price_current"] == row["price_current"]


def test_rationale_contains_the_actual_numbers(client, snapshot):
    body = client.get("/api/securities/051900.KS").json()
    joined = " ".join(body["rationale"])

    assert "295,000" in joined
    assert "-41.1%" in joined


def test_excluded_row_names_its_filter(client, snapshot):
    body = client.get("/api/securities/105560.KS").json()

    assert body["passed_filters"] is False
    assert any("FINANCIAL_SECTOR" in line for line in body["rationale"])


def test_financials_are_empty_everywhere(client, snapshot):
    for row in client.get("/api/securities").json()[:5]:
        body = client.get(f"/api/securities/{row['ticker']}").json()
        assert body["financials"] == []


def test_watchlist_round_trips(client, snapshot):
    client.delete("/api/watchlist/005930.KS")
    assert "005930.KS" not in client.get("/api/watchlist").json()

    client.post("/api/watchlist/005930.KS")
    assert "005930.KS" in client.get("/api/watchlist").json()

    rows = {r["ticker"]: r for r in client.get("/api/securities").json()}
    assert rows["005930.KS"]["in_watchlist"] is True

    client.delete("/api/watchlist/005930.KS")
```

- [ ] **Step 2: 통합 테스트를 실행한다**

Expected: PASS — `11 passed`

- [ ] **Step 3: 브라우저에서 전체 흐름을 확인한다**

`stockscout serve` 를 띄우고 다음을 순서대로 확인한다.

1. 상단 정보가 `기준일 2026-09-10 · 42종목 · 통과 34 · 제외 8`
2. 슬라이더를 -40%에서 -20%로 움직인다 → 해당 종목 수가 즉시 바뀌고 **네트워크 요청이 없다**
3. `임계치 이하` 칩을 누르면 3종목만 남는다
4. LG생활건강 선택 → 차트에 평균선 3개와 고점선이 그려진다
5. 판정 근거에 실제 계산식이 나온다
6. 재무 추이 자리에 DART 안내가 나온다
7. KB금융 선택 → `제외` 뱃지와 필터 사유
8. 별을 누르고 브라우저를 새로고침 → 유지된다
9. 검색창에 `삼성` 입력 → 삼성전자·삼성SDI·삼성전기·삼성물산·삼성생명·삼성화재·삼성바이오로직스·삼성에스디에스가 남는다

**스크린샷을 찍어 보고한다.**

- [ ] **Step 4: 전체 스위트를 돌리고 커밋한다**

Expected: `356 passed, 2 skipped`

```bash
git add tests/test_web_integration.py
git commit -m "test: 웹 API 실제 데이터 관통 검증"
```

---

## 완료 기준

- [ ] `stockscout serve` 로 브라우저에서 42종목을 보고 고를 수 있다
- [ ] 하락 임계치 슬라이더가 **서버 왕복 없이** 즉시 반영된다
- [ ] 우량주 점수 슬라이더와 등급 칩은 비활성이고, 왜 그런지와 무엇을 하면 켜지는지를 화면이 말한다
- [ ] 종목을 고르면 주가 차트에 1·3·5년 평균선과 5년 고점선이 그려진다
- [ ] 판정 근거가 실제 계산식으로 나온다 (`295,000 / 500,559 - 1 = -41.1%`)
- [ ] 재무 추이 자리에 DART 인증키 발급과 실행 명령이 안내된다
- [ ] 관심종목이 저장되고 새로고침 후에도 유지된다
- [ ] `web/`이 `analysis/`·`sources/`·`pipeline/`을 import하지 않는다
- [ ] `pytest tests/` 전체 통과, 통합 테스트를 뺀 전부가 DB 없이 실행된다

## 다음 계획

**계획 C — 우량주 판정과 등급** (설계서 §8 전체·§9.3). **DART 키가 있어야 한다.**

계획 C가 채우면 이 화면에서 자동으로 살아나는 것들:

- 우량주 점수 슬라이더와 ★★/★ 등급 칩 (`has_scores` 가 true가 된다)
- 우량주 점수 KPI
- 재무 추이 5년표 (`financials` 가 채워진다)
- 판정 근거의 점수 산출 부분

**설계서 §11.2의 「점수 분해」 4축 막대는 계획 C에서 추가한다.** 점수 구조(`score_detail`의 형태)가 계획 C에서 정해지므로, 그것을 먼저 만들고 화면을 붙이는 편이 맞다.
