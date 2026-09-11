from datetime import date

import pytest

from stockscout.models import (
    AnalysisResult,
    FilterOutcome,
    PriceStats,
    Snapshot,
    Valuation,
)


def test_filter_outcome_passed_when_no_failures():
    assert FilterOutcome(failures=[]).passed is True


def test_filter_outcome_failed_when_any_failure():
    outcome = FilterOutcome(failures=["FINANCIAL_SECTOR"])

    assert outcome.passed is False
    assert outcome.failures == ["FINANCIAL_SECTOR"]


def test_price_stats_holds_windowed_values():
    stats = PriceStats(
        price_current=41300.0,
        avg_price={1: 55000.0, 3: 64200.0, 5: 71400.0},
        high_5y=88800.0,
        drop_vs_avg={1: -0.249, 3: -0.357, 5: -0.422},
        drop_vs_high=-0.535,
    )

    assert stats.avg_price[5] == 71400.0
    assert stats.drop_vs_avg[5] == pytest.approx(-0.422)


def test_price_stats_allows_missing_windows():
    """커버리지 미달 창은 NULL이다 (설계서 §9.1)."""
    stats = PriceStats(
        price_current=100.0,
        avg_price={1: 120.0, 3: None, 5: None},
        high_5y=150.0,
        drop_vs_avg={1: -0.1667, 3: None, 5: None},
        drop_vs_high=-0.3333,
    )

    assert stats.avg_price[3] is None
    assert stats.drop_vs_avg[5] is None


def test_valuation_all_none_is_allowed():
    """shares_outstanding이 없으면 셋 다 NULL (설계서 §9.2)."""
    v = Valuation()

    assert (v.market_cap, v.per, v.pbr) == (None, None, None)


def test_snapshot_carries_config_identity():
    snap = Snapshot(run_at="2026-09-11T02:00:00+00:00", config_hash="abc123",
                    config_json='{"signal": {}}', note="첫 실행")

    assert snap.snapshot_id is None      # 저장 전에는 없다
    assert snap.config_hash == "abc123"


def test_analysis_result_defaults_score_fields_to_none():
    """점수 칸은 계획 C가 채운다."""
    r = AnalysisResult(
        ticker="005930.KS",
        filters=FilterOutcome(failures=[]),
        stats=PriceStats(price_current=1.0, avg_price={}, high_5y=1.0,
                         drop_vs_avg={}, drop_vs_high=0.0),
        valuation=Valuation(),
    )

    assert r.score_total is None
    assert r.signal_grade is None
    assert r.passed_filters is True


def test_analysis_result_passed_filters_mirrors_outcome():
    r = AnalysisResult(
        ticker="X",
        filters=FilterOutcome(failures=["INSUFFICIENT_HISTORY"]),
        stats=PriceStats(price_current=1.0, avg_price={}, high_5y=1.0,
                         drop_vs_avg={}, drop_vs_high=0.0),
        valuation=Valuation(),
    )

    assert r.passed_filters is False
