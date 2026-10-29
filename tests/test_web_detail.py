import json

import pytest
from fastapi.testclient import TestClient

from stockscout.models import (
    AnalysisResult,
    FilterOutcome,
    Financials,
    PriceStats,
    Security,
    Snapshot,
    Valuation,
)
from stockscout.store.db import connect, init_db
from stockscout.store.repository import Repository
from stockscout.web.api import create_app


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "t.db"
    with connect(path) as conn:
        init_db(conn)
        repo = Repository(conn)
        repo.upsert_security(Security(
            ticker="005930.KS", market="KR", name="삼성전자",
            exchange="KOSPI", currency="KRW"))
        repo.upsert_security(Security(
            ticker="105560.KS", market="KR", name="KB금융",
            exchange="KOSPI", currency="KRW", is_financial=True))
        repo.save_snapshot(
            Snapshot(run_at="2026-09-11T02:00:00+00:00", as_of="2026-09-10",
                     config_hash="h1", config_json="{}"),
            [
                AnalysisResult(
                    ticker="005930.KS", filters=FilterOutcome(failures=[]),
                    stats=PriceStats(
                        price_current=60.0,
                        avg_price={1: 80.0, 3: 90.0, 5: 100.0},
                        high_5y=200.0,
                        drop_vs_avg={1: -0.25, 3: -1/3, 5: -0.4},
                        drop_vs_high=-0.7),
                    valuation=Valuation()),
                AnalysisResult(
                    ticker="105560.KS",
                    filters=FilterOutcome(failures=["FINANCIAL_SECTOR"]),
                    stats=PriceStats(
                        price_current=100.0, avg_price={1: 100.0, 3: 100.0, 5: 100.0},
                        high_5y=100.0, drop_vs_avg={1: 0.0, 3: 0.0, 5: 0.0},
                        drop_vs_high=0.0),
                    valuation=Valuation()),
            ],
        )
    return path


@pytest.fixture
def client(db):
    return TestClient(create_app(db_path=db))


def test_detail_returns_master_and_metrics(client):
    body = client.get("/api/securities/005930.KS").json()

    assert body["name"] == "삼성전자"
    assert body["price_current"] == 60.0
    assert body["drop_vs_avg_5y"] == pytest.approx(-0.4)


def test_detail_includes_computation_lines(client):
    """설계서 §11.2: 판정 근거를 실제 숫자로 노출한다."""
    body = client.get("/api/securities/005930.KS").json()

    lines = body["rationale"]
    joined = " ".join(lines)
    assert "60" in joined and "100" in joined and "-40.0%" in joined


def test_rationale_names_each_filter_failure(client):
    body = client.get("/api/securities/105560.KS").json()

    assert any("FINANCIAL_SECTOR" in line for line in body["rationale"])
    assert body["passed_filters"] is False


def test_financials_are_empty_until_collected(client):
    body = client.get("/api/securities/005930.KS").json()

    assert body["financials"] == []


def test_financials_are_returned_when_present(client, db):
    with connect(db) as conn:
        Repository(conn).save_financials([
            Financials(ticker="005930.KS", fiscal_year=2025, currency="KRW",
                       revenue=300.0, operating_income=30.0,
                       total_equity=200.0, total_liabilities=60.0,
                       net_income=20.0, operating_cash_flow=40.0),
        ])

    body = client.get("/api/securities/005930.KS").json()

    assert len(body["financials"]) == 1
    row = body["financials"][0]
    assert row["fiscal_year"] == 2025
    assert row["revenue"] == 300.0
    assert row["operating_margin"] == pytest.approx(0.1)
    assert row["debt_ratio"] == pytest.approx(30.0)


def test_score_breakdown_is_null_until_scored(client):
    body = client.get("/api/securities/005930.KS").json()

    assert body["score_total"] is None
    assert body["score_detail"] is None


def test_unknown_ticker_is_404(client):
    response = client.get("/api/securities/NOPE.KS")

    assert response.status_code == 404
    assert "NOPE.KS" in response.json()["detail"]


# --- 추가 테스트 -------------------------------------------------------
#
# 위 7개는 플랜 D의 지정 테스트다. 아래는 "신중히 확인할 것" 항목들을
# 실제로 테스트로 굳힌 것.


def test_rationale_is_cp949_safe_when_passed(client):
    """한국어 Windows 콘솔(cp949)은 체크마크 같은 문자를 출력하면 죽는다.

    이 문자열은 웹뿐 아니라 CLI로도 갈 수 있으므로 기호를 쓰지 않는다.
    """
    body = client.get("/api/securities/005930.KS").json()

    joined = "\n".join(body["rationale"])
    joined.encode("cp949")  # UnicodeEncodeError가 나면 실패


def test_rationale_is_cp949_safe_when_excluded(client):
    body = client.get("/api/securities/105560.KS").json()

    joined = "\n".join(body["rationale"])
    joined.encode("cp949")


def test_financial_years_come_back_ascending(client, db):
    """프론트는 재무 표를 과거->최근 순으로 왼쪽에서 오른쪽으로 그린다.

    save_financials를 최근 연도 먼저 넣어서, 응답이 저장 순서가 아니라
    실제로 연도 오름차순인지 확인한다.
    """
    with connect(db) as conn:
        Repository(conn).save_financials([
            Financials(ticker="005930.KS", fiscal_year=2025, currency="KRW",
                       revenue=300.0),
            Financials(ticker="005930.KS", fiscal_year=2023, currency="KRW",
                       revenue=250.0),
            Financials(ticker="005930.KS", fiscal_year=2024, currency="KRW",
                       revenue=280.0),
        ])

    body = client.get("/api/securities/005930.KS").json()

    years = [row["fiscal_year"] for row in body["financials"]]
    assert years == [2023, 2024, 2025]


def test_rationale_states_coverage_miss_in_words(client, db):
    """3년 평균이 없는(커버리지 미달) 종목은 None을 그대로 찍지 않고 문장으로 말한다."""
    with connect(db) as conn:
        repo = Repository(conn)
        repo.upsert_security(Security(
            ticker="900001.KS", market="KR", name="신규상장",
            exchange="KOSPI", currency="KRW"))
        repo.save_snapshot(
            Snapshot(run_at="2026-09-12T02:00:00+00:00", as_of="2026-09-11",
                     config_hash="h2", config_json="{}"),
            [
                AnalysisResult(
                    ticker="900001.KS", filters=FilterOutcome(failures=[]),
                    stats=PriceStats(
                        price_current=50.0,
                        avg_price={1: 55.0, 5: 60.0},
                        high_5y=70.0,
                        drop_vs_avg={1: 50.0 / 55.0 - 1, 5: 50.0 / 60.0 - 1},
                        drop_vs_high=50.0 / 70.0 - 1),
                    valuation=Valuation()),
            ],
        )

    body = client.get("/api/securities/900001.KS").json()

    assert body["avg_price_3y"] is None
    assert body["drop_vs_avg_3y"] is None
    joined = " ".join(body["rationale"])
    assert "None" not in joined
    assert "데이터 커버리지 미달" in joined


def test_rationale_names_every_failed_filter_with_matching_count(client, db):
    """필터를 여러 개 걸친 종목은 근거 문장에 전부 이름과 개수가 맞아야 한다."""
    with connect(db) as conn:
        repo = Repository(conn)
        repo.upsert_security(Security(
            ticker="900002.KS", market="KR", name="다중탈락",
            exchange="KOSPI", currency="KRW", is_financial=True))
        repo.save_snapshot(
            Snapshot(run_at="2026-09-12T03:00:00+00:00", as_of="2026-09-11",
                     config_hash="h3", config_json="{}"),
            [
                AnalysisResult(
                    ticker="900002.KS",
                    filters=FilterOutcome(
                        failures=["FINANCIAL_SECTOR", "INSUFFICIENT_HISTORY"]),
                    stats=PriceStats(
                        price_current=50.0, avg_price={1: 50.0, 3: 50.0, 5: 50.0},
                        high_5y=50.0, drop_vs_avg={1: 0.0, 3: 0.0, 5: 0.0},
                        drop_vs_high=0.0),
                    valuation=Valuation()),
            ],
        )

    body = client.get("/api/securities/900002.KS").json()

    joined = " ".join(body["rationale"])
    assert "FINANCIAL_SECTOR" in joined
    assert "INSUFFICIENT_HISTORY" in joined
    assert "2건" in joined


def test_currency_falls_back_to_krw_when_master_missing(db):
    """스냅샷에는 있지만 securities 마스터에는 없는 종목(예: 상장폐지)."""
    with connect(db) as conn:
        repo = Repository(conn)
        repo.save_snapshot(
            Snapshot(run_at="2026-09-12T04:00:00+00:00", as_of="2026-09-11",
                     config_hash="h4", config_json="{}"),
            [
                AnalysisResult(
                    ticker="900003.KS", filters=FilterOutcome(failures=[]),
                    stats=PriceStats(
                        price_current=10.0, avg_price={1: 10.0, 3: 10.0, 5: 10.0},
                        high_5y=10.0, drop_vs_avg={1: 0.0, 3: 0.0, 5: 0.0},
                        drop_vs_high=0.0),
                    valuation=Valuation()),
            ],
        )

    response = TestClient(create_app(db_path=db)).get("/api/securities/900003.KS")

    assert response.status_code == 200
    body = response.json()
    assert body["currency"] == "KRW"
    assert body["name"] == "900003.KS"


# --- 계획 C: 점수 산출 근거 -------------------------------------------

def _axis(name, earned, denominator, status="OK"):
    return {"axis": name, "metrics": [], "earned": earned,
            "denominator": denominator, "max_points": 25.0, "status": status}


UNMEASURABLE_DETAIL = json.dumps({"axes": [
    _axis("profitability", 20.0, 25.0),
    _axis("growth", 15.0, 25.0),
    _axis("stability", 18.0, 20.0),
    _axis("cashflow", 0.0, 25.0, status="AXIS_UNMEASURABLE"),
]})


@pytest.fixture
def scored(db):
    """점수가 채워진 두 번째 스냅샷. 최신이므로 API가 이것을 읽는다."""
    stats = PriceStats(
        price_current=60.0, avg_price={1: 80.0, 3: 90.0, 5: 100.0},
        high_5y=200.0, drop_vs_avg={1: -0.25, 3: -1/3, 5: -0.4},
        drop_vs_high=-0.7)
    with connect(db) as conn:
        repo = Repository(conn)
        repo.save_snapshot(
            Snapshot(run_at="2026-09-12T02:00:00+00:00", as_of="2026-09-11",
                     config_hash="h2", config_json="{}"),
            [
                AnalysisResult(
                    ticker="005930.KS", filters=FilterOutcome(failures=[]),
                    stats=stats, valuation=Valuation(),
                    score_total=53.0, score_detail=UNMEASURABLE_DETAIL,
                    signal_grade="NONE",
                    signal_reason="우량주 점수 53점이 기준 70점에 못 미칩니다"),
                AnalysisResult(
                    ticker="105560.KS",
                    filters=FilterOutcome(failures=["FINANCIAL_SECTOR"]),
                    stats=stats, valuation=Valuation(),
                    signal_grade="NONE",
                    signal_reason="하드필터에 걸려 등급을 매기지 않습니다"),
            ],
        )
    return db


def _rationale(client, ticker):
    return "\n".join(client.get(f"/api/securities/{ticker}").json()["rationale"])


def test_rationale_shows_the_score_per_axis(client, scored):
    joined = _rationale(client, "005930.KS")

    assert "우량주 점수 53점" in joined
    assert "수익성 20.0점" in joined
    # 결측으로 유효만점이 20이 된 축은 25점 만점으로 환산해 보여준다
    assert "안정성 22.5점 (획득 18.0 / 유효만점 20.0)" in joined


def test_rationale_names_an_unmeasurable_axis(client, scored):
    """§8.3의 축 하한에 걸린 축은 왜 0점인지 말해야 한다.

    "현금흐름 0.0 / 25"만 보여주면 사업이 나쁜 것처럼 읽힌다. 실제로는
    볼 수 없었던 것이다.
    """
    line = next(l for l in _rationale(client, "005930.KS").splitlines()
                if "현금흐름" in l)

    assert "측정 불가" in line


def test_rationale_includes_the_signal_reason(client, scored):
    assert "신호 판정: 우량주 점수 53점이 기준 70점에 못 미칩니다" \
        in _rationale(client, "005930.KS")


def test_rationale_discloses_a_proxy_interest_expense(client, scored):
    """설계서 §17.3: 금융비용으로 이자보상배율을 계산했으면 그렇게 말한다."""
    with connect(scored) as conn:
        Repository(conn).save_financials([
            Financials(ticker="005930.KS", fiscal_year=2025, currency="KRW",
                       revenue=300.0, operating_income=30.0,
                       interest_expense=5.0, proxy_fields=["interest_expense"]),
        ])

    assert "금융비용" in _rationale(client, "005930.KS")


def test_rationale_is_silent_about_proxies_when_none_were_used(client, scored):
    with connect(scored) as conn:
        Repository(conn).save_financials([
            Financials(ticker="005930.KS", fiscal_year=2025, currency="KRW",
                       revenue=300.0, operating_income=30.0, interest_expense=5.0),
        ])

    assert "금융비용" not in _rationale(client, "005930.KS")


def test_rationale_has_no_score_lines_for_an_excluded_security(client, scored):
    """제외된 종목은 점수가 없다. 0점이라고 쓰면 사업 평가로 읽힌다.

    "재무 데이터가 없어"라고 쓰면 거짓이다 - 재무는 있고, 하드필터에
    걸려 채점하지 않은 것이다.
    """
    joined = _rationale(client, "105560.KS")

    assert "[제외]" in joined
    assert "수익성" not in joined
    assert "재무 데이터가 없어" not in joined
    assert "채점하지 않았습니다" in joined


def test_rationale_is_cp949_safe_when_scored(client, scored):
    _rationale(client, "005930.KS").encode("cp949")


def test_rationale_discloses_proxy_shares(client, scored):
    """주식수를 가중평균으로 대신했으면 시가총액·PER·PBR이 근사값이라고 말한다."""
    with connect(scored) as conn:
        Repository(conn).save_financials([
            Financials(ticker="005930.KS", fiscal_year=2025, currency="KRW",
                       revenue=300.0, operating_income=30.0,
                       shares_outstanding=100.0, proxy_fields=["shares_outstanding"]),
        ])

    joined = _rationale(client, "005930.KS")
    assert "가중평균" in joined
    assert "금융비용" not in joined
