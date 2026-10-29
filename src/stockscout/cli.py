"""명령줄 진입점."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta

from stockscout.config import DEFAULT_CONFIG_PATH, load_config, load_secrets
from stockscout.pipeline.analyze import analyze_market
from stockscout.pipeline.collect import collect_market, sync_universe
from stockscout.pipeline.normalize import normalize_market
from stockscout.sources.dart import DartSource
from stockscout.sources.edgar import EdgarSource
from stockscout.sources.krx import KrxPriceSource, KrxUniverse, to_ticker
from stockscout.sources.universe import YamlUniverse, load_cik_overrides
from stockscout.sources.yahoo import YahooPriceSource
from stockscout.store.db import DEFAULT_DB_PATH, connect, init_db
from stockscout.store.repository import Repository

UNIVERSE_SOURCES = ("yaml", "krx")

# 주가 수집 시작일을 "어제 - N년"보다 이만큼 앞당긴다. 분석의 기준일(as_of)은
# 어제가 아니라 마지막 봉의 날짜라 주말·휴장·미확정 봉만큼 이르다. 시작일을
# 딱 맞추면 가격 필터의 요건 시작일(as_of - N년)이 첫 봉보다 앞서 전 종목이
# INSUFFICIENT_HISTORY가 된다(실측: 2026-10-29 미국 40종목 전부). 긴 연휴
# (설·추석)를 덮는 2주다. 평균은 analyze가 as_of 기준 창으로 잘라 읽으므로
# 앞당긴 봉이 섞이지 않는다.
PRICE_START_MARGIN_DAYS = 14


def price_window(years: int, today: date | None = None) -> tuple[date, date]:
    """수집할 주가 구간. 오늘은 제외한다.

    장중에 수집하면 "오늘 종가"가 확정 종가가 아니라 그 순간의 체결가다.
    실측: 2026-09-11 11:24에 삼성전자를 두 번 수집했더니 거래량이
    6,294,923 -> 6,302,129로 늘어나는 중이었고, 몇 분 전 종가는
    259,750이었는데 258,000으로 바뀌어 있었다.

    그 봉을 담으면 설계서 §6.1이 약속한 "같은 날짜 결과가 항상 동일하게
    재현된다"가 깨진다. 11시에 계산한 하락률과 14시에 계산한 하락률이
    달라지고 ★★/★ 등급까지 바뀐다. 개장 직후에 돌리면 시가 한 틱이
    "현재가"가 된다.

    그래서 하루 늦은 확정 종가를 쓴다. 5년 평균 대비 -40%를 재는
    도구에서 장중 몇 시간의 노이즈는 신호와 무관하다. 주말·공휴일이면
    해당 날짜에 봉이 없을 뿐이므로 거래일 계산은 따로 하지 않는다.
    """
    end = (today or date.today()) - timedelta(days=1)
    try:
        start = end.replace(year=end.year - years)
    except ValueError:  # 2월 29일
        start = end.replace(year=end.year - years, day=28)
    return start - timedelta(days=PRICE_START_MARGIN_DAYS), end


def fiscal_years(count: int, today: date | None = None, market: str = "KR") -> list[int]:
    """수집할 회계연도. 올해 사업보고서는 아직 공시되지 않았으므로 작년부터.

    미국은 올해까지 본다. 회계연도가 결산일의 연도라(sources/edgar.py)
    1월 결산 회사(월마트·엔비디아)의 2026-01 결산분은 FY2026이다. 작년까지만
    요청하면 이미 나온 최신 10-K가 빠진다. 아직 없는 연도는 EDGAR가
    돌려주지 않을 뿐이다.
    """
    year = (today or date.today()).year
    first = year - count
    last = year if market == "US" else year - 1
    return list(range(first, last + 1))


def build_universe(source: str, market: str):
    """유니버스 provider를 고른다.

    krx는 KRX 계정이 있어야 한다. 2026-09 기준 KRX가 data.krx.co.kr를
    로그인 기반으로 전환해 지수 구성종목 조회가 익명으로는 막혔다.
    """
    if source == "yaml":
        return YamlUniverse(market=market)
    if source == "krx":
        if not (os.environ.get("KRX_ID") and os.environ.get("KRX_PW")):
            raise SystemExit(
                "krx 유니버스에는 KRX_ID·KRX_PW 환경변수가 필요합니다.\n"
                "계정이 없으면 --source yaml 을 쓰세요 (config/universe.yaml을 읽습니다)."
            )
        return KrxUniverse()
    raise SystemExit(
        f"알 수 없는 유니버스 소스: {source} (가능: {', '.join(UNIVERSE_SOURCES)})"
    )


class SkippedFundamentals:
    """재무 수집을 건너뛸 때 쓰는 자리표시자.

    반드시 years=[] 와 함께 쓰인다. collect 파이프라인은 years가 비면
    소스를 호출하지 않으므로 fetch_annual은 실행되지 않고, name은
    collect_errors에 남길 소스 이름으로만 쓰인다.
    """

    name = "skipped"

    def fetch_annual(self, ticker: str, years: list[int]) -> list:
        raise AssertionError("years=[] 이므로 호출되지 않아야 한다")


def build_fundamentals(skip: bool, years: list[int], market: str = "KR"):
    """재무 소스와 수집할 회계연도를 정한다.

    주가는 재무 소스의 비밀값이 필요 없다. 비밀값 하나가 없다고 수집
    전체를 막을 이유가 없으므로, 없으면 재무만 건너뛰고 주가는 받는다.
    수집은 증분이라, 나중에 값을 넣고 다시 돌리면 재무만 채워진다.
    """
    if skip:
        print("재무제표를 건너뜁니다 (--skip-financials). 주가만 수집합니다.")
        return SkippedFundamentals(), []
    try:
        secrets = load_secrets(market=market)
    except ValueError as exc:
        print("재무제표를 건너뜁니다. 주가만 수집합니다.", file=sys.stderr)
        print(f"  이유: {exc}", file=sys.stderr)
        return SkippedFundamentals(), []
    if market == "US":
        return EdgarSource(secrets.sec_user_agent,
                           cik_overrides=load_cik_overrides()), years
    return DartSource(secrets.dart_api_key), years


def build_prices(market: str):
    """시장의 주가 소스. 한국 pykrx, 미국 yfinance (설계서 §5.1)."""
    return YahooPriceSource() if market == "US" else KrxPriceSource()


def resolve_tickers(
    repo: Repository, market: str, tickers: list[str] | None
) -> list[str] | None:
    """--tickers 인자를 DB의 ticker 형식으로 맞추고, 목록에 없는 종목을 경고한다.

    DB의 ticker는 거래소 접미사가 붙는다('005930.KS'). 증권사 화면에 찍히는
    코드는 접미사가 없는 6자리('005930')라서, 맞춰주지 않으면
    collect_market()의 문자열 비교가 실패해 대상 0종목으로 조용히 끝난다
    (버그 I6). to_ticker()는 무조건 '.KS'를 붙이므로 KR에서만 쓴다.

    오탈자나 in_universe=0인 종목은 collect_market()이 조용히 건너뛰므로,
    여기서 이름을 밝혀 경고한다.
    """
    if tickers is None:
        return None
    if market == "KR":
        tickers = [to_ticker(t) for t in tickers]

    known = {s.ticker: s for s in repo.list_securities(market=market, in_universe_only=False)}
    for ticker in tickers:
        security = known.get(ticker)
        if security is None:
            print(f"경고: 알 수 없는 종목입니다 (목록에 없음): {ticker}")
        elif not security.in_universe:
            print(f"경고: 분석 대상에서 제외된 종목입니다 (in_universe=0): {ticker}")

    return tickers


def serve(db_path: str, host: str, port: int) -> None:
    """개발용 서버를 띄운다.

    소켓은 uvicorn에 맡기지 않고 여기서 먼저 연다. uvicorn.run에 맡기면
    바인드가 실패해도 그 앞에서 찍은 "실행 중입니다"가 화면에 남아,
    서버가 떠 있다고 믿게 된다. 포트가 점유돼 있으면 아무것도 출력하지
    않고 바로 끝나야 한다.

    uvicorn과 web 모듈을 함수 안에서 import한다. 다른 하위명령은
    FastAPI가 필요 없으므로, 최상단에서 import하면 collect 한 번
    돌리는 데도 웹 스택 전체가 로드된다.
    """
    import socket

    # uvicorn은 SO_REUSEADDR을 켜고 바인드한다. 윈도우에서 그 옵션은
    # "이미 쓰는 주소에도 붙어라"는 뜻이라, 남의 서버 위에 얹히고도
    # 성공한 척할 수 있다. 여기서는 켜지 않는다.
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        sock.bind((host, port))
        sock.listen(2048)
    except OSError as exc:
        sock.close()
        raise SystemExit(
            f"{host}:{port} 를 열 수 없습니다 ({exc.strerror or exc}). "
            f"다른 포트를 --port 로 지정하세요."
        ) from exc

    import uvicorn

    from stockscout.web.api import create_app

    try:
        app = create_app(db_path)
    except BaseException:
        sock.close()
        raise

    # flush: 파이프로 넘기면 블록 버퍼링이라, 서버가 도는 내내 이 줄이
    # 버퍼에 갇혀 있다가 종료할 때 나온다. 주소는 지금 보여야 한다.
    print(f"http://{host}:{port} 에서 실행 중입니다. 종료하려면 Ctrl+C.", flush=True)
    uvicorn.Server(uvicorn.Config(app, log_level="warning")).run(sockets=[sock])


def render_ranking(repo, snapshot_id: int, top: int, window: int,
                   market: str = "KR") -> str:
    """하락률 상위 종목표. ASCII 기호만 쓴다 (콘솔이 cp949다).

    달러는 센트까지 보인다. 20달러짜리 종목을 정수로 찍으면 평균과 현재가가
    같아 보인다.
    """
    money = ",.2f" if market == "US" else ",.0f"
    rows = [r for r in repo.get_analysis_results(snapshot_id)
            if r["passed_filters"] and r[f"drop_vs_avg_{window}y"] is not None]
    rows.sort(key=lambda r: r[f"drop_vs_avg_{window}y"])

    names = {s.ticker: s.name for s in repo.list_securities()}
    lines = [f"{'종목':<18}{'현재가':>12}{f'{window}년평균':>12}"
             f"{'평균대비':>10}{'고점대비':>10}",
             "-" * 62]
    for r in rows[:top]:
        lines.append(
            f"{names.get(r['ticker'], r['ticker']):<18}"
            f"{r['price_current']:>12{money}}"
            f"{r[f'avg_price_{window}y']:>12{money}}"
            f"{r[f'drop_vs_avg_{window}y'] * 100:>9.1f}%"
            f"{r['drop_vs_high'] * 100:>9.1f}%"
        )
    if not rows:
        lines.append("(통과 종목 없음)")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stockscout", description="한국·미국 우량주 낙폭 스크리너"
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    sub = parser.add_subparsers(dest="command", required=True)

    p_uni = sub.add_parser("universe", help="분석 대상 종목 목록을 갱신한다")
    p_uni.add_argument("--market", default="KR", choices=["KR", "US"])
    p_uni.add_argument(
        "--source", default="yaml", choices=list(UNIVERSE_SOURCES),
        help="yaml=config/universe.yaml (기본), krx=KRX 지수 API (KRX 계정 필요)",
    )

    p_col = sub.add_parser("collect", help="재무제표와 주가를 수집한다")
    p_col.add_argument("--market", default="KR", choices=["KR", "US"])
    p_col.add_argument("--tickers", nargs="+", default=None, help="특정 종목만")
    p_col.add_argument("--force", action="store_true", help="이미 받은 것도 다시 받는다")
    p_col.add_argument(
        "--skip-financials", action="store_true",
        help="주가만 수집한다 (DART 키 없이 동작)",
    )

    p_norm = sub.add_parser("normalize", help="원본을 공통 스키마로 변환한다")
    p_norm.add_argument("--market", default="KR", choices=["KR", "US"])

    p_ana = sub.add_parser("analyze", help="저장된 데이터로 하락률을 계산한다")
    p_ana.add_argument("--market", default="KR", choices=["KR", "US"])
    p_ana.add_argument("--note", default=None, help="스냅샷에 남길 메모")
    p_ana.add_argument("--top", type=int, default=20,
                       help="하락률 상위 몇 종목을 출력할지")

    p_srv = sub.add_parser("serve", help="브라우저에서 결과를 본다")
    p_srv.add_argument("--host", default="127.0.0.1")
    p_srv.add_argument("--port", type=int, default=8000)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)

    with connect(args.db) as conn:
        init_db(conn)
        repo = Repository(conn)

        if args.command == "universe":
            universe = build_universe(args.source, args.market)
            n = sync_universe(repo, universe, market=args.market)
            print(f"유니버스 갱신 완료: {n}종목 (소스: {args.source})")

        elif args.command == "collect":
            tickers = resolve_tickers(repo, market=args.market, tickers=args.tickers)
            start, end = price_window(max(config.lookback.price_years))
            fundamentals, years = build_fundamentals(
                skip=args.skip_financials,
                years=fiscal_years(config.lookback.financial_years,
                                   market=args.market),
                market=args.market,
            )
            summary = collect_market(
                repo,
                market=args.market,
                fundamentals=fundamentals,
                prices=build_prices(args.market),
                years=years,
                price_start=start,
                price_end=end,
                tickers=tickers,
                force=args.force,
            )
            print(summary.render())
            if summary.failed:
                print("\n최근 오류:")
                for err in repo.recent_errors(limit=5):
                    print(f"  {err['ticker']}: {err['message']}")

        elif args.command == "normalize":
            print(normalize_market(repo, market=args.market).render())

        elif args.command == "analyze":
            summary = analyze_market(repo, market=args.market, config=config,
                                     note=args.note)
            print(summary.render())
            if summary.snapshot_id is not None and args.top > 0:
                print()
                print(render_ranking(repo, summary.snapshot_id, args.top,
                                     config.lookback.primary_window,
                                     market=args.market))

        elif args.command == "serve":
            serve(args.db, host=args.host, port=args.port)

    return 0


if __name__ == "__main__":
    sys.exit(main())
