from datetime import date, timedelta

import pytest

from stockscout.analysis.valuation import compute_price_stats, compute_valuation, window_start
from stockscout.models import Financials, PriceBar


def bars(ticker="X", start=date(2021, 1, 1), closes=None):
    """closes 길이만큼 하루씩 증가하는 일봉을 만든다."""
    closes = closes or [100.0]
    return [
        PriceBar(ticker=ticker, date=start + timedelta(days=i), close=c)
        for i, c in enumerate(closes)
    ]


# --- 창 시작일 --------------------------------------------------------


def test_window_start_uses_calendar_years():
    assert window_start(date(2026, 9, 10), 5) == date(2021, 9, 10)
    assert window_start(date(2026, 9, 10), 1) == date(2025, 9, 10)


def test_window_start_handles_leap_day():
    assert window_start(date(2024, 2, 29), 5) == date(2019, 2, 28)


# --- 평균과 하락률 ----------------------------------------------------


def test_average_is_arithmetic_mean_of_closes_in_window():
    series = bars(closes=[100.0, 200.0, 300.0, 400.0])
    as_of = series[-1].date
    calendar = [b.date for b in series]

    stats = compute_price_stats(series, as_of=as_of, windows=[1],
                                calendar=calendar, min_coverage=0.8)

    assert stats.avg_price[1] == pytest.approx(250.0)
    assert stats.price_current == 400.0


def test_drop_vs_avg_is_current_over_average_minus_one():
    series = bars(closes=[100.0, 100.0, 100.0, 60.0])
    as_of = series[-1].date
    calendar = [b.date for b in series]

    stats = compute_price_stats(series, as_of=as_of, windows=[1],
                                calendar=calendar, min_coverage=0.8)

    # 평균 90, 현재가 60 → 60/90 - 1 = -0.3333
    assert stats.avg_price[1] == pytest.approx(90.0)
    assert stats.drop_vs_avg[1] == pytest.approx(-1 / 3)


def test_drop_vs_high_uses_max_close_in_five_year_window():
    series = bars(closes=[50.0, 200.0, 80.0, 100.0])
    as_of = series[-1].date
    calendar = [b.date for b in series]

    stats = compute_price_stats(series, as_of=as_of, windows=[5],
                                calendar=calendar, min_coverage=0.8)

    assert stats.high_5y == 200.0
    assert stats.drop_vs_high == pytest.approx(-0.5)


def test_average_only_counts_bars_inside_the_window():
    """1년 창은 1년 전 이후 봉만 센다."""
    old = [PriceBar(ticker="X", date=date(2020, 1, 1), close=1000.0)]
    recent = bars(start=date(2026, 1, 1), closes=[100.0, 100.0])
    series = old + recent
    as_of = date(2026, 1, 2)
    calendar = [b.date for b in recent]

    stats = compute_price_stats(series, as_of=as_of, windows=[1],
                                calendar=calendar, min_coverage=0.8)

    assert stats.avg_price[1] == pytest.approx(100.0)   # 1000은 창 밖


# --- 커버리지 관문 ----------------------------------------------------


def test_window_below_coverage_threshold_is_none():
    """설계서 §9.1: 해당 창 거래일의 80% 미만이면 NULL."""
    calendar = [date(2026, 1, 1) + timedelta(days=i) for i in range(10)]
    series = bars(start=date(2026, 1, 1), closes=[100.0] * 5)   # 50%

    stats = compute_price_stats(series, as_of=calendar[-1], windows=[1],
                                calendar=calendar, min_coverage=0.8)

    assert stats.avg_price[1] is None
    assert stats.drop_vs_avg[1] is None


def test_window_at_coverage_threshold_is_kept():
    """정확히 80%는 통과한다 (경계값)."""
    calendar = [date(2026, 1, 1) + timedelta(days=i) for i in range(10)]
    series = bars(start=date(2026, 1, 1), closes=[100.0] * 8)   # 80%

    stats = compute_price_stats(series, as_of=calendar[-1], windows=[1],
                                calendar=calendar, min_coverage=0.8)

    assert stats.avg_price[1] == pytest.approx(100.0)


def test_high_and_current_survive_coverage_failure():
    """고점과 현재가는 커버리지와 무관하게 산출된다.

    창별 평균만 관문의 대상이다. 고점 대비 하락률은 계획 C의 등급
    판정에서 쓰이므로 여기서 지워버리면 안 된다.
    """
    calendar = [date(2026, 1, 1) + timedelta(days=i) for i in range(10)]
    series = bars(start=date(2026, 1, 1), closes=[200.0, 100.0])   # 20%

    stats = compute_price_stats(series, as_of=calendar[-1], windows=[1],
                                calendar=calendar, min_coverage=0.8)

    assert stats.avg_price[1] is None
    assert stats.high_5y == 200.0
    assert stats.price_current == 100.0
    assert stats.drop_vs_high == pytest.approx(-0.5)


# --- 경계 조건 --------------------------------------------------------


def test_empty_series_raises():
    with pytest.raises(ValueError, match="주가"):
        compute_price_stats([], as_of=date(2026, 1, 1), windows=[1],
                            calendar=[date(2026, 1, 1)], min_coverage=0.8)


def test_empty_calendar_treats_every_window_as_uncovered():
    series = bars(closes=[100.0])

    stats = compute_price_stats(series, as_of=series[-1].date, windows=[1],
                                calendar=[], min_coverage=0.8)

    assert stats.avg_price[1] is None


def test_multiple_windows_computed_independently():
    calendar = [date(2021, 9, 10) + timedelta(days=i) for i in range(1827)]
    closes = [100.0] * 1000 + [50.0] * 827
    series = bars(start=date(2021, 9, 10), closes=closes)

    stats = compute_price_stats(series, as_of=series[-1].date, windows=[1, 3, 5],
                                calendar=calendar, min_coverage=0.8)

    assert set(stats.avg_price) == {1, 3, 5}
    assert stats.avg_price[1] == pytest.approx(50.0)     # 최근 1년은 전부 50
    assert stats.avg_price[5] > stats.avg_price[1]       # 5년은 100이 섞인다


# --- 추가: as_of가 최종 봉보다 이른 경우 ------------------------------
#
# 아래 두 테스트는 "옳은" 동작이 아니라 *현재 구현이 실제로 하는 일*을
# 고정한다(pin). 조사 결과 as_of보다 늦은 날짜의 봉이 존재하면
# price_current와 high_5y 둘 다 그 미래 봉을 반영해 버린다 —
# ordered[-1]은 날짜 정렬의 마지막일 뿐 as_of 이하로 자르지 않고,
# in_five도 "b.date >= five_year_start"만 걸고 "b.date <= as_of"는
# 걸지 않는다. 설계서 §9.1 계획대로라면 파이프라인(Task 6)은 항상
# as_of = 전 종목 관측일의 최댓값을 넘기므로 실사용에서는 봉이
# as_of를 앞지르는 일이 없어 드러나지 않는다. 하지만 이 함수는 "순수
# 함수"로 독립 검증 가능하다고 명시돼 있고, 호출자가 과거 시점을
# as_of로 넘기는 시나리오(백테스트, 특정일 재현)에서는 미래 정보가
# "현재가"·"5년 고점"으로 새어 들어가는 룩어헤드 결함이다.
#
# Step 3는 검증서에 코드가 그대로 주어져 있어 이 태스크에서 임의로
# 고치지 않았다 — 대신 여기서 실제 동작을 못박고 보고서의 Concerns에
# 버그로 명시한다.


def test_as_of_caps_price_current_no_look_ahead():
    """as_of 이후 봉은 현재가에 섞이지 않는다.

    금융 도구에서 가장 위험한 버그 유형이 look-ahead bias다. "2024년
    1월에 이 스크리너가 뭐라고 했나"를 물었을 때 미래 가격이 답에
    섞이면, 전략이 실제보다 좋아 보이면서 아무 에러도 나지 않는다.
    """
    series = bars(closes=[100.0, 110.0, 120.0, 130.0])  # 2021-01-01..04
    as_of = date(2021, 1, 2)   # 두 번째 봉(110) 시점
    calendar = [b.date for b in series]

    stats = compute_price_stats(series, as_of=as_of, windows=[1],
                                calendar=calendar, min_coverage=0.8)

    assert stats.price_current == 110.0   # 130(01-04)이 아니다


def test_as_of_caps_high_5y_no_look_ahead():
    """고점도 as_of로 상한을 둔다.

    in_five가 five_year_start 하한만 걸러 상한이 없으면, 과거 시점을
    물었을 때 미래의 고점이 "그때의 5년 고점"으로 보고된다.
    """
    series = bars(closes=[100.0, 300.0, 120.0])  # 01-01, 01-02, 01-03
    as_of = date(2021, 1, 1)   # 첫 봉(100)만 유효한 시점
    calendar = [b.date for b in series]

    stats = compute_price_stats(series, as_of=as_of, windows=[1],
                                calendar=calendar, min_coverage=0.8)

    assert stats.high_5y == 100.0        # 300(01-02)이 아니다
    assert stats.price_current == 100.0
    assert stats.drop_vs_high == pytest.approx(0.0)


def test_all_bars_after_as_of_raises():
    """as_of 이전 봉이 하나도 없으면 계산할 수 없다."""
    series = bars(start=date(2026, 1, 1), closes=[100.0, 110.0])

    with pytest.raises(ValueError, match="이전에 없습니다"):
        compute_price_stats(series, as_of=date(2025, 1, 1), windows=[1],
                            calendar=[date(2025, 1, 1)], min_coverage=0.8)


# --- 추가: 0 이하 평균 ------------------------------------------------


def test_zero_or_negative_mean_yields_none_drop_not_crash():
    """평균이 0 이하이면 drop_vs_avg는 크래시 없이 None이어야 한다.

    close는 정상적으로는 0 이하일 수 없지만, 방어적으로 짜여 있는지
    확인한다 — 0으로 나누기나 부호가 뒤집힌 무의미한 비율을 만들면
    안 된다.
    """
    series = bars(closes=[0.0, 0.0])
    as_of = series[-1].date
    calendar = [b.date for b in series]

    stats = compute_price_stats(series, as_of=as_of, windows=[1],
                                calendar=calendar, min_coverage=0.8)

    assert stats.avg_price[1] == pytest.approx(0.0)
    assert stats.drop_vs_avg[1] is None


# --- 추가: 정렬되지 않은 입력 ------------------------------------------


def test_unsorted_input_still_finds_latest_close_by_date():
    """입력 리스트의 순서가 아니라 날짜로 최신 봉을 가려야 한다."""
    ordered = bars(closes=[100.0, 110.0, 120.0, 130.0])  # 2021-01-01..04
    # 마지막 리스트 원소(인덱스 -1)가 최신 날짜가 아니도록 고의로 섞는다.
    shuffled = [ordered[2], ordered[0], ordered[3], ordered[1]]
    assert shuffled[-1].date != ordered[-1].date

    as_of = ordered[-1].date
    calendar = [b.date for b in ordered]

    stats = compute_price_stats(shuffled, as_of=as_of, windows=[1],
                                calendar=calendar, min_coverage=0.8)

    assert stats.price_current == 130.0


# --- 밸류에이션 (설계서 §9.2) -----------------------------------------


def fin(**overrides) -> Financials:
    base = dict(ticker="X", fiscal_year=2025, currency="KRW",
                revenue=1000.0, operating_income=100.0, total_equity=500.0,
                total_liabilities=200.0, net_income=50.0,
                shares_outstanding=100.0)
    base.update(overrides)
    return Financials(**base)


def test_valuation_computes_market_cap_per_pbr():
    v = compute_valuation(price=60.0, latest=fin())

    assert v.market_cap == pytest.approx(6000.0)   # 60 x 100
    assert v.per == pytest.approx(120.0)           # 60 / (50/100)
    assert v.pbr == pytest.approx(12.0)            # 60 / (500/100)


def test_valuation_is_all_none_without_shares():
    v = compute_valuation(price=60.0, latest=fin(shares_outstanding=None))

    assert (v.market_cap, v.per, v.pbr) == (None, None, None)


def test_valuation_is_all_none_without_financials():
    v = compute_valuation(price=60.0, latest=None)

    assert (v.market_cap, v.per, v.pbr) == (None, None, None)


def test_per_is_none_when_eps_nonpositive():
    """적자 기업의 PER은 의미가 없다 (설계서 §9.2)."""
    assert compute_valuation(price=60.0, latest=fin(net_income=-10.0)).per is None
    assert compute_valuation(price=60.0, latest=fin(net_income=0.0)).per is None


def test_pbr_is_none_when_bps_nonpositive():
    assert compute_valuation(price=60.0, latest=fin(total_equity=-10.0)).pbr is None


def test_market_cap_survives_when_per_is_none():
    """지표는 각각 독립이다. 하나가 없다고 나머지를 지우지 않는다."""
    v = compute_valuation(price=60.0, latest=fin(net_income=-10.0))

    assert v.market_cap == pytest.approx(6000.0)
    assert v.per is None
    assert v.pbr == pytest.approx(12.0)


def test_zero_shares_does_not_divide_by_zero():
    v = compute_valuation(price=60.0, latest=fin(shares_outstanding=0.0))

    assert (v.market_cap, v.per, v.pbr) == (None, None, None)


def test_negative_shares_yields_all_none():
    """발행주식수가 음수인 데이터는 있을 수 없지만, 방어적으로 짜여 있는지 확인한다.

    첫 번째 관문(`not latest.shares_outstanding`)은 None과 0.0만 걸러낸다.
    음수는 그 관문을 통과하므로, 음수를 걸러내는 두 번째 관문
    (`shares <= 0`)이 실제로 쓰인다는 것을 이 테스트가 고정한다.
    """
    v = compute_valuation(price=60.0, latest=fin(shares_outstanding=-100.0))

    assert (v.market_cap, v.per, v.pbr) == (None, None, None)


def test_per_is_none_when_net_income_missing():
    """net_income이 아예 없을 때도 (음수·0이 아니라 None) PER만 빠진다."""
    v = compute_valuation(price=60.0, latest=fin(net_income=None))

    assert v.market_cap == pytest.approx(6000.0)
    assert v.per is None
    assert v.pbr == pytest.approx(12.0)


def test_pbr_is_none_when_total_equity_missing():
    """total_equity가 아예 없을 때도 (음수·0이 아니라 None) PBR만 빠진다."""
    v = compute_valuation(price=60.0, latest=fin(total_equity=None))

    assert v.market_cap == pytest.approx(6000.0)
    assert v.per == pytest.approx(120.0)
    assert v.pbr is None


def test_zero_price_does_not_crash():
    """가격 0은 krx.py가 걸러내지만(§9.1), 이 함수 자체는 크래시 없이 0을 낸다."""
    v = compute_valuation(price=0.0, latest=fin())

    assert v.market_cap == pytest.approx(0.0)
    assert v.per == pytest.approx(0.0)
    assert v.pbr == pytest.approx(0.0)


def test_market_cap_uses_issued_shares_but_eps_uses_free_float():
    """설계서 §9.2: 자기주식은 배당도 의결권도 없다."""
    latest = Financials(
        ticker="005930.KS", fiscal_year=2024, currency="KRW",
        revenue=1.0, operating_income=1.0,
        net_income=1_000.0, total_equity=10_000.0, total_liabilities=1.0,
        shares_outstanding=1_000.0, shares_treasury=200.0,
    )

    val = compute_valuation(price=50.0, latest=latest)

    assert val.market_cap == 50_000.0             # 50 x 1,000 (발행)
    # EPS = 1,000 / 800 = 1.25 -> PER = 50 / 1.25 = 40
    assert val.per == pytest.approx(40.0)
    # BPS = 10,000 / 800 = 12.5 -> PBR = 50 / 12.5 = 4
    assert val.pbr == pytest.approx(4.0)


def test_missing_treasury_falls_back_to_issued_shares():
    """자기주식 수집이 실패해도 PER·PBR을 포기하지 않는다.

    자기주식이 없는 회사와 못 구한 회사를 구분할 방법이 없지만, 대개
    자기주식 비중은 작다. NULL로 만드는 쪽이 손해가 크다.
    """
    latest = Financials(
        ticker="000660.KS", fiscal_year=2024, currency="KRW",
        revenue=1.0, operating_income=1.0,
        net_income=1_000.0, total_equity=10_000.0, total_liabilities=1.0,
        shares_outstanding=1_000.0, shares_treasury=None,
    )

    val = compute_valuation(price=50.0, latest=latest)

    assert val.per == pytest.approx(50.0)         # EPS = 1.0


def test_treasury_larger_than_issued_falls_back_to_issued():
    """말이 안 되는 데이터로 0이나 음수로 나누지 않는다."""
    latest = Financials(
        ticker="000000.KS", fiscal_year=2024, currency="KRW",
        revenue=1.0, operating_income=1.0,
        net_income=1_000.0, total_equity=10_000.0, total_liabilities=1.0,
        shares_outstanding=1_000.0, shares_treasury=1_000.0,
    )

    val = compute_valuation(price=50.0, latest=latest)

    assert val.market_cap == 50_000.0
    assert val.per == pytest.approx(50.0)


# --- 기간별 고점 (화면의 비교 기간 슬라이더) ------------------------------


def test_high_is_computed_for_every_window():
    """2년 전의 고점은 1년 고점에 안 잡히고 2년 고점에는 잡힌다."""
    as_of = date(2026, 1, 1)
    series = [
        PriceBar(ticker="X", date=date(2024, 6, 1), close=300.0),   # 1.5년 전 고점
        PriceBar(ticker="X", date=date(2025, 6, 1), close=200.0),   # 0.5년 전
        PriceBar(ticker="X", date=date(2026, 1, 1), close=150.0),   # 현재
    ]
    calendar = [b.date for b in series]

    stats = compute_price_stats(series, as_of=as_of, windows=[1, 2],
                                calendar=calendar, min_coverage=0.0)

    assert stats.high_by_window == {1: 200.0, 2: 300.0}
    assert stats.drop_vs_high_by_window[1] == pytest.approx(150 / 200 - 1)
    assert stats.drop_vs_high_by_window[2] == pytest.approx(150 / 300 - 1)


def test_window_highs_ignore_the_coverage_gate():
    """평균과 달리 고점은 커버리지와 무관하다 (5년 고점과 같은 규칙)."""
    as_of = date(2026, 1, 1)
    series = [PriceBar(ticker="X", date=as_of, close=100.0)]
    calendar = [as_of - timedelta(days=i) for i in range(300)]

    stats = compute_price_stats(series, as_of=as_of, windows=[1],
                                calendar=calendar, min_coverage=0.8)

    assert stats.avg_price[1] is None
    assert stats.high_by_window == {1: 100.0}


def test_the_five_year_high_matches_its_window_entry():
    as_of = date(2026, 1, 1)
    series = [PriceBar(ticker="X", date=date(2022, 1, 1), close=500.0),
              PriceBar(ticker="X", date=as_of, close=100.0)]

    stats = compute_price_stats(series, as_of=as_of, windows=[5],
                                calendar=[b.date for b in series], min_coverage=0.0)

    assert stats.high_by_window[5] == stats.high_5y == 500.0



def test_fcf_yield_is_free_cash_flow_over_market_cap():
    fin = Financials(ticker="X", fiscal_year=2025, currency="KRW",
                     shares_outstanding=100.0, operating_cash_flow=500.0, capex=200.0)

    v = compute_valuation(10.0, fin)   # 시가총액 1,000

    assert v.fcf_yield == pytest.approx(0.3)


def test_fcf_yield_is_none_without_cash_flow():
    fin = Financials(ticker="X", fiscal_year=2025, currency="KRW", shares_outstanding=100.0)

    assert compute_valuation(10.0, fin).fcf_yield is None
