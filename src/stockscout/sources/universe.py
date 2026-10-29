"""config/universe.yaml 기반 유니버스.

KRX가 data.krx.co.kr를 로그인 기반으로 전환하면서 get_index_portfolio_deposit_file
같은 지수 구성종목 API가 익명 접근을 막았다(2026-09 확인). 그래서 구성종목을
KrxUniverse처럼 자동으로 받는 대신 config/universe.yaml에 손으로 적은 목록을
읽는다. 종목명은 pykrx 종목별 조회(get_market_ticker_name)로 채우는데, 이쪽은
여전히 익명으로 동작한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import yaml

from stockscout.models import Security
from stockscout.sources.krx import to_ticker

DEFAULT_UNIVERSE_PATH = Path("config/universe.yaml")


def _default_name_fn(stock_code: str) -> str:
    from pykrx import stock

    return stock.get_market_ticker_name(stock_code)


class YamlUniverse:
    """config/universe.yaml에 적힌 종목 목록을 읽는다.

    KRX 지수 API가 로그인 필수로 바뀌어 구성종목 자동 수집이 막혔다.
    종목명만 pykrx 종목별 조회로 채운다 — 그쪽은 익명으로 동작한다.
    """

    name = "yaml"

    def __init__(
        self,
        path: Path | str = DEFAULT_UNIVERSE_PATH,
        market: str = "KR",
        name_fn: Callable[[str], str] | None = None,
    ) -> None:
        self.path = Path(path)
        self.market = market
        self._name = name_fn or _default_name_fn

    def fetch(self) -> list[Security]:
        if not self.path.exists():
            raise FileNotFoundError(f"유니버스 설정 파일이 없습니다: {self.path}")

        raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        if self.market not in raw:
            raise ValueError(
                f"유니버스 설정에 '{self.market}' 시장이 없습니다: {self.path}"
            )

        section = raw[self.market]
        exchange = section.get("exchange")
        currency = section.get("currency")
        # (코드, 거래소). KR 섹션의 kosdaq 목록은 코스닥 종목이다 - 접미사(.KQ)와
        # 거래소 표시, 시장 비교 지수(코스닥)가 달라진다.
        tickers: list[tuple[str, str | None]] = (
            [(str(t), exchange) for t in section.get("tickers", [])]
            + [(str(t), "KOSDAQ") for t in section.get("kosdaq", [])]
        )
        names: dict[str, str] = {
            self._symbol(str(k)): v for k, v in (section.get("names") or {}).items()
        }
        financial = {self._symbol(str(t)) for t in section.get("is_financial", [])}

        securities: list[Security] = []
        for code, code_exchange in tickers:
            symbol = self._symbol(code)
            securities.append(
                Security(
                    ticker=self._ticker(code, code_exchange),
                    market=self.market,
                    name=names.get(symbol) or self._lookup_name(code),
                    exchange=code_exchange,
                    currency=currency,
                    is_financial=symbol in financial,
                    in_universe=True,
                )
            )
        return securities

    def _symbol(self, code: str) -> str:
        """YAML에 적힌 코드를 비교용으로 맞춘다. 미국 티커는 대문자다."""
        return code if self.market == "KR" else code.upper()

    def _ticker(self, code: str, exchange: str | None = None) -> str:
        """DB의 ticker. 한국은 거래소 접미사를 붙이고 미국은 그대로 둔다.

        미국은 SEC(company_tickers.json)와 Yahoo가 둘 다 'BRK-B'처럼 하이픈을
        쓴다. 변환하지 않는 것이 두 소스를 함께 맞추는 방법이다.
        """
        if self.market == "KR":
            return to_ticker(code, exchange if exchange == "KOSDAQ" else "KOSPI")
        return code.upper()

    def _lookup_name(self, code: str) -> str:
        """YAML에 이름이 없을 때. pykrx는 한국 종목만 안다."""
        if self.market != "KR":
            return self._symbol(code)
        try:
            return self._name(code)
        except Exception:
            # 손으로 고른 목록이라 조회 실패는 일시적 네트워크 문제일 뿐,
            # 종목이 잘못된 게 아니다. KrxUniverse와 달리 건너뛰지 않는다.
            return code


def load_cik_overrides(path: Path | str = DEFAULT_UNIVERSE_PATH) -> dict[str, int]:
    """universe.yaml US 섹션의 cik: {티커: CIK}. 없으면 빈 dict.

    SEC 티커 목록의 CIK가 틀릴 때 쓴다 - XOM은 목록이 아직 10-K가 없는
    신설 지주사를 가리킨다(설계서 §18.4). 유니버스와 같은 파일에 두는
    이유: 어느 종목을 볼지와 그 종목을 어디서 찾을지는 함께 바뀐다.
    """
    path = Path(path)
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    section = raw.get("US") or {}
    return {str(k).upper(): int(v) for k, v in (section.get("cik") or {}).items()}
