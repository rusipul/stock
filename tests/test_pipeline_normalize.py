import pytest

from stockscout.models import RawStatement, Security
from stockscout.pipeline.normalize import NormalizeSummary, normalize_market
from stockscout.store.db import connect, init_db
from stockscout.store.repository import Repository


@pytest.fixture
def repo(tmp_path):
    with connect(tmp_path / "t.db") as conn:
        init_db(conn)
        yield Repository(conn)


def item(sj_div, account_id, account_nm, amount):
    return {"sj_div": sj_div, "account_id": account_id,
            "account_nm": account_nm, "thstrm_amount": amount}


COMPLETE_PAYLOAD = {"list": [
    item("IS", "ifrs-full_Revenue", "매출액", "300,000"),
    item("IS", "dart_OperatingIncomeLoss", "영업이익", "30,000"),
    item("BS", "ifrs-full_Liabilities", "부채총계", "60,000"),
    item("BS", "ifrs-full_Equity", "자본총계", "200,000"),
]}


def setup_ticker(repo, ticker="005930.KS", years=(2024, 2025), payload=None):
    repo.upsert_security(
        Security(ticker=ticker, market="KR", name="삼성전자", exchange="KOSPI", currency="KRW")
    )
    repo.save_raw_statements([
        RawStatement(ticker=ticker, fiscal_year=y, source="dart",
                     payload=payload if payload is not None else COMPLETE_PAYLOAD)
        for y in years
    ])


def test_normalize_writes_financials(repo):
    setup_ticker(repo)

    summary = normalize_market(repo, market="KR")

    fins = repo.get_financials("005930.KS")
    assert [f.fiscal_year for f in fins] == [2024, 2025]
    assert fins[0].revenue == 300000.0
    assert summary.normalized == 2
    assert summary.incomplete == 0


def test_normalize_counts_incomplete_records(repo):
    setup_ticker(repo, payload={"list": []})

    summary = normalize_market(repo, market="KR")

    assert summary.normalized == 2
    assert summary.incomplete == 2


def test_normalize_is_idempotent(repo):
    setup_ticker(repo)

    normalize_market(repo, market="KR")
    normalize_market(repo, market="KR")

    assert len(repo.get_financials("005930.KS")) == 2


def test_normalize_skips_tickers_without_raw_data(repo):
    repo.upsert_security(
        Security(ticker="EMPTY.KS", market="KR", name="빈종목", currency="KRW")
    )

    summary = normalize_market(repo, market="KR")

    assert summary.normalized == 0
    assert repo.get_financials("EMPTY.KS") == []


def test_summary_reports_missing_field_counts(repo):
    setup_ticker(repo, years=(2025,))

    summary = normalize_market(repo, market="KR")

    assert summary.missing_counts["capex"] == 1
    assert "capex" in summary.render()


# --- 추가 테스트 ------------------------------------------------------

def test_normalize_unknown_market_raises(repo):
    with pytest.raises(ValueError, match="XX"):
        normalize_market(repo, market="XX")


def test_normalize_handles_multiple_tickers(repo):
    setup_ticker(repo, ticker="005930.KS", years=(2024, 2025))
    setup_ticker(repo, ticker="000660.KS", years=(2024,))

    summary = normalize_market(repo, market="KR")

    assert summary.tickers == 2
    assert summary.normalized == 3
    assert [f.fiscal_year for f in repo.get_financials("005930.KS")] == [2024, 2025]
    assert [f.fiscal_year for f in repo.get_financials("000660.KS")] == [2024]


def test_normalize_skips_ticker_out_of_universe(repo):
    repo.upsert_security(
        Security(
            ticker="005930.KS", market="KR", name="삼성전자",
            exchange="KOSPI", currency="KRW", in_universe=False,
        )
    )
    repo.save_raw_statements([
        RawStatement(ticker="005930.KS", fiscal_year=2024, source="dart", payload=COMPLETE_PAYLOAD)
    ])

    summary = normalize_market(repo, market="KR")

    assert summary.tickers == 0
    assert summary.normalized == 0
    assert repo.get_financials("005930.KS") == []


def test_normalize_rerun_overwrites_existing_values(repo):
    setup_ticker(repo, years=(2024,), payload={"list": []})
    normalize_market(repo, market="KR")
    assert repo.get_financials("005930.KS")[0].revenue is None

    repo.save_raw_statements([
        RawStatement(ticker="005930.KS", fiscal_year=2024, source="dart", payload=COMPLETE_PAYLOAD)
    ])
    normalize_market(repo, market="KR")

    fins = repo.get_financials("005930.KS")
    assert len(fins) == 1
    assert fins[0].revenue == 300000.0


# --- I4: 종목 단위 격리, source 필터링 -----------------------------------

# DART 응답이 계정과목 dict가 아니라 문자열 리스트로 깨져 오는 경우.
# 재현: pick_amount()가 it.get(...)을 호출하는데 it이 str이라
# "AttributeError: 'str' object has no attribute 'get'" 로 죽는다.
MALFORMED_PAYLOAD = {"list": ["broken"]}


def test_normalize_isolates_bad_payload_per_ticker(repo):
    """한 종목의 깨진 payload가 전체 실행을 막으면 안 된다 (버그 I4-a).

    수정 전에는 normalize_dart_statement가 잡히지 않는 AttributeError를
    던져서 normalize_market() 자체가 예외로 끝나버린다. 그러면 두번째
    종목은 0건 처리되고, summary도 반환되지 않으며, 결측 상위 보고서도
    나오지 않는다 - '정규화 실행 → 결측 상위 확인 → 매핑 보강' 워크플로가
    깨진다.
    """
    setup_ticker(repo, ticker="005930.KS", years=(2024,), payload=MALFORMED_PAYLOAD)
    setup_ticker(repo, ticker="000660.KS", years=(2025,), payload=COMPLETE_PAYLOAD)

    summary = normalize_market(repo, market="KR")

    # 정상 종목은 영향받지 않는다.
    assert summary.tickers == 1
    assert summary.normalized == 1
    assert repo.get_financials("000660.KS") != []

    # 깨진 종목은 실패로 집계되고, financials에는 아무것도 쓰이지 않는다.
    assert summary.failed == 1
    assert repo.get_financials("005930.KS") == []

    # collect 파이프라인과 동일한 방식으로 오류가 기록된다.
    errors = repo.recent_errors()
    assert any(e["ticker"] == "005930.KS" for e in errors)

    # 결측 상위 보고서는 여전히 만들어진다 (루프 밖에서, 예외로 건너뛰지 않는다).
    assert summary.missing_counts

    # render()가 실패를 알려준다.
    text = summary.render()
    assert "1" in text and ("실패" in text or "fail" in text.lower())


def test_normalize_summary_render_is_printable_on_cp949_console():
    """한국어 Windows 콘솔은 cp949다. CollectSummary.render()와 동일한 회귀 테스트."""
    summary = NormalizeSummary(
        tickers=1, normalized=1, incomplete=0, failed=1,
        missing_counts={"capex": 1},
    )

    assert summary.failed == 1
    summary.render().encode("cp949")  # 예외가 나면 실패


def test_normalize_only_processes_matching_source(repo):
    """raw_financials는 (ticker, fiscal_year, source)로 dart/edgar를 함께 담을 수 있다.

    market="KR"의 정규화기는 dart 원본만 봐야 한다. source를 넘기지
    않으면 같은 (ticker, fiscal_year)로 두 소스가 충돌해 financials PK에서
    임의로 하나가 이긴다 (버그 I4-b). 아직 edgar용 정규화기는 없으므로,
    edgar payload가 dart 정규화기를 통과해도 우연히 크래시하지 않는
    구조를 이용해 '몇 건이 정규화됐는가'로 결함을 드러낸다.
    """
    repo.upsert_security(
        Security(ticker="005930.KS", market="KR", name="삼성전자", exchange="KOSPI", currency="KRW")
    )
    repo.save_raw_statements([
        RawStatement(ticker="005930.KS", fiscal_year=2024, source="dart", payload=COMPLETE_PAYLOAD),
        RawStatement(ticker="005930.KS", fiscal_year=2024, source="edgar", payload={"facts": {}}),
    ])

    summary = normalize_market(repo, market="KR")

    assert summary.normalized == 1
    fins = repo.get_financials("005930.KS")
    assert len(fins) == 1
    assert fins[0].revenue == 300000.0


# --- 보강 소스 병합 (ENRICHERS) --------------------------------------

# 삼성전자 2024 실측 형태. 보통주와 합계가 함께 오므로, 병합 경로가
# parse_share_counts를 제대로 거치는지도 함께 드러난다.
SHARES_PAYLOAD = {"list": [
    {"se": "보통주", "istc_totqy": "5,969,782,550", "tesstk_co": "29,700,000"},
    {"se": "우선주", "istc_totqy": "822,886,700", "tesstk_co": "4,050,000"},
    {"se": "합계", "istc_totqy": "6,792,669,250", "tesstk_co": "33,750,000"},
    {"se": "비고", "istc_totqy": "-", "tesstk_co": "-"},
]}


def test_normalize_merges_share_counts_onto_financials(repo):
    """dart_shares 원본이 같은 (ticker, 연도)의 Financials 위에 얹힌다.
    합계(6,792,669,250)가 아니라 보통주를 써야 한다."""
    setup_ticker(repo, years=(2024,))
    repo.save_raw_statements([
        RawStatement(ticker="005930.KS", fiscal_year=2024,
                     source="dart_shares", payload=SHARES_PAYLOAD)
    ])

    normalize_market(repo, market="KR")

    (fin,) = repo.get_financials("005930.KS")
    assert fin.shares_outstanding == 5_969_782_550.0
    assert fin.shares_treasury == 29_700_000.0


def test_normalize_leaves_years_without_share_data_alone(repo):
    """보강은 있는 연도에만 얹힌다. 없는 연도가 덮어써져 NULL이 되면 안 된다."""
    setup_ticker(repo, years=(2024, 2025))
    repo.save_raw_statements([
        RawStatement(ticker="005930.KS", fiscal_year=2024,
                     source="dart_shares", payload=SHARES_PAYLOAD)
    ])

    normalize_market(repo, market="KR")

    by_year = {f.fiscal_year: f for f in repo.get_financials("005930.KS")}
    assert by_year[2024].shares_outstanding == 5_969_782_550.0
    assert by_year[2025].shares_outstanding is None


def test_normalize_does_not_create_rows_for_share_only_years(repo):
    """주식수만 있고 재무제표가 없는 연도에는 financials 행을 만들지 않는다.

    금융 7종목의 2021·2022년이 정확히 그 모양이다(DART에 CFS 재무제표가
    없는데 주식수는 온다). 행을 만들면 필수 항목이 전부 비어
    DATA_INSUFFICIENT만 늘어난다 — ENRICHERS 주석이 약속하는 바다.
    """
    setup_ticker(repo, years=(2024,))
    repo.save_raw_statements([
        RawStatement(ticker="005930.KS", fiscal_year=2022,
                     source="dart_shares", payload=SHARES_PAYLOAD)
    ])

    summary = normalize_market(repo, market="KR")

    assert [f.fiscal_year for f in repo.get_financials("005930.KS")] == [2024]
    assert summary.normalized == 1


# --- 감사의견 병합 -----------------------------------------------------

# 삼성전자 2024 실측 형태. 기수당 2행씩 중복으로 오고 bsns_year에
# 줄바꿈이 박혀 있다.
AUDIT_PAYLOAD = {"list": [
    {"bsns_year": "제56기\n(당기)", "adt_opinion": "적정의견", "adtor": "안진회계법인"},
    {"bsns_year": "제56기\n(당기)", "adt_opinion": "적정의견", "adtor": "안진회계법인"},
    {"bsns_year": "제55기\n(전기)", "adt_opinion": "적정의견", "adtor": "안진회계법인"},
    {"bsns_year": "제55기\n(전기)", "adt_opinion": "적정의견", "adtor": "안진회계법인"},
]}


def test_normalize_merges_audit_opinion_onto_financials(repo):
    """dart_audit 원본이 같은 (ticker, 연도)의 Financials 위에 얹힌다.

    ENRICHERS 표는 (source 문자열, 함수) 쌍으로 배선되므로 어느 쪽에
    오타가 나도 코드는 조용히 아무것도 얹지 않는다. 그 오타를 잡는다.
    """
    setup_ticker(repo, years=(2024,))
    repo.save_raw_statements([
        RawStatement(ticker="005930.KS", fiscal_year=2024,
                     source="dart_audit", payload=AUDIT_PAYLOAD)
    ])

    normalize_market(repo, market="KR")

    (fin,) = repo.get_financials("005930.KS")
    assert fin.audit_opinion == "적정의견"


def test_normalize_leaves_years_without_audit_data_alone(repo):
    """보강은 있는 연도에만 얹힌다. 없는 연도가 덮어써져 NULL이 되면 안 된다."""
    setup_ticker(repo, years=(2024, 2025))
    repo.save_raw_statements([
        RawStatement(ticker="005930.KS", fiscal_year=2024,
                     source="dart_audit", payload=AUDIT_PAYLOAD)
    ])

    normalize_market(repo, market="KR")

    by_year = {f.fiscal_year: f for f in repo.get_financials("005930.KS")}
    assert by_year[2024].audit_opinion == "적정의견"
    assert by_year[2025].audit_opinion is None


def test_normalize_keeps_audit_opinion_null_for_placeholder_payloads(repo):
    """현대차 2024·2025 실측. status=000인데 모든 칸이 '-'다.
    빈 문자열이 아니라 NULL로 남아야 "의견을 모른다"가 된다."""
    setup_ticker(repo, years=(2024,))
    repo.save_raw_statements([
        RawStatement(ticker="005930.KS", fiscal_year=2024, source="dart_audit",
                     payload={"list": [{"bsns_year": "-", "adtor": "-"}] * 3})
    ])

    normalize_market(repo, market="KR")

    (fin,) = repo.get_financials("005930.KS")
    assert fin.audit_opinion is None


def test_normalize_merges_shares_and_audit_together(repo):
    """보강 소스가 둘로 늘어나도 서로를 지우지 않아야 한다."""
    setup_ticker(repo, years=(2024,))
    repo.save_raw_statements([
        RawStatement(ticker="005930.KS", fiscal_year=2024,
                     source="dart_shares", payload=SHARES_PAYLOAD),
        RawStatement(ticker="005930.KS", fiscal_year=2024,
                     source="dart_audit", payload=AUDIT_PAYLOAD),
    ])

    normalize_market(repo, market="KR")

    (fin,) = repo.get_financials("005930.KS")
    assert fin.shares_outstanding == 5_969_782_550.0
    assert fin.audit_opinion == "적정의견"
