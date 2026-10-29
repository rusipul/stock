"""테이블 읽기·쓰기. 이 프로젝트의 SQL은 전부 여기에 있다."""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import date, datetime, timezone

from stockscout.models import (
    AnalysisResult,
    Financials,
    PriceBar,
    RawStatement,
    Security,
    Snapshot,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def json_safe(value):
    """JSON으로 직렬화할 수 없는 float를 None으로 바꾼다.

    json.dumps(float('inf'))는 표준 JSON이 아닌 Infinity 토큰을 내보내고
    JSON.parse가 이를 거부한다. 계획 C의 interest_coverage가 무차입
    기업에 대해 inf를 반환하므로(설계서 §8.2의 만점 조건), 그 값이
    score_detail에 들어가면 웹 화면이 통째로 깨진다.

    직렬화 경계에서 한 번만 처리한다. 계산 쪽은 inf를 그대로 쓴다 —
    "이자 부담 없음"과 "데이터 없음"은 다른 상태이기 때문이다.
    """
    if isinstance(value, float):
        return None if math.isinf(value) or math.isnan(value) else value
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value


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

    def first_price_date(self, ticker: str) -> date | None:
        """그 종목의 가장 이른 저장 봉. 이력 판정에 쓴다.

        조회 구간과 무관해야 한다 — 구간으로 잘린 목록에서 첫 봉을 구하면
        "구간 시작일이 거래일인가"를 묻는 것이 되어 판정이 무의미해진다.
        """
        row = self.conn.execute(
            "SELECT MIN(date) AS d FROM raw_prices WHERE ticker = ?", (ticker,)
        ).fetchone()
        return date.fromisoformat(row["d"]) if row and row["d"] else None

    # --- 정규화 재무 -------------------------------------------------

    _FIN_COLUMNS = (
        "ticker", "fiscal_year", "currency", "revenue", "operating_income",
        "net_income", "total_assets", "total_liabilities", "total_equity",
        "current_assets", "current_liabilities", "interest_expense",
        "operating_cash_flow", "capex", "shares_outstanding", "dividend_per_share",
        "shares_treasury", "audit_opinion",
    )

    def save_financials(self, records: list[Financials]) -> None:
        extra = ("missing_fields", "proxy_fields")
        cols = ", ".join(self._FIN_COLUMNS + extra)
        placeholders = ", ".join("?" * (len(self._FIN_COLUMNS) + len(extra)))
        updates = ", ".join(
            f"{c}=excluded.{c}" for c in self._FIN_COLUMNS[2:] + extra
        )
        self.conn.executemany(
            f"""
            INSERT INTO financials ({cols}) VALUES ({placeholders})
            ON CONFLICT(ticker, fiscal_year) DO UPDATE SET {updates}
            """,
            [
                tuple(getattr(r, c) for c in self._FIN_COLUMNS)
                + (
                    json.dumps(r.missing_fields, ensure_ascii=False),
                    json.dumps(r.proxy_fields, ensure_ascii=False),
                )
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
            Financials(
                **{c: row[c] for c in self._FIN_COLUMNS},
                # 계획 C 이전 행은 이 컬럼이 NULL이다. json.loads(None)은 터진다.
                proxy_fields=json.loads(row["proxy_fields"] or "[]"),
            )
            for row in rows
        ]

    def missing_field_counts(self) -> dict[str, int]:
        """정규화 품질 보고용. 어떤 계정과목 매핑이 자주 실패하는지 보여준다."""
        counts: dict[str, int] = {}
        for row in self.conn.execute("SELECT missing_fields FROM financials"):
            for field in json.loads(row["missing_fields"] or "[]"):
                counts[field] = counts.get(field, 0) + 1
        return counts

    # --- 스냅샷 -------------------------------------------------------

    def save_snapshot(self, snapshot: Snapshot, results: list[AnalysisResult]) -> int:
        """스냅샷과 결과를 한 트랜잭션으로 쓴다. 실패하면 아무것도 남지 않는다.

        설계서 §13이 "배치 중 예외 → 스냅샷을 커밋하지 않는다. 직전
        스냅샷이 유지되므로 화면은 계속 동작한다"를 요구한다.

        범용 transaction() 컨텍스트 매니저를 만들지 않는 이유: SQLite는
        중첩 트랜잭션을 지원하지 않으므로, 안쪽 메서드가 commit()을 부르는
        한 바깥 트랜잭션까지 함께 커밋돼 원자성이 깨진다. 스냅샷 쓰기는
        개념적으로 한 번의 연산이므로 전용 메서드로 둔다.
        """
        try:
            cur = self.conn.execute(
                "INSERT INTO snapshots "
                "(run_at, as_of, config_hash, config_json, note, market) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (snapshot.run_at, snapshot.as_of, snapshot.config_hash,
                 snapshot.config_json, snapshot.note, snapshot.market),
            )
            snapshot_id = cur.lastrowid

            self.conn.executemany(
                """
                INSERT INTO analysis_results (
                    snapshot_id, ticker, passed_filters, filter_failures,
                    score_total, score_profit, score_growth, score_stability,
                    score_cashflow, score_detail,
                    price_current, avg_price_1y, avg_price_3y, avg_price_5y,
                    high_5y, drop_vs_avg_1y, drop_vs_avg_3y, drop_vs_avg_5y,
                    drop_vs_high, market_cap, per, pbr,
                    signal_grade, signal_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?, ?, ?)
                """,
                [self._result_row(snapshot_id, r) for r in results],
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return snapshot_id

    @staticmethod
    def _result_row(snapshot_id: int, r: AnalysisResult) -> tuple:
        s = r.stats
        return (
            snapshot_id, r.ticker, int(r.passed_filters),
            json.dumps(r.filters.failures, ensure_ascii=False),
            r.score_total, r.score_profit, r.score_growth, r.score_stability,
            r.score_cashflow, r.score_detail,
            s.price_current, s.avg_price.get(1), s.avg_price.get(3),
            s.avg_price.get(5), s.high_5y,
            s.drop_vs_avg.get(1), s.drop_vs_avg.get(3), s.drop_vs_avg.get(5),
            s.drop_vs_high,
            r.valuation.market_cap, r.valuation.per, r.valuation.pbr,
            r.signal_grade, r.signal_reason,
        )

    def get_latest_snapshot(self, market: str | None = None) -> Snapshot | None:
        """가장 최근 스냅샷. market을 넘기면 그 시장의 것만 본다.

        화면과 API는 항상 market을 넘긴다 - 미국을 나중에 분석했다고
        한국 화면이 미국 결과를 보여주면 안 된다. market을 생략하면 전
        시장에서 가장 최근 것이다 (latest_price_date_any와 같은 규칙).
        """
        if market:
            row = self.conn.execute(
                "SELECT * FROM snapshots WHERE market = ? "
                "ORDER BY snapshot_id DESC LIMIT 1",
                (market,),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM snapshots ORDER BY snapshot_id DESC LIMIT 1"
            ).fetchone()
        if not row:
            return None
        return Snapshot(
            snapshot_id=row["snapshot_id"], run_at=row["run_at"],
            as_of=row["as_of"],
            config_hash=row["config_hash"], config_json=row["config_json"],
            note=row["note"], market=row["market"],
        )

    def get_analysis_results(self, snapshot_id: int) -> list[dict]:
        """스냅샷의 결과를 원본 행 그대로 반환한다.

        AnalysisResult로 되돌리지 않는 이유: 읽는 쪽(요약 출력, 나중의
        웹 API)이 컬럼을 그대로 쓰는 편이 단순하고, 창 매핑을 다시
        조립할 이유가 없다.
        """
        rows = self.conn.execute(
            "SELECT * FROM analysis_results WHERE snapshot_id = ? ORDER BY ticker",
            (snapshot_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def trading_dates(
        self, start: date, end: date, market: str | None = None
    ) -> list[date]:
        """수집된 종목들의 날짜 합집합. 거래소 달력 대신 쓴다.

        별도 거래일 달력을 두지 않는 이유: 이미 수집한 데이터가 곧
        관측된 거래일이고, 휴장일 정의를 따로 관리하면 소스와 어긋난다.

        market을 넘기면 그 시장 종목의 봉만 본다. 시장마다 휴장일이
        다르다 - 미국 장이 열린 한국 공휴일이 한국 달력에 섞이면, 한국
        종목 전부가 그날 봉이 빠진 것처럼 보여 커버리지가 깎이고 평균이
        사라질 수 있다. analyze_market은 항상 분석 중인 시장을 넘긴다.
        market을 생략하면 전 시장의 합집합이다 (latest_price_date_any와
        같은 이유로 무필터 동작을 남긴다).
        """
        if market:
            rows = self.conn.execute(
                """
                SELECT DISTINCT rp.date
                FROM raw_prices rp
                JOIN securities s ON s.ticker = rp.ticker
                WHERE s.market = ? AND rp.date BETWEEN ? AND ?
                ORDER BY rp.date
                """,
                (market, start.isoformat(), end.isoformat()),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT DISTINCT date FROM raw_prices WHERE date BETWEEN ? AND ? "
                "ORDER BY date",
                (start.isoformat(), end.isoformat()),
            ).fetchall()
        return [date.fromisoformat(r["date"]) for r in rows]

    def latest_price_date_any(self, market: str | None = None) -> date | None:
        """가장 최근 거래일. 분석 기준일(as_of)이 된다.

        market을 넘기면 그 시장의 종목에 달린 봉만 본다. 시장마다 거래
        캘린더가 다르다 — 미국 장은 한국 장보다 늦게 마감하므로, market을
        구분하지 않으면 US 종목의 최신 봉 날짜가 KR 분석의 as_of를 밀어
        버려 KR 종목 전부가 아직 그 시장에 존재하지 않는 날짜를 기준으로
        평가된다(실측: US 종목 봉 하나로 재현됨). analyze_market은 항상
        분석 중인 시장을 넘긴다.

        market을 생략하면(None) 시장 구분 없이 전체 최댓값을 낸다 — 이
        무필터 동작은 유지한다. list_securities와 달리 raw_prices는
        securities 없이도 채워질 수 있으므로(가격만 먼저 수집하는 흐름),
        시장을 강제하면 그 데이터를 볼 방법이 없어진다.
        """
        if market:
            row = self.conn.execute(
                """
                SELECT MAX(rp.date) AS d
                FROM raw_prices rp
                JOIN securities s ON s.ticker = rp.ticker
                WHERE s.market = ?
                """,
                (market,),
            ).fetchone()
        else:
            row = self.conn.execute("SELECT MAX(date) AS d FROM raw_prices").fetchone()
        return date.fromisoformat(row["d"]) if row and row["d"] else None

    # --- 관심종목 -----------------------------------------------------

    def watchlist_tickers(self) -> set[str]:
        rows = self.conn.execute("SELECT ticker FROM watchlist").fetchall()
        return {r["ticker"] for r in rows}

    def add_to_watchlist(self, ticker: str, memo: str | None = None) -> None:
        """이미 있으면 아무것도 하지 않는다. 별 토글은 멱등해야 한다."""
        self.conn.execute(
            "INSERT INTO watchlist (ticker, added_at, memo) VALUES (?, ?, ?) "
            "ON CONFLICT(ticker) DO NOTHING",
            (ticker, _now(), memo),
        )
        self.conn.commit()

    def remove_from_watchlist(self, ticker: str) -> None:
        self.conn.execute("DELETE FROM watchlist WHERE ticker = ?", (ticker,))
        self.conn.commit()

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
