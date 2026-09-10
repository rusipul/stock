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
