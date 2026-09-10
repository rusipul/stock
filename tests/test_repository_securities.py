import pytest

from stockscout.models import Security
from stockscout.store.db import connect, init_db
from stockscout.store.repository import Repository


@pytest.fixture
def repo(tmp_path):
    with connect(tmp_path / "t.db") as conn:
        init_db(conn)
        yield Repository(conn)


def make_security(ticker="005930.KS", **overrides) -> Security:
    base = dict(
        ticker=ticker,
        market="KR",
        name="삼성전자",
        exchange="KOSPI",
        currency="KRW",
        source_id="00126380",
        is_financial=False,
        in_universe=True,
    )
    base.update(overrides)
    return Security(**base)


def test_upsert_then_get(repo):
    repo.upsert_security(make_security())

    got = repo.get_security("005930.KS")

    assert got is not None
    assert got.name == "삼성전자"
    assert got.source_id == "00126380"
    assert got.is_financial is False


def test_get_missing_returns_none(repo):
    assert repo.get_security("NOPE") is None


def test_upsert_overwrites_existing(repo):
    repo.upsert_security(make_security(name="구명칭"))
    repo.upsert_security(make_security(name="삼성전자"))

    assert repo.get_security("005930.KS").name == "삼성전자"
    assert len(repo.list_securities()) == 1


def test_list_securities_filters_by_market_and_universe(repo):
    repo.upsert_security(make_security("005930.KS", market="KR"))
    repo.upsert_security(make_security("AAPL", market="US", name="Apple", currency="USD"))
    repo.upsert_security(make_security("000660.KS", market="KR", name="탈락", in_universe=False))

    kr = repo.list_securities(market="KR")
    all_including_dropped = repo.list_securities(market="KR", in_universe_only=False)

    assert [s.ticker for s in kr] == ["005930.KS"]
    assert len(all_including_dropped) == 2


def test_mark_out_of_universe(repo):
    repo.upsert_security(make_security())

    repo.mark_out_of_universe(["005930.KS"])

    assert repo.list_securities(market="KR") == []
    assert repo.get_security("005930.KS").in_universe is False
