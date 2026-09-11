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


# --- 필드 왕복 -------------------------------------------------------
#
# 아래 테스트가 없으면 _to_security가 컬럼을 뒤바꿔 매핑해도 아무도 모른다.
# 코드 리뷰에서 exchange↔sector를 서로 바꿔치기했는데 13개 테스트가
# 전부 통과했다.


def test_every_field_round_trips(repo):
    original = Security(
        ticker="005930.KS",
        market="KR",
        name="삼성전자",
        name_en="Samsung Electronics",
        exchange="KOSPI",
        sector="반도체",
        is_financial=False,
        source_id="00126380",
        currency="KRW",
        listed_at="1975-06-11",
        in_universe=True,
    )

    repo.upsert_security(original)

    assert repo.get_security("005930.KS") == original


def test_every_field_round_trips_through_list(repo):
    original = Security(
        ticker="AAPL",
        market="US",
        name="Apple Inc.",
        name_en="Apple Inc.",
        exchange="NASDAQ",
        sector="Technology",
        is_financial=False,
        source_id="0000320193",
        currency="USD",
        listed_at="1980-12-12",
        in_universe=True,
    )

    repo.upsert_security(original)

    assert repo.list_securities(market="US") == [original]


def test_financial_sector_flag_round_trips(repo):
    """금융업은 하드필터에서 통째로 제외되므로 이 플래그가 뒤집히면 결과가 달라진다."""
    repo.upsert_security(make_security("105560.KS", name="KB금융", is_financial=True))

    assert repo.get_security("105560.KS").is_financial is True


def test_mark_out_of_universe_handles_multiple_and_empty(repo):
    repo.upsert_security(make_security("005930.KS"))
    repo.upsert_security(make_security("000660.KS", name="SK하이닉스"))

    repo.mark_out_of_universe([])
    assert len(repo.list_securities(market="KR")) == 2

    repo.mark_out_of_universe(["005930.KS", "000660.KS"])
    assert repo.list_securities(market="KR") == []
    assert len(repo.list_securities(market="KR", in_universe_only=False)) == 2
