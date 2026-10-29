"""영탁센스: 거래량이 터지며 신고가를 돌파하고 그 위를 이어가는 종목.

낙폭 우량주(grading.py)와 정반대 성격이다 - 빠진 것이 돌아오기를 기다리는
대신, 오르는 것이 계속 오르기를 기대하는 추세 추종이다. 52주 신고가 근처
종목이 이후 더 오르는 경향은 실증 연구에도 있다. 다만 돌파 뒤 크게 꺾이는
경우도 있어(실측: 한화시스템 돌파 후 -64%) stockscout backtest로 검증한다.

순수 함수다. 돌파의 원재료(기간별 돌파일·거래량 배수·유지 여부)는
compute_breakouts가 계산하고, 판정(돌파·임박)은 breakout_signal이 설정으로
한다 - 화면은 같은 원재료로 슬라이더 값만 바꿔 다시 판정한다.
"""

from __future__ import annotations

from datetime import date

from stockscout.config import BreakoutConfig
from stockscout.models import BreakoutStat, PriceBar

SIGNAL_BREAKOUT = "BREAKOUT"   # 거래량을 동반해 신고가를 돌파했고 그 위를 유지
SIGNAL_NEAR = "NEAR"           # 신고가 바로 아래에서 거래량이 늘고 있다

VOLUME_WINDOW = 20      # 거래량 배수의 분모: 직전 20거래일 평균
SCAN_DAYS = 20          # 최근 이 거래일 안의 돌파를 본다 (화면은 그중 recent_days)
RECENT_VOLUME_DAYS = 5  # 임박 판정의 최근 거래량 구간
MIN_HISTORY_RATIO = 0.9 # 기간의 90% 이상 과거가 있어야 그 기간 신고가를 말한다


def compute_breakouts(
    bars: list[PriceBar], as_of: date, lookbacks: list[int]
) -> dict[int, BreakoutStat]:
    """기간(거래일)별 신고가 돌파의 원재료.

    돌파일 = 종가가 직전 L거래일 최고 종가를 넘은 날. 최근 SCAN_DAYS 안의
    돌파 중 거래량 배수가 가장 큰 날을 남긴다 - 첫 돌파는 약하고 다음 경신에서
    거래량이 터지는 경우가 흔하다.
    """
    ordered = [b for b in sorted(bars, key=lambda b: b.date) if b.date <= as_of]
    if not ordered:
        return {}
    closes = [b.close for b in ordered]
    volumes = [b.volume for b in ordered]
    n = len(ordered)
    current = closes[-1]

    def avg_volume(end: int, days: int) -> float | None:
        window = [v for v in volumes[max(0, end - days):end] if v is not None]
        return sum(window) / len(window) if window and sum(window) > 0 else None

    out: dict[int, BreakoutStat] = {}
    for lb in lookbacks:
        if n - 1 < lb * MIN_HISTORY_RATIO:
            continue
        best = None   # (ratio, i, level)
        latest_any = None
        for i in range(max(1, n - SCAN_DAYS), n):
            prior = closes[max(0, i - lb):i]
            level = max(prior)
            if closes[i] <= level:
                continue
            base = avg_volume(i, VOLUME_WINDOW)
            ratio = volumes[i] / base if base and volumes[i] is not None else None
            latest_any = (ratio, i, level)
            if ratio is not None and (best is None or ratio >= best[0]):
                best = (ratio, i, level)
        pick = best or latest_any
        high = max(closes[max(0, n - lb):])
        recent_base = avg_volume(n - RECENT_VOLUME_DAYS, VOLUME_WINDOW)
        recent = avg_volume(n, RECENT_VOLUME_DAYS)
        out[lb] = BreakoutStat(
            high=high,
            distance_to_high=current / high - 1 if high > 0 else 0.0,
            breakout_days_ago=(n - 1 - pick[1]) if pick else None,
            breakout_date=ordered[pick[1]].date if pick else None,
            breakout_level=pick[2] if pick else None,
            breakout_volume_ratio=pick[0] if pick else None,
            holding=(current >= pick[2] * (1 - HOLD_TOLERANCE)) if pick else False,
            recent_volume_ratio=(recent / recent_base) if recent and recent_base else None,
        )
    return out


# 돌파선 아래로 이만큼까지는 '유지'로 본다. 돌파 직후 되돌림은 흔하다.
HOLD_TOLERANCE = 0.03


def breakout_signal(stat: BreakoutStat, config: BreakoutConfig) -> str | None:
    """돌파·임박·없음. 화면(app.js의 senseOf)과 같은 규칙이다."""
    if (stat.breakout_days_ago is not None
            and stat.breakout_days_ago <= config.recent_days
            and stat.breakout_volume_ratio is not None
            and stat.breakout_volume_ratio >= config.volume_multiple
            and stat.holding):
        return SIGNAL_BREAKOUT
    if (stat.distance_to_high >= -config.near_pct
            and stat.recent_volume_ratio is not None
            and stat.recent_volume_ratio >= config.near_volume_multiple):
        return SIGNAL_NEAR
    return None
