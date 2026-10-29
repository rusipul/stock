"""가격 통계와 밸류에이션. 설계서 §9.1·§9.2.

순수 함수만 둔다. 네트워크도 DB도 모르고, 입력은 값, 출력은 값이다.
덕분에 네트워크 없이 단독으로 검증할 수 있다.
"""

from __future__ import annotations

from datetime import date

from stockscout.models import Financials, PriceBar, PriceStats, Valuation

# 고점 창은 설계서 §9.1이 5년으로 못박은 값이다. 창별 평균(1·3·5년)과
# 달리 설정으로 바뀌지 않으므로 상수로 둔다. analysis_results 컬럼명도
# high_5y이고, 계획 C의 고점 대비 등급 판정이 이 값을 쓴다.
HIGH_WINDOW_YEARS = 5


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

    # as_of 이후 봉은 전부 버린다. 이 함수는 "as_of 시점에 무엇이 보였는가"를
    # 답해야 한다. 현재가와 고점만 상한 없이 계산하면, 과거 시점을 물었을 때
    # 미래 가격이 섞여 들어온다(look-ahead bias). 파이프라인이 늘 최신
    # 날짜를 넘기므로 지금은 결과가 같지만, 그 호출 습관에 정확성을
    # 의존시키면 안 된다 — 백테스트나 "그때는 뭐라고 했나" 조회가
    # 조용히 낙관적인 답을 받게 되고 에러는 나지 않는다.
    ordered = [b for b in sorted(bars, key=lambda b: b.date) if b.date <= as_of]
    if not ordered:
        raise ValueError(f"주가 데이터가 {as_of} 이전에 없습니다")

    price_current = ordered[-1].close

    five_year_start = window_start(as_of, HIGH_WINDOW_YEARS)
    in_five = [b for b in ordered if b.date >= five_year_start]
    high_5y = max(b.close for b in (in_five or ordered))

    avg_price: dict[int, float | None] = {}
    drop_vs_avg: dict[int, float | None] = {}
    high_by_window: dict[int, float] = {}
    drop_vs_high_by_window: dict[int, float] = {}

    for years in windows:
        start = window_start(as_of, years)
        # 고점은 커버리지 관문 밖이다 - 5년 고점과 같은 규칙(위 docstring).
        in_window = [b.close for b in ordered if b.date >= start]
        high = max(in_window or [price_current])
        high_by_window[years] = high
        drop_vs_high_by_window[years] = price_current / high - 1 if high > 0 else 0.0

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
        high_by_window=high_by_window,
        drop_vs_high_by_window=drop_vs_high_by_window,
    )


def compute_valuation(price: float, latest: Financials | None) -> Valuation:
    """시가총액·PER·PBR (설계서 §9.2).

    화면 표시와 정렬에만 쓴다. 점수에는 넣지 않는다 — 설계서 §8.2가
    밸류에이션을 스코어링에서 뺐기 때문이다.

    발행주식수가 없으면 셋 다 None이다. 발행주식수는 전체 재무제표
    API(fnlttSinglAcntAll)가 아니라 「주식의 총수 현황」(stockTotqySttus)
    에서 따로 수집하므로, 그 수집이 실패한 종목에서만 None이 된다.

    세 지표는 서로 독립이다. 적자 기업이라 PER이 없어도 시가총액과
    PBR은 남긴다.
    """
    if latest is None or not latest.shares_outstanding:
        return Valuation()

    issued = latest.shares_outstanding
    if issued <= 0:
        return Valuation()

    # 시가총액은 발행주식총수, EPS·BPS는 유통주식수를 쓴다 (설계서 §9.2).
    # 자기주식은 배당도 의결권도 없으므로 주당 지표의 분모에서 빼는 것이
    # 맞다. 자기주식을 못 구했거나 값이 이상하면 발행주식총수로 돌아간다 -
    # 자사주 비중은 대개 작아서, NULL로 만드는 쪽이 손해가 크다.
    free_float = issued
    if latest.shares_treasury is not None:
        remaining = issued - latest.shares_treasury
        if remaining > 0:
            free_float = remaining

    market_cap = price * issued

    per = None
    if latest.net_income is not None:
        eps = latest.net_income / free_float
        if eps > 0:
            per = price / eps

    pbr = None
    if latest.total_equity is not None:
        bps = latest.total_equity / free_float
        if bps > 0:
            pbr = price / bps

    return Valuation(market_cap=market_cap, per=per, pbr=pbr)
