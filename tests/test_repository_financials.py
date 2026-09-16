import pytest

from stockscout.models import Financials
from stockscout.store.db import connect, init_db
from stockscout.store.repository import Repository


@pytest.fixture
def repo(tmp_path):
    with connect(tmp_path / "t.db") as conn:
        init_db(conn)
        yield Repository(conn)


def make(year: int, **overrides) -> Financials:
    base = dict(
        ticker="005930.KS", fiscal_year=year, currency="KRW",
        revenue=300.0, operating_income=30.0, total_equity=200.0,
        total_liabilities=60.0, capex=None,
    )
    base.update(overrides)
    return Financials(**base)


def test_save_and_read_back(repo):
    repo.save_financials([make(2024), make(2025)])

    got = repo.get_financials("005930.KS")

    assert [f.fiscal_year for f in got] == [2024, 2025]
    assert got[0].revenue == 300.0
    assert got[0].currency == "KRW"


def test_missing_fields_round_trips(repo):
    repo.save_financials([make(2025)])

    got = repo.get_financials("005930.KS")[0]

    assert "capex" in got.missing_fields


def test_save_overwrites_same_year(repo):
    repo.save_financials([make(2025, revenue=100.0)])
    repo.save_financials([make(2025, revenue=999.0)])

    got = repo.get_financials("005930.KS")

    assert len(got) == 1
    assert got[0].revenue == 999.0


def test_get_financials_returns_ascending_years(repo):
    repo.save_financials([make(2025), make(2021), make(2023)])

    got = repo.get_financials("005930.KS")

    assert [f.fiscal_year for f in got] == [2021, 2023, 2025]


def test_get_financials_empty_for_unknown_ticker(repo):
    assert repo.get_financials("NOPE") == []


def test_coverage_report_counts_missing_per_field(repo):
    repo.save_financials([
        make(2024, capex=None, net_income=1.0),
        make(2025, capex=5.0, net_income=None),
    ])

    report = repo.missing_field_counts()

    assert report["capex"] == 1
    assert report["net_income"] == 1


# --- 컬럼 매핑 -----------------------------------------------------
#
# 위 테스트들은 revenue·currency·fiscal_year만 확인한다. _FIN_COLUMNS는
# 16개인데, save_financials/get_financials가 컬럼 하나를 잘못 매핑해도
# (예: current_assets와 current_liabilities를 맞바꾸거나
# shares_outstanding을 빠뜨려도) 위 테스트는 전부 통과한다.


def test_all_fields_round_trip_without_mixing_columns(repo):
    original = Financials(
        ticker="005930.KS", fiscal_year=2025, currency="KRW",
        revenue=1.0, operating_income=2.0, net_income=3.0,
        total_assets=4.0, total_liabilities=5.0, total_equity=6.0,
        current_assets=7.0, current_liabilities=8.0, interest_expense=9.0,
        operating_cash_flow=10.0, capex=11.0, shares_outstanding=12.0,
        dividend_per_share=13.0,
    )

    repo.save_financials([original])
    got = repo.get_financials("005930.KS")[0]

    assert got == original


# --- executemany + ON CONFLICT 엣지 케이스 ----------------------------


def test_saving_empty_list_is_a_noop(repo):
    repo.save_financials([])

    assert repo.get_financials("005930.KS") == []


def test_duplicate_fiscal_year_within_one_batch_keeps_the_last(repo):
    """한 배치 안에 같은 (ticker, fiscal_year)가 두 번 오면 마지막 값이 남아야 한다."""
    repo.save_financials([
        make(2025, revenue=1.0),
        make(2025, revenue=99.0),
    ])

    got = repo.get_financials("005930.KS")

    assert len(got) == 1
    assert got[0].revenue == 99.0


# --- 여러 티커 집계 ---------------------------------------------------


def test_missing_field_counts_aggregates_across_tickers(repo):
    repo.save_financials([
        make(2025, ticker="005930.KS", capex=None),
        make(2025, ticker="000660.KS", capex=None),
    ])

    report = repo.missing_field_counts()

    assert report["capex"] == 2


# --- proxy_fields (계획 C Task 1) -------------------------------------


def test_proxy_fields_round_trip(repo):
    repo.save_financials([Financials(
        ticker="051900.KS", fiscal_year=2024, currency="KRW",
        revenue=1.0, operating_income=1.0, total_equity=1.0,
        total_liabilities=1.0, interest_expense=900.0,
        proxy_fields=["interest_expense"],
    )])

    (loaded,) = repo.get_financials("051900.KS")

    assert loaded.proxy_fields == ["interest_expense"]


def test_proxy_fields_reads_as_empty_list_when_column_is_null(repo):
    """계획 C 이전에 저장된 196행은 이 컬럼이 NULL이다."""
    repo.save_financials([Financials(
        ticker="005930.KS", fiscal_year=2024, currency="KRW",
        revenue=1.0, operating_income=1.0, total_equity=1.0, total_liabilities=1.0,
    )])
    repo.conn.execute("UPDATE financials SET proxy_fields = NULL")

    (loaded,) = repo.get_financials("005930.KS")

    assert loaded.proxy_fields == []
