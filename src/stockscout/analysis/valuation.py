"""가격 통계와 밸류에이션. 설계서 §9.1·§9.2.

순수 함수만 둔다. 네트워크도 DB도 모르고, 입력은 값, 출력은 값이다.
덕분에 네트워크 없이 단독으로 검증할 수 있다.
"""

from __future__ import annotations

from datetime import date

from stockscout.models import PriceBar, PriceStats


def window_start(as_of: date, years: int) -> date:
    """as_of로부터 years년 전 날짜. 달력 기준이다.

    연분수(days / 365.25)로 계산하면 안 된다. 2021-09-10 ~ 2026-09-10은
    정확히 5달력년이지만 1,826일이고 1826/365.25 = 4.9993이라 5년
    미만으로 판정된다. 실측으로 확인했고 전 종목이 탈락했다.
    """
    try:
        return as_of.replace(year=as_of.year - years)
    except ValueError:  # 2월 29일
        return as_of.replace(year=as_of.year - years, day=28)


def compute_price_stats(
    bars: list[PriceBar],
    as_of: date,
    windows: list[int],
    calendar: list[date],
    min_coverage: float,
) -> PriceStats:
    """창별 평균·하락률과 5년 고점을 계산한다.

    calendar는 시장 전체의 관측된 거래일 목록이다. 거래소 달력을 따로
    두지 않고, 수집된 전 종목의 날짜 합집합을 거래일로 본다. 한 종목의
    봉 수를 그 창의 거래일 수로 나눈 값이 커버리지다(설계서 §9.1).

    커버리지 관문은 창별 평균에만 적용한다. 현재가와 5년 고점은
    관문과 무관하게 산출한다 — 고점 대비 하락률은 계획 C의 등급 판정에
    쓰이므로 평균이 없다고 함께 지워버리면 안 된다.
    """
    if not bars:
        raise ValueError("주가 데이터가 없습니다")

    ordered = sorted(bars, key=lambda b: b.date)
    price_current = ordered[-1].close

    five_year_start = window_start(as_of, 5)
    in_five = [b for b in ordered if b.date >= five_year_start]
    high_5y = max(b.close for b in (in_five or ordered))

    avg_price: dict[int, float | None] = {}
    drop_vs_avg: dict[int, float | None] = {}

    for years in windows:
        start = window_start(as_of, years)
        closes = [b.close for b in ordered if start <= b.date <= as_of]
        expected = sum(1 for d in calendar if start <= d <= as_of)

        if not closes or expected == 0 or len(closes) / expected < min_coverage:
            avg_price[years] = None
            drop_vs_avg[years] = None
            continue

        mean = sum(closes) / len(closes)
        avg_price[years] = mean
        drop_vs_avg[years] = price_current / mean - 1 if mean > 0 else None

    return PriceStats(
        price_current=price_current,
        avg_price=avg_price,
        high_5y=high_5y,
        drop_vs_avg=drop_vs_avg,
        drop_vs_high=price_current / high_5y - 1 if high_5y > 0 else 0.0,
    )
