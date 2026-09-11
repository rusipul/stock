# 우량주 낙폭 스크리너 — 설계 문서

작성일: 2026-09-10

## 1. 목적

한국·미국 상장 우량주 중 **주가가 장기 평균 대비 크게 빠진 종목**을 찾아내는 로컬 웹 도구.

두 축으로 판정한다.

1. **질(質)** — DART·SEC 공시 재무제표로 우량주 여부를 가린다
2. **가격** — 최근 1·3·5년 평균 주가 및 고점 대비 현재가의 하락률을 잰다

두 조건을 모두 만족하는 종목을 등급으로 나눠 제시하고, **왜 그렇게 판정했는지 계산식까지 노출**한다.

### 설계 원칙

- **판정 근거를 숨기지 않는다.** 점수만 던지는 화면은 실제 투자 판단에 쓸 수 없다. 모든 수치는 원본 재무 항목까지 역추적 가능해야 한다.
- **기준은 코드가 아니라 설정이다.** 임계치·배점·필터는 전부 `settings.yaml`에 있고, 바꾸려고 코드를 고칠 일이 없어야 한다.
- **수집과 분석을 분리한다.** 기준을 바꿀 때 API를 다시 긁지 않는다.

## 2. 범위

### v1에 포함

- 유니버스: KOSPI 200 + S&P 500 (약 700종목)
- 연간 재무제표 5개년 수집·정규화
- 일별 수정주가 5년치 수집
- 하드필터 + 4축 100점 스코어링
- 1·3·5년 평균 대비 / 5년 고점 대비 하락률
- 2단계 신호 등급 (★★ 강한 / ★ 관찰)
- 로컬 웹 UI (좌: 종목 선택 / 우: 상세 리포트)
- 관심종목 담기
- CLI (`universe` / `collect` / `analyze` / `serve` / `run`)

### v1에서 제외

| 제외 항목 | 사유 |
|---|---|
| 금융업 (은행·보험·증권) | 부채비율·유동비율이 구조적으로 달라 같은 잣대가 무의미. 전용 필터셋을 별도로 설계해야 한다 |
| 분기 재무제표 | 연간만으로 우량주 판정에 충분. 수집량이 4배로 늘어난다 |
| 실시간 시세·장중 데이터 | 일봉 종가 기준으로 충분 |
| 백테스팅 | 지수 편입 이력 데이터가 없으면 생존편향으로 결과가 왜곡된다 |
| 알림 (텔레그램·이메일) | 기준이 확정되기 전에 자동화하면 오탐만 계속 받게 된다 |
| 환율 변환 | 미국 종목은 USD 기준으로 계산·표시. 환율을 끼우면 하락률이 왜곡된다 |
| 자동 매매·주문 | 범위 밖 |

### 알려진 한계

- **생존편향**: 현재 지수에 편입된 종목만 본다. 과거 편출 종목은 유니버스에 없다. v1은 백테스팅을 하지 않으므로 실질적 문제는 없으나, 나중에 백테스팅을 붙일 때는 편입 이력 데이터가 먼저 필요하다.
- **무료 소스 의존**: pykrx·yfinance는 비공식 경로다. 깨질 수 있다. 이에 대비해 소스 계층을 인터페이스로 격리했다(§5.2).

## 3. 용어

| 용어 | 정의 |
|---|---|
| 유니버스 | 분석 대상 종목 집합 |
| 하드필터 | 통과 못 하면 즉시 제외되는 결격 조건. 점수를 계산하지 않는다 |
| 우량주 점수 | 하드필터 통과 종목에 매기는 0~100점 |
| 평균 대비 하락률 | `현재가 ÷ N년 평균 종가 − 1` |
| 고점 대비 하락률 | `현재가 ÷ 5년 최고 종가 − 1` |
| 스냅샷 | 특정 시점 설정으로 돌린 분석 결과 1세트 |

## 4. 아키텍처

5개 계층. 각 계층은 바로 아래 계층의 **결과물만** 받는다. 위 계층이 아래 계층의 내부를 알지 못하므로 한 계층을 통째로 갈아끼워도 나머지는 그대로다.

```
① 수집   sources/     DART · pykrx · SEC EDGAR · yfinance
              ↓  [PriceSource / FundamentalSource 인터페이스]
② 정규화 normalize/   한국 계정과목 · US-GAAP 태그 → 공통 Financials 스키마
              ↓
③ 분석   analysis/    하드필터 + 스코어링 / 하락률 / 신호 등급
              ↓
④ 저장   store/       SQLite (원본 · 정규화 · 스냅샷 · 관심종목)
              ↓
⑤ 표현   web/         FastAPI + 정적 프론트 (읽기 전용)
```

### 4.1 디렉터리 구조

```
주식투자/
├── config/
│   ├── settings.yaml          기준·배점·임계치 (§10)
│   ├── universe.yaml          지수 구성종목 정의
│   └── .env                   DART_API_KEY, SEC_USER_AGENT
├── data/
│   └── stockscout.db          SQLite 단일 파일
├── src/stockscout/
│   ├── sources/
│   │   ├── base.py            PriceSource / FundamentalSource 프로토콜
│   │   ├── dart.py            OpenDART 재무제표
│   │   ├── krx.py             pykrx 주가
│   │   ├── edgar.py           SEC EDGAR XBRL 재무제표
│   │   ├── yahoo.py           yfinance 주가
│   │   └── ratelimit.py       소스별 토큰버킷
│   ├── normalize/
│   │   ├── schema.py          Financials (pydantic)
│   │   ├── kr_mapping.py      DART 계정과목 매핑
│   │   └── us_mapping.py      US-GAAP 태그 매핑
│   ├── analysis/
│   │   ├── filters.py         하드필터
│   │   ├── scoring.py         4축 스코어링
│   │   ├── valuation.py       평균·고점 대비 하락률
│   │   └── signal.py          등급 판정
│   ├── store/
│   │   ├── schema.sql         DDL
│   │   └── repository.py      읽기·쓰기
│   ├── pipeline.py            배치 오케스트레이션
│   ├── web/
│   │   ├── api.py             FastAPI 라우트
│   │   └── static/            index.html · app.js · style.css
│   └── cli.py
├── tests/
│   └── fixtures/              실제 API 응답 저장본
└── docs/superpowers/specs/
```

### 4.2 기술 스택

| 영역 | 선택 | 사유 |
|---|---|---|
| 언어 | Python 3.11+ | 금융 데이터 라이브러리 생태계 |
| 웹 | FastAPI + uvicorn | 타입 기반 자동 검증, 가벼움 |
| DB | SQLite (stdlib `sqlite3`) | 단일 파일, 서버 불필요. ORM 없이 얇은 repository 레이어 |
| 계산 | pandas | 시계열 평균·CAGR |
| 스키마 | pydantic | 정규화 결과 검증 |
| 프론트 | 정적 HTML + vanilla JS + Chart.js (CDN) | **빌드 스텝 없음.** npm·번들러 없이 파일을 바로 고칠 수 있다 |
| 테스트 | pytest | |

프론트에 React를 쓰지 않는 이유: 이 도구는 화면이 하나이고 상태가 단순하다. 빌드 파이프라인을 들이면 얻는 것보다 유지비가 크다.

## 5. 데이터 소스

### 5.1 소스 매트릭스

| 시장 | 재무 | 주가 |
|---|---|---|
| 한국 | **OpenDART** — 무료 API 키, 일 20,000건. 단일회사 전체 재무제표 | **pykrx** — KRX 공개데이터, 수정주가 |
| 미국 | **SEC EDGAR XBRL** — `data.sec.gov/api/xbrl/companyfacts`. 키 불필요, User-Agent 헤더 필수, 10 req/s | **yfinance** — 수정종가 |

> 구현 착수 시 각 소스의 현행 엔드포인트·정책을 먼저 확인한다. 무료 소스는 사양이 바뀔 수 있다.

#### 쓰는 DART 엔드포인트 (공식 가이드로 확인함)

네 엔드포인트가 **파라미터를 공유한다**(`crtfc_key`·`corp_code`·`bsns_year`·`reprt_code`). 수집 코드를 한 번 만들면 나머지는 경로와 응답 파싱만 바꾸면 된다.

| 용도 | 경로 | 그룹 | 상태 |
|---|---|---|---|
| 고유번호 매핑 | `corpCode.xml` | DS001 | 구현됨 (`fetch_corp_codes`) |
| 재무제표 | `fnlttSinglAcntAll.json` (+ `fs_div`) | DS003 | 구현됨 (`fetch_annual`) |
| 발행주식수 | `stockTotqySttus.json` | DS002 | §9.2 — 미구현 |
| 감사의견 | `accnutAdtorNmNdAdtOpinion.json` | DS002 | §8.1 — 미구현 |

`reprt_code`는 `11011`(사업보고서)·`11012`(반기)·`11013`(1분기)·`11014`(3분기)이고, `fs_div`는 `CFS`(연결)·`OFS`(별도)다. `bsns_year`는 2015년 이후만 제공된다.

**응답 status 코드는 7개다.** 초기 구현이 세 개만 알고 있어서, 잘못된 인증키(`010`)가 종목별 오류로 처리돼 42종목 전부에 같은 메시지가 쌓였다.

| 코드 | 의미 | 처리 |
|---|---|---|
| `000` | 정상 | 페이로드 반환 |
| `010` | 미등록 키 | **배치 중단** — 다음 종목도 똑같이 실패한다 |
| `013` | 조회된 데이터 없음 | 해당 연도만 건너뜀 (정상 상황) |
| `020` | 요청 제한 초과 | **배치 중단** |
| `100` | 필드 값 오류 | **배치 중단** — 요청 자체가 잘못됐다 |
| `800` | 서비스 점검 중 | 재시도 |
| `900` | 정의되지 않은 오류 | 종목 단위 실패 |

### 5.2 교체 지점 — 인터페이스 2개

모든 소스는 아래 두 프로토콜만 구현한다. 나중에 유료 API로 옮기거나 pykrx가 깨졌을 때, **파일 하나를 추가하고 설정에서 소스 이름만 바꾸면 된다.** 정규화 이상 계층은 손대지 않는다.

```python
class PriceSource(Protocol):
    def fetch_daily(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        """반환: index=date, columns=[close, volume]. close는 수정종가."""

class FundamentalSource(Protocol):
    def fetch_annual(self, ticker: str, years: int) -> list[RawStatement]:
        """반환: 소스 원형을 담은 RawStatement. 정규화하지 않는다."""
```

`RawStatement`는 `{ticker, fiscal_year, source, payload: dict}` 형태로, 소스 응답을 가공 없이 담는다. 정규화는 ② 계층의 책임이다.

### 5.3 수집 정책

- **증분 수집**: 이미 있는 `(ticker, fiscal_year)`·`(ticker, date)`는 건너뛴다. `--force`로 강제 재수집.
- **종목 단위 격리**: 한 종목이 실패해도 배치를 멈추지 않는다. 실패는 `collect_errors`에 기록하고 배치 종료 시 요약 출력.
- **재시도**: 네트워크 오류에 한해 3회 지수 백오프(1s → 2s → 4s). 404·인증 오류는 재시도하지 않는다.
- **레이트리밋**: 소스별 토큰버킷. SEC는 10 req/s, DART는 일 20,000건 카운터를 로컬에 유지한다.

## 6. 데이터 모델

```sql
-- 종목 마스터
CREATE TABLE securities (
  ticker        TEXT PRIMARY KEY,   -- '005930.KS' | 'AAPL'
  market        TEXT NOT NULL,      -- 'KR' | 'US'
  name          TEXT NOT NULL,
  name_en       TEXT,
  exchange      TEXT,               -- 'KOSPI' | 'NYSE' | 'NASDAQ'
  sector        TEXT,
  is_financial  INTEGER NOT NULL DEFAULT 0,
  source_id     TEXT,               -- DART corp_code | SEC CIK
  currency      TEXT NOT NULL,      -- 'KRW' | 'USD'
  listed_at     TEXT,
  in_universe   INTEGER NOT NULL DEFAULT 1,
  updated_at    TEXT NOT NULL
);

-- ① 원본 재무 (수집 그대로 보존)
CREATE TABLE raw_financials (
  ticker      TEXT NOT NULL,
  fiscal_year INTEGER NOT NULL,
  source      TEXT NOT NULL,        -- 'dart' | 'edgar'
  payload     TEXT NOT NULL,        -- JSON 원본
  fetched_at  TEXT NOT NULL,
  PRIMARY KEY (ticker, fiscal_year, source)
);

-- ① 원본 주가 (수정종가)
CREATE TABLE raw_prices (
  ticker TEXT NOT NULL,
  date   TEXT NOT NULL,
  close  REAL NOT NULL,        -- 수정종가
  volume INTEGER,
  source TEXT NOT NULL,        -- 'pykrx' | 'yfinance' (추적용, 키 아님)
  PRIMARY KEY (ticker, date)
);

-- ② 정규화 재무 (한·미 공통 스키마)
CREATE TABLE financials (
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
  missing_fields      TEXT,          -- JSON 배열: 매핑 실패 항목
  PRIMARY KEY (ticker, fiscal_year)
);

-- ③ 분석 스냅샷
CREATE TABLE snapshots (
  snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_at      TEXT NOT NULL,
  config_hash TEXT NOT NULL,
  config_json TEXT NOT NULL,
  note        TEXT
);

CREATE TABLE analysis_results (
  snapshot_id     INTEGER NOT NULL,
  ticker          TEXT NOT NULL,
  passed_filters  INTEGER NOT NULL,
  filter_failures TEXT,              -- JSON 배열
  score_total     REAL,              -- 0~100 (환산 후)
  score_profit    REAL,
  score_growth    REAL,
  score_stability REAL,
  score_cashflow  REAL,
  score_detail    TEXT,              -- JSON: 지표별 원값·획득점수·유효만점
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
  signal_grade    TEXT,              -- 'STRONG' | 'WATCH' | 'NONE'
  signal_reason   TEXT,              -- JSON
  PRIMARY KEY (snapshot_id, ticker)
);

-- 관심종목
CREATE TABLE watchlist (
  ticker   TEXT PRIMARY KEY,
  added_at TEXT NOT NULL,
  memo     TEXT
);

-- 수집 실패 기록
CREATE TABLE collect_errors (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  ticker     TEXT NOT NULL,
  source     TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  message    TEXT NOT NULL
);
```

### 6.1 슬라이더가 재계산을 요구하지 않는 이유

`score_total`, `drop_vs_*` 는 **임계치와 무관하게** 계산된다. 임계치는 이 값들을 **등급으로 나눌 때만** 쓰인다.

따라서 API는 지표를 내려주고, 프론트가 슬라이더 값으로 즉석에서 등급을 다시 매긴다. 서버 왕복도, 재계산도 없다. DB의 `signal_grade`는 설정 파일 기본값 기준의 등급이다.

## 7. 정규화

### 7.1 공통 스키마

`financials` 테이블 컬럼과 동일한 pydantic 모델. 한국·미국 데이터가 이 한 형태로 수렴한 뒤에는 분석 계층이 시장을 구분하지 않는다.

### 7.2 매핑

- **한국 (DART)**: 계정과목명 기반 매핑. 표준 계정과목이 있으나 회사별 표기 편차가 있어, 매핑 테이블에 동의어 목록을 둔다. (예: 매출액 ← `매출액`, `수익(매출액)`, `영업수익`)
- **미국 (SEC XBRL)**: US-GAAP 태그 기반. 태그도 회사별로 갈린다. (예: 매출 ← `Revenues`, `RevenueFromContractWithCustomerExcludingAssessedTax`, `SalesRevenueNet`) 우선순위 목록으로 첫 매치를 취한다.

### 7.3 매핑 실패 처리

| 구분 | 항목 | 실패 시 |
|---|---|---|
| **필수** | revenue, operating_income, total_equity, total_liabilities | 하드필터 `DATA_INSUFFICIENT`로 제외 |
| **선택** | interest_expense, capex, current_assets, current_liabilities, dividend_per_share | NULL 저장. 해당 지표를 점수 계산에서 빼고 **유효만점을 줄여 환산**(§8.3) |

실패 항목은 `financials.missing_fields`에 남겨 화면에서 확인할 수 있게 한다.

## 8. 우량주 판정

### 8.1 하드필터

하나라도 걸리면 제외하고 점수를 계산하지 않는다. 모든 판정은 **최근 연도** 기준이며, 연속 조건은 명시된 기간을 본다.

| ID | 조건 | 기준 |
|---|---|---|
| `CAPITAL_IMPAIRED` | 자본잠식 | `total_equity <= 0` |
| `CONSECUTIVE_LOSS` | 연속 영업적자 | 최근 3년 모두 `operating_income < 0` |
| `HIGH_DEBT` | 과다 부채 | `total_liabilities / total_equity * 100 > 200` |
| `INTEREST_UNCOVERED` | 이자 미충당 | 최근 2년 모두 `operating_income / interest_expense < 1`. `interest_expense`가 NULL이거나 0이면 통과 |
| `REVENUE_COLLAPSE` | 매출 급감 | `revenue[최근] / revenue[3년전] - 1 <= -0.5` |
| `INSUFFICIENT_HISTORY` | 이력 부족 | 재무 5개년 미만 또는 주가 5년 미만 |
| `DATA_INSUFFICIENT` | 필수 항목 결측 | §7.3 |
| `FINANCIAL_SECTOR` | 금융업 | `is_financial = 1` |
| `ADVERSE_AUDIT_OPINION` | 감사의견 비적정 | 최근 사업연도 `adt_opinion`이 적정이 아님 |

#### 감사의견 필터에 관하여 — 앞선 판단을 정정한다

이 문서의 초안은 감사의견 필터를 "데이터 확보 난이도가 있어 v1에서는 옵션"이라며 뺐다. **그 판단의 근거가 틀렸다.** DART가 전용 API를 제공한다.

```
GET /api/accnutAdtorNmNdAdtOpinion.json
    crtfc_key · corp_code · bsns_year · reprt_code
  → adtor        감사인명
    adt_opinion  감사의견
```

파라미터가 재무제표 API(`fnlttSinglAcntAll.json`)와 동일하므로 수집 코드를 거의 그대로 재사용할 수 있다. 종목당 호출이 5회(5개년) 늘어난다.

감사의견 비적정은 우량주 판정에서 가장 강한 결격 신호 중 하나다. 회계법인이 재무제표를 믿을 수 없다고 말한 것이므로, 그 재무제표로 계산한 점수도 믿을 수 없다. 자본잠식이나 연속 적자보다 앞서는 신호다.

**판정 기준:** `adt_opinion`이 「적정」이 아니면 제외한다. 문서가 값 목록을 명시하지 않으므로, 실제 응답을 수집한 뒤 어떤 문자열들이 오는지 확인하고 매칭 규칙을 정한다(§7.2의 계정과목 매핑과 같은 방식).

### 8.2 4축 스코어링

통과 종목만. 각 축 25점, 합계 100점.

**수익성 (25)**

| 지표 | 배점 | 구간 |
|---|---|---|
| ROE 3년 평균 | 10 | ≥15% → 10 / ≥10% → 7 / ≥5% → 4 / ≥0% → 1 / 그 외 0 |
| 영업이익률 3년 평균 | 10 | ≥15% → 10 / ≥10% → 7 / ≥5% → 4 / ≥0% → 1 / 그 외 0 |
| 영업이익률 추세 | 5 | 최근 3년 영업이익률의 선형회귀 기울기(연당 %p)로 판정. `> +0.5%p/년` → 5 / `−0.5 ~ +0.5%p/년`(보합) → 2 / `< −0.5%p/년` → 0 |

**성장성 (25)**

| 지표 | 배점 | 구간 |
|---|---|---|
| 매출 CAGR 3년 | 10 | ≥15% → 10 / ≥10% → 8 / ≥5% → 5 / ≥0% → 2 / 그 외 0 |
| 영업이익 CAGR 3년 | 10 | 동일 구간. 기저(3년 전)가 음수면 CAGR이 정의되지 않으므로 **0점 처리하고 `score_detail`에 `BASE_NEGATIVE` 기록** |
| 성장 일관성 | 5 | 최근 4년 중 매출이 전년 대비 증가한 해의 수 × 1.25 |

**안정성 (25)**

| 지표 | 배점 | 구간 |
|---|---|---|
| 부채비율 | 10 | ≤50% → 10 / ≤100% → 7 / ≤150% → 4 / ≤200% → 1 |
| 유동비율 | 5 | ≥200% → 5 / ≥150% → 3.5 / ≥100% → 2 / 그 외 0 |
| 이자보상배율 | 10 | ≥10배 → 10 / ≥5배 → 7 / ≥3배 → 4 / ≥1배 → 2 / 그 외 0. 이자비용 0이면 10 |

**현금흐름 (25)**

| 지표 | 배점 | 구간 |
|---|---|---|
| OCF 흑자 연속년수 | 10 | 5년 → 10 / 4년 → 8 / 3년 → 5 / 2년 → 3 / 그 외 0 |
| OCF ÷ 영업이익 (3년 누계) | 10 | ≥1.2 → 10 / ≥1.0 → 8 / ≥0.8 → 5 / ≥0.5 → 2 / 그 외 0. **3년 누계 영업이익이 0 이하이면 비율이 무의미하므로 0점 처리**하고 `score_detail`에 `DENOMINATOR_NONPOSITIVE` 기록 |
| FCF(= OCF − capex) 흑자 연속년수 | 5 | ≥3년 → 5 / 2년 → 3 / 1년 → 1 / 0 → 0 |

**배당은 점수에 넣지 않는다.** 배당을 안 주는 우량 성장주가 부당하게 깎이기 때문이다. `dividend_per_share`는 참고 지표로 화면에만 표시한다.

### 8.3 결측 지표 환산

선택 항목 결측으로 계산할 수 없는 지표는 **획득점수와 만점 양쪽에서 뺀다.**

```
score_total = (획득점수 합계 / 유효만점 합계) × 100
```

예: capex 결측으로 FCF 지표(5점)를 못 구한 종목은 유효만점 95점이 되고, 78점을 얻었다면 `78 / 95 × 100 = 82.1점`이다. 0점으로 처리하면 데이터 결측이 사업 부실로 둔갑한다.

`score_detail`에 지표별 `{원값, 획득점수, 만점, 상태}`를 남겨 화면에서 근거를 보여준다.

#### 축 단위 하한 — 환산이 축을 통째로 지우지 못하게 한다

위 환산 규칙에는 하한이 필요하다. 그대로 두면 **한 축이 통째로 사라진 기업이 오히려 후하게 채점된다.**

영업현금흐름을 못 구한 기업을 보자. `operating_cash_flow`가 없으면 현금흐름 축 25점 중 OCF 흑자 연속년수(10점)와 OCF÷영업이익(10점)이 모두 계산되지 않는다. 환산 규칙대로면 그 20점이 **분모에서도 빠져** 80점 만점으로 채점된다. 즉 "이 회사의 현금흐름을 알 수 없다"가 감점이 아니라 **면제**가 된다. 우량주를 가리는 도구에서 방향이 뒤집힌 동작이고, §8.1이 이자보상배율 미달을 아예 결격으로 삼은 것과도 어긋난다.

§8.3의 원래 의도는 **한두 지표**가 빠질 때 데이터 공백을 사업 부실로 둔갑시키지 않는 것이었다. 축 전체가 빠지는 경우까지 감싸주려던 것이 아니다.

그래서 규칙을 하나 더 둔다.

> **한 축의 유효만점이 그 축 배점의 절반 미만이면, 환산하지 않고 그 축을 0점 처리한다.** `score_detail`의 해당 축에 `AXIS_UNMEASURABLE`을 기록한다.

현금흐름 축 25점 중 5점만 측정 가능한 기업은 80점 만점으로 후하게 채점되는 대신 그 축에서 0점을 받는다. "볼 수 없다"가 면제가 아니라 감점이 된다.

**필수/선택 분류는 바꾸지 않는다.** §7.3의 필수 목록은 4개(`revenue`·`operating_income`·`total_equity`·`total_liabilities`)이며, `operating_cash_flow`를 필수로 올리는 방식은 쓰지 않는다. 그러면 계정과목 매핑이 그 항목에서 실패한 기업이 점수 계산조차 되지 않고 통째로 제외되는데, DART 계정과목 매핑이 회사마다 갈리는 상황에서 그 영향 범위를 예측할 수 없다. 문제는 분류가 아니라 환산이므로 환산에서 막는다.

## 9. 하락 신호와 등급

### 9.1 계산

- **평균 종가**: 해당 창(1·3·5년)의 일별 **수정종가 산술평균**
- **고점**: 5년 내 최고 수정종가
- **데이터 요건**: 해당 창 거래일의 80% 이상 데이터가 있어야 유효. 미달 시 `NULL` (등급 판정에서 제외)
- **수정주가 필수**: 액면분할·병합·유상증자로 인한 단절을 제거해야 한다. pykrx·yfinance 모두 수정종가를 제공한다
- **통화**: 미국 종목은 USD 기준으로 계산·표시. 환율 변환 없음

```
drop_vs_avg_Ny = 현재가 ÷ avg_price_Ny − 1
drop_vs_high   = 현재가 ÷ high_5y − 1
```

### 9.2 밸류에이션 지표

화면 표시와 정렬에만 쓴다. **점수에는 반영하지 않는다.**

```
market_cap = 현재가 × shares_outstanding[최근연도]
EPS        = net_income[최근연도] ÷ shares_outstanding[최근연도]
BPS        = total_equity[최근연도] ÷ shares_outstanding[최근연도]
PER        = 현재가 ÷ EPS      # EPS ≤ 0 이면 NULL
PBR        = 현재가 ÷ BPS      # BPS ≤ 0 이면 NULL (자본잠식은 이미 하드필터에서 제외됨)
```

`shares_outstanding`이 결측이면 세 값 모두 NULL이며, 화면에는 `—`로 표시한다. 시가총액순 정렬 시 NULL은 항상 뒤로 보낸다.

#### 발행주식수는 별도 API에서 온다 — 앞선 기록을 정정한다

계획 A는 "DART 전체 재무제표 API에 발행주식수가 없으므로 세 지표를 포기하거나 다른 소스를 찾아야 한다"고 기록했고, 계획 B는 그에 따라 세 지표를 전부 NULL로 두었다. **전용 API가 있다.**

```
GET /api/stockTotqySttus.json
    crtfc_key · corp_code · bsns_year · reprt_code      (재무제표 API와 동일)
  → istc_totqy      발행주식의 총수
    distb_stock_co  유통주식수
    tesstk_co       자기주식수
```

파라미터가 같으므로 수집 코드를 재사용할 수 있다. 종목당 호출이 5회 늘어난다.

**어느 주식수를 쓸지는 결정이 필요하다.**

- **시가총액** — 관례적으로 발행주식총수(`istc_totqy`)를 쓴다. 지수 산출도 상장주식수 기준이다
- **EPS·BPS** — 자기주식은 배당도 의결권도 없으므로 유통주식수(`distb_stock_co`)가 더 정확하다. 자기주식 비중이 큰 기업(예: 자사주 매입을 많이 한 회사)에서 차이가 유의미해진다

두 값을 모두 저장하고 용도별로 쓰는 것을 권한다. `Financials`에 `shares_outstanding`(발행) 외에 `shares_treasury`(자기주식)를 두면 유통주식수는 뺄셈으로 얻는다.

### 9.3 등급 판정

기준 창은 설정값(`primary_window`, 기본 5년)을 쓴다.

```
quality_pass  = passed_filters AND score_total >= min_score
avg_hit       = drop_vs_avg[primary_window] <= drop_vs_avg_threshold
high_hit      = drop_vs_high                <= drop_vs_high_threshold

STRONG (★★) = quality_pass AND avg_hit AND high_hit
WATCH  (★)  = quality_pass AND (avg_hit OR high_hit) AND NOT (avg_hit AND high_hit)
NONE   (—)  = 그 외
```

### 9.4 두 임계치의 관계 — 유의사항

고점은 정의상 평균보다 크거나 같다. 따라서 항상 `drop_vs_high <= drop_vs_avg` 다.

**두 임계치를 같은 값으로 두면 `avg_hit`은 반드시 `high_hit`을 함의한다.** 즉 등급이 다음과 같이 포개진다.

- ★★ = 평균 대비까지 뚫린 종목 (더 극단적)
- ★ = 고점 대비만 뚫린 종목

이는 오류가 아니라 자연스러운 위계이며 그대로 유용하다. 다만 두 지표를 **독립적인 두 관문**으로 쓰고 싶다면 고점 임계치를 더 엄격하게 잡아야 한다(예: 평균 −40%, 고점 −55%). 그래서 두 임계치를 **각각 따로** 설정할 수 있게 한다.

### 9.5 기대 신호 빈도

"5년 평균 대비 −40%"는 드물게 걸린다. 고점이 아니라 *평균* 대비이기 때문이다. 5년 평균 7만원인 종목은 4.2만원까지 빠져야 한다 — 시장 전체가 무너졌거나 그 기업에 심각한 악재가 터진 상황이다. 우량주 필터까지 통과한 종목 중 그런 게 남을 확률은 낮다.

**첫 실행에서 ★★가 0건이어도 정상이다.** 이 때문에 UI에 슬라이더와 실시간 종목 수 표시를 넣어(§11.1) 사용자가 자기 기준을 직접 잡을 수 있게 한다.

## 10. 설정 파일

`config/settings.yaml`. 임계치·배점·필터 기준은 전부 여기 있다.

```yaml
universe:
  markets: [KR, US]
  kr_index: KOSPI200
  us_index: SP500
  exclude_financials: true

lookback:
  price_years: [1, 3, 5]
  primary_window: 5           # 등급 판정에 쓸 창
  financial_years: 5
  min_data_coverage: 0.8      # 해당 창 거래일의 80% 이상 필요

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
    revenue_cagr3:   {points: 10, bands: [[0.15, 10], [0.10, 8], [0.05, 5], [0.0, 2]]}
    op_income_cagr3: {points: 10, bands: [[0.15, 10], [0.10, 8], [0.05, 5], [0.0, 2]]}
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

비밀값은 `config/.env`에 둔다 (`DART_API_KEY`, `SEC_USER_AGENT`). `.gitignore` 대상이다.

## 11. 웹 UI

좌우 분할 단일 화면. 왼쪽에서 종목을 고르면 오른쪽에 그 종목 리포트가 뜬다.

### 11.1 좌측 패널 — 종목 선택

1. **기준 슬라이더**
   - 우량주 점수 최소 (기본 70)
   - 하락 임계치 (기본 −40%) — **기본 상태에서는 슬라이더 하나가 평균·고점 두 임계치를 함께 움직인다.** 「고급」을 펼치면 §9.4대로 두 임계치를 따로 조정할 수 있다
   - 바로 아래 `현재 기준 → ★★ N종목 · ★ M종목 / 700` 실시간 표시
   - **서버 왕복 없이 즉시 반영**된다(§6.1)
   - 슬라이더 값은 `localStorage`에 저장해 다음 방문에 복원한다. 설정 파일 기본값은 건드리지 않는다
2. **등급 필터 칩** — ★★ / ★ / ☆관심 / 전체
3. **정렬** — 점수순 / 하락률순 / 시가총액순
4. **검색창** — 종목명·티커
5. **종목 리스트** — 등급 뱃지 · 종목명 · 점수 · 평균대비 · ☆관심 토글

### 11.2 우측 패널 — 상세 리포트

1. **헤더** — 종목명 · 코드 · 거래소 · 등급 뱃지 · ☆관심
2. **주가 차트** — 5년 일봉 위에 1·3·5년 평균선과 5년 고점선을 겹쳐 그린다. 현재 위치가 한눈에 보인다
3. **KPI 4장** — 우량주 점수 / 평균대비 / 고점대비 / PER
4. **점수 분해** — 4축 막대 그래프. 어느 축에서 깎였는지 즉시 보인다. 막대 클릭 시 지표별 상세 펼침
5. **재무 추이 5년표** — 매출 · 영업이익 · 영업이익률 · ROE · 부채비율 · OCF
6. **판정 근거** — 하드필터 통과 내역, 점수 산출 근거, 신호 판정식을 **실제 숫자로** 노출
   ```
   ✅ 평균 대비  41,300 ÷ 71,400 − 1 = −42.2%  ≤ −40%
   ✅ 고점 대비  41,300 ÷ 88,800 − 1 = −53.5%  ≤ −40%
   ```

### 11.3 API

| 메서드 | 경로 | 용도 |
|---|---|---|
| GET | `/api/snapshot` | 최신 스냅샷 메타 (수집일시, 종목 수, 설정) |
| GET | `/api/securities` | 리스트 + 지표. `?market=&q=&sort=` |
| GET | `/api/securities/{ticker}` | 상세 (재무추이 · 점수분해 · 판정근거) |
| GET | `/api/securities/{ticker}/prices?years=5` | 차트용 시계열 + 평균선 · 고점 |
| GET/POST/DELETE | `/api/watchlist[/{ticker}]` | 관심종목 |
| POST | `/api/refresh` | 수집+분석 백그라운드 실행 |
| GET | `/api/refresh/status` | 진행률 |

등급 필터를 쿼리에 두지 않는 이유: 등급은 프론트가 슬라이더로 즉석 판정한다(§6.1).

## 12. CLI

```bash
python -m stockscout universe sync              # 지수 구성종목 목록 갱신
python -m stockscout collect [--market KR|US] [--tickers ...] [--force]
python -m stockscout analyze [--config settings.yaml] [--note "임계치 -35% 실험"]
python -m stockscout serve [--port 8000]
python -m stockscout run                        # universe → collect → analyze 일괄
```

`analyze`는 API를 호출하지 않는다. 저장된 원본만 읽으므로 700종목이 몇 초에 끝난다. **기준을 바꿔가며 반복 실행하는 것이 이 도구의 주 사용 패턴이다.**

## 13. 오류 처리와 데이터 품질

| 상황 | 처리 |
|---|---|
| 소스 네트워크 오류 | 3회 지수 백오프 재시도. 실패 시 `collect_errors` 기록 후 다음 종목으로 |
| 404 · 인증 오류 | 재시도하지 않고 즉시 기록 |
| 레이트리밋 도달 | 소스별 토큰버킷으로 사전 차단. DART 일일 한도 초과 시 배치 중단하고 남은 종목 목록 출력 |
| 필수 계정과목 결측 | 하드필터 `DATA_INSUFFICIENT`로 제외 |
| 선택 계정과목 결측 | 유효만점 환산(§8.3), `missing_fields` 기록 |
| 주가 데이터 커버리지 미달 | 해당 창 지표를 NULL, 등급 판정에서 제외 |
| 배치 중 예외 | 스냅샷을 커밋하지 않는다. 직전 스냅샷이 유지되므로 화면은 계속 동작한다 |

배치 종료 시 요약을 출력한다: 성공 N · 실패 M · 필터 제외 K · 결측 환산 J.

## 14. 테스트 전략

계층 경계가 명확하므로 각 계층을 독립적으로 검증한다.

| 대상 | 방식 |
|---|---|
| 소스 어댑터 | `tests/fixtures/`에 저장한 **실제 API 응답 JSON**으로 테스트. 네트워크 없이 동작 |
| 정규화 | 삼성전자·애플 실제 응답 픽스처 → 기대 `Financials`. 계정과목 동의어 분기를 모두 덮는다 |
| 하드필터 | 조건별 합성 데이터. 경계값 포함 (부채비율 정확히 200%는 통과) |
| 스코어링 | 구간 경계값 테스트 (ROE 정확히 15.0%가 10점인지). 결측 환산 로직 별도 |
| 하락률 | 알려진 가격 시리즈 → 기대 평균·하락률. 커버리지 미달 케이스 포함 |
| 신호 등급 | `(quality_pass, avg_hit, high_hit)` 8조합 전수 테이블 테스트 |
| 파이프라인 | 5종목 픽스처로 end-to-end → 스냅샷 골든 비교 |
| API | FastAPI TestClient로 각 엔드포인트 스키마 검증 |

## 15. 구현 순서

틀을 먼저 세우고 하나씩 채운다. 각 단계는 그 자체로 동작해야 한다.

| 단계 | 내용 | 완료 기준 |
|---|---|---|
| 1 | 뼈대 — 디렉터리, 설정 로더, SQLite 스키마, CLI 골격 | `stockscout --help`가 뜨고 빈 DB가 생성된다 |
| 2 | 한국 수집 — DART + pykrx, 유니버스 sync | 삼성전자 5개년 재무와 5년 주가가 DB에 들어간다 |
| 3 | 정규화 — 한국 매핑 | KOSPI 200 전체가 `financials`로 들어가고 결측률이 집계된다 |
| 4 | 분석 — 하드필터 + 스코어링 + 하락률 + 등급 | `analyze`가 스냅샷을 만들고 콘솔에 상위 종목을 출력한다 |
| 5 | 웹 — API + 좌우분할 UI | 브라우저에서 한국 종목 선별·리포트 확인이 된다 |
| 6 | 미국 수집·정규화 — SEC EDGAR + yfinance | S&P 500이 같은 화면에 합류한다 |
| 7 | 다듬기 — 관심종목, 슬라이더 실시간 판정, 근거 표시 | 설계서의 화면이 완성된다 |

1~5단계까지는 한국만 다룬다. 파이프라인 전체를 한 시장으로 먼저 관통시켜 구조를 검증한 뒤, 6단계에서 미국을 붙인다. 이때 ②계층 위쪽은 손대지 않아야 한다 — **그것이 이 설계가 맞았는지에 대한 검증이다.**

## 16. 향후 확장

- 금융업 전용 필터셋 (BIS비율·NIM·대손충당금커버리지)
- 분기 재무제표
- 텔레그램·이메일 알림 + 스케줄러
- 유료 데이터 소스 어댑터 (§5.2 인터페이스만 구현)
- 지수 편입 이력 확보 후 백테스팅
- 섹터 상대평가 (같은 섹터 내 백분위)
- 관심종목 메모·매수 기록
