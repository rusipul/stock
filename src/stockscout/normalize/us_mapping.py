"""US-GAAP 태그 -> 공통 Financials 매핑. 설계서 §7.2·§18.4.

payload는 sources/edgar.py의 annual_facts가 고른 한 해의 연간 값이다.
한 해씩 받으므로 태그 우선순위는 저절로 연도마다 적용된다 - AAPL처럼
회사가 중간에 태그를 바꿔도 해마다 있는 쪽을 쓴다.
"""

from __future__ import annotations

from datetime import date

from stockscout.models import Financials, InterimResults, RawStatement

# 필드 -> 태그 우선순위. 위에서부터 첫 매치를 취한다.
#
# 한국(kr_mapping.ACCOUNT_MAP)과 달리 단계를 나누지 않는다. 한국은 이자비용이
# 없으면 금융비용으로 대신했지만(§17.3), 미국에는 그런 대체값을 두지 않는다
# (계획 E 핵심 결정 3). 여기 있는 동의어는 전부 정확한 값이다.
#
# 자본·순이익은 비지배지분 포함을 먼저 본다. 한국이 자본총계·당기순이익
# (ifrs-full_Equity·ProfitLoss, 둘 다 비지배지분 포함)을 쓰므로 같은 개념을
# 맞춘다. ROE의 분자와 분모가 같은 범위여야 한다.
TAG_MAP: dict[str, list[str]] = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
    ],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["ProfitLoss", "NetIncomeLoss"],
    "total_assets": ["Assets"],
    "total_liabilities": ["Liabilities"],
    "total_equity": [
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        "StockholdersEquity",
    ],
    "current_assets": ["AssetsCurrent"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "interest_expense": [
        "InterestExpense",
        "InterestExpenseNonoperating",
        "InterestExpenseDebt",
        "InterestAndDebtExpense",    # BA·HON (실측 §18.4)
    ],
    "operating_cash_flow": [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ],
    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
        "PaymentsToAcquireOtherProductiveAssets",   # VZ (실측 §18.4)
    ],
    # 미국의 발행주식수는 자기주식을 뺀 유통주식수다(§18.2). 재무상태표
    # 값이 없으면 10-K 표지 값을 쓴다.
    "shares_outstanding": [
        "CommonStockSharesOutstanding",
        "EntityCommonStockSharesOutstanding",
    ],
}

# 양수 크기로 저장하는 필드 (kr_mapping.ABS_FIELDS와 같다)
ABS_FIELDS = {"capex", "interest_expense"}

LIABILITIES_AND_EQUITY = "LiabilitiesAndStockholdersEquity"

# 마지막 후보는 현금흐름표의 기말 현금(사용 제한 현금 포함, 대개 작다).
# 실측: CVX·PG·TGT 등 10곳은 재무상태표 현금 태그 없이 이것만 낸다.
CASH_TAGS = ["CashAndCashEquivalentsAtCarryingValue", "CashAndCashEquivalents",
             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"]
# 1년 안에 갚을 차입금. DebtCurrent는 이미 합계라 있으면 그것만 쓴다.
# 단기차입(ShortTermBorrowings)은 대개 기업어음(CommercialPaper)을 포함하므로
# 기업어음은 단기차입이 없을 때만 본다 - 함께 더하면 두 번 센다.
DEBT_CURRENT_TOTAL = ["DebtCurrent"]
SHORT_BORROWING_TAGS = ["ShortTermBorrowings", "CommercialPaper"]
CURRENT_LONG_TERM_DEBT_TAGS = ["LongTermDebtCurrent",
                               "LongTermDebtAndCapitalLeaseObligationsCurrent"]


def short_term_debt(facts: dict[str, float]) -> float | None:
    total = pick(facts, DEBT_CURRENT_TOTAL)
    if total is not None:
        return total
    parts = [pick(facts, SHORT_BORROWING_TAGS), pick(facts, CURRENT_LONG_TERM_DEBT_TAGS)]
    found = [p for p in parts if p is not None]
    return sum(found) if found else None

# 기말 주식수가 없을 때의 대체값. 주식 클래스가 여럿인 회사(META·NKE·
# EL·UPS)는 클래스별로만 보고해 companyfacts에 회사 전체 값이 없다.
# 기본 EPS의 분모라 PER에는 정의대로 맞고, 시가총액·PBR에는 근사값이다.
# 그래서 proxy_fields에 남겨 화면이 근거에 밝힌다 (한국 §17.3과 같다).
SHARES_PROXY = "WeightedAverageNumberOfSharesOutstandingBasic"

# 영업이익이 없을 때의 대체값: 법인세비용차감전 계속사업이익(회사 전체).
# 미국 GAAP는 단계식 손익을 요구하지 않아 JNJ·MRK·PFE·CVX·GE·NKE·XOM은
# 영업이익 줄이 없다(실측 §18.4). 세전이익은 영업외손익(이자·투자손익)이
# 섞인 다른 지표라 proxy_fields에 남기고 화면 근거가 밝힌다. 국내분
# (…Domestic)·해외분(…Foreign)만 있는 태그는 회사 전체가 아니라 쓰지 않는다.
OPERATING_INCOME_PROXIES = [
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
]


def pick(facts: dict[str, float], tags: list[str]) -> float | None:
    for tag in tags:
        value = facts.get(tag)
        if value is not None:
            return float(value)
    return None


def normalize_edgar_statement(stmt: RawStatement) -> Financials:
    """EDGAR 연간 값 하나를 공통 스키마로 옮긴다. 순수 함수다."""
    facts: dict[str, float] = stmt.payload.get("facts", {}) or {}

    values: dict[str, float | None] = {}
    for field, tags in TAG_MAP.items():
        amount = pick(facts, tags)
        if amount is not None and field in ABS_FIELDS:
            amount = abs(amount)
        values[field] = amount

    # 총부채를 따로 보고하지 않는 회사가 있다. 부채와자본총계 - 자본총계는
    # 회계 항등식이라 대체값이 아니므로 proxy_fields에 남기지 않는다.
    if values["total_liabilities"] is None:
        total = facts.get(LIABILITIES_AND_EQUITY)
        if total is not None and values["total_equity"] is not None:
            values["total_liabilities"] = float(total) - values["total_equity"]

    proxies: list[str] = []
    if values["operating_income"] is None:
        pretax = pick(facts, OPERATING_INCOME_PROXIES)
        if pretax is not None:
            values["operating_income"] = pretax
            proxies.append("operating_income")
    if values["shares_outstanding"] is None and facts.get(SHARES_PROXY) is not None:
        values["shares_outstanding"] = float(facts[SHARES_PROXY])
        proxies.append("shares_outstanding")

    return Financials(
        ticker=stmt.ticker,
        fiscal_year=stmt.fiscal_year,
        currency="USD",
        proxy_fields=proxies,
        cash=pick(facts, CASH_TAGS),
        short_term_debt=short_term_debt(facts),
        **values,
    )


# --- 중간보고서 (최근 실적 변화) ---------------------------------------------


def normalize_edgar_interim(stmt: RawStatement) -> InterimResults | None:
    """sources/edgar.interim_facts가 고른 10-Q 누적과 전년 동기를 옮긴다."""
    p = stmt.payload
    now, prior = p.get("current", {}), p.get("prior", {})
    op_tags = TAG_MAP["operating_income"] + OPERATING_INCOME_PROXIES
    op, op_prior = pick(now, op_tags), pick(prior, op_tags)
    if op is None or op_prior is None:
        return None
    return InterimResults(
        period_end=date.fromisoformat(p["period_end"]), months=int(p["months"]),
        revenue=pick(now, TAG_MAP["revenue"]),
        revenue_prior=pick(prior, TAG_MAP["revenue"]),
        operating_income=op, operating_income_prior=op_prior,
    )
