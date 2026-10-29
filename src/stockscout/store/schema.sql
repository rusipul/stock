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
  open   REAL,    -- 캔들 표시용 수정 시가·고가·저가 (분석은 종가만 쓴다)
  high   REAL,
  low    REAL,
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
  shares_treasury     REAL,
  audit_opinion       TEXT,
  proxy_fields        TEXT,
  missing_fields      TEXT,
  cash                REAL,
  short_term_debt     REAL,
  PRIMARY KEY (ticker, fiscal_year)
);

-- 최근 공시 (원문 그대로). 분류는 normalize/filings.py가 읽을 때 한다.
CREATE TABLE IF NOT EXISTS filings (
  ticker TEXT NOT NULL,
  filed  TEXT NOT NULL,
  title  TEXT NOT NULL,
  url    TEXT NOT NULL,
  form   TEXT,
  items  TEXT,
  PRIMARY KEY (ticker, url)
);

CREATE TABLE IF NOT EXISTS snapshots (
  snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_at      TEXT NOT NULL,
  config_hash TEXT NOT NULL,
  config_json TEXT NOT NULL,
  note        TEXT,
  as_of       TEXT,  -- 이 스냅샷이 고정한 가격 기준일. run_at(실행 시각)과는
                      -- 별개다: 수집을 며칠 건너뛰면 run_at은 흘러가도
                      -- as_of는 그대로일 수 있다. 기존 DB는 store/db.py의
                      -- init_db()가 ALTER TABLE로 얹어주므로 NULL 허용이다.
  market      TEXT NOT NULL DEFAULT 'KR'  -- 시장마다 따로 분석·저장한다 (설계서 §18.5)
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
  window_stats    TEXT,
  drawdown_stats  TEXT,
  weak_axes       TEXT,
  fcf_yield       REAL,
  interim         TEXT,
  breakout_stats  TEXT,   -- 영탁센스 {"signal": ..., "lookbacks": {"252": BreakoutStat}}   -- 최근 중간보고서 누적 vs 전년 동기 (InterimResults JSON)   -- 영역 최소점 미달 축 JSON 목록   -- {"benchmark": "^KS11", "windows": {"3": DrawdownStat}}   -- {"1": {avg_price, drop_vs_avg, high, drop_vs_high}, ...}
                          -- 1~5년 전부. 화면의 비교 기간 슬라이더가 쓴다
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
