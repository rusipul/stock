"""4축 스코어링. 설계서 §8.2·§8.3.

순수 함수다. 네트워크도 DB도 모른다. 배점과 구간은 전부 설정(config)에서
오고 이 파일에는 숫자가 없다 - 사용자가 YAML만 고쳐 기준을 바꿀 수 있어야
한다.
"""

from __future__ import annotations

from stockscout.config import MetricConfig
from stockscout.models import (
    AXIS_UNMEASURABLE,
    AxisScore,
    Financials,
    MetricScore,
    ScoreCard,
    STATUS_BASE_NEGATIVE,
    STATUS_DENOMINATOR_NONPOSITIVE,
    STATUS_END_NONPOSITIVE,
    STATUS_MISSING,
    STATUS_OK,
)

CAGR_YEARS = 3               # 설계서 §8.2의 "3년 CAGR"
AVG_YEARS = 3                # "3년 평균"
TREND_YEARS = 3              # "최근 3년 추세"
CONSISTENCY_COMPARISONS = 4  # "최근 4년 중 증가한 해"
STREAK_YEARS = 5             # 흑자 연속년수를 세는 최대 구간
MIN_AVG_YEARS = 2            # 평균을 낼 최소 표본
MIN_CONSISTENCY_COMPARISONS = 3
TREND_FLAT_BAND = 0.5        # %p/년. 이 안쪽은 보합으로 본다
TREND_FLAT_POINTS_RATIO = 0.4  # 보합은 배점의 40% (5점 중 2점)


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


def _missing(name: str, cfg: MetricConfig) -> MetricScore:
    return MetricScore(metric=name, value=None, points=0.0,
                       max_points=cfg.points, status=STATUS_MISSING)


def _scored(name: str, value: float, cfg: MetricConfig,
            points: float | None = None, status: str = STATUS_OK) -> MetricScore:
    return MetricScore(
        metric=name, value=value,
        points=band_score(value, cfg) if points is None else points,
        max_points=cfg.points, status=status,
    )


def _latest(years: list[Financials]) -> Financials | None:
    return years[-1] if years else None


# --- 수익성 ---------------------------------------------------------

def metric_roe_avg3(years: list[Financials], cfg: MetricConfig) -> MetricScore:
    values = [f.roe for f in years[-AVG_YEARS:] if f.roe is not None]
    if len(values) < MIN_AVG_YEARS:
        return _missing("roe_avg3", cfg)
    return _scored("roe_avg3", sum(values) / len(values), cfg)


def metric_op_margin_avg3(years: list[Financials], cfg: MetricConfig) -> MetricScore:
    values = [f.operating_margin for f in years[-AVG_YEARS:]
              if f.operating_margin is not None]
    if len(values) < MIN_AVG_YEARS:
        return _missing("op_margin_avg3", cfg)
    return _scored("op_margin_avg3", sum(values) / len(values), cfg)


def metric_op_margin_trend(years: list[Financials], cfg: MetricConfig) -> MetricScore:
    """최근 3년 영업이익률의 선형회귀 기울기 (연당 %p).

    bands를 쓰지 않는다. 설계서 §8.2가 보합 구간을 따로 정의하기 때문에
    구간표로 표현되지 않는다.
    """
    margins = [f.operating_margin for f in years[-TREND_YEARS:]]
    if len(margins) < TREND_YEARS or any(m is None for m in margins):
        return _missing("op_margin_trend", cfg)

    n = len(margins)
    mean_x = (n - 1) / 2
    mean_y = sum(margins) / n
    numerator = sum((i - mean_x) * (m - mean_y) for i, m in enumerate(margins))
    denominator = sum((i - mean_x) ** 2 for i in range(n))
    slope_pp = numerator / denominator * 100   # 비율 -> %p

    if slope_pp > TREND_FLAT_BAND:
        points = cfg.points
    elif slope_pp >= -TREND_FLAT_BAND:
        points = cfg.points * TREND_FLAT_POINTS_RATIO
    else:
        points = 0.0
    return _scored("op_margin_trend", slope_pp, cfg, points=points)


# --- 성장성 ---------------------------------------------------------

def _cagr(years: list[Financials], field: str, name: str,
          cfg: MetricConfig) -> MetricScore:
    if len(years) <= CAGR_YEARS:
        return _missing(name, cfg)
    base = getattr(years[-1 - CAGR_YEARS], field)
    now = getattr(years[-1], field)
    if base is None or now is None:
        return _missing(name, cfg)
    if base <= 0:
        # 기저가 음수·0이면 CAGR이 정의되지 않는다. 0점 처리하되 유효만점에는
        # 남긴다 - 3년 전 적자는 데이터 공백이 아니라 사업 사실이다.
        return MetricScore(metric=name, value=None, points=0.0,
                           max_points=cfg.points, status=STATUS_BASE_NEGATIVE)
    if now <= 0:
        # 흑자에서 적자로 돌아섰다. 음수의 거듭제곱근은 복소수가 되어 구간
        # 비교에서 TypeError로 죽는다(실측: 삼성SDI). 이것도 사업 사실이므로
        # 0점이되 유효만점에 남긴다.
        return MetricScore(metric=name, value=None, points=0.0,
                           max_points=cfg.points, status=STATUS_END_NONPOSITIVE)
    return _scored(name, (now / base) ** (1 / CAGR_YEARS) - 1, cfg)


def metric_revenue_cagr3(years: list[Financials], cfg: MetricConfig) -> MetricScore:
    return _cagr(years, "revenue", "revenue_cagr3", cfg)


def metric_op_income_cagr3(years: list[Financials], cfg: MetricConfig) -> MetricScore:
    return _cagr(years, "operating_income", "op_income_cagr3", cfg)


def metric_growth_consistency(years: list[Financials],
                              cfg: MetricConfig) -> MetricScore:
    """최근 4년 중 매출이 전년 대비 증가한 해의 비율.

    설계서 §8.2는 "증가한 해의 수 x 1.25"라고 쓰지만, 비교 가능한 해가
    4년보다 적을 수 있으므로 비율로 계산해 배점을 곱한다. 4년이 전부
    있으면 결과가 같다.
    """
    window = years[-(CONSISTENCY_COMPARISONS + 1):]
    pairs = [
        (prev.revenue, curr.revenue)
        for prev, curr in zip(window, window[1:])
        if prev.revenue is not None and curr.revenue is not None
    ]
    if len(pairs) < MIN_CONSISTENCY_COMPARISONS:
        return _missing("growth_consistency", cfg)
    ratio = sum(1 for prev, curr in pairs if curr > prev) / len(pairs)
    return _scored("growth_consistency", ratio, cfg, points=ratio * cfg.points)


# --- 안정성 ---------------------------------------------------------

def metric_debt_ratio(years: list[Financials], cfg: MetricConfig) -> MetricScore:
    latest = _latest(years)
    if latest is None or latest.debt_ratio is None:
        return _missing("debt_ratio", cfg)
    return _scored("debt_ratio", latest.debt_ratio, cfg)


def metric_current_ratio(years: list[Financials], cfg: MetricConfig) -> MetricScore:
    latest = _latest(years)
    if latest is None or latest.current_ratio is None:
        return _missing("current_ratio", cfg)
    return _scored("current_ratio", latest.current_ratio, cfg)


def metric_interest_coverage(years: list[Financials],
                             cfg: MetricConfig) -> MetricScore:
    latest = _latest(years)
    if latest is None or latest.interest_coverage is None:
        return _missing("interest_coverage", cfg)
    # interest_coverage는 이자비용이 0이면 inf다. band_score가 첫 구간을
    # 집으므로 자연히 만점이 된다.
    return _scored("interest_coverage", latest.interest_coverage, cfg)


# --- 현금흐름 -------------------------------------------------------

def _positive_streak(values: list[float | None]) -> int:
    """뒤에서부터 연속으로 0보다 큰 값의 개수. None을 만나면 멈춘다."""
    streak = 0
    for value in reversed(values):
        if value is None or value <= 0:
            break
        streak += 1
    return streak


def metric_ocf_positive_years(years: list[Financials],
                              cfg: MetricConfig) -> MetricScore:
    window = years[-STREAK_YEARS:]
    if all(f.operating_cash_flow is None for f in window):
        return _missing("ocf_positive_years", cfg)
    streak = _positive_streak([f.operating_cash_flow for f in window])
    return _scored("ocf_positive_years", float(streak), cfg)


def metric_ocf_to_op_income(years: list[Financials],
                            cfg: MetricConfig) -> MetricScore:
    window = years[-AVG_YEARS:]
    ocf = [f.operating_cash_flow for f in window]
    op = [f.operating_income for f in window]
    if len(window) < AVG_YEARS or any(v is None for v in ocf + op):
        return _missing("ocf_to_op_income", cfg)
    denominator = sum(op)
    if denominator <= 0:
        return MetricScore(metric="ocf_to_op_income", value=None, points=0.0,
                           max_points=cfg.points,
                           status=STATUS_DENOMINATOR_NONPOSITIVE)
    return _scored("ocf_to_op_income", sum(ocf) / denominator, cfg)


def metric_fcf_positive_years(years: list[Financials],
                              cfg: MetricConfig) -> MetricScore:
    window = years[-STREAK_YEARS:]
    if all(f.free_cash_flow is None for f in window):
        return _missing("fcf_positive_years", cfg)
    streak = _positive_streak([f.free_cash_flow for f in window])
    return _scored("fcf_positive_years", float(streak), cfg)


# 축 이름 -> 지표 이름 -> 계산 함수. 설정의 scoring 섹션 구조와 맞물린다.
METRIC_FUNCTIONS = {
    "profitability": {
        "roe_avg3": metric_roe_avg3,
        "op_margin_avg3": metric_op_margin_avg3,
        "op_margin_trend": metric_op_margin_trend,
    },
    "growth": {
        "revenue_cagr3": metric_revenue_cagr3,
        "op_income_cagr3": metric_op_income_cagr3,
        "growth_consistency": metric_growth_consistency,
    },
    "stability": {
        "debt_ratio": metric_debt_ratio,
        "current_ratio": metric_current_ratio,
        "interest_coverage": metric_interest_coverage,
    },
    "cashflow": {
        "ocf_positive_years": metric_ocf_positive_years,
        "ocf_to_op_income": metric_ocf_to_op_income,
        "fcf_positive_years": metric_fcf_positive_years,
    },
}

# 축 하한 (설계서 §8.3). 유효만점이 배점의 이 비율 미만이면 축을 0점 처리한다.
AXIS_FLOOR_RATIO = 0.5


def score_security(
    financials: list[Financials],
    scoring: dict[str, dict[str, MetricConfig]],
) -> ScoreCard:
    """재무 목록을 4축 100점으로 채점한다. 설계서 §8.2·§8.3.

    두 규칙이 서로 반대로 당긴다.

    환산: 못 구한 지표는 획득점수와 유효만점 양쪽에서 뺀다. 계정과목
    매핑 실패가 사업 부실로 둔갑하지 않게 하려는 것이다.

    축 하한: 한 축의 유효만점이 배점의 절반 미만이면 환산하지 않고 그
    축을 0점 처리한다. 이때 **배점 전액을 분모에 남긴다** - 분모에서도
    빼면 0점 처리가 아무 효과가 없어진다(0/0이라 총점이 그대로다).
    "볼 수 없다"가 면제가 아니라 감점이 되어야 한다.
    """
    years = sorted(financials, key=lambda f: f.fiscal_year)

    axes: list[AxisScore] = []
    for axis_name, metrics_config in scoring.items():
        if axis_name not in METRIC_FUNCTIONS:
            raise ValueError(
                f"설정의 scoring에 모르는 축이 있습니다: {axis_name} "
                f"(가능: {sorted(METRIC_FUNCTIONS)})"
            )
        known = METRIC_FUNCTIONS[axis_name]

        scores: list[MetricScore] = []
        for metric_name, cfg in metrics_config.items():
            if metric_name not in known:
                raise ValueError(
                    f"설정의 scoring.{axis_name}에 모르는 지표가 있습니다: "
                    f"{metric_name} (가능: {sorted(known)})"
                )
            scores.append(known[metric_name](years, cfg))

        axis_max = sum(m.max_points for m in scores)
        measurable = [m for m in scores if m.status != STATUS_MISSING]
        effective_max = sum(m.max_points for m in measurable)

        if effective_max < axis_max * AXIS_FLOOR_RATIO:
            axes.append(AxisScore(
                axis=axis_name, metrics=scores,
                earned=0.0, denominator=axis_max, max_points=axis_max,
                status=AXIS_UNMEASURABLE,
            ))
        else:
            axes.append(AxisScore(
                axis=axis_name, metrics=scores,
                earned=sum(m.points for m in measurable),
                denominator=effective_max, max_points=axis_max,
                status=STATUS_OK,
            ))

    return ScoreCard(axes=axes)
