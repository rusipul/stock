"""FastAPI 앱. 저장된 스냅샷을 읽기만 한다.

계산하지 않는다 — analysis/·sources/·pipeline/을 import하지 않는 이유다.
화면에 보이는 모든 숫자는 analyze가 이미 계산해 스냅샷에 넣은 것이다.
그래야 "화면의 숫자가 어느 실행에서 나왔는가"가 항상 명확하다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from stockscout.store.db import DEFAULT_DB_PATH, connect
from stockscout.store.repository import Repository
from stockscout.web.schemas import SecurityRow, SnapshotInfo

STATIC_DIR = Path(__file__).parent / "static"


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

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
