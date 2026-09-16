"""DART 계정과목 → 공통 Financials 매핑. 설계서 §7.2.

DART는 IFRS 표준계정ID(account_id)를 주지만 회사마다 편차가 있다.
account_id를 먼저 보고, 없으면 계정과목명(account_nm)으로 폴백한다.
둘 다 동의어 목록을 두고 위에서부터 첫 매치를 취한다.
"""

from __future__ import annotations

from stockscout.models import Financials, RawStatement

# 값이 없음을 나타내는 DART 표기
BLANK_AMOUNTS = {"", "-", "－", "N/A"}

# 한 단계 = (account_id 후보, account_nm 후보)
AccountTier = tuple[list[str], list[str]]

# 필드 → 우선순위 단계 목록.
# 단계는 위에서부터 시도하고, 한 단계 안에서는 id를 먼저 본다.
#
# 단계를 나눈 이유는 interest_expense 하나다. pick_amount이 모든 id를
# 이름보다 먼저 보기 때문에, 대체 계정(금융비용)의 id가 진짜 계정
# (이자비용)의 이름을 이겨버린다. 실측 13건이 그 모양이었고 그 안에
# LG생활건강이 있었다. 설계서 §17.3 참조.
#
# 단계를 더할 때의 계약: 0단계는 정확한 계정, 1단계 이상은 대체값이다.
# 2단계 이후가 쓰이면 그 필드가 proxy_fields에 기록되고 화면이 사용자에게
# "대체 계정으로 계산했다"고 말한다. 그러니 진짜 대체 계정일 때만 단계를
# 나눠라 — 동급 동의어는 단계를 늘리지 말고 같은 단계 안에 나열해라.
# 동의어를 2단계로 내려보내면 정확한 값인데도 대체값으로 찍혀 화면이
# 거짓말을 하게 된다.
ACCOUNT_MAP: dict[str, list[AccountTier]] = {
    "revenue": [(
        ["ifrs-full_Revenue", "ifrs-full_RevenueFromContractsWithCustomers"],
        ["매출액", "수익(매출액)", "영업수익", "매출"],
    )],
    "operating_income": [(
        ["dart_OperatingIncomeLoss", "ifrs-full_ProfitLossFromOperatingActivities"],
        ["영업이익", "영업이익(손실)", "영업손익"],
    )],
    "net_income": [(
        ["ifrs-full_ProfitLoss"],
        ["당기순이익", "당기순이익(손실)", "당기순손익"],
    )],
    "total_assets": [(["ifrs-full_Assets"], ["자산총계"])],
    "total_liabilities": [(["ifrs-full_Liabilities"], ["부채총계"])],
    "total_equity": [(
        ["ifrs-full_Equity"],
        ["자본총계", "자본총계(지배기업의 소유주지분)"],
    )],
    "current_assets": [(["ifrs-full_CurrentAssets"], ["유동자산"])],
    "current_liabilities": [(["ifrs-full_CurrentLiabilities"], ["유동부채"])],
    "interest_expense": [
        # 1단계: 진짜 이자비용
        (["ifrs-full_InterestExpense", "dart_InterestExpense"], ["이자비용"]),
        # 2단계: 금융비용. 이자비용에 환차손·파생손실이 더해진 상위 개념이라
        # 이자보상배율이 실제보다 낮게 나온다. 품질 필터에서는 보수적인
        # 방향이므로 쓰되, proxy_fields에 남겨 근거에 드러낸다.
        (["ifrs-full_FinanceCosts"], ["금융비용", "금융원가"]),
    ],
    "operating_cash_flow": [(
        ["ifrs-full_CashFlowsFromUsedInOperatingActivities"],
        ["영업활동현금흐름", "영업활동으로 인한 현금흐름", "영업활동으로인한현금흐름"],
    )],
    "capex": [(
        [
            "ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
        ],
        ["유형자산의 취득", "유형자산의취득", "유형자산의 증가"],
    )],
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


def pick_tiered(
    items: list[dict], tiers: list[AccountTier]
) -> tuple[float | None, int | None]:
    """단계를 순서대로 시도해 (금액, 사용한 단계 번호)를 반환한다.

    단계 번호가 0보다 크면 대체 계정을 썼다는 뜻이다. 호출자는 그것을
    proxy_fields에 남겨 화면이 근거를 말할 수 있게 한다.
    """
    for index, (ids, names) in enumerate(tiers):
        amount = pick_amount(items, ids, names)
        if amount is not None:
            return amount, index
    return None, None


def normalize_dart_statement(stmt: RawStatement) -> Financials:
    """DART 원본 하나를 공통 스키마로 옮긴다. 순수 함수이며 네트워크·DB를 모른다."""
    items = stmt.payload.get("list", []) or []

    values: dict[str, float | None] = {}
    proxies: list[str] = []
    for field, tiers in ACCOUNT_MAP.items():
        amount, tier = pick_tiered(items, tiers)
        if amount is not None and field in ABS_FIELDS:
            amount = abs(amount)
        if tier is not None and tier > 0:   # 0단계 = 정확한 계정
            proxies.append(field)
        values[field] = amount

    return Financials(
        ticker=stmt.ticker,
        fiscal_year=stmt.fiscal_year,
        currency="KRW",
        proxy_fields=proxies,
        **values,
    )
