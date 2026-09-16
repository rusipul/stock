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


# stockTotqySttus.json의 se(구분)는 자유 문자열이라 회사마다 다르게 쓴다.
# KOSPI 42종목 210건에서 실제로 온 표기가 27가지였고, 그중 보통주를 뜻하는
# 것만 9가지다('보통주' · '보통주식' · '의결권 있는 보통주' · '의결권 있는
# 주식' · '의결권이 있는\n주식(보통주)' …). 공백뿐 아니라 줄바꿈이 문자열
# 한가운데 박혀 오기도 한다.
#
# 그래서 정확 일치가 아니라 공백·줄바꿈을 모두 지운 뒤 부분 문자열로 본다.
# 정확 일치로 '보통주'만 찾던 때에는 196행 중 25행(7종목)이 합계 행으로
# 폴백했고, 합계는 우선주를 포함하므로 시가총액이 부풀려졌다 —
# 기업은행 +7.1%, 삼성전기 +3.9%, 신한지주 +3.4% (설계서 §17.4).
SE_COMMON_MARKERS = ("보통주",)          # '보통주식', '…주식(보통주)'까지 잡힌다
SE_VOTING_MARKERS = ("의결권있는", "의결권이있는")  # 의결권 있는 주식 = 보통주
SE_PREFERRED_MARKER = "우선"             # 우선주·배당우선주식·제1종 우선주 …
SE_TOTAL_MARKER = "합계"

# 낮을수록 먼저 쓴다.
TIER_COMMON = 0    # 보통주라고 이름 붙은 행
TIER_VOTING = 1    # 의결권으로만 구분한 행 (기업은행은 '보통주'를 안 쓴다)
TIER_TOTAL = 2     # 최후 폴백. 우선주를 포함하지만 NULL보다는 낫다


def _share_row_tier(se: str | None) -> int | None:
    """se(구분) 표기 → 우선순위 단계. 발행주식총수로 쓸 수 없으면 None.

    '우선'이 들어 있으면 의결권 표기가 있어도 보통주로 보지 않는다.
    지금 실측 데이터에 '의결권 있는 우선주'는 없지만 실재하는 증권이고,
    한 번 들어오면 조용히 틀린 시가총액이 나가기 때문이다. '종류주'처럼
    어디에도 걸리지 않는 표기는 None이 되어 그냥 무시된다 — 보통주가
    아닌 행을 발행주식총수로 쓰는 것보다 합계로 폴백하는 편이 낫다.
    """
    key = "".join((se or "").split())   # 공백·줄바꿈·탭을 모두 제거한다
    # 합계를 우선 가드보다 먼저 본다. 뒤로 내리면 '합계(보통주+우선주)'처럼
    # 우선주를 언급하는 표기가 None이 되어 최후 폴백 자체가 사라진다 —
    # 지금 실측 데이터는 순수 '합계'뿐이라 순서를 바꿔도 테스트가 다 통과한다.
    if SE_TOTAL_MARKER in key:
        return TIER_TOTAL
    if SE_PREFERRED_MARKER in key:
        return None
    if any(marker in key for marker in SE_COMMON_MARKERS):
        return TIER_COMMON
    if any(marker in key for marker in SE_VOTING_MARKERS):
        return TIER_VOTING
    return None


def parse_share_counts(payload: dict) -> tuple[float | None, float | None]:
    """주식의 총수 현황 → (발행주식총수, 자기주식수).

    se가 보통주/우선주/합계/비고 4행으로 오되 표기는 회사마다 다르다.
    비고 행은 어느 단계에도 걸리지 않고, 설령 걸려도 수량이 '-'라
    _parse_amount가 None을 돌려주므로 두 겹으로 걸러진다.
    """
    rows = payload.get("list", []) or []
    best: dict[int, dict] = {}
    for row in rows:
        tier = _share_row_tier(row.get("se"))
        if tier is not None:
            best.setdefault(tier, row)   # 같은 단계가 여럿이면 첫 행을 쓴다

    for tier in (TIER_COMMON, TIER_VOTING, TIER_TOTAL):
        row = best.get(tier)
        if row is None:
            continue
        issued = _parse_amount(row.get("istc_totqy"))
        if issued is None:
            continue
        return issued, _parse_amount(row.get("tesstk_co"))
    return None, None


def apply_share_counts(fin: Financials, payload: dict) -> None:
    """주식수 원본을 이미 만들어진 Financials 위에 얹는다."""
    issued, treasury = parse_share_counts(payload)
    if issued is not None:
        fin.shares_outstanding = issued
    if treasury is not None:
        fin.shares_treasury = treasury


# accnutAdtorNmNdAdtOpinion.json의 bsns_year는 연도가 아니라 기수 표기다.
# KOSPI 42종목 210건 전수 조사에서 표기가 407종이었다 — '제56기\n(당기)',
# '제 50 기\n(당기)', '제23기(당기)', '제4(당)기', "제52기\n('24.01.01~
# '24.12.31)", '제46기 연간\n제46기 3분기…', 그리고 모든 칸이 '-'인 행까지.
#
# 그래서 연도로 파싱하지 않고 당기 표기를 찾는다. 실측 210건 중 164건이
# '(당기)'를 썼고 1건이 '(당)기'였다(우리금융지주 2022). 나머지 45건은
# 당기를 표시하지 않는다 — 그 경우 첫 행이 최신 기수다(210건 전수에서
# 첫 행이 최신이 아닌 응답은 0건이었다).
CURRENT_PERIOD_MARKERS = ("(당기)", "(당)기")


def _is_current_period(bsns_year: str | None) -> bool:
    """기수 표기가 당기를 가리키는가. 공백·줄바꿈을 모두 지우고 본다."""
    key = "".join((bsns_year or "").split())
    return any(marker in key for marker in CURRENT_PERIOD_MARKERS)


def parse_audit_opinion(payload: dict) -> str | None:
    """회계감사인의 명칭 및 감사의견 → 최근 사업연도의 감사의견 원문.

    같은 기수가 여러 행으로 중복돼 오는 응답이 있다. 계획서는 "기수당
    2행"이라 했지만 실측은 그렇지 않다. 아래 두 숫자는 세는 기준이 다르니
    섞어 읽지 말 것 — 기준을 흐린 것이 이 주석의 첫 판이 틀렸던 이유다.

    같은 기수 표기의 최대 중복 행 수로 세면 (합 210건):
        1행 116건 · 2행 79건 · 3행 10건 · 4행 1건 · 6행 4건
    응답 전체 행 수로 세면 3행 126건 · 6행 84건이고, 모든 칸이 '-'인
    빈 응답 14건은 위 3행 버킷 10건과 6행 버킷 4건에 섞여 있다.

    당기 행은 여럿일 수 있다 — 실측 210건 중 74건이 당기 행을 2개 이상
    갖는다. 그래서 첫 당기 행의 의견이 비어 있어도 나머지 당기 행을 계속
    본다. 같은 기수의 행들이 서로 다른 의견을 말한 경우는 실측 0건이라
    첫 매치를 써도 안전하다.

    다만 전기로는 내려가지 않는다. 당기 행이 하나라도 있으면 그 밖으로
    나가지 않는다 — 작년 의견을 올해 것으로 보고하면 감사의견이 적정에서
    한정으로 바뀌는 해에 정확히 틀리는 방향이다. 폴백은 당기 표기가 아예
    없는 응답에만 쓴다.
    """
    rows = payload.get("list", []) or []
    current = [row for row in rows if _is_current_period(row.get("bsns_year"))]
    for row in current or rows:
        opinion = (row.get("adt_opinion") or "").strip()
        if opinion:
            return opinion
    return None


def apply_audit_opinion(fin: Financials, payload: dict) -> None:
    """감사의견 원본을 이미 만들어진 Financials 위에 얹는다."""
    opinion = parse_audit_opinion(payload)
    if opinion is not None:
        fin.audit_opinion = opinion
