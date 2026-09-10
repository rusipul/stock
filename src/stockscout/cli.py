"""명령줄 진입점."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date

from stockscout.config import DEFAULT_CONFIG_PATH, load_config, load_secrets
from stockscout.pipeline.collect import collect_market, sync_universe
from stockscout.pipeline.normalize import normalize_market
from stockscout.sources.dart import DartSource
from stockscout.sources.krx import KrxPriceSource, KrxUniverse
from stockscout.sources.universe import YamlUniverse
from stockscout.store.db import DEFAULT_DB_PATH, connect, init_db
from stockscout.store.repository import Repository

UNIVERSE_SOURCES = ("yaml", "krx")


def price_window(years: int, today: date | None = None) -> tuple[date, date]:
    end = today or date.today()
    try:
        start = end.replace(year=end.year - years)
    except ValueError:  # 2월 29일
        start = end.replace(year=end.year - years, day=28)
    return start, end


def fiscal_years(count: int, today: date | None = None) -> list[int]:
    """수집할 회계연도. 올해 사업보고서는 아직 공시되지 않았으므로 작년부터."""
    last = (today or date.today()).year - 1
    return list(range(last - count + 1, last + 1))


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

    p_norm = sub.add_parser("normalize", help="원본을 공통 스키마로 변환한다")
    p_norm.add_argument("--market", default="KR", choices=["KR", "US"])

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
            secrets = load_secrets()
            start, end = price_window(max(config.lookback.price_years))
            summary = collect_market(
                repo,
                market=args.market,
                fundamentals=DartSource(secrets.dart_api_key),
                prices=KrxPriceSource(),
                years=fiscal_years(config.lookback.financial_years),
                price_start=start,
                price_end=end,
                tickers=args.tickers,
                force=args.force,
            )
            print(summary.render())
            if summary.failed:
                print("\n최근 오류:")
                for err in repo.recent_errors(limit=5):
                    print(f"  {err['ticker']}: {err['message']}")

        elif args.command == "normalize":
            print(normalize_market(repo, market=args.market).render())

    return 0


if __name__ == "__main__":
    sys.exit(main())
