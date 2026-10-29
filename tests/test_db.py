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


# --- pre-v1 스키마 추가: snapshots.as_of -------------------------------
#
# CREATE TABLE IF NOT EXISTS는 이미 존재하는 테이블에 새 컬럼을 얹어주지
# 않는다. data/stockscout.db는 51,231개 가격 봉이 든 실제 운영 DB이고
# 다시 받으려면 몇 분이 걸린다 — init_db()가 이 DB를 건드리지 않고도
# 새 컬럼을 얹을 수 있어야 한다. 이 테스트는 컬럼 없이 만든 구 스키마
# DB에 init_db를 돌려서, 컬럼이 생기고 기존 행이 살아남는지 확인한다.


def test_init_db_adds_as_of_column_to_pre_existing_snapshots_table(tmp_path):
    db_path = tmp_path / "old.db"

    with connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE snapshots (
                snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at      TEXT NOT NULL,
                config_hash TEXT NOT NULL,
                config_json TEXT NOT NULL,
                note        TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO snapshots (run_at, config_hash, config_json, note) "
            "VALUES ('2026-01-01T00:00:00+00:00', 'h1', '{}', '기존 행')"
        )
        conn.commit()

    with connect(db_path) as conn:
        init_db(conn)  # 여기서 컬럼을 얹어야 한다
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(snapshots)")}
        rows = conn.execute("SELECT run_at, note, as_of FROM snapshots").fetchall()

    assert "as_of" in cols
    assert len(rows) == 1                 # 기존 행이 살아남았다
    assert rows[0]["note"] == "기존 행"
    assert rows[0]["as_of"] is None       # 옛 행은 값이 없을 뿐, 사라지지 않는다


def test_init_db_as_of_migration_is_idempotent(tmp_path):
    """이미 컬럼이 있는(새로 만든) DB에 다시 돌려도 에러가 나면 안 된다."""
    db_path = tmp_path / "new.db"

    with connect(db_path) as conn:
        init_db(conn)
        init_db(conn)  # 중복 ALTER TABLE이면 여기서 예외가 난다
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(snapshots)")}

    assert "as_of" in cols


# --- 계획 C 스키마 추가: financials의 세 컬럼 ---------------------------
#
# as_of와 같은 문제, 같은 패턴이다. 다만 이 테스트가 없으면 검증 구멍이
# 생긴다: 신규 DB는 schema.sql이 세 컬럼을 이미 만들어주므로
# _add_financials_columns_if_missing의 `if name not in columns`가 항상
# 거짓이 되고, ALTER TABLE 줄은 어느 테스트에서도 실행되지 않는다.
# 컬럼명이나 타입에 오타가 나도 스위트는 전부 초록이고, 몇 분 걸려 받은
# 실제 DB에서만 조용히 어긋난다. 그래서 옛 스키마를 직접 만들어 그 줄을
# 실제로 통과시킨다.


def test_init_db_adds_plan_c_columns_to_pre_existing_financials_table(tmp_path):
    db_path = tmp_path / "old.db"

    with connect(db_path) as conn:
        conn.execute(
            """
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
                missing_fields      TEXT,
                PRIMARY KEY (ticker, fiscal_year)
            )
            """
        )
        conn.execute(
            "INSERT INTO financials (ticker, fiscal_year, currency, revenue) "
            "VALUES ('005930.KS', 2024, 'KRW', 300.0)"
        )
        conn.commit()

    with connect(db_path) as conn:
        init_db(conn)  # 여기서 컬럼 셋을 얹어야 한다
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(financials)")}
        rows = conn.execute(
            "SELECT revenue, shares_treasury, audit_opinion, proxy_fields FROM financials"
        ).fetchall()

    assert {"shares_treasury", "audit_opinion", "proxy_fields"} <= cols
    assert len(rows) == 1                     # 마이그레이션이 데이터를 날리지 않았다
    assert rows[0]["revenue"] == 300.0        # 기존 값도 그대로다
    assert rows[0]["proxy_fields"] is None    # 옛 행은 값이 없을 뿐이다


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


def test_init_db_adds_window_stats_to_an_old_database(tmp_path):
    """기간별 통계 이전의 결과 행은 window_stats가 NULL이다. API가 컬럼으로 메운다."""
    import sqlite3

    from stockscout.store.db import init_db

    conn = sqlite3.connect(tmp_path / "old.db")
    conn.execute("CREATE TABLE analysis_results (snapshot_id INTEGER, ticker TEXT)")
    conn.execute("INSERT INTO analysis_results VALUES (1, 'A')")
    conn.commit()

    init_db(conn)

    (value,) = conn.execute("SELECT window_stats FROM analysis_results").fetchone()
    assert value is None
    conn.close()
