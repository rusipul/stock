"""FastAPI 앱. 저장된 스냅샷을 읽기만 한다.

계산하지 않는다 — analysis/·sources/·pipeline/을 import하지 않는 이유다.
화면에 보이는 모든 숫자는 analyze가 이미 계산해 스냅샷에 넣은 것이다.
그래야 "화면의 숫자가 어느 실행에서 나왔는가"가 항상 명확하다.
"""

from __future__ import annotations

import json
from datetime import date
from enum import IntEnum
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from stockscout.store.db import DEFAULT_DB_PATH, connect
from stockscout.store.repository import Repository
from stockscout.web.schemas import (
    FinancialRow,
    PricePoint,
    PriceSeries,
    SecurityDetail,
    SecurityRow,
    SnapshotInfo,
)

STATIC_DIR = Path(__file__).parent / "static"


class ChartWindow(IntEnum):
    """차트에 그릴 구간(년). 스키마가 허용값을 스스로 문서화한다.

    Literal[1, 3, 5]를 쓰면 안 된다. 이 FastAPI/pydantic 조합에서
    쿼리스트링 "1"이 int 리터럴 1로 캐스팅되지 않아 1·3·5가 전부
    422로 거부된다(실측 확인). IntEnum은 정상 캐스팅되고 OpenAPI
    스키마에도 허용값이 남는다.
    """

    ONE = 1
    THREE = 3
    FIVE = 5


def create_app(db_path: Path | str = DEFAULT_DB_PATH) -> FastAPI:
    """앱을 만든다. db_path를 주입받아 테스트가 임시 DB를 쓸 수 있게 한다."""
    app = FastAPI(title="stockscout")
    db_path = Path(db_path)

    def read(fn):
        """요청마다 연결을 열고 닫는다.

        SQLite 연결은 스레드를 넘나들 수 없고, 이 앱은 읽기 전용이라
        연결 풀링이 필요할 만큼 무겁지 않다.
        """
        with connect(db_path) as conn:
            return fn(Repository(conn))

    @app.get("/api/snapshot", response_model=SnapshotInfo)
    def get_snapshot() -> SnapshotInfo:
        def build(repo: Repository) -> SnapshotInfo:
            snap = repo.get_latest_snapshot()
            if snap is None:
                raise HTTPException(
                    status_code=404,
                    detail="분석 결과가 없습니다. stockscout analyze 를 먼저 실행하세요.",
                )
            rows = repo.get_analysis_results(snap.snapshot_id)
            return SnapshotInfo(
                snapshot_id=snap.snapshot_id,
                run_at=snap.run_at,
                as_of=snap.as_of,
                config_hash=snap.config_hash,
                note=snap.note,
                total=len(rows),
                passed=sum(1 for r in rows if r["passed_filters"]),
                excluded=sum(1 for r in rows if not r["passed_filters"]),
                has_scores=any(r["score_total"] is not None for r in rows),
                has_valuation=any(r["per"] is not None for r in rows),
            )

        return read(build)

    @app.get("/api/securities", response_model=list[SecurityRow])
    def list_securities(
        q: str | None = None,
        sort: Literal["drop", "name", "ticker"] = "drop",
    ) -> list[SecurityRow]:
        """스냅샷의 전 종목을 한 번에 내려보낸다.

        페이지네이션을 두지 않는 이유: 유니버스가 수백 종목이고, 프론트가
        슬라이더로 즉시 재판정하려면 어차피 전부 필요하다(설계서 §6.1).
        서버 왕복을 없애는 것이 이 화면의 핵심 반응성이다.
        """
        def build(repo: Repository) -> list[SecurityRow]:
            snap = repo.get_latest_snapshot()
            if snap is None:
                raise HTTPException(
                    status_code=404,
                    detail="분석 결과가 없습니다. stockscout analyze 를 먼저 실행하세요.",
                )
            masters = {s.ticker: s for s in repo.list_securities(in_universe_only=False)}
            watched = repo.watchlist_tickers()

            rows = []
            for r in repo.get_analysis_results(snap.snapshot_id):
                master = masters.get(r["ticker"])
                rows.append(SecurityRow(
                    ticker=r["ticker"],
                    name=master.name if master else r["ticker"],
                    exchange=master.exchange if master else None,
                    is_financial=bool(master.is_financial) if master else False,
                    passed_filters=bool(r["passed_filters"]),
                    filter_failures=json.loads(r["filter_failures"] or "[]"),
                    price_current=r["price_current"],
                    avg_price_1y=r["avg_price_1y"],
                    avg_price_3y=r["avg_price_3y"],
                    avg_price_5y=r["avg_price_5y"],
                    high_5y=r["high_5y"],
                    drop_vs_avg_1y=r["drop_vs_avg_1y"],
                    drop_vs_avg_3y=r["drop_vs_avg_3y"],
                    drop_vs_avg_5y=r["drop_vs_avg_5y"],
                    drop_vs_high=r["drop_vs_high"],
                    score_total=r["score_total"],
                    market_cap=r["market_cap"],
                    per=r["per"],
                    pbr=r["pbr"],
                    in_watchlist=r["ticker"] in watched,
                ))

            if q:
                needle = q.lower()
                rows = [x for x in rows
                        if needle in x.name.lower() or needle in x.ticker.lower()]

            if sort == "drop":
                # 하락률이 없는 종목(커버리지 미달)은 항상 뒤로 보낸다.
                rows.sort(key=lambda x: (x.drop_vs_avg_5y is None,
                                         x.drop_vs_avg_5y or 0.0))
            elif sort == "name":
                rows.sort(key=lambda x: x.name)
            else:
                rows.sort(key=lambda x: x.ticker)

            return rows

        return read(build)

    @app.get("/api/securities/{ticker}", response_model=SecurityDetail)
    def get_security(ticker: str) -> SecurityDetail:
        def build(repo: Repository) -> SecurityDetail:
            snap = repo.get_latest_snapshot()
            if snap is None:
                raise HTTPException(status_code=404, detail="분석 결과가 없습니다.")
            row = next(
                (r for r in repo.get_analysis_results(snap.snapshot_id)
                 if r["ticker"] == ticker),
                None,
            )
            if row is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"{ticker} 는 최신 스냅샷에 없습니다.",
                )
            master = repo.get_security(ticker)
            failures = json.loads(row["filter_failures"] or "[]")

            return SecurityDetail(
                ticker=ticker,
                name=master.name if master else ticker,
                exchange=master.exchange if master else None,
                is_financial=bool(master.is_financial) if master else False,
                currency=master.currency if master else "KRW",
                passed_filters=bool(row["passed_filters"]),
                filter_failures=failures,
                price_current=row["price_current"],
                avg_price_1y=row["avg_price_1y"],
                avg_price_3y=row["avg_price_3y"],
                avg_price_5y=row["avg_price_5y"],
                high_5y=row["high_5y"],
                drop_vs_avg_1y=row["drop_vs_avg_1y"],
                drop_vs_avg_3y=row["drop_vs_avg_3y"],
                drop_vs_avg_5y=row["drop_vs_avg_5y"],
                drop_vs_high=row["drop_vs_high"],
                score_total=row["score_total"],
                market_cap=row["market_cap"],
                per=row["per"],
                pbr=row["pbr"],
                in_watchlist=ticker in repo.watchlist_tickers(),
                rationale=_rationale(row, failures),
                score_detail=row["score_detail"],
                financials=[
                    FinancialRow(
                        fiscal_year=f.fiscal_year,
                        revenue=f.revenue,
                        operating_income=f.operating_income,
                        net_income=f.net_income,
                        operating_margin=f.operating_margin,
                        roe=f.roe,
                        debt_ratio=f.debt_ratio,
                        operating_cash_flow=f.operating_cash_flow,
                    )
                    for f in repo.get_financials(ticker)
                ],
            )

        return read(build)

    @app.get("/api/securities/{ticker}/prices", response_model=PriceSeries)
    def get_prices(
        ticker: str,
        years: ChartWindow = ChartWindow.FIVE,
    ) -> PriceSeries:
        def build(repo: Repository) -> PriceSeries:
            snap = repo.get_latest_snapshot()
            if snap is None:
                raise HTTPException(status_code=404, detail="분석 결과가 없습니다.")
            row = next(
                (r for r in repo.get_analysis_results(snap.snapshot_id)
                 if r["ticker"] == ticker),
                None,
            )
            if row is None:
                raise HTTPException(
                    status_code=404, detail=f"{ticker} 는 최신 스냅샷에 없습니다.")

            as_of = date.fromisoformat(snap.as_of) if snap.as_of else None
            start = _window_start(as_of, years) if as_of else None
            bars = repo.get_price_bars(ticker, start=start, end=as_of)
            if not bars:
                raise HTTPException(
                    status_code=404, detail=f"{ticker} 의 주가 데이터가 없습니다.")

            return PriceSeries(
                ticker=ticker,
                as_of=snap.as_of,
                series=[PricePoint(date=b.date.isoformat(), close=b.close)
                        for b in bars],
                price_current=row["price_current"],
                avg_price_1y=row["avg_price_1y"],
                avg_price_3y=row["avg_price_3y"],
                avg_price_5y=row["avg_price_5y"],
                high_5y=row["high_5y"],
            )

        return read(build)

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


def _window_start(as_of: date, years: int) -> date:
    """as_of로부터 years년 전. 달력 기준이다.

    analysis.valuation.window_start와 같은 계산이지만 여기서 다시 쓴다.
    web/은 analysis/를 import하지 않는다 — 웹은 계산 계층이 아니라
    저장된 것을 보여주는 계층이고, 이 함수는 차트 구간을 자르기 위한
    표시용 계산이지 판정에 쓰이는 값이 아니다.
    """
    try:
        return as_of.replace(year=as_of.year - years)
    except ValueError:  # 2월 29일
        return as_of.replace(year=as_of.year - years, day=28)


def _rationale(row: dict, failures: list[str]) -> list[str]:
    """판정 근거를 실제 숫자가 담긴 문장으로 만든다 (설계서 §11.2).

    점수만 던지는 화면은 실제 투자 판단에 쓸 수 없다. 계산식을 그대로
    보여줘야 사용자가 검증할 수 있다.

    기호를 쓰지 않는다. 이 문자열은 웹으로 가지만 CLI로도 갈 수 있고,
    한국어 Windows 콘솔은 cp949라 체크표시 같은 문자를 출력하면 죽는다.
    """
    lines: list[str] = []

    if failures:
        lines.append(f"[제외] 하드필터 {len(failures)}건에 걸렸습니다: "
                     + ", ".join(failures))
    else:
        lines.append("[통과] 가격 기반 하드필터를 모두 통과했습니다 "
                     "(금융업 아님, 5년 이력 충족)")

    for years in (1, 3, 5):
        avg = row[f"avg_price_{years}y"]
        drop = row[f"drop_vs_avg_{years}y"]
        if avg is None or drop is None:
            lines.append(f"{years}년 평균 대비: 데이터 커버리지 미달로 계산하지 않음")
            continue
        lines.append(
            f"{years}년 평균 대비  {row['price_current']:,.0f}"
            f" / {avg:,.0f} - 1 = {drop * 100:.1f}%"
        )

    lines.append(
        f"5년 고점 대비  {row['price_current']:,.0f}"
        f" / {row['high_5y']:,.0f} - 1 = {row['drop_vs_high'] * 100:.1f}%"
    )

    if row["score_total"] is None:
        lines.append("우량주 점수: 재무 데이터가 없어 계산되지 않았습니다")

    return lines
