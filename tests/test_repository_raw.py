from datetime import date

import pytest

from stockscout.models import PriceBar, RawStatement
from stockscout.store.db import connect, init_db
from stockscout.store.repository import Repository


@pytest.fixture
def repo(tmp_path):
    with connect(tmp_path / "t.db") as conn:
        init_db(conn)
        yield Repository(conn)


def test_save_and_read_raw_statement(repo):
    stmt = RawStatement(
        ticker="005930.KS", fiscal_year=2025, source="dart",
        payload={"list": [{"account_id": "ifrs-full_Revenue", "thstrm_amount": "300"}]},
    )

    repo.save_raw_statements([stmt])
    got = repo.get_raw_statements("005930.KS")

    assert len(got) == 1
    assert got[0].payload["list"][0]["account_id"] == "ifrs-full_Revenue"


def test_existing_statement_years_supports_incremental_collect(repo):
    repo.save_raw_statements([
        RawStatement(ticker="005930.KS", fiscal_year=2024, source="dart", payload={}),
        RawStatement(ticker="005930.KS", fiscal_year=2025, source="dart", payload={}),
    ])

    years = repo.existing_statement_years("005930.KS", source="dart")

    assert years == {2024, 2025}


def test_save_raw_statements_overwrites_same_key(repo):
    repo.save_raw_statements([
        RawStatement(ticker="005930.KS", fiscal_year=2025, source="dart", payload={"v": 1})
    ])
    repo.save_raw_statements([
        RawStatement(ticker="005930.KS", fiscal_year=2025, source="dart", payload={"v": 2})
    ])

    got = repo.get_raw_statements("005930.KS")

    assert len(got) == 1
    assert got[0].payload["v"] == 2


def test_save_and_read_price_bars(repo):
    bars = [
        PriceBar(ticker="005930.KS", date=date(2026, 1, 5), close=71000.0, volume=1000),
        PriceBar(ticker="005930.KS", date=date(2026, 1, 6), close=72000.0, volume=2000),
    ]

    repo.save_price_bars(bars, source="pykrx")
    got = repo.get_price_bars("005930.KS")

    assert [b.close for b in got] == [71000.0, 72000.0]
    assert got[0].date == date(2026, 1, 5)


def test_latest_price_date_is_none_when_empty(repo):
    assert repo.latest_price_date("005930.KS") is None


def test_latest_price_date_returns_max(repo):
    repo.save_price_bars([
        PriceBar(ticker="005930.KS", date=date(2026, 1, 5), close=1.0),
        PriceBar(ticker="005930.KS", date=date(2026, 3, 2), close=2.0),
    ], source="pykrx")

    assert repo.latest_price_date("005930.KS") == date(2026, 3, 2)


def test_get_price_bars_respects_date_range(repo):
    repo.save_price_bars([
        PriceBar(ticker="A", date=date(2026, 1, 1), close=1.0),
        PriceBar(ticker="A", date=date(2026, 6, 1), close=2.0),
    ], source="pykrx")

    got = repo.get_price_bars("A", start=date(2026, 5, 1))

    assert [b.close for b in got] == [2.0]


def test_record_error_and_summarize(repo):
    repo.record_error("005930.KS", "dart", "timeout")
    repo.record_error("000660.KS", "dart", "404")

    errors = repo.recent_errors(limit=10)

    assert len(errors) == 2
    assert {e["ticker"] for e in errors} == {"005930.KS", "000660.KS"}


# --- 소스 구분 -------------------------------------------------------
#
# raw_financials가 dart와 edgar 페이로드를 함께 담는 것이 이 테이블의 존재
# 이유인데, source 필터를 통째로 지워도 기존 테스트가 전부 통과했다.


def test_get_raw_statements_filters_by_source(repo):
    repo.save_raw_statements([
        RawStatement(ticker="X", fiscal_year=2025, source="dart", payload={"from": "dart"}),
        RawStatement(ticker="X", fiscal_year=2025, source="edgar", payload={"from": "edgar"}),
    ])

    dart_only = repo.get_raw_statements("X", source="dart")
    both = repo.get_raw_statements("X")

    assert [s.payload["from"] for s in dart_only] == ["dart"]
    assert len(both) == 2


def test_existing_statement_years_is_scoped_to_source(repo):
    repo.save_raw_statements([
        RawStatement(ticker="X", fiscal_year=2024, source="dart", payload={}),
        RawStatement(ticker="X", fiscal_year=2025, source="edgar", payload={}),
    ])

    assert repo.existing_statement_years("X", source="dart") == {2024}
    assert repo.existing_statement_years("X", source="edgar") == {2025}


# --- 정렬 ------------------------------------------------------------


def test_get_price_bars_sorts_ascending_regardless_of_insert_order(repo):
    """평균·하락률 계산이 시계열 순서에 의존한다. 순서가 틀어지면 조용히 틀린다.

    주의 — 이 테스트는 ORDER BY 제거를 잡아내지 못한다(확인함).
    raw_prices의 PK가 (ticker, date)라서 `WHERE ticker = ?` 조회가 늘 그
    인덱스를 타고, SQLite가 삽입 순서와 무관하게 정렬된 순서로 돌려주기
    때문이다. 즉 get_price_bars의 ORDER BY는 현재 스키마에서 중복이며,
    행동만으로는 유무를 구별할 수 없다.

    그래도 남겨두는 이유는 두 가지다. 소비자가 의존하는 계약(오름차순)을
    문서로 고정하고, 나중에 PK나 인덱스가 바뀌어 그 우연한 정렬이 사라지면
    그때는 실제로 이 테스트가 잡아낸다.
    """
    repo.save_price_bars([
        PriceBar(ticker="A", date=date(2026, 6, 1), close=3.0),
        PriceBar(ticker="A", date=date(2026, 1, 1), close=1.0),
        PriceBar(ticker="A", date=date(2026, 3, 1), close=2.0),
    ], source="pykrx")

    got = repo.get_price_bars("A")

    assert [b.date for b in got] == [date(2026, 1, 1), date(2026, 3, 1), date(2026, 6, 1)]


def test_get_raw_statements_sorts_by_fiscal_year(repo):
    repo.save_raw_statements([
        RawStatement(ticker="X", fiscal_year=2025, source="dart", payload={}),
        RawStatement(ticker="X", fiscal_year=2021, source="dart", payload={}),
        RawStatement(ticker="X", fiscal_year=2023, source="dart", payload={}),
    ])

    assert [s.fiscal_year for s in repo.get_raw_statements("X")] == [2021, 2023, 2025]


# --- 날짜 범위 경계 ---------------------------------------------------


def test_get_price_bars_end_and_both_bounds_are_inclusive(repo):
    bars = [PriceBar(ticker="A", date=date(2026, m, 1), close=float(m)) for m in (1, 3, 6)]
    repo.save_price_bars(bars, source="pykrx")

    end_only = repo.get_price_bars("A", end=date(2026, 3, 1))
    both = repo.get_price_bars("A", start=date(2026, 3, 1), end=date(2026, 6, 1))

    assert [b.close for b in end_only] == [1.0, 3.0]   # 경계(3/1) 포함
    assert [b.close for b in both] == [3.0, 6.0]       # 양끝 포함


# --- executemany + ON CONFLICT 엣지 케이스 ----------------------------


def test_saving_empty_lists_is_a_noop(repo):
    repo.save_raw_statements([])
    repo.save_price_bars([], source="pykrx")

    assert repo.get_raw_statements("X") == []
    assert repo.get_price_bars("X") == []


def test_duplicate_keys_within_one_batch_keep_the_last(repo):
    """한 배치 안에 같은 키가 두 번 오면 마지막 값이 남아야 한다.

    수집기가 겹치는 구간을 다시 받아올 때 실제로 발생한다.
    """
    repo.save_price_bars([
        PriceBar(ticker="A", date=date(2026, 1, 1), close=1.0),
        PriceBar(ticker="A", date=date(2026, 1, 1), close=99.0),
    ], source="pykrx")
    repo.save_raw_statements([
        RawStatement(ticker="A", fiscal_year=2025, source="dart", payload={"v": 1}),
        RawStatement(ticker="A", fiscal_year=2025, source="dart", payload={"v": 99}),
    ])

    assert [b.close for b in repo.get_price_bars("A")] == [99.0]
    assert [s.payload["v"] for s in repo.get_raw_statements("A")] == [99]
