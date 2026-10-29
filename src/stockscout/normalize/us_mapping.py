"""US-GAAP 태그 -> 공통 Financials 매핑. 설계서 §7.2·§18.4.

payload는 sources/edgar.py의 annual_facts가 고른 한 해의 연간 값이다.
한 해씩 받으므로 태그 우선순위는 저절로 연도마다 적용된다 - AAPL처럼
회사가 중간에 태그를 바꿔도 해마다 있는 쪽을 쓴다.
"""

from __future__ import annotations

from stockscout.models import Financials, RawStatement

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
    ],
    "operating_cash_flow": [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ],
    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
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

    return Financials(
        ticker=stmt.ticker,
        fiscal_year=stmt.fiscal_year,
        currency="USD",
        **values,
    )
