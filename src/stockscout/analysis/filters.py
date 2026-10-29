"""하드필터. 설계서 §8.1.

걸리면 점수를 계산하지 않고 제외한다. 가격·업종 기반 판정은
apply_price_filters가, 재무제표 기반 판정은 apply_financial_filters가 한다.

순수 함수다. 네트워크도 DB도 모른다.
"""

from __future__ import annotations

from datetime import date

from stockscout.analysis.valuation import window_start
from stockscout.config import HardFilterConfig
from stockscout.models import FilterOutcome, Financials, Security, is_adverse_opinion

FINANCIAL_SECTOR = "FINANCIAL_SECTOR"
INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
CAPITAL_IMPAIRED = "CAPITAL_IMPAIRED"
CONSECUTIVE_LOSS = "CONSECUTIVE_LOSS"
HIGH_DEBT = "HIGH_DEBT"
INTEREST_UNCOVERED = "INTEREST_UNCOVERED"
REVENUE_COLLAPSE = "REVENUE_COLLAPSE"
# 1년 안에 갚을 차입금 > 현금 + 1년 영업현금흐름. 과거 흑자여도 만기 부채를
# 당장 갚지 못할 수 있다. 차환(롤오버)이 막히면 우량주도 무너진다.
LIQUIDITY_SHORTFALL = "LIQUIDITY_SHORTFALL"
# 최근 20거래일 평균 거래대금이 하한 미만. 계산상 수익과 실제 매매 수익의
# 차이(호가 공백·체결 불가)를 줄인다.
LOW_TRADING_VALUE = "LOW_TRADING_VALUE"
TRADING_VALUE_DAYS = 20


def apply_trading_value_filter(bars, min_value: float | None) -> list[str]:
    """최근 TRADING_VALUE_DAYS 봉의 평균 거래대금(종가 x 거래량)이 하한 미만이면 실패.

    하한이 없거나 거래량을 모르면 건너뛴다 (모르는 것으로 떨어뜨리지 않는다).
    """
    if min_value is None:
        return []
    recent = sorted(bars, key=lambda b: b.date)[-TRADING_VALUE_DAYS:]
    values = [b.close * b.volume for b in recent if b.volume is not None]
    if not values:
        return []
    return [LOW_TRADING_VALUE] if sum(values) / len(values) < min_value else []
DATA_INSUFFICIENT = "DATA_INSUFFICIENT"
ADVERSE_AUDIT_OPINION = "ADVERSE_AUDIT_OPINION"

# 매출 급감을 재는 간격. revenue[최근] 대 revenue[3년전]이므로 목록에서
# 4개 연도가 필요하다 (설계서 §8.1).
REVENUE_LOOKBACK_YEARS = 3


def apply_price_filters(
    security: Security,
    first_bar_date: date | None,
    as_of: date,
    min_history_years: int,
) -> FilterOutcome:
    """가격·업종 기반 하드필터를 적용한다.

    첫 실패에서 멈추지 않고 모든 사유를 모은다. 화면에서 "왜 제외됐나"를
    보여줄 때 사유가 하나만 있으면 고치고 다시 돌렸을 때 또 다른 사유로
    걸려, 사용자가 같은 과정을 반복하게 된다.

    first_bar_date는 그 종목의 "진짜" 첫 저장 봉 날짜여야 한다 —
    Repository.first_price_date()처럼 조회 구간과 무관하게 구한 값.
    봉 목록(list[PriceBar])을 받아 여기서 min()을 구하던 옛 설계로
    되돌리면 안 된다. 호출자(analyze.py)는 평균 계산을 위해 주가를
    5년 창으로 잘라 읽는데, 그 잘린 목록을 그대로 넘기면
    min_history_years가 그 창의 길이와 같을 때(기본값 5년) 목록의
    최솟값이 구성상 항상 창의 시작일 이상이 된다. 그러면 아래의
    "first_bar_date > required_start"가 사실상 "창 시작일에 정확히
    거래일이 있는가"만 묻는 것으로 퇴화하고, 5년을 뒤로 미는 시프트가
    요일을 바꾸므로 이는 동전 던지기가 된다 — 실측: as_of=2026-09-11이면
    required_start(2021-09-11)가 토요일이 되어 42종목 전부가
    INSUFFICIENT_HISTORY로 탈락했다. date 하나만 받게 한 것은 그 실수를
    애초에 불가능하게 만들기 위해서다: 호출자가 잘린 목록을 실수로
    넘길 수 없다. "단순화"해 list를 다시 받도록 되돌리고 싶어질 수
    있는데, 그게 바로 이 버그였다.
    """
    failures: list[str] = []

    # 은행·보험·증권은 부채비율·유동비율이 구조적으로 달라 제조업과
    # 같은 잣대로 재면 무의미하다. 전용 필터셋은 설계서 §16의 과제다.
    if security.is_financial:
        failures.append(FINANCIAL_SECTOR)

    # 달력 기준으로 잰다. 연분수(days / 365.25)로 재면 정확히 5달력년인
    # 구간이 4.9993년이 되어 전 종목이 탈락한다.
    required_start = window_start(as_of, min_history_years)
    if first_bar_date is None or first_bar_date > required_start:
        failures.append(INSUFFICIENT_HISTORY)

    return FilterOutcome(failures=failures)


def apply_financial_filters(
    financials: list[Financials], config: HardFilterConfig
) -> FilterOutcome:
    """재무제표 기반 하드필터를 적용한다. 설계서 §8.1.

    첫 실패에서 멈추지 않고 모든 사유를 모은다 — apply_price_filters와
    같은 이유다.

    "모르는 것으로 떨어뜨리지 않는다"가 이 함수 전체를 관통하는 규칙이다.
    이자비용이 NULL이면 이자보상배율 필터를 건너뛰고, 감사의견이 NULL이면
    감사의견 필터를 건너뛴다. 계정과목 매핑 실패가 사업 부실로 둔갑하면
    안 된다. 결측 자체는 DATA_INSUFFICIENT가 필수 4항목에 대해서만 잡는다.
    """
    failures: list[str] = []

    # Repository는 연도 오름차순으로 주지만 그것에 기대지 않는다.
    years = sorted(financials, key=lambda f: f.fiscal_year)

    if len(years) < config.min_history_years:
        failures.append(INSUFFICIENT_HISTORY)

    if not years or not years[-1].has_required:
        failures.append(DATA_INSUFFICIENT)

    if not years:
        return FilterOutcome(failures=failures)

    latest = years[-1]

    if latest.total_equity is not None and latest.total_equity <= 0:
        failures.append(CAPITAL_IMPAIRED)

    loss_window = years[-config.consecutive_operating_loss_years:]
    if (
        len(loss_window) == config.consecutive_operating_loss_years
        and all(
            f.operating_income is not None and f.operating_income < 0
            for f in loss_window
        )
    ):
        failures.append(CONSECUTIVE_LOSS)

    # debt_ratio는 자본이 0 이하면 None이다. 그 경우는 CAPITAL_IMPAIRED가
    # 이미 잡았으므로 여기서 또 떨어뜨리지 않는다.
    if latest.debt_ratio is not None and latest.debt_ratio > config.max_debt_ratio:
        failures.append(HIGH_DEBT)

    # 창의 모든 해에서 이자보상배율을 실제로 구할 수 있을 때만 판정한다.
    # 한 해라도 빠지면 len 검사에서 걸려 필터 자체를 건너뛴다 — 무차입이었던
    # 해가 섞여 있다고 해서 나머지 한 해의 부진으로 떨어뜨리지 않는다.
    # §8.1이 요구하는 것은 "최근 2년 *모두* 배율 < 1"이고, 두 해 중 하나를
    # 잴 수 없으면 "모두"를 확인할 방법이 없다. 실측 한 해만으로 떨어뜨리면
    # 그건 2년 필터가 아니라 1년 필터다.
    #
    # 금융원가로 채운 해를 빼는 이유(설계서 §8.1.1): 금융원가는 이자비용의
    # 상위 개념이라 외화부채 환산손실·파생상품손실이 섞여 있고, 그 비중이
    # 회사마다 다르다. 실측에서 SK이노베이션의 "이자비용"은 6.36조 —
    # 매출 80.3조의 8%였다. 어떤 제조업체도 매출의 8%를 이자로 내지 않는다.
    # 배율이 3~10배 낮게 나오므로 이 값으로 내리는 이진 판정은 보수적인
    # 것이 아니라 틀린 것이다(대체값을 안 쓴 한국전력은 배율 3.11로 멀쩡히
    # 통과했다 — 규칙이 아니라 입력이 문제라는 뜻이다).
    #
    # 근거는 §8.1이 이미 세운 규칙이다. §8.1의 표는 interest_expense가
    # NULL이면 통과시킨다. 금융원가로 채운 값은 이자비용을 *모르는* 상태의 상한
    # 추정치이지 이자비용이 아니다. 인식론적으로 NULL 쪽에 속하므로 같은
    # 대접을 받아야 한다. "대체값이라도 쓰는 게 보수적이지 않나?" 싶다면
    # 위의 6.36조를 다시 봐라.
    #
    # 아래 세 조건 중 겹치는 것은 첫째와 셋째다 — 가운데 proxy_fields
    # 조건이 아니다. 가운데는 방금 말한 §8.1.1 그 자체이며 하중을 받는
    # 유일한 조건이다. 떼면 테스트가 즉시 실패한다. **지우지 마라.**
    #
    # 첫째와 셋째가 겹치는 이유: interest_coverage가 interest_expense=None일
    # 때 이미 None을 주고, 0일 때 주는 inf는 all(c < 임계치)를 항상 False로
    # 만든다. 변이 테스트로 확인한 결과는 이렇다 — 첫째만 떼도 전 테스트가
    # 통과하고, 셋째만 떼도 전 테스트가 통과한다. **둘 다 단독으로는
    # 등가 변이다.** 둘을 함께 떼야 coverages에 None이 섞여 아래 비교가
    # TypeError로 죽는다.
    #
    # 그렇다면 왜 첫째를 남기는가. 그것이 §8.1 표의 문장("NULL이거나 0이면
    # 통과")을 코드에 그대로 옮긴 유일한 자리이기 때문이다. 셋째는 그 규칙을
    # 우연히 포함하는 구현 수준의 None 가드일 뿐이다. 첫째를 지우면 규칙이
    # 이 코드 어디에도 문장으로 남지 않고, 이자비용 0인 해의 처리가 두 파일
    # 건너 models.py의 inf 반환에 암묵적으로 의존하게 된다. 여기서는 DRY보다
    # 규칙을 눈에 보이게 두는 쪽이 이긴다.
    coverage_window = years[-config.interest_coverage_years:]
    coverages = [
        f.interest_coverage
        for f in coverage_window
        if f.interest_expense not in (None, 0)
        and "interest_expense" not in f.proxy_fields
        and f.interest_coverage is not None
    ]
    if (
        len(coverages) == config.interest_coverage_years
        and all(c < config.min_interest_coverage for c in coverages)
    ):
        failures.append(INTEREST_UNCOVERED)

    if len(years) > REVENUE_LOOKBACK_YEARS:
        base = years[-1 - REVENUE_LOOKBACK_YEARS].revenue
        now = latest.revenue
        if base is not None and base > 0 and now is not None:
            if now / base - 1 <= config.max_revenue_decline_3y:
                failures.append(REVENUE_COLLAPSE)

    # 12개월 자금 여력. 현금이나 단기차입금을 모르면 건너뛴다. 영업현금흐름
    # 적자는 재원이 아니므로 0으로 본다.
    if latest.cash is not None and latest.short_term_debt is not None:
        resources = latest.cash + max(latest.operating_cash_flow or 0.0, 0.0)
        if latest.short_term_debt > resources:
            failures.append(LIQUIDITY_SHORTFALL)

    if is_adverse_opinion(latest.audit_opinion):
        failures.append(ADVERSE_AUDIT_OPINION)

    return FilterOutcome(failures=failures)
