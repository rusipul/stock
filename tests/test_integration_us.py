"""실제 DB의 미국 스냅샷 검증. 미국을 분석하기 전에는 skip된다."""

import json
from pathlib import Path

import pytest

from stockscout.store.db import DEFAULT_DB_PATH, connect
from stockscout.store.repository import Repository

pytestmark = pytest.mark.skipif(
    not Path(DEFAULT_DB_PATH).exists(),
    reason="stockscout analyze --market US 를 먼저 실행해야 한다",
)

FINANCIALS = {"JPM", "BAC", "GS", "BRK-B"}


@pytest.fixture
def rows():
    with connect(DEFAULT_DB_PATH) as conn:
        repo = Repository(conn)
        snap = repo.get_latest_snapshot(market="US")
        if snap is None:
            pytest.skip("미국 스냅샷이 없다")
        return repo.get_analysis_results(snap.snapshot_id)


def test_every_us_security_has_a_row(rows):
    with connect(DEFAULT_DB_PATH) as conn:
        expected = {s.ticker for s in Repository(conn).list_securities(market="US")}

    assert {r["ticker"] for r in rows} == expected


def test_no_kr_ticker_leaks_into_the_us_snapshot(rows):
    assert not any(r["ticker"].endswith(".KS") for r in rows)


def test_financials_are_excluded(rows):
    for row in rows:
        if row["ticker"] in FINANCIALS:
            assert "FINANCIAL_SECTOR" in json.loads(row["filter_failures"])


def test_the_mapping_leaves_most_non_financials_measurable(rows):
    """매핑이 크게 비면 대부분이 DATA_INSUFFICIENT로 떨어진다.

    "채점됐는가"로 재지 않는다. 채점은 하드필터(자본잠식·이자 미충당 등)도
    막는데 그것은 매핑이 아니라 기준의 문제다. 영업이익 줄이 없는
    회사(JNJ·MRK·PFE·CVX·GE·NKE·XOM)는 세전이익 대체값으로 측정된다 (§18.4).
    """
    others = [r for r in rows if r["ticker"] not in FINANCIALS]
    insufficient = [r["ticker"] for r in others
                    if "DATA_INSUFFICIENT" in json.loads(r["filter_failures"])]

    assert len(others) - len(insufficient) >= len(others) * 0.8, insufficient


def test_us_scores_spread_out(rows):
    """점수가 한곳에 몰리면 매핑이나 단위가 어딘가 일괄로 틀린 것이다."""
    scores = [r["score_total"] for r in rows if r["score_total"] is not None]

    assert len({round(x, 1) for x in scores}) >= 5, sorted(scores)
    assert max(scores) - min(scores) >= 20, sorted(scores)


def test_market_cap_is_in_dollars(rows):
    """AAPL 시가총액은 조 단위 달러다. 원으로 섞이면 1000배가 넘게 커진다."""
    aapl = next(r for r in rows if r["ticker"] == "AAPL")

    assert 1e12 < aapl["market_cap"] < 1e13


def test_the_kr_snapshot_is_still_the_kr_default():
    """미국을 분석한 뒤에도 한국의 최신 스냅샷은 한국 것이다."""
    with connect(DEFAULT_DB_PATH) as conn:
        kr = Repository(conn).get_latest_snapshot(market="KR")

    assert kr is not None and kr.market == "KR"


def test_us_hard_filters_do_not_exclude_for_debt(rows):
    """미국은 부채비율 하드필터를 끈다 (settings.yaml hard_filters_by_market)."""
    assert not any("HIGH_DEBT" in json.loads(r["filter_failures"]) for r in rows)
