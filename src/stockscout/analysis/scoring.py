"""4축 스코어링. 설계서 §8.2·§8.3.

순수 함수다. 네트워크도 DB도 모른다. 배점과 구간은 전부 설정(config)에서
오고 이 파일에는 숫자가 없다 - 사용자가 YAML만 고쳐 기준을 바꿀 수 있어야
한다.
"""

from __future__ import annotations

from stockscout.config import MetricConfig


def band_score(value: float, metric: MetricConfig) -> float:
    """구간표에서 점수를 찾는다. 위에서부터 첫 매치를 취한다.

    bands가 비어 있으면 0을 돌려준다. 전용 계산이 필요한 지표
    (op_margin_trend, growth_consistency)를 실수로 여기에 넘겼을 때
    조용히 만점이 나가는 것보다 0점이 낫다 - 0점은 눈에 띈다.
    """
    for threshold, points in metric.bands:
        if metric.lower_is_better:
            if value <= threshold:
                return points
        elif value >= threshold:
            return points
    return 0.0
