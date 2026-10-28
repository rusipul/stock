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
    primary_window: int,
) -> tuple[str, str]:
    """(등급, 판정 근거 문장)을 낸다.

    품질이 먼저다. 아무리 많이 떨어져도 우량주가 아니면 등급이 없다 -
    많이 떨어진 데에는 대개 이유가 있기 때문이다.
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

    drop_avg = stats.drop_vs_avg.get(primary_window)
    avg_hit = drop_avg is not None and drop_avg <= config.drop_vs_avg_threshold
    high_hit = stats.drop_vs_high <= config.drop_vs_high_threshold

    parts = []
    if drop_avg is None:
        parts.append(f"{primary_window}년 평균 대비: 데이터 부족으로 판정 제외")
    else:
        parts.append(
            f"{primary_window}년 평균 대비 {drop_avg * 100:.1f}% "
            f"{'<=' if avg_hit else '>'} {config.drop_vs_avg_threshold * 100:.0f}%"
        )
    parts.append(
        f"5년 고점 대비 {stats.drop_vs_high * 100:.1f}% "
        f"{'<=' if high_hit else '>'} {config.drop_vs_high_threshold * 100:.0f}%"
    )
    reason = f"우량주 점수 {score_total:.0f}점 · " + " · ".join(parts)

    if avg_hit and high_hit:
        return GRADE_STRONG, reason
    if avg_hit or high_hit:
        return GRADE_WATCH, reason
    return GRADE_NONE, reason
