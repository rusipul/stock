"""SQLite 연결과 초기화."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA_PATH = Path(__file__).parent / "schema.sql"
DEFAULT_DB_PATH = Path("data/stockscout.db")


@contextmanager
def connect(db_path: Path | str = DEFAULT_DB_PATH) -> Iterator[sqlite3.Connection]:
    """행을 dict처럼 다룰 수 있는 연결을 연다. 블록을 벗어나면 닫힌다."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
    finally:
        conn.close()


def init_db(conn: sqlite3.Connection) -> None:
    """스키마를 적용한다. 여러 번 호출해도 안전하다(CREATE TABLE IF NOT EXISTS)."""
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
