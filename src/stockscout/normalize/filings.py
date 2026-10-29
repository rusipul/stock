"""공시 분류. 제목(한국)·양식과 항목(미국)으로 성격을 나눈다. 순수 함수다.

목적은 하락 원인과 회복 계기를 사람이 판단하도록 돕는 것이다. 키워드
분류라 완전하지 않다 - 분류가 없는 공시도 화면에 그대로 보인다.

위험(RISK)만 판정에 쓴다(analyze의 RISK_DISCLOSURE). 나머지는 표시용이다.
저장은 원문 그대로 하고 분류는 읽을 때 한다 - 규칙을 고쳐도 다시 받지
않아도 된다.
"""

from __future__ import annotations

from stockscout.models import Filing

CATEGORY_RISK = "RISK"            # 존속·신뢰를 흔드는 공시. 1년 안에 있으면 제외
CATEGORY_DILUTION = "DILUTION"    # 자금조달·지분 희석
CATEGORY_EARNINGS = "EARNINGS"    # 실적
CATEGORY_LEGAL = "LEGAL"          # 소송

# 한국(DART 공시 제목). 공백을 지우고 부분 문자열로 본다.
KR_RISK = ("관리종목", "상장적격성", "불성실공시", "회생절차", "파산", "부도",
           "횡령", "배임", "의견거절", "부적정의견", "감사범위제한", "상장폐지",
           "매매거래정지")
# 매매거래정지는 액면분할·병합 때도 나온다. 그 경우는 위험이 아니다.
KR_HALT = "매매거래정지"
KR_BENIGN_HALT = ("분할", "병합")
KR_DILUTION = ("유상증자", "전환사채", "신주인수권부사채", "교환사채")
KR_EARNINGS = ("영업(잠정)실적", "매출액또는손익구조", "사업보고서", "분기보고서",
               "반기보고서")
KR_LEGAL = ("소송",)

# 미국(SEC). 8-K 항목 번호: https://www.sec.gov/fast-answers/answersform8khtm
US_RISK_FORMS = ("NT 10-K", "NT 10-Q", "NT 20-F")    # 정기 공시 지연
US_RISK_ITEMS = ("1.03",   # 파산·법정관리
                 "2.04",   # 채무 가속·의무 발동
                 "4.02")   # 과거 재무제표 신뢰 불가(정정)
US_EARNINGS_ITEMS = ("2.02",)
US_EARNINGS_FORMS = ("10-K", "10-Q", "10-K/A", "10-Q/A")
US_DILUTION_PREFIXES = ("S-1", "S-3", "424B")


def classify_filing(filing: Filing) -> str | None:
    if filing.form is not None:
        return _classify_us(filing.form, filing.items or "")
    return _classify_kr(filing.title)


def _classify_kr(title: str) -> str | None:
    t = title.replace(" ", "")
    for word in KR_RISK:
        if word in t:
            if word == KR_HALT and any(b in t for b in KR_BENIGN_HALT):
                continue
            return CATEGORY_RISK
    if any(w in t for w in KR_DILUTION):
        return CATEGORY_DILUTION
    if any(w in t for w in KR_EARNINGS):
        return CATEGORY_EARNINGS
    if any(w in t for w in KR_LEGAL):
        return CATEGORY_LEGAL
    return None


def _classify_us(form: str, items: str) -> str | None:
    codes = {c.strip() for c in items.split(",") if c.strip()}
    if form in US_RISK_FORMS or codes & set(US_RISK_ITEMS):
        return CATEGORY_RISK
    if form.startswith(US_DILUTION_PREFIXES):
        return CATEGORY_DILUTION
    if form in US_EARNINGS_FORMS or codes & set(US_EARNINGS_ITEMS):
        return CATEGORY_EARNINGS
    return None
