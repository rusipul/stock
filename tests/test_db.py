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
