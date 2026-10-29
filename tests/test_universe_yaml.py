from pathlib import Path

import pytest
import yaml

from stockscout.sources.universe import YamlUniverse


def _write_universe(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "universe.yaml"
    path.write_text(content, encoding="utf-8")
    return path


SAMPLE_YAML = """
KR:
  exchange: KOSPI
  currency: KRW
  tickers:
    - "005930"
    - "000660"
    - "105560"
  is_financial:
    - "105560"
"""


def test_fetch_returns_securities_with_expected_fields(tmp_path):
    path = _write_universe(tmp_path, SAMPLE_YAML)
    names = {"005930": "삼성전자", "000660": "SK하이닉스", "105560": "KB금융"}
    uni = YamlUniverse(path=path, market="KR", name_fn=lambda code: names[code])

    secs = uni.fetch()

    assert [s.ticker for s in secs] == ["005930.KS", "000660.KS", "105560.KS"]
    for s in secs:
        assert s.market == "KR"
        assert s.exchange == "KOSPI"
        assert s.currency == "KRW"
        assert s.in_universe is True


def test_names_come_from_injected_name_fn(tmp_path):
    path = _write_universe(tmp_path, SAMPLE_YAML)
    names = {"005930": "삼성전자", "000660": "SK하이닉스", "105560": "KB금융"}
    uni = YamlUniverse(path=path, market="KR", name_fn=lambda code: names[code])

    secs = uni.fetch()

    assert secs[0].name == "삼성전자"
    assert secs[1].name == "SK하이닉스"
    assert secs[2].name == "KB금융"


def test_is_financial_flags_only_listed_tickers(tmp_path):
    path = _write_universe(tmp_path, SAMPLE_YAML)
    uni = YamlUniverse(path=path, market="KR", name_fn=lambda code: code)

    secs = uni.fetch()
    by_ticker = {s.ticker: s for s in secs}

    assert by_ticker["105560.KS"].is_financial is True
    assert by_ticker["005930.KS"].is_financial is False
    assert by_ticker["000660.KS"].is_financial is False


def test_name_lookup_failure_keeps_ticker_with_bare_code_as_name(tmp_path):
    """KrxUniverse와 달리 손으로 고른 목록이라 이름 조회 실패는 일시적 네트워크
    문제일 뿐, 종목이 잘못된 게 아니다. 그래서 건너뛰지 않고 코드를 이름으로 쓴다."""
    path = _write_universe(tmp_path, SAMPLE_YAML)

    def flaky_name(code):
        if code == "000660":
            raise ValueError("network down")
        return {"005930": "삼성전자", "105560": "KB금융"}[code]

    uni = YamlUniverse(path=path, market="KR", name_fn=flaky_name)

    secs = uni.fetch()

    assert [s.ticker for s in secs] == ["005930.KS", "000660.KS", "105560.KS"]
    by_ticker = {s.ticker: s for s in secs}
    assert by_ticker["000660.KS"].name == "000660"


def test_missing_config_file_raises_file_not_found(tmp_path):
    missing = tmp_path / "does_not_exist.yaml"

    uni = YamlUniverse(path=missing, market="KR", name_fn=lambda code: code)

    with pytest.raises(FileNotFoundError) as excinfo:
        uni.fetch()

    assert str(missing) in str(excinfo.value)


def test_unknown_market_key_raises_value_error(tmp_path):
    path = _write_universe(tmp_path, SAMPLE_YAML)

    uni = YamlUniverse(path=path, market="US", name_fn=lambda code: code)

    with pytest.raises(ValueError) as excinfo:
        uni.fetch()

    assert "US" in str(excinfo.value)


def test_fetch_preserves_yaml_ticker_order(tmp_path):
    yaml_text = """
KR:
  exchange: KOSPI
  currency: KRW
  tickers:
    - "000660"
    - "005930"
    - "105560"
  is_financial: []
"""
    path = _write_universe(tmp_path, yaml_text)
    uni = YamlUniverse(path=path, market="KR", name_fn=lambda code: code)

    secs = uni.fetch()

    assert [s.ticker for s in secs] == ["000660.KS", "005930.KS", "105560.KS"]


# --- 회귀 테스트: 실제 config/universe.yaml -----------------------------------

REAL_UNIVERSE_PATH = Path("config/universe.yaml")


def test_real_universe_yaml_has_42_tickers():
    raw = yaml.safe_load(REAL_UNIVERSE_PATH.read_text(encoding="utf-8"))
    tickers = raw["KR"]["tickers"]

    assert len(tickers) == 42


def test_real_universe_yaml_codes_are_six_digit_strings():
    """언쿼트된 YAML 정수로 앞자리 0이 날아가는 버그를 잡는다."""
    raw = yaml.safe_load(REAL_UNIVERSE_PATH.read_text(encoding="utf-8"))
    tickers = raw["KR"]["tickers"]

    for code in tickers:
        assert isinstance(code, str), f"{code!r} is not a str"
        assert len(code) == 6, f"{code!r} is not 6 chars"
        assert code.isdigit(), f"{code!r} is not all digits"


def test_real_universe_yaml_is_financial_entries_are_subset_of_tickers():
    raw = yaml.safe_load(REAL_UNIVERSE_PATH.read_text(encoding="utf-8"))
    tickers = set(raw["KR"]["tickers"])
    is_financial = raw["KR"]["is_financial"]

    assert len(is_financial) == 7
    assert set(is_financial) <= tickers


def test_real_universe_yaml_has_no_duplicate_tickers():
    raw = yaml.safe_load(REAL_UNIVERSE_PATH.read_text(encoding="utf-8"))
    tickers = raw["KR"]["tickers"]

    assert len(tickers) == len(set(tickers))


def test_real_universe_yaml_loads_via_yaml_universe():
    uni = YamlUniverse(path=REAL_UNIVERSE_PATH, market="KR", name_fn=lambda code: code)

    secs = uni.fetch()

    assert len(secs) == 42
    financial_count = sum(1 for s in secs if s.is_financial)
    assert financial_count == 7


US_YAML = """
US:
  exchange: US
  currency: USD
  tickers:
    - AAPL
    - brk-b
    - JPM
  names:
    AAPL: Apple
    BRK-B: Berkshire Hathaway
  is_financial:
    - BRK-B
    - JPM
"""


def test_us_tickers_keep_their_symbol_without_a_suffix(tmp_path):
    path = _write_universe(tmp_path, US_YAML)

    secs = YamlUniverse(path=path, market="US").fetch()

    assert [s.ticker for s in secs] == ["AAPL", "BRK-B", "JPM"]
    assert all(s.currency == "USD" and s.market == "US" for s in secs)


def test_us_names_come_from_the_yaml(tmp_path):
    """pykrx는 미국 종목을 모른다. 이름이 없으면 티커를 이름으로 쓴다."""
    path = _write_universe(tmp_path, US_YAML)

    secs = {s.ticker: s for s in YamlUniverse(path=path, market="US").fetch()}

    assert secs["AAPL"].name == "Apple"
    assert secs["BRK-B"].name == "Berkshire Hathaway"
    assert secs["JPM"].name == "JPM"


def test_us_name_lookup_never_calls_pykrx(tmp_path):
    path = _write_universe(tmp_path, US_YAML)

    def explode(code):
        raise AssertionError("미국 종목에 pykrx 이름 조회를 쓰면 안 된다")

    YamlUniverse(path=path, market="US", name_fn=explode).fetch()


def test_us_financial_flags_match_after_upper_casing(tmp_path):
    path = _write_universe(tmp_path, US_YAML)

    secs = {s.ticker: s for s in YamlUniverse(path=path, market="US").fetch()}

    assert secs["BRK-B"].is_financial is True
    assert secs["JPM"].is_financial is True
    assert secs["AAPL"].is_financial is False


def test_kr_names_in_yaml_override_pykrx(tmp_path):
    """names는 시장과 무관하게 먼저 본다. 조회 실패를 피하고 싶을 때 쓴다."""
    path = _write_universe(tmp_path, SAMPLE_YAML + '  names:\n    "005930": 삼성전자\n')

    secs = YamlUniverse(path=path, market="KR",
                        name_fn=lambda code: f"pykrx-{code}").fetch()

    assert secs[0].name == "삼성전자"
    assert secs[1].name == "pykrx-000660"


def test_the_real_universe_has_a_us_section():
    secs = YamlUniverse(market="US").fetch()

    assert 35 <= len(secs) <= 45
    assert any(s.is_financial for s in secs)
    assert all("." not in s.ticker for s in secs)


def test_cik_overrides_are_read_from_the_us_section(tmp_path):
    from stockscout.sources.universe import load_cik_overrides

    path = _write_universe(tmp_path, US_YAML + "  cik:\n    xom: 34088\n")

    assert load_cik_overrides(path) == {"XOM": 34088}


def test_no_cik_section_means_no_overrides(tmp_path):
    from stockscout.sources.universe import load_cik_overrides

    assert load_cik_overrides(_write_universe(tmp_path, US_YAML)) == {}


def test_the_real_universe_points_xom_at_the_old_cik():
    from stockscout.sources.universe import load_cik_overrides

    assert load_cik_overrides()["XOM"] == 34088
