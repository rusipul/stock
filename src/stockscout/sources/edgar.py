"""SEC EDGAR XBRL companyfacts. https://www.sec.gov/edgar/sec-api-documentation

응답에서 연간 값을 골라 회계연도별로 나눈다. 태그 해석(어떤 태그가
매출인가)은 normalize/us_mapping.py의 몫이다 - DART의 dart.py와
kr_mapping.py의 분담과 같다.
"""

from __future__ import annotations

import time
from datetime import date

import httpx

from stockscout.models import RawStatement
from stockscout.sources.base import FatalSourceError, PermanentSourceError
from stockscout.sources.ratelimit import TokenBucket

ANNUAL_FORMS = {"10-K", "10-K/A"}

# 흐름 항목(손익·현금흐름)을 연간으로 볼 기간. 52/53주 회계연도가 있어
# 365일 정확히가 아니다(AAPL 2023 = 371일). 분기(약 91일)와는 넉넉히 떨어진다.
MIN_ANNUAL_DAYS = 350
MAX_ANNUAL_DAYS = 380

# 표지(dei:Entity...) 값은 결산일이 아니라 표지 작성일로 온다. 결산 후
# 이 일수 안의 10-K 표지 값을 그 회계연도 것으로 본다. 10-K 제출 기한이
# 결산 후 최장 90일이다.
COVER_WINDOW_DAYS = 120

# 금액과 주식수만 본다. 'USD/shares'(EPS) 같은 비율 단위는 쓰지 않는다.
UNITS = ("USD", "shares")
NAMESPACES = ("us-gaap", "dei")


def _date(text: str) -> date:
    return date.fromisoformat(text)


def _is_annual_period(entry: dict) -> bool:
    start = entry.get("start")
    if not start:
        return False
    days = (_date(entry["end"]) - _date(start)).days
    return MIN_ANNUAL_DAYS <= days <= MAX_ANNUAL_DAYS


def _annual_entries(companyfacts: dict) -> dict[str, list[dict]]:
    """태그 -> 10-K 계열 항목. 태그마다 첫 번째로 있는 단위 하나만 본다."""
    out: dict[str, list[dict]] = {}
    for namespace in NAMESPACES:
        for tag, body in companyfacts.get("facts", {}).get(namespace, {}).items():
            units = body.get("units", {})
            for unit in UNITS:
                if unit in units:
                    out[tag] = [e for e in units[unit]
                                if e.get("form") in ANNUAL_FORMS]
                    break
    return out


def _fiscal_year_ends(entries: dict[str, list[dict]]) -> dict[int, date]:
    """회계연도 -> 결산일. 연간 흐름 항목의 end에서 얻는다.

    fy를 쓰지 않는다. 10-K의 전기 비교 수치가 그 공시의 fy를 달고 오기
    때문이다(설계서 §18.2).

    한 해에 12개월짜리 기간의 end가 여럿이면 그날짜로 끝나는 태그가 가장
    많은 날을 고른다. 주석에 회계연도가 아닌 12개월이 섞여 온다 - 실측:
    PG(6월 결산)의 구조조정 비용 2023-10-01 ~ 2024-09-30. "늦은 쪽"으로
    고르면 그 한 항목이 2024년의 결산일을 가져가 매출·자산이 전부 빠진다.
    같으면 늦은 쪽(결산월을 바꾼 해).
    """
    tags_by_end: dict[date, set[str]] = {}
    for tag, items in entries.items():
        for entry in items:
            if _is_annual_period(entry):
                tags_by_end.setdefault(_date(entry["end"]), set()).add(tag)

    ends: dict[int, date] = {}
    for end, tags in tags_by_end.items():
        best = ends.get(end.year)
        if best is None or (len(tags), end) > (len(tags_by_end[best]), best):
            ends[end.year] = end
    return ends


def _matches(tag: str, entry: dict, year_end: date) -> bool:
    end = _date(entry["end"])
    if entry.get("start"):
        return end == year_end and _is_annual_period(entry)
    if tag.startswith("Entity"):
        return 0 <= (end - year_end).days <= COVER_WINDOW_DAYS
    return end == year_end


def annual_facts(companyfacts: dict) -> dict[int, dict]:
    """companyfacts -> {회계연도: {"period_end": ISO 날짜, "facts": {태그: 값}}}.

    - 흐름 항목: 10-K 계열이고 기간이 350~380일인 것. 10-K 안에 분기 값이
      섞여 있다(실측: AAPL Revenues 2018-07-01 ~ 2018-09-29)
    - 시점 항목: 그 회계연도 결산일과 같은 날짜
    - 표지 항목(dei:Entity...): 결산 후 COVER_WINDOW_DAYS 안
    - 같은 (태그, 기간)이 여러 번 공시되면 filed가 가장 늦은 값(정정 공시)
    - 회계연도 = 결산일의 연도. 월마트 2025-01-31 -> 2025, 회사의 호칭과 같다
    """
    entries = _annual_entries(companyfacts)
    out: dict[int, dict] = {}
    for year, year_end in _fiscal_year_ends(entries).items():
        picked: dict[str, float] = {}
        for tag, items in entries.items():
            best: dict | None = None
            for entry in items:
                if not _matches(tag, entry, year_end):
                    continue
                if best is None or entry.get("filed", "") > best.get("filed", ""):
                    best = entry
            if best is not None:
                picked[tag] = best["val"]
        out[year] = {"period_end": year_end.isoformat(), "facts": picked}
    return out


TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

# 재시도할 상태. 429(속도 제한)와 5xx는 정의상 일시적이다.
RETRY_STATUSES = {429, 500, 502, 503, 504}
RETRY_ATTEMPTS = 3


class SecTemporarilyUnavailable(ConnectionError):
    """SEC가 지금 응답을 내주지 못한다 (429·5xx).

    ConnectionError를 상속하는 이유는 dart.DartServiceUnavailable과 같다 -
    '지금은 연결해 데이터를 받을 수 없다'는 뜻이고, 다 써도 실패하면
    네트워크 장애와 똑같이 그 종목만 실패한다.
    """


def parse_company_tickers(data: dict) -> dict[str, int]:
    """company_tickers.json -> {티커: CIK}. 티커는 대문자, SEC 표기(BRK-B) 그대로."""
    return {row["ticker"].upper(): int(row["cik_str"]) for row in data.values()}


class EdgarSource:
    """FundamentalSource 프로토콜 구현.

    DART와 달리 회사당 호출 1회로 전 연도가 온다. 받은 것을 연도별로
    나눠 요청한 연도만 돌려준다.
    """

    name = "edgar"

    def __init__(
        self,
        user_agent: str,
        rate_per_sec: float = 5.0,
        client: httpx.Client | None = None,
        cik_overrides: dict[str, int] | None = None,
    ) -> None:
        # SEC 한도는 초당 10회다. 절반으로 둔다.
        self._headers = {"User-Agent": user_agent,
                         "Accept-Encoding": "gzip, deflate"}
        self.bucket = TokenBucket(rate_per_sec)
        self._client = client or httpx.Client(timeout=30)
        self._retry_delay = 1.0
        self.ciks: dict[str, int] = {}
        # SEC 티커 목록보다 먼저 본다. 지주사 전환 등으로 목록의 CIK가
        # 아직 10-K가 없는 회사를 가리킬 때 쓴다 (실측: XOM, 설계서 §18.4).
        self.cik_overrides = {k.upper(): v for k, v in (cik_overrides or {}).items()}

    def fetch_annual(self, ticker: str, years: list[int]) -> list[RawStatement]:
        cik = self.cik_overrides.get(ticker.upper())
        if cik is None:
            if not self.ciks:
                self.ciks = parse_company_tickers(self._get_json(TICKERS_URL))
            cik = self.ciks.get(ticker.upper())
        if cik is None:
            raise PermanentSourceError(f"SEC 티커 목록에 없습니다: {ticker}")

        by_year = annual_facts(self._get_json(FACTS_URL.format(cik=cik)))
        if not by_year:
            # 빈 목록을 조용히 돌려주면 collect_errors에 흔적이 남지 않는다.
            # 실측: XOM은 신설 지주사(CIK 2115436)로 옮겨 아직 10-K가 없다.
            raise PermanentSourceError(
                f"SEC에 {ticker}(CIK {cik})의 연간 재무가 없습니다. "
                "지주사 전환 등으로 티커의 CIK가 바뀌었을 수 있습니다."
            )
        return [
            RawStatement(ticker=ticker, fiscal_year=year, source=self.name,
                         payload=by_year[year])
            for year in years if year in by_year
        ]

    def _get_json(self, url: str) -> dict:
        """GET 하나. 429·5xx만 지수 백오프로 재시도한다.

        retry_on_transient를 쓰지 않는 이유: 그 데코레이터는 TRANSIENT
        예외만 보는데, 여기서는 HTTP 상태를 보고 판단해야 한다. 상태를
        SecTemporarilyUnavailable로 바꿔 던지고 같은 횟수만 재시도한다.
        """
        last: Exception | None = None
        for attempt in range(RETRY_ATTEMPTS):
            self.bucket.wait()
            resp = self._client.get(url, headers=self._headers)
            if resp.status_code in RETRY_STATUSES:
                last = SecTemporarilyUnavailable(
                    f"SEC가 일시적으로 응답하지 않습니다 ({resp.status_code}): {url}")
                if attempt < RETRY_ATTEMPTS - 1 and self._retry_delay > 0:
                    time.sleep(self._retry_delay * (2 ** attempt))
                continue
            if resp.status_code == 403:
                # 이 종목이 아니라 요청 자체가 막혔다. 남은 종목도 전부 403이다.
                raise FatalSourceError(
                    "SEC가 요청을 거부했습니다(403). config/.env의 "
                    "SEC_USER_AGENT에 연락처(이메일)가 들어 있는지 확인하세요."
                )
            if resp.status_code == 404:
                raise PermanentSourceError(f"SEC에 데이터가 없습니다(404): {url}")
            resp.raise_for_status()
            return resp.json()
        assert last is not None
        raise last
