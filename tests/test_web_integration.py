"""실제 DB에 대한 웹 API 검증.

DB가 없거나 스냅샷이 없으면 모듈 전체가 skip된다.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stockscout.store.db import DEFAULT_DB_PATH
from stockscout.web.api import create_app

pytestmark = pytest.mark.skipif(
    not Path(DEFAULT_DB_PATH).exists(),
    reason="stockscout analyze 를 먼저 실행해야 한다",
)


@pytest.fixture
def client():
    return TestClient(create_app(DEFAULT_DB_PATH))


@pytest.fixture
def snapshot(client):
    res = client.get("/api/snapshot")
    if res.status_code == 404:
        pytest.skip("스냅샷이 없다")
    return res.json()


def test_snapshot_matches_cli_counts(snapshot):
    assert snapshot["total"] == 42
    # 계획 C에서 재무 필터가 붙어 34/8에서 31/11이 됐다. 늘어난 제외 3종목은
    # 전부 HIGH_DEBT(한국전력·한화오션·한국항공우주)다.
    assert snapshot["passed"] == 31
    assert snapshot["excluded"] == 11


def test_scores_are_present(snapshot):
    """계획 C가 점수를 채웠다. has_scores가 화면 전체의 스위치다.

    이름이 `..._absent_until_plan_c`였고 False를 단언했다. 계획 C가
    점수(4축 스코어링)와 밸류에이션(발행주식수 수집)을 함께 켜서 방향을
    뒤집었다. 이 값이 true가 되는 순간 우량주 점수 슬라이더, 등급 칩,
    점수 KPI가 전부 켜진다 (계획 D가 그렇게 만들어 두었다).
    """
    assert snapshot["has_scores"] is True
    assert snapshot["has_valuation"] is True


def test_list_returns_every_security(client, snapshot):
    rows = client.get("/api/securities").json()

    assert len(rows) == snapshot["total"]


def test_list_is_sorted_most_fallen_first(client, snapshot):
    rows = client.get("/api/securities", params={"sort": "drop"}).json()
    drops = [r["drop_vs_avg_5y"] for r in rows if r["drop_vs_avg_5y"] is not None]

    assert drops == sorted(drops)


def test_every_row_can_be_fetched_in_detail(client, snapshot):
    """목록에 있는 종목은 전부 상세를 열 수 있어야 한다."""
    for row in client.get("/api/securities").json():
        res = client.get(f"/api/securities/{row['ticker']}")
        assert res.status_code == 200, row["ticker"]


def test_every_passing_row_has_a_chart(client, snapshot):
    for row in client.get("/api/securities").json():
        if not row["passed_filters"]:
            continue
        body = client.get(f"/api/securities/{row['ticker']}/prices").json()
        assert len(body["series"]) > 1000, row["ticker"]


def test_chart_reference_lines_match_the_list_row(client, snapshot):
    """차트 기준선과 목록 지표가 같은 스냅샷에서 나와야 한다."""
    row = client.get("/api/securities").json()[0]
    chart = client.get(f"/api/securities/{row['ticker']}/prices").json()

    assert chart["avg_price_5y"] == row["avg_price_5y"]
    assert chart["high_5y"] == row["high_5y"]
    assert chart["price_current"] == row["price_current"]


def test_rationale_contains_the_actual_numbers(client, snapshot):
    """근거 문장의 숫자가 같은 스냅샷의 목록 행과 일치해야 한다.

    원래는 LG생활건강의 `295,000`과 `-41.1%`를 그대로 박아뒀다. 주가를
    다시 받을 때마다 깨지는 테스트였고 실제로 깨졌다 — 5일치를 더 받자
    302,500원이 되면서 -39.2%로 바뀌었다.

    값을 박는 대신 두 표현이 서로 맞는지를 본다. 이쪽이 원래 지키려던
    것("근거에 엉뚱한 숫자가 나오지 않는다")을 더 정확히 잡으면서
    데이터 갱신에는 견딘다.
    """
    row = client.get("/api/securities").json()[0]
    assert row["drop_vs_avg_5y"] is not None, "가장 많이 떨어진 행에 하락률이 없다"

    rationale = client.get(f"/api/securities/{row['ticker']}").json()["rationale"]

    # 줄 하나를 통째로 맞춘다. 근거 전체를 join해서 `in`으로 보면 다른
    # 줄에 남은 같은 숫자가 단언을 만족시켜, 이 줄이 망가져도 통과한다.
    # (실제로 현재가를 +1 한 변이가 그렇게 빠져나갔다.)
    expected = (
        f"5년 평균 대비  {row['price_current']:,.0f}"
        f" / {row['avg_price_5y']:,.0f}"
        f" - 1 = {row['drop_vs_avg_5y'] * 100:.1f}%"
    )
    assert expected in rationale, f"{expected!r} 가 근거에 없다: {rationale}"


def test_excluded_row_names_its_filter(client, snapshot):
    body = client.get("/api/securities/105560.KS").json()

    assert body["passed_filters"] is False
    assert any("FINANCIAL_SECTOR" in line for line in body["rationale"])


def test_financials_are_filled_in(client, snapshot):
    """DART 인증키가 들어온 뒤로 재무 연도별 자료가 있어야 한다.

    이 테스트는 원래 `financials == []`를 단언했다. 키가 없던 시절의
    제품 상태를 지키던 장치다. 키가 들어왔으므로 방향을 뒤집는다.
    지워버리면 재무가 조용히 사라져도 아무도 모르는 상태로 돌아간다.

    삼성전자를 쓴다. 금융업은 계정 구조가 달라 revenue가 잡히지 않고,
    목록 첫 행은 주가에 따라 바뀐다.
    """
    body = client.get("/api/securities/005930.KS").json()
    years = [f["fiscal_year"] for f in body["financials"]]

    assert len(years) >= 3
    assert years == sorted(years), "연도가 오름차순이어야 화면 표가 맞는다"
    for f in body["financials"]:
        assert f["revenue"] is not None, f["fiscal_year"]
        assert f["operating_income"] is not None, f["fiscal_year"]


def test_watchlist_round_trips(client, snapshot):
    client.delete("/api/watchlist/005930.KS")
    assert "005930.KS" not in client.get("/api/watchlist").json()

    client.post("/api/watchlist/005930.KS")
    assert "005930.KS" in client.get("/api/watchlist").json()

    rows = {r["ticker"]: r for r in client.get("/api/securities").json()}
    assert rows["005930.KS"]["in_watchlist"] is True

    client.delete("/api/watchlist/005930.KS")
