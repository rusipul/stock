"""테이블 읽기·쓰기. 이 프로젝트의 SQL은 전부 여기에 있다."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from stockscout.models import Security


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Repository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # --- 종목 마스터 -------------------------------------------------

    def upsert_security(self, sec: Security) -> None:
        self.conn.execute(
            """
            INSERT INTO securities (
                ticker, market, name, name_en, exchange, sector,
                is_financial, source_id, currency, listed_at, in_universe, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                market=excluded.market,
                name=excluded.name,
                name_en=excluded.name_en,
                exchange=excluded.exchange,
                sector=excluded.sector,
                is_financial=excluded.is_financial,
                source_id=excluded.source_id,
                currency=excluded.currency,
                listed_at=excluded.listed_at,
                in_universe=excluded.in_universe,
                updated_at=excluded.updated_at
            """,
            (
                sec.ticker, sec.market, sec.name, sec.name_en, sec.exchange,
                sec.sector, int(sec.is_financial), sec.source_id, sec.currency,
                sec.listed_at, int(sec.in_universe), _now(),
            ),
        )
        self.conn.commit()

    def get_security(self, ticker: str) -> Security | None:
        row = self.conn.execute(
            "SELECT * FROM securities WHERE ticker = ?", (ticker,)
        ).fetchone()
        return self._to_security(row) if row else None

    def list_securities(
        self, market: str | None = None, in_universe_only: bool = True
    ) -> list[Security]:
        sql = "SELECT * FROM securities WHERE 1=1"
        params: list[object] = []
        if market:
            sql += " AND market = ?"
            params.append(market)
        if in_universe_only:
            sql += " AND in_universe = 1"
        sql += " ORDER BY ticker"
        rows = self.conn.execute(sql, params).fetchall()
        return [self._to_security(r) for r in rows]

    def mark_out_of_universe(self, tickers: list[str]) -> None:
        self.conn.executemany(
            "UPDATE securities SET in_universe = 0, updated_at = ? WHERE ticker = ?",
            [(_now(), t) for t in tickers],
        )
        self.conn.commit()

    @staticmethod
    def _to_security(row: sqlite3.Row) -> Security:
        return Security(
            ticker=row["ticker"],
            market=row["market"],
            name=row["name"],
            name_en=row["name_en"],
            exchange=row["exchange"],
            sector=row["sector"],
            is_financial=bool(row["is_financial"]),
            source_id=row["source_id"],
            currency=row["currency"],
            listed_at=row["listed_at"],
            in_universe=bool(row["in_universe"]),
        )
