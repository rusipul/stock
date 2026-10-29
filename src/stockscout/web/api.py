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

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from stockscout.models import AXIS_UNMEASURABLE, Financials, InterimResults
from stockscout.normalize.filings import classify_filing, is_noise
from stockscout.store.db import DEFAULT_DB_PATH, connect, init_db
from stockscout.store.repository import Repository
from stockscout.web.schemas import (
    FilingRow,
    InterimRow,
    FinancialRow,
    PricePoint,
    PriceSeries,
    SecurityDetail,
    SecurityRow,
    SnapshotInfo,
)

STATIC_DIR = Path(__file__).parent / "static"
FILINGS_SHOWN = 15

Market = Literal["KR", "US"]


def _no_snapshot(market: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail=(f"{market} 분석 결과가 없습니다. "
                f"stockscout analyze --market {market} 를 먼저 실행하세요."),
    )


def _snapshot_for_ticker(repo: Repository, ticker: str):
    """티커의 시장에서 최신 스냅샷과 그 종목의 행을 찾는다.

    상세·차트 API는 티커만 받는다. 시장을 모르고 전체 최신 스냅샷을 보면
    미국을 나중에 분석한 순간 한국 종목이 전부 404가 된다.
    """
    master = repo.get_security(ticker)
    market = master.market if master else "KR"
    snap = repo.get_latest_snapshot(market=market)
    if snap is None:
        raise _no_snapshot(market)
    row = next(
        (r for r in repo.get_analysis_results(snap.snapshot_id)
         if r["ticker"] == ticker),
        None,
    )
    if row is None:
        raise HTTPException(status_code=404,
                            detail=f"{ticker} 는 최신 스냅샷에 없습니다.")
    return master, snap, row


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
    # 스키마를 한 번 맞춘다. 새 기능이 테이블·컬럼을 더한 뒤 analyze를 아직
    # 돌리지 않은 DB로 serve해도 죽지 않게 한다. CREATE IF NOT EXISTS와
    # ADD COLUMN뿐이라 데이터는 건드리지 않는다.
    with connect(db_path) as conn:
        init_db(conn)

    def read(fn):
        """요청마다 연결을 열고 닫는다.

        SQLite 연결은 스레드를 넘나들 수 없고, 이 앱은 읽기 전용이라
        연결 풀링이 필요할 만큼 무겁지 않다.
        """
        with connect(db_path) as conn:
            return fn(Repository(conn))

    @app.get("/api/snapshot", response_model=SnapshotInfo)
    def get_snapshot(market: Market = "KR") -> SnapshotInfo:
        def build(repo: Repository) -> SnapshotInfo:
            snap = repo.get_latest_snapshot(market=market)
            if snap is None:
                raise _no_snapshot(market)
            rows = repo.get_analysis_results(snap.snapshot_id)
            return SnapshotInfo(
                snapshot_id=snap.snapshot_id,
                market=snap.market,
                run_at=snap.run_at,
                as_of=snap.as_of,
                config_hash=snap.config_hash,
                note=snap.note,
                total=len(rows),
                passed=sum(1 for r in rows if r["passed_filters"]),
                excluded=sum(1 for r in rows if not r["passed_filters"]),
                has_scores=any(r["score_total"] is not None for r in rows),
                has_valuation=any(r["per"] is not None for r in rows),
                **_signal_settings(snap.config_json),
            )

        return read(build)

    @app.get("/api/securities", response_model=list[SecurityRow])
    def list_securities(
        market: Market = "KR",
        q: str | None = None,
        sort: Literal["drop", "name", "ticker"] = "drop",
    ) -> list[SecurityRow]:
        """스냅샷의 전 종목을 한 번에 내려보낸다.

        페이지네이션을 두지 않는 이유: 유니버스가 수백 종목이고, 프론트가
        슬라이더로 즉시 재판정하려면 어차피 전부 필요하다(설계서 §6.1).
        서버 왕복을 없애는 것이 이 화면의 핵심 반응성이다.
        """
        def build(repo: Repository) -> list[SecurityRow]:
            snap = repo.get_latest_snapshot(market=market)
            if snap is None:
                raise _no_snapshot(market)
            masters = {s.ticker: s for s in
                       repo.list_securities(market=market, in_universe_only=False)}
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
                    window_stats=_window_stats(r),
                    drawdown=_drawdown(r),
                    benchmark=_benchmark(r),
                    **_axis_fields(r),
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
            master, snap, row = _snapshot_for_ticker(repo, ticker)
            failures = json.loads(row["filter_failures"] or "[]")
            financials = repo.get_financials(ticker)

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
                window_stats=_window_stats(row),
                drawdown=_drawdown(row),
                benchmark=_benchmark(row),
                **_axis_fields(row),
                interim=_interim(row),
                filings=[
                    FilingRow(filed=f.filed.isoformat(), title=f.title, url=f.url,
                              category=classify_filing(f))
                    for f in [f for f in repo.get_filings(ticker)
                              if not is_noise(f)][:FILINGS_SHOWN]
                ],
                score_total=row["score_total"],
                market_cap=row["market_cap"],
                per=row["per"],
                pbr=row["pbr"],
                in_watchlist=ticker in repo.watchlist_tickers(),
                rationale=_rationale(row, failures, financials,
                                     currency=master.currency if master else "KRW"),
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
                    for f in financials
                ],
            )

        return read(build)

    @app.get("/api/securities/{ticker}/prices", response_model=PriceSeries)
    def get_prices(
        ticker: str,
        years: ChartWindow = ChartWindow.FIVE,
    ) -> PriceSeries:
        def build(repo: Repository) -> PriceSeries:
            _, snap, row = _snapshot_for_ticker(repo, ticker)
            as_of = date.fromisoformat(snap.as_of) if snap.as_of else None
            start = _window_start(as_of, years) if as_of else None
            bars = repo.get_price_bars(ticker, start=start, end=as_of)
            if not bars:
                raise HTTPException(
                    status_code=404, detail=f"{ticker} 의 주가 데이터가 없습니다.")

            return PriceSeries(
                ticker=ticker,
                as_of=snap.as_of,
                series=[PricePoint(date=b.date.isoformat(), close=b.close,
                                   volume=b.volume, open=b.open, high=b.high, low=b.low)
                        for b in bars],
                price_current=row["price_current"],
                avg_price_1y=row["avg_price_1y"],
                avg_price_3y=row["avg_price_3y"],
                avg_price_5y=row["avg_price_5y"],
                high_5y=row["high_5y"],
            )

        return read(build)

    @app.get("/api/watchlist", response_model=list[str])
    def get_watchlist() -> list[str]:
        return read(lambda repo: sorted(repo.watchlist_tickers()))

    @app.post("/api/watchlist/{ticker}", status_code=204)
    def add_watchlist(ticker: str) -> Response:
        def run(repo: Repository) -> None:
            if repo.get_security(ticker) is None:
                raise HTTPException(
                    status_code=404, detail=f"{ticker} 는 종목 목록에 없습니다.")
            repo.add_to_watchlist(ticker)

        read(run)
        return Response(status_code=204)

    @app.delete("/api/watchlist/{ticker}", status_code=204)
    def remove_watchlist(ticker: str) -> Response:
        read(lambda repo: repo.remove_from_watchlist(ticker))
        return Response(status_code=204)

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


def _drawdown_json(row) -> dict:
    stored = row.get("drawdown_stats")
    return json.loads(stored) if stored else {}


def _drawdown(row) -> dict[int, dict]:
    """개월 -> 단기 하락. 이 컬럼 이전 스냅샷은 빈 dict다(화면은 등급을 비운다)."""
    return {int(k): v for k, v in _drawdown_json(row).get("windows", {}).items()}


def _interim(row) -> InterimRow | None:
    stored = row.get("interim")
    if not stored:
        return None
    r = InterimResults.model_validate_json(stored)
    return InterimRow(
        period_end=r.period_end.isoformat(), months=r.months,
        revenue_growth=r.revenue_growth, operating_income_growth=r.operating_income_growth,
        operating_margin=r.operating_margin, operating_margin_prior=r.operating_margin_prior,
        deteriorated=r.deteriorated,
    )


def _breakout(row) -> dict:
    stored = json.loads(row.get("breakout_stats") or "{}")
    return {
        "breakout": {int(k): v for k, v in (stored.get("lookbacks") or {}).items()},
        "breakout_signal": stored.get("signal"),
    }


def _axis_fields(row) -> dict:
    return {
        "score_profit": row.get("score_profit"),
        "score_growth": row.get("score_growth"),
        "score_stability": row.get("score_stability"),
        "score_cashflow": row.get("score_cashflow"),
        "weak_axes": json.loads(row.get("weak_axes") or "[]"),
        "fcf_yield": row.get("fcf_yield"),
        **_breakout(row),
    }


def _benchmark(row) -> str | None:
    return _drawdown_json(row).get("benchmark")


def _signal_settings(config_json: str) -> dict:
    """스냅샷을 만든 설정의 하락 기준. 화면 슬라이더의 기본값이 된다.

    스냅샷의 config_json에서 읽는다 - 지금 settings.yaml이 아니라 그 실행이
    무엇으로 판정했는지가 화면과 맞아야 한다. 없으면 SignalConfig 기본값.
    """
    from stockscout.config import BreakoutConfig, SignalConfig

    stored = json.loads(config_json or "{}") or {}
    signal = SignalConfig.model_validate(stored.get("signal") or {})
    breakout = BreakoutConfig.model_validate(stored.get("breakout") or {})
    return {"drawdown_months": signal.drawdown_months,
            "drawdown_threshold": signal.drawdown_threshold,
            "drawdown_choices": signal.drawdown_choices,
            "breakout": breakout.model_dump()}


def _window_stats(row) -> dict[int, dict]:
    """저장된 기간별 통계. 그 컬럼이 생기기 전 스냅샷은 1·3·5년 컬럼으로 메운다.

    옛 행에는 1·3년 고점이 없다(5년 고점만 저장했다). 없는 값은 None으로
    두고 화면이 "—"로 보인다 - 5년 고점으로 대신하면 다른 숫자가 된다.
    """
    # .get: 마이그레이션 전 DB(init_db를 거치지 않고 연 DB)에는 컬럼이 없다.
    stored = row.get("window_stats")
    if stored:
        return {int(k): v for k, v in json.loads(stored).items()}
    out = {}
    for years in (1, 3, 5):
        out[years] = {
            "avg_price": row[f"avg_price_{years}y"],
            "drop_vs_avg": row[f"drop_vs_avg_{years}y"],
            "high": row["high_5y"] if years == 5 else None,
            "drop_vs_high": row["drop_vs_high"] if years == 5 else None,
        }
    return out


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


def _rationale(
    row: dict, failures: list[str], financials: list[Financials],
    currency: str = "KRW",
) -> list[str]:
    """판정 근거를 실제 숫자가 담긴 문장으로 만든다 (설계서 §11.2).

    점수만 던지는 화면은 실제 투자 판단에 쓸 수 없다. 계산식을 그대로
    보여줘야 사용자가 검증할 수 있다.

    기호를 쓰지 않는다. 이 문자열은 웹으로 가지만 CLI로도 갈 수 있고,
    한국어 Windows 콘솔은 cp949라 체크표시 같은 문자를 출력하면 죽는다.
    """
    lines: list[str] = []
    # 달러는 센트까지. 정수로 찍으면 $249.76 / $268.83이 250 / 269가 되어
    # 사용자가 계산을 다시 해볼 수 없다.
    price = ",.2f" if currency == "USD" else ",.0f"

    if failures:
        lines.append(f"[제외] 하드필터 {len(failures)}건에 걸렸습니다: "
                     + ", ".join(failures))
    else:
        lines.append("[통과] 하드필터를 모두 통과했습니다 "
                     "(가격·재무 기반)")

    for years in (1, 3, 5):
        avg = row[f"avg_price_{years}y"]
        drop = row[f"drop_vs_avg_{years}y"]
        if avg is None or drop is None:
            lines.append(f"{years}년 평균 대비: 데이터 커버리지 미달로 계산하지 않음")
            continue
        lines.append(
            f"{years}년 평균 대비  {row['price_current']:{price}}"
            f" / {avg:{price}} - 1 = {drop * 100:.1f}%"
        )

    lines.append(
        f"5년 고점 대비  {row['price_current']:{price}}"
        f" / {row['high_5y']:{price}} - 1 = {row['drop_vs_high'] * 100:.1f}%"
    )

    if row["score_total"] is None:
        # 계획 C부터 점수가 없는 것은 하드필터에 걸렸다는 뜻이다. 계획 C
        # 이전 스냅샷은 통과 종목도 점수가 없으므로 그때만 옛 문장을 쓴다.
        if failures:
            lines.append("우량주 점수: 하드필터에 걸려 채점하지 않았습니다")
        else:
            lines.append("우량주 점수: 재무 데이터가 없어 계산되지 않았습니다")

    lines.extend(_score_lines(row))
    lines.extend(_proxy_lines(financials))
    if row.get("signal_reason"):
        lines.append(f"신호 판정: {row['signal_reason']}")
    return lines


AXIS_LABELS = {
    "profitability": "수익성",
    "growth": "성장성",
    "stability": "안정성",
    "cashflow": "현금흐름",
}


def _score_lines(row: dict) -> list[str]:
    """점수 산출 근거. 축별 점수와, 못 잰 축이 있으면 그 사실.

    점수가 없으면 빈 목록을 돌려준다 - 제외된 종목에 "수익성 0점"을
    쓰면 사업 평가로 읽힌다. 실제로는 채점하지 않은 것이다.
    """
    if row.get("score_total") is None or not row.get("score_detail"):
        return []

    try:
        card = json.loads(row["score_detail"])
    except (TypeError, ValueError):
        return []

    lines = [f"우량주 점수 {row['score_total']:.0f}점 / 100"]
    for axis in card.get("axes", []):
        label = AXIS_LABELS.get(axis.get("axis"), axis.get("axis", ""))
        earned = axis.get("earned", 0.0)
        denominator = axis.get("denominator", 0.0)
        max_points = axis.get("max_points", 25.0)
        if axis.get("status") == AXIS_UNMEASURABLE:
            lines.append(
                f"  {label} 0.0점 - 지표를 절반도 못 구해 측정 불가로 0점 처리"
            )
            continue
        scaled = 0.0 if denominator <= 0 else earned / denominator * max_points
        lines.append(
            f"  {label} {scaled:.1f}점 "
            f"(획득 {earned:.1f} / 유효만점 {denominator:.1f})"
        )
    return lines


def _proxy_lines(financials: list[Financials]) -> list[str]:
    """대체 계정을 쓴 항목을 밝힌다 (설계서 §17.3).

    이자보상배율이 금융비용으로 계산됐다면 사용자가 그것을 알아야 한다.
    금융비용은 이자비용에 환차손·파생손실이 더해진 상위 개념이라 배율이
    실제보다 낮게 나온다.
    """
    if not financials:
        return []
    proxies = financials[-1].proxy_fields
    lines = []
    if "operating_income" in proxies:
        lines.append("영업이익을 보고하지 않는 회사라 세전이익으로 대신했습니다. "
                     "영업외손익이 섞여 이익률·성장률이 근사값입니다")
    if "interest_expense" in proxies:
        lines.append("이자비용을 찾지 못해 금융비용으로 대신했습니다. "
                     "이자보상배율이 실제보다 낮게 계산됩니다")
    if "shares_outstanding" in proxies:
        lines.append("기말 주식수를 찾지 못해 가중평균 주식수로 대신했습니다. "
                     "시가총액·PBR은 근사값입니다")
    return lines
