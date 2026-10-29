"""실제 DB에 대한 스코어링 검증.

DB가 없거나 점수가 없으면 모듈 전체가 skip된다.
"""

import json
from pathlib import Path

import pytest

from stockscout.store.db import DEFAULT_DB_PATH, connect
from stockscout.store.repository import Repository

pytestmark = pytest.mark.skipif(
    not Path(DEFAULT_DB_PATH).exists(),
    reason="stockscout analyze 를 먼저 실행해야 한다",
)


@pytest.fixture
def rows():
    with connect(DEFAULT_DB_PATH) as conn:
        repo = Repository(conn)
        snap = repo.get_latest_snapshot(market="KR")
        if snap is None:
            pytest.skip("스냅샷이 없다")
        data = repo.get_analysis_results(snap.snapshot_id)
    scored = [r for r in data if r["score_total"] is not None]
    if not scored:
        pytest.skip("점수가 계산된 종목이 없다")
    return data


def test_every_passing_security_is_scored(rows):
    for row in rows:
        if row["passed_filters"]:
            assert row["score_total"] is not None, row["ticker"]


def test_no_excluded_security_carries_a_score(rows):
    """제외됐는데 점수가 있으면 화면에 모순된 표시가 난다."""
    for row in rows:
        if not row["passed_filters"]:
            assert row["score_total"] is None, row["ticker"]


def test_scores_stay_inside_zero_and_a_hundred(rows):
    for row in rows:
        if row["score_total"] is not None:
            assert 0 <= row["score_total"] <= 100, row["ticker"]


def test_axis_scores_stay_inside_their_max_points(rows):
    """축 점수는 0~배점이다. 배점은 설정에서 오므로 score_detail에서 읽는다."""
    columns = {"profitability": "score_profit", "growth": "score_growth",
               "stability": "score_stability", "cashflow": "score_cashflow"}
    for row in rows:
        if row["score_detail"] is None:
            continue
        for axis in json.loads(row["score_detail"])["axes"]:
            value = row[columns[axis["axis"]]]
            assert 0 <= value <= axis["max_points"], (row["ticker"], axis["axis"])


def test_score_detail_is_parseable_and_has_four_axes(rows):
    for row in rows:
        if row["score_detail"] is None:
            continue
        card = json.loads(row["score_detail"])
        assert {a["axis"] for a in card["axes"]} == {
            "profitability", "growth", "stability", "cashflow"
        }, row["ticker"]


def test_scores_are_not_all_identical(rows):
    """전 종목이 같은 점수면 구간표가 안 먹은 것이다."""
    scores = {r["score_total"] for r in rows if r["score_total"] is not None}

    assert len(scores) > 3, f"점수가 {len(scores)}종류뿐이다: {sorted(scores)}"


def test_few_axes_are_unmeasurable(rows):
    """측정 불가 축이 많으면 계정과목 매핑이 부족하다는 신호다.

    실측(스냅샷 #7)은 31종목 중 0건이다. 1/5을 넘으면 매핑을 의심해라.
    """
    scored = [r for r in rows if r["score_detail"] is not None]
    unmeasurable = [r["ticker"] for r in scored
                    if "AXIS_UNMEASURABLE" in r["score_detail"]]

    assert len(unmeasurable) <= len(scored) / 5, unmeasurable


def test_every_row_has_a_grade(rows):
    for row in rows:
        assert row["signal_grade"] in ("STRONG", "WATCH", "NONE"), row["ticker"]


def _dd3(row):
    return json.loads(row["drawdown_stats"])["windows"]["3"]


def test_a_graded_security_is_both_quality_and_fallen(rows):
    """등급이 붙었으면 점수 기준과 단기 하락 기준(3개월 -25%)을 둘 다 넘었어야 한다."""
    for row in rows:
        if row["signal_grade"] in ("STRONG", "WATCH"):
            assert row["passed_filters"], row["ticker"]
            assert row["score_total"] >= 70, row["ticker"]
            assert _dd3(row)["drop_from_high"] <= -0.25, row["ticker"]


def test_two_stars_mean_the_drop_has_stopped(rows):
    for row in rows:
        if row["signal_grade"] == "STRONG":
            assert _dd3(row)["stabilized"], row["ticker"]
        if row["signal_grade"] == "WATCH":
            assert not _dd3(row)["stabilized"], row["ticker"]
