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
| `INTEREST_UNCOVERED` | 이자 미충당 | 최근 2년 모두 `operating_income / interest_expense < 1`. `interest_expense`가 NULL·0이거나 **대체값(§8.1.1)이면 통과** |
| `REVENUE_COLLAPSE` | 매출 급감 | `revenue[최근] / revenue[3년전] - 1 <= -0.5` |
| `INSUFFICIENT_HISTORY` | 이력 부족 | 재무 5개년 미만 또는 주가 5년 미만 |
| `DATA_INSUFFICIENT` | 필수 항목 결측 | §7.3 |
| `FINANCIAL_SECTOR` | 금융업 | `is_financial = 1` |
| `ADVERSE_AUDIT_OPINION` | 감사의견 비적정 | 최근 사업연도 `adt_opinion`이 적정이 아님 |

#### 8.1.1 대체값으로 잰 이자보상배율은 하드필터에 쓰지 않는다

`interest_expense`를 금융원가(`ifrs-full_FinanceCosts`)로 대신 채운 해에는
`INTEREST_UNCOVERED`를 **적용하지 않는다.** 스코어링(§8.2)에서도 이자보상배율
지표를 `MISSING`으로 처리해 유효만점에서 뺀다.

**§17.3이 "금융원가는 보수적인 방향이라 안전하다"고 적은 것을 여기서 좁힌다.**
그 판단은 **점수**를 염두에 둔 것이었다. 점수에서 대체값은 몇 점을 깎을 뿐이고
등급으로 완충된다. 하드필터는 다르다 — 점수를 계산하기도 전에 종목을 지운다.
3~10배 부풀려진 입력으로 내리는 이진 판정은 보수적인 것이 아니라 틀린 것이다.

실측이 그것을 보여준다. 42종목에 하드필터를 돌린 결과 `INTEREST_UNCOVERED`에
걸린 비금융 7종목이 **전부 대체값 기반**이었다.

| 종목 | 매출 | "이자비용"(대체값) | 배율 |
|---|---|---|---|
| SK이노베이션 | 80.3조 | **6.36조 (매출의 8%)** | 0.07 |
| POSCO홀딩스 | 69.1조 | 3.91조 | 0.47 |
| LG화학 | 45.9조 | 2.16조 | 0.55 |
| S-Oil | 34.2조 | 0.68조 | 0.35 |

**어떤 제조업체도 매출의 8%를 이자로 내지 않는다.** 금융원가에는 외화부채
환산손실과 파생상품손실이 섞여 있고, 그 비중이 회사마다 다르다. 대체값을
안 쓴 한국전력은 배율 3.11로 이 필터를 멀쩡히 통과했다(대신 부채비율
416.9%로 `HIGH_DEBT`에 걸렸다) — 규칙 자체는 멀쩡하고 입력이 문제라는 뜻이다.

**근거는 §8.1이 이미 세운 규칙이다.** 이 표는 `interest_expense`가 NULL이면
통과시킨다 — "모르는 것으로 떨어뜨리지 않는다"는 원칙이다. 금융원가로 채운
값은 **이자비용을 모르는 상태의 상한 추정치**이지 이자비용이 아니다. 인식론적으로
NULL 쪽에 속하므로 같은 대접을 받아야 한다.

스코어링에서도 `MISSING`으로 두는 이유는 같다. 10배 틀린 입력으로 매긴
0점은 "부채 부담이 크다"가 아니라 "계정과목을 못 찾았다"를 뜻한다. §8.3의
환산이 그런 경우를 위해 있다 — 안정성 축은 부채비율(10)과 유동비율(5)이
남아 유효만점 15점이 되고, 축 배점 25의 절반(12.5)을 넘으므로 축 하한에도
걸리지 않는다.

**판정 근거 화면은 이 사실을 말해야 한다** — "이자보상배율: 이자비용을 찾지
못해 계산하지 않았습니다"처럼.

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

## 17. 실제 DART 데이터로 확인한 것 (2026-09-16)

인증키를 넣고 42종목 × 5년을 실제로 수집한 결과다. 계획 C는 이 절을
전제로 써야 한다.

### 17.1 수집 실적

| 항목 | 결과 |
|---|---|
| 종목 | 42 성공 · 0 실패 |
| 재무제표 | 196건 (42종목 × 5년 중 14건 부재) |
| 주가 | 51,357봉 (2021-09-10 ~ 2026-09-15) |

부재 14건은 전부 **금융 7종목의 2021·2022년**이다. DART에 데이터가
실제로 없다 — `fnlttSinglAcntAll`을 연결(CFS)·별도(OFS) 양쪽으로 물어도
`013 조회된 데이타가 없습니다`가 돌아온다. 우리 쪽 오류가 아니며
`collect_errors`도 0건이다. 이 7종목은 `FINANCIAL_SECTOR`로 제외된다.

### 17.2 ACCOUNT_MAP 적중률

삼성전자 하나로는 필수·선택 항목이 전부 잡혔다. 42종목으로 넓히자
갈라졌다.

| 필드 | 결측 | 원인 |
|---|---|---|
| `dividend_per_share` | 196건 (전부) | 「배당에 관한 사항」 — 다른 엔드포인트 |
| `shares_outstanding` | 196건 (전부) | 「주식의 총수 현황」 — 다른 엔드포인트 (§9.2) |
| `interest_expense` | 129건 (66%) | **매핑 누락. 아래 참조** |
| `revenue`·`current_assets`·`current_liabilities` | 각 21건 | 금융 7종목 × 3년. 은행·보험은 계정 구조가 다르다 |
| `capex` | 10건 | 현금흐름표 표기 차이 |
| `net_income` | 3건 | |

### 17.3 이자비용 — 계획 C가 결정해야 할 것

매핑표에 `"금융원가"`라고 적혀 있는데 **실제 계정명은 `"금융비용"`**이고,
`ifrs-full_FinanceCosts`라는 id는 매핑표에 아예 없다. 129건 중 119건이
이 계정을 갖고 있다.

**그러나 금융비용은 이자비용이 아니다.** 이자비용에 환차손·파생상품손실
등이 더해진 상위 개념이라, 대체값으로 쓰면 이자보상배율이 실제보다 **낮게**
나온다. 품질 필터에서는 보수적인 방향(좋은 회사를 가끔 떨어뜨림)이므로
반대보다 안전하지만, 지표의 의미가 달라지는 것은 사실이다.

권고: **쓰되, 이자비용을 먼저 찾고 없을 때만 쓴다.** 그리고 대체값을
썼다는 사실을 판정 근거에 드러낸다 — §11.2가 계산식을 그대로 보여주는
것과 같은 이유다.

**주의 — 단순히 id 목록에 추가하면 안 된다.** `pick_amount`은 *모든 id를
먼저, 그다음 모든 name*을 본다. `ifrs-full_FinanceCosts`를 id에 넣으면,
표준 id 없이 이름으로만 `이자비용`을 쓰는 회사에서 금융비용이 이자비용을
이긴다. 실측 13건이 여기 해당하고 그 안에 **LG생활건강·한국전력·
삼성에스디에스**가 있다.

| 경로 | 건수 |
|---|---|
| 표준 id로 잡힘 | 21 |
| 이름 `이자비용`으로만 잡힘 | 3 |
| **충돌 (이름 `이자비용` + id `FinanceCosts`)** | **13** |
| 금융비용으로만 복구 가능 | 149 |

따라서 `ACCOUNT_MAP`의 값 형태를 **우선순위 단계의 목록**으로 바꿔야 한다.
한 단계 안에서는 id → name 순으로 보고, 단계는 순서대로 시도한다.
`interest_expense`는 1단계 = 이자비용(id·name), 2단계 = 금융비용(id·name).

### 17.4 새 엔드포인트 두 개의 실제 응답

**`stockTotqySttus.json`** — `se` 필드로 주식 종류별 여러 행이 온다.
삼성전자 2024 기준 보통주 59.7억 주, 합계 67.9억 주.
**시가총액을 합계로 계산하면 13.8% 과대평가된다.** 005930은 보통주
티커이므로 보통주 행을 써야 한다. 우선주(005935)는 별도 티커다.
`비고` 행의 수량은 `-`이므로 건너뛴다.

**`se` 표기를 정확 일치로 찾으면 안 된다 — 이 문서의 앞선 서술을 정정한다.**
초안은 삼성전자 하나의 응답만 보고 "보통주 / 우선주 / 합계 / 비고 4행"이라
단정했다. 42종목 210건을 전수 조사하니 **27종**이었다.

보통주를 뜻하는 표기만 9종이다.

| 표기 | 건수 |
|---|---|
| `보통주` | 179 |
| `의결권 있는 보통주` | 8 |
| `보통주식` | 5 |
| `보통주식\n(의결권 있는주식)` | 4 |
| `의결권 있는 주식` | 4 |
| `의결권이 있는\n주식(보통주)` | 3 |
| `의결권 있는\n보통주` | 3 |
| `의결권이 있는주식(보통주)` | 2 |
| `의결권 있는\n주식` | 2 |

**공백뿐 아니라 줄바꿈(`\n`)이 박혀 있다.** `.replace(" ", "")`로는 부족하고
`"".join(se.split())`처럼 모든 공백류를 지워야 한다.

정확 일치로 구현하면 `보통주` 179건만 잡고 나머지 31건이 합계로 폴백한다.
실측 피해: 삼성전기가 77,600,680(합계) 대신 74,693,696(보통주)이어야 하는데
**3.9% 과대평가**됐다.

판정은 **부분 일치 3단계**로 한다. 공백류를 모두 지운 뒤:

1. `합계`가 들어 있으면 합계 행 (최후 폴백용으로 분류)
2. `우선`이 들어 있으면 **버린다** — 아래 두 규칙보다 먼저 본다.
   `의결권 있는 우선주` 같은 표기가 나중에 오면 조용히 틀린 값을 쓰게 된다
3. `보통주`가 들어 있으면 보통주 행
4. `의결권있는` 또는 `의결권이있는`이 들어 있으면 보통주 행
   (의결권 있는 주식 = 보통주. 기업은행·셀트리온이 이 표기를 쓴다)

우선순위는 3 → 4 → 1이다. 어디에도 안 걸리는 표기(`종류주` 등)는 버린다 —
잘못된 행을 쓰느니 합계로 폴백하는 편이 낫다.

`의결권없는주식`에 `의결권있는`은 부분 문자열이 아니므로(있 ≠ 없) 4번 규칙이
우선주를 잡지 않는다.

**`accnutAdtorNmNdAdtOpinion.json`** — `bsns_year`가 연도가 아니라
`"제56기
(당기)"` 형태이고 **줄바꿈이 박혀 있다.** 연도로 파싱하면 깨진다.
`adt_opinion`은 `"적정의견"` 같은 문자열이다.

**여기서도 삼성전자 하나로 일반화했다가 틀렸다 — 정정한다.**
42종목 210건을 전수 조사한 결과다.

| 초안의 주장 | 실측 |
|---|---|
| `bsns_year`가 `"제56기
(당기)"` 형태 | 표기 **407종**. 210건 중 **46건에 당기 표기가 아예 없다** (164건 `(당기)`, 1건 `(당)기`, 45건 없음) |
| 기수당 2행씩 중복 (3기 → 6행) | **116건(55%)은 중복이 없다.** 2행 79 · 3행 10 · 4행 1 · 6행 4 |
| `adt_opinion`은 `"적정의견"` | 맞다. 실측 10종 |

`bsns_year` 실제 표기 예: `"제 50 기
(당기)"`(공백+줄바꿈), `"제4(당)기"`,
`"제60기"`(기수만), `"제52기
('24.01.01~'24.12.31)"`(기간),
`"제 16기(2022년)"`(연도), `"제46기 연간
제46기 3분기
…"`(한 칸에 넷),
그리고 **전 행이 `'-'`인 빈 응답 14건**(`status=000`인데 `adt_opinion` 키 자체가 없다).

`adt_opinion` 실측 10종 (882행 기준): `'적정의견'` 506 · `'적정'` 298 ·
키 없음 55 · `'적 정'` 9 · `'적정
예외사항 없음
…'` 9 · `' 적정의견'` 1 ·
`' 적정'` 1 · `'(*1)' '(*2)' '(*3)'` 각 1(**각주 참조**다. 의견 문구가 아니다).

**비적정은 0건이었다.** 42종목 KOSPI 대형주에서는 정상이다.

#### 파싱 규칙 — 실측에서 나온 것

1. **모든 공백류를 지우고 비교한다.** `se` 필드와 같은 규칙이다
   (`"".join(x.split())`). 값 한가운데 줄바꿈이 박혀 오기 때문이다
   (`'적정
예외사항 없음…'` 9건). `.replace(" ", "")`만 쓰면
   `'의견
거절'`이 `"의견거절"`과 매칭되지 않아 **가장 강한 결격 신호를
   조용히 놓친다.** 결격 단어 목록도 같은 방식으로 정규화해 비교하므로,
   목록에 `"의견 거절"` 같은 공백 변형을 적을 필요가 없다
2. **당기 마커는 `(당기)`와 `(당)기` 둘이다.**
3. **당기 행이 여럿일 수 있다**(74건). 첫 당기 행의 의견이 비어 있으면
   나머지 당기 행을 계속 본다. 같은 기수의 행이 서로 다른 의견을 말한
   경우는 실측 0건이라 첫 매치로 안전하다
4. **당기 마커가 없으면 첫 행만 본다.** 이 폴백의 유일한 근거는
   "첫 행이 최신 기수"이고(실측 반례 0건), 그 근거는 둘째 행에 대해
   아무 말도 하지 않는다. 첫 행이 비었다고 다음 행으로 내려가면 **전기
   의견을 당기 것으로 보고하게 된다** — 낡은 정보로 종목을 제외하는
   것이며, §8.1의 "모르는 것으로 떨어뜨리지 않는다"를 거스른다
5. **모르는 의견 문자열은 결격으로 보지 않는다.** 화이트리스트가 아니라
   블랙리스트다. `'(*1)'` 같은 각주 참조나 처음 보는 표기로 회사가 통째로
   사라지면 아무도 이유를 모른다

---

## 16. 향후 확장

- 금융업 전용 필터셋 (BIS비율·NIM·대손충당금커버리지)
- 분기 재무제표
- 텔레그램·이메일 알림 + 스케줄러
- 유료 데이터 소스 어댑터 (§5.2 인터페이스만 구현)
- 지수 편입 이력 확보 후 백테스팅
- 섹터 상대평가 (같은 섹터 내 백분위)
- 관심종목 메모·매수 기록

---

## 18. 계획 E — 미국 시장 (2026-10-28)

§15의 6단계다. **②정규화 계층 위쪽(`analysis/`)은 한 줄도 바꾸지 않는다.**
그것이 §7.1의 "이 아래로는 어떤 코드도 시장을 구분하지 않는다"가 맞았는지에
대한 검증이며, 계획 E의 완료 기준이다(`git diff -- src/stockscout/analysis/`가
비어 있어야 한다).

### 18.1 범위

- 유니버스: `config/universe.yaml`에 `US:` 섹션을 두고 **약 40종목**을 직접
  적는다. 업종을 고루 섞고 금융 몇 종목은 `is_financial`로 표시해 필터를
  관통시킨다. 매핑 적중률을 실측하고 고친 뒤 S&P 500 전체로 넓히는 것은
  다음 계획이다 — 한국도 KOSPI 200이 아니라 42종목으로 구조를 검증했다
- 하지 않는 것: S&P 500 구성종목 자동 수집, 배당, 감사의견, 섹터 자동 판별

### 18.2 실제 SEC 응답에서 확인한 것

`companyfacts`(AAPL, 2026-10-28 수집, 272KB)를 직접 받아 봤다.

1. **User-Agent가 없으면 403이다.** 실측했다. 현재 기본값 `stockscout/0.1`은
   연락처가 없어 정책 위반이다. `config/.env`의 `SEC_USER_AGENT`에
   `stockscout rusipul@gmail.com`을 넣는다(사용자 승인)
2. **10-K 안에 분기 값이 섞여 있다.** `Revenues`의 한 항목은
   `form=10-K`인데 기간이 2018-07-01 ~ 2018-09-29(한 분기)다. `form`만
   보면 분기 매출이 연간 매출로 들어간다. 흐름 항목은 **기간이 350~380일**인
   것만 연간으로 본다
3. **`fy`는 공시의 회계연도이지 값의 기간이 아니다.** 10-K에는 전기 비교
   수치가 같은 `fy`로 함께 들어 있다. 기간은 `end`(와 `start`)로 식별한다
4. **태그가 해마다 바뀐다.** AAPL은 `Revenues`(~2018)에서
   `RevenueFromContractWithCustomerExcludingAssessedTax`로 옮겼고,
   `InterestExpense`는 2023년 이후 따로 보고하지 않는다. 우선순위는
   **회사 단위가 아니라 연도 단위로** 적용한다
5. **감사의견은 XBRL에 없다.** `audit_opinion`은 None이고, §8.1의 규칙대로
   감사의견 필터는 건너뛴다(모르는 것으로 떨어뜨리지 않는다)
6. **미국의 `CommonStockSharesOutstanding`은 자기주식을 뺀 유통주식수다.**
   한국의 발행주식총수와 뜻이 다르다. 미국은 시가총액도 유통주식수로 내는
   것이 관행이므로 `shares_outstanding`에 그대로 넣고 `shares_treasury`는
   None으로 둔다 — §9.2의 폴백이 유통주식수 = 발행주식수로 처리한다
7. `company_tickers.json`(티커 → CIK)은 10,430종목, 798KB다

### 18.3 수집 — `sources/edgar.py`, `sources/yahoo.py`

**EDGAR (`FundamentalSource`)**

- 실행마다 `company_tickers.json`을 한 번 받아 티커 → CIK를 만든다
  (DART의 corp_code와 같은 방식). 티커 표기는 `BRK.B` ↔ `BRK-B`를 맞춘다
- 회사당 `companyfacts` 1회로 전 연도를 받는다. 어댑터가 연도별로 잘라
  `RawStatement`로 돌려주므로 `FundamentalSource` 인터페이스는 그대로다.
  `raw_financials`에 `source='edgar'`로 저장한다. 원본 전체(회사당 수백
  KB, 분기·비교 수치 포함)가 아니라 **연간 값 전체**를 담는다 - AAPL 한
  해가 10KB(태그 약 200개)다. 후보 태그만 담으면 매핑에 태그를 더할
  때마다 SEC에서 다시 받아야 한다
- 연간 값 고르기:
  - 대상 form: `10-K`, `10-K/A`
  - 흐름 항목: 기간 350~380일
  - 시점 항목(재무상태표): 그 회계연도 흐름 기간의 `end`와 같은 날짜의 값
  - `fiscal_year` = `end`의 연도. AAPL 2025-09-27 → 2025, 월마트
    2025-01-31 → 2025로, 회사가 부르는 회계연도와 맞는다
  - 같은 (태그, 기간)이 여러 번 공시되면 `filed`가 가장 늦은 값(정정 공시)
- 속도: 초당 5회(SEC 한도 10회의 절반)로 기존 `ratelimit`을 쓴다
- `SEC_USER_AGENT`가 없거나 연락처(`@`)가 없으면 `FatalSourceError`로
  배치를 멈추고 무엇을 넣으라고 안내한다. `load_secrets`는 지금 DART 키를
  무조건 요구하므로, 시장별로 필요한 비밀값만 요구하게 바꾼다 — 미국만
  수집하는데 DART 키가 없다고 죽으면 안 된다

**yfinance (`PriceSource`)**

- 수정종가(`auto_adjust=True`). `yfinance`를 의존성에 추가하고 버전을 고정한다
- 비공식 경로라 깨질 수 있다(§2). 실패는 종목 단위로 격리한다(§13)

### 18.4 정규화 — `normalize/us_mapping.py`

- 필드마다 태그 우선순위 목록을 두고 **연도마다** 첫 매치를 취한다
- 이자비용은 `InterestExpense` → `InterestExpenseNonoperating` →
  `InterestExpenseDebt`. 한국의 금융비용 같은 상위 개념 대체값은 두지 않는다
  — 셋 다 없으면 None이고 이자보상배율 필터는 건너뛴다(§8.1)
- capex는 `PaymentsToAcquirePropertyPlantAndEquipment`를 양수 크기로
- 구체적인 목록은 실측 후 확정하고, 적중률을 §17.2처럼 이 절에 남긴다

### 18.5 시장별 스냅샷

- `snapshots`에 `market` 컬럼을 더한다. 기존 행은 마이그레이션에서 `KR`로
  채운다. `get_latest_snapshot(market)`
- 미국을 분석해도 한국 결과가 가려지지 않는다. 시장마다 기준일·통화·수집
  시점이 달라 한 스냅샷에 섞을 이유가 없다
- API: `/api/snapshot`과 `/api/securities`가 `?market=`(기본 KR)을 받는다
  (§11.3이 이미 그렇게 적었다). 상세·차트는 티커로 찾으므로 그대로다
- 화면: 상단에 **KR | US 탭**. USD는 `$1,234.56`으로 표시한다. 고른 탭은
  localStorage에 기억한다

### 18.6 검증

- 단위 테스트: 실제 AAPL 응답을 줄인 픽스처로 분기 값 배제, 정정 공시 우선,
  연도별 태그 전환, 시점 항목의 날짜 맞춤을 고정한다
- 실데이터 관통: 점수 분포, 측정 불가 축 수, 등급을 미국 종목으로 확인한다
- **`analysis/` 무변경**을 마지막 Task에서 diff로 확인한다
