"""DART 계정과목 → 공통 Financials 매핑. 설계서 §7.2.

DART는 IFRS 표준계정ID(account_id)를 주지만 회사마다 편차가 있다.
account_id를 먼저 보고, 없으면 계정과목명(account_nm)으로 폴백한다.
둘 다 동의어 목록을 두고 위에서부터 첫 매치를 취한다.
"""

from __future__ import annotations

from stockscout.models import Financials, RawStatement

# 값이 없음을 나타내는 DART 표기
BLANK_AMOUNTS = {"", "-", "－", "N/A"}

# 필드 → (account_id 후보, account_nm 후보)
# Task 8 Step 1의 spike 출력으로 확인·보강한다.
ACCOUNT_MAP: dict[str, tuple[list[str], list[str]]] = {
    "revenue": (
        ["ifrs-full_Revenue", "ifrs-full_RevenueFromContractsWithCustomers"],
        ["매출액", "수익(매출액)", "영업수익", "매출"],
    ),
    "operating_income": (
        ["dart_OperatingIncomeLoss", "ifrs-full_ProfitLossFromOperatingActivities"],
        ["영업이익", "영업이익(손실)", "영업손익"],
    ),
    "net_income": (
        ["ifrs-full_ProfitLoss"],
        ["당기순이익", "당기순이익(손실)", "당기순손익"],
    ),
    "total_assets": (["ifrs-full_Assets"], ["자산총계"]),
    "total_liabilities": (["ifrs-full_Liabilities"], ["부채총계"]),
    "total_equity": (
        ["ifrs-full_Equity"],
        ["자본총계", "자본총계(지배기업의 소유주지분)"],
    ),
    "current_assets": (["ifrs-full_CurrentAssets"], ["유동자산"]),
    "current_liabilities": (["ifrs-full_CurrentLiabilities"], ["유동부채"]),
    "interest_expense": (
        ["ifrs-full_InterestExpense", "dart_InterestExpense"],
        ["이자비용", "금융원가"],
    ),
    "operating_cash_flow": (
        ["ifrs-full_CashFlowsFromUsedInOperatingActivities"],
        ["영업활동현금흐름", "영업활동으로 인한 현금흐름", "영업활동으로인한현금흐름"],
    ),
    "capex": (
        [
            "ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
        ],
        ["유형자산의 취득", "유형자산의취득", "유형자산의 증가"],
    ),
}

# 값을 양수 크기로 저장할 필드 (DART 현금흐름표는 지출을 음수로 표기한다)
ABS_FIELDS = {"capex", "interest_expense"}


def _parse_amount(text: str | None) -> float | None:
    """'1,234' → 1234.0, '(1,234)' → -1234.0, '-' → None"""
    if text is None:
        return None
    cleaned = str(text).strip().replace(",", "").replace(" ", "")
    if cleaned in BLANK_AMOUNTS:
        return None
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    if negative:
        cleaned = cleaned[1:-1]
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return -value if negative else value


def pick_amount(
    items: list[dict], ids: list[str], names: list[str]
) -> float | None:
    """account_id 우선, account_nm 폴백으로 첫 매치의 금액을 반환한다."""
    by_id = {}
    by_name = {}
    for it in items:
        amount = _parse_amount(it.get("thstrm_amount"))
        if amount is None:
            continue
        account_id = (it.get("account_id") or "").strip()
        account_nm = (it.get("account_nm") or "").strip().replace(" ", "")
        by_id.setdefault(account_id, amount)
        by_name.setdefault(account_nm, amount)

    for candidate in ids:
        if candidate in by_id:
            return by_id[candidate]
    for candidate in names:
        if candidate.replace(" ", "") in by_name:
            return by_name[candidate.replace(" ", "")]
    return None


def normalize_dart_statement(stmt: RawStatement) -> Financials:
    """DART 원본 하나를 공통 스키마로 옮긴다. 순수 함수이며 네트워크·DB를 모른다."""
    items = stmt.payload.get("list", []) or []

    values: dict[str, float | None] = {}
    for field, (ids, names) in ACCOUNT_MAP.items():
        amount = pick_amount(items, ids, names)
        if amount is not None and field in ABS_FIELDS:
            amount = abs(amount)
        values[field] = amount

    return Financials(
        ticker=stmt.ticker,
        fiscal_year=stmt.fiscal_year,
        currency="KRW",
        **values,
    )
