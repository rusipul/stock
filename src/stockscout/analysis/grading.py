"""등급 판정. 설계서 §9.3.

점수(품질)와 하락(가격)을 함께 본다. scoring.py가 가격을 모르고
valuation.py가 점수를 모르므로, 둘을 합치는 자리를 따로 둔다.

기호를 쓰지 않는다. 이 문자열은 웹으로도 CLI로도 가고, 한국어 Windows
콘솔은 cp949라 별표 같은 문자를 출력하면 UnicodeEncodeError로 죽는다.
등급 값은 STRONG/WATCH/NONE이고 별은 화면이 붙인다.
"""

from __future__ import annotations

from stockscout.config import SignalConfig
from stockscout.models import PriceStats

GRADE_STRONG = "STRONG"   # 화면에서 별 둘
GRADE_WATCH = "WATCH"     # 화면에서 별 하나
GRADE_NONE = "NONE"


def grade_signal(
    stats: PriceStats,
    score_total: float | None,
    passed_filters: bool,
    config: SignalConfig,
) -> tuple[str, str]:
    """(등급, 판정 근거 문장)을 낸다.

    목적: 우량주가 최근 몇 달 사이 크게 빠진 경우를 찾는다.

    품질이 먼저다. 아무리 많이 떨어져도 우량주가 아니면 등급이 없다 -
    많이 떨어진 데에는 대개 이유가 있기 때문이다. 그다음 최근
    drawdown_months개월 고점 대비 하락이 임계치 이하면 후보이고, 그 하락이
    멈췄으면 별 둘, 아직 빠지는 중이면 별 하나다.
    """
    if not passed_filters:
        return GRADE_NONE, "하드필터에 걸려 등급을 매기지 않습니다"
    if score_total is None:
        return GRADE_NONE, "우량주 점수가 없어 등급을 매기지 않습니다"
    if score_total < config.min_score:
        return GRADE_NONE, (
            f"우량주 점수 {score_total:.0f}점이 기준 "
            f"{config.min_score:.0f}점에 못 미칩니다"
        )

    months = config.drawdown_months
    dd = stats.drawdown.get(months)
    if dd is None:
        return GRADE_NONE, f"최근 {months}개월 주가가 없어 등급을 매기지 않습니다"

    hit = dd.drop_from_high <= config.drawdown_threshold
    reason = (
        f"우량주 점수 {score_total:.0f}점 · 최근 {months}개월 고점 대비 "
        f"{dd.drop_from_high * 100:.1f}% {'<=' if hit else '>'} "
        f"{config.drawdown_threshold * 100:.0f}%"
    )
    if not hit:
        return GRADE_NONE, reason
    if dd.stabilized:
        return GRADE_STRONG, (
            f"{reason} · 하락 멈춤 (저점 이후 {dd.days_since_low}거래일"
            f"{', ' + str(config.ma_days) + '일 이동평균 위' if dd.above_ma else ''})"
        )
    return GRADE_WATCH, f"{reason} · 아직 하락 중 (저점 이후 {dd.days_since_low}거래일)"
