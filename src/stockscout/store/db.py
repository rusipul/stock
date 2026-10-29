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
    _add_snapshots_as_of_column_if_missing(conn)
    _add_financials_columns_if_missing(conn)
    _add_snapshots_market_column_if_missing(conn)
    _add_window_stats_column_if_missing(conn)
    _add_drawdown_stats_column_if_missing(conn)
    _add_weak_axes_column_if_missing(conn)
    conn.commit()


def _add_weak_axes_column_if_missing(conn: sqlite3.Connection) -> None:
    """영역 최소점 컬럼. 옛 결과 행은 NULL이고 API가 빈 목록으로 본다."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(analysis_results)")}
    if "weak_axes" not in columns:
        conn.execute("ALTER TABLE analysis_results ADD COLUMN weak_axes TEXT")
    if "fcf_yield" not in columns:
        conn.execute("ALTER TABLE analysis_results ADD COLUMN fcf_yield REAL")
    if "interim" not in columns:
        conn.execute("ALTER TABLE analysis_results ADD COLUMN interim TEXT")
    if "breakout_stats" not in columns:
        conn.execute("ALTER TABLE analysis_results ADD COLUMN breakout_stats TEXT")


def _add_drawdown_stats_column_if_missing(conn: sqlite3.Connection) -> None:
    """단기 하락(최근 N개월) 컬럼. 옛 결과 행은 NULL이고 화면은 등급을 비운다."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(analysis_results)")}
    if "drawdown_stats" not in columns:
        conn.execute("ALTER TABLE analysis_results ADD COLUMN drawdown_stats TEXT")


def _add_window_stats_column_if_missing(conn: sqlite3.Connection) -> None:
    """비교 기간 슬라이더가 더한 컬럼을 기존 DB에 얹는다.

    옛 결과 행은 NULL로 남는다. 다시 계산할 원본이 이 행에 없기 때문이다.
    API는 NULL이면 1·3·5년 컬럼으로 메운다 (web/api.py의 _window_stats).
    """
    columns = {row[1] for row in conn.execute("PRAGMA table_info(analysis_results)")}
    if "window_stats" not in columns:
        conn.execute("ALTER TABLE analysis_results ADD COLUMN window_stats TEXT")


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


def _add_snapshots_as_of_column_if_missing(conn: sqlite3.Connection) -> None:
    """v1 이전에 추가된 컬럼을 기존 DB에 얹는다.

    CREATE TABLE IF NOT EXISTS는 이미 존재하는 테이블에 새 컬럼을 얹어
    주지 않는다. data/stockscout.db처럼 몇 분 걸려 받은 실제 운영 DB를
    다시 만들지 않고도 스키마를 따라잡아야 한다. 이 프로젝트는 별도
    마이그레이션 프레임워크를 두지 않는다 — 테이블 몇 개짜리 SQLite에는
    과하다. 대신 "컬럼이 있는지 보고 없으면 ALTER TABLE"이라는 이 짧은
    패턴으로 pre-v1 스키마 추가를 하나씩 처리한다. 필요해지면 함수를
    하나 더 늘리는 정도로 충분하다.
    """
    columns = {row[1] for row in conn.execute("PRAGMA table_info(snapshots)")}
    if "as_of" not in columns:
        conn.execute("ALTER TABLE snapshots ADD COLUMN as_of TEXT")


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
        ("cash", "REAL"),
        ("short_term_debt", "REAL"),
    ):
        if name not in columns:
            conn.execute(f"ALTER TABLE financials ADD COLUMN {name} {sql_type}")
