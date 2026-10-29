"""SEC EDGAR XBRL companyfacts. https://www.sec.gov/edgar/sec-api-documentation

응답에서 연간 값을 골라 회계연도별로 나눈다. 태그 해석(어떤 태그가
매출인가)은 normalize/us_mapping.py의 몫이다 - DART의 dart.py와
kr_mapping.py의 분담과 같다.
"""

from __future__ import annotations

from datetime import date

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
    때문이다(설계서 §18.2). 한 해에 결산일이 둘이면(결산월 변경) 늦은 쪽.
    """
    ends: dict[int, date] = {}
    for items in entries.values():
        for entry in items:
            if _is_annual_period(entry):
                end = _date(entry["end"])
                if end > ends.get(end.year, date.min):
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
