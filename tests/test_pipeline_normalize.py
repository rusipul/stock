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
