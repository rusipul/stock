"""테이블 읽기·쓰기. 이 프로젝트의 SQL은 전부 여기에 있다."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone

from stockscout.models import Financials, PriceBar, RawStatement, Security


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

    # --- 원본 재무 ---------------------------------------------------

    def save_raw_statements(self, statements: list[RawStatement]) -> None:
        self.conn.executemany(
            """
            INSERT INTO raw_financials (ticker, fiscal_year, source, payload, fetched_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(ticker, fiscal_year, source) DO UPDATE SET
                payload=excluded.payload, fetched_at=excluded.fetched_at
            """,
            [
                (s.ticker, s.fiscal_year, s.source, json.dumps(s.payload, ensure_ascii=False), _now())
                for s in statements
            ],
        )
        self.conn.commit()

    def get_raw_statements(self, ticker: str, source: str | None = None) -> list[RawStatement]:
        sql = "SELECT * FROM raw_financials WHERE ticker = ?"
        params: list[object] = [ticker]
        if source:
            sql += " AND source = ?"
            params.append(source)
        sql += " ORDER BY fiscal_year"
        rows = self.conn.execute(sql, params).fetchall()
        return [
            RawStatement(
                ticker=r["ticker"],
                fiscal_year=r["fiscal_year"],
                source=r["source"],
                payload=json.loads(r["payload"]),
            )
            for r in rows
        ]

    def existing_statement_years(self, ticker: str, source: str) -> set[int]:
        """증분 수집용. 이미 받아둔 연도는 다시 요청하지 않는다."""
        rows = self.conn.execute(
            "SELECT fiscal_year FROM raw_financials WHERE ticker = ? AND source = ?",
            (ticker, source),
        ).fetchall()
        return {r["fiscal_year"] for r in rows}

    # --- 원본 주가 ---------------------------------------------------

    def save_price_bars(self, bars: list[PriceBar], source: str) -> None:
        self.conn.executemany(
            """
            INSERT INTO raw_prices (ticker, date, close, volume, source)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(ticker, date) DO UPDATE SET
                close=excluded.close, volume=excluded.volume, source=excluded.source
            """,
            [(b.ticker, b.date.isoformat(), b.close, b.volume, source) for b in bars],
        )
        self.conn.commit()

    def get_price_bars(
        self, ticker: str, start: date | None = None, end: date | None = None
    ) -> list[PriceBar]:
        sql = "SELECT * FROM raw_prices WHERE ticker = ?"
        params: list[object] = [ticker]
        if start:
            sql += " AND date >= ?"
            params.append(start.isoformat())
        if end:
            sql += " AND date <= ?"
            params.append(end.isoformat())
        sql += " ORDER BY date"
        rows = self.conn.execute(sql, params).fetchall()
        return [
            PriceBar(
                ticker=r["ticker"],
                date=date.fromisoformat(r["date"]),
                close=r["close"],
                volume=r["volume"],
            )
            for r in rows
        ]

    def latest_price_date(self, ticker: str) -> date | None:
        """증분 수집용. 이 날짜 다음날부터 받으면 된다."""
        row = self.conn.execute(
            "SELECT MAX(date) AS d FROM raw_prices WHERE ticker = ?", (ticker,)
        ).fetchone()
        return date.fromisoformat(row["d"]) if row and row["d"] else None

    # --- 정규화 재무 -------------------------------------------------

    _FIN_COLUMNS = (
        "ticker", "fiscal_year", "currency", "revenue", "operating_income",
        "net_income", "total_assets", "total_liabilities", "total_equity",
        "current_assets", "current_liabilities", "interest_expense",
        "operating_cash_flow", "capex", "shares_outstanding", "dividend_per_share",
    )

    def save_financials(self, records: list[Financials]) -> None:
        cols = ", ".join(self._FIN_COLUMNS) + ", missing_fields"
        placeholders = ", ".join("?" * (len(self._FIN_COLUMNS) + 1))
        updates = ", ".join(
            f"{c}=excluded.{c}" for c in self._FIN_COLUMNS[2:]
        ) + ", missing_fields=excluded.missing_fields"
        self.conn.executemany(
            f"""
            INSERT INTO financials ({cols}) VALUES ({placeholders})
            ON CONFLICT(ticker, fiscal_year) DO UPDATE SET {updates}
            """,
            [
                tuple(getattr(r, c) for c in self._FIN_COLUMNS)
                + (json.dumps(r.missing_fields, ensure_ascii=False),)
                for r in records
            ],
        )
        self.conn.commit()

    def get_financials(self, ticker: str) -> list[Financials]:
        rows = self.conn.execute(
            "SELECT * FROM financials WHERE ticker = ? ORDER BY fiscal_year",
            (ticker,),
        ).fetchall()
        return [
            Financials(**{c: row[c] for c in self._FIN_COLUMNS}) for row in rows
        ]

    def missing_field_counts(self) -> dict[str, int]:
        """정규화 품질 보고용. 어떤 계정과목 매핑이 자주 실패하는지 보여준다."""
        counts: dict[str, int] = {}
        for row in self.conn.execute("SELECT missing_fields FROM financials"):
            for field in json.loads(row["missing_fields"] or "[]"):
                counts[field] = counts.get(field, 0) + 1
        return counts

    # --- 오류 기록 ---------------------------------------------------

    def record_error(self, ticker: str, source: str, message: str) -> None:
        self.conn.execute(
            "INSERT INTO collect_errors (ticker, source, occurred_at, message) VALUES (?, ?, ?, ?)",
            (ticker, source, _now(), message),
        )
        self.conn.commit()

    def recent_errors(self, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            "SELECT ticker, source, occurred_at, message FROM collect_errors "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

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
