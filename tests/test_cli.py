from datetime import date

import pytest

from stockscout.cli import build_parser, build_universe, fiscal_years, main, price_window
from stockscout.sources.krx import KrxUniverse
from stockscout.sources.universe import YamlUniverse


# --- 인자 파싱 --------------------------------------------------------

def test_parser_universe_command():
    args = build_parser().parse_args(["universe", "--market", "KR"])

    assert args.command == "universe"
    assert args.market == "KR"


def test_parser_universe_defaults_to_yaml_source():
    """KRX 지수 API가 로그인 필수라, 기본값은 동작하는 쪽이어야 한다."""
    args = build_parser().parse_args(["universe"])

    assert args.source == "yaml"


def test_parser_collect_defaults():
    args = build_parser().parse_args(["collect"])

    assert args.command == "collect"
    assert args.market == "KR"
    assert args.force is False
    assert args.tickers is None


def test_parser_collect_accepts_tickers_and_force():
    args = build_parser().parse_args(
        ["collect", "--tickers", "005930.KS", "000660.KS", "--force"]
    )

    assert args.tickers == ["005930.KS", "000660.KS"]
    assert args.force is True


def test_parser_normalize_command():
    args = build_parser().parse_args(["normalize"])

    assert args.command == "normalize"


def test_parser_requires_a_command():
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_parser_rejects_unknown_universe_source():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["universe", "--source", "nonsense"])


# --- 날짜 계산 --------------------------------------------------------

def test_price_window_spans_requested_years():
    start, end = price_window(years=5, today=date(2026, 9, 10))

    assert end == date(2026, 9, 10)
    assert start == date(2021, 9, 10)


def test_price_window_handles_leap_day():
    """2/29에 5년을 빼면 존재하지 않는 날짜가 된다."""
    start, _ = price_window(years=5, today=date(2024, 2, 29))

    assert start == date(2019, 2, 28)


def test_fiscal_years_excludes_current_year():
    """올해 사업보고서는 아직 안 나왔다. 작년부터 거슬러 올라간다."""
    assert fiscal_years(count=5, today=date(2026, 9, 10)) == [2021, 2022, 2023, 2024, 2025]


# --- 유니버스 provider 선택 -------------------------------------------

def test_build_universe_yaml_is_default(monkeypatch):
    monkeypatch.delenv("KRX_ID", raising=False)
    monkeypatch.delenv("KRX_PW", raising=False)

    assert isinstance(build_universe("yaml", "KR"), YamlUniverse)


def test_build_universe_krx_requires_credentials(monkeypatch):
    monkeypatch.delenv("KRX_ID", raising=False)
    monkeypatch.delenv("KRX_PW", raising=False)

    with pytest.raises(SystemExit, match="KRX_ID"):
        build_universe("krx", "KR")


def test_build_universe_krx_with_credentials(monkeypatch):
    monkeypatch.setenv("KRX_ID", "someone")
    monkeypatch.setenv("KRX_PW", "secret")

    assert isinstance(build_universe("krx", "KR"), KrxUniverse)


def test_build_universe_rejects_unknown_source():
    with pytest.raises(SystemExit, match="yaml"):
        build_universe("bogus", "KR")


# --- main() 디스패치 ---------------------------------------------------

def test_main_normalize_on_empty_db_returns_zero(tmp_path, capsys):
    db_path = tmp_path / "scout.db"

    exit_code = main(["--db", str(db_path), "--config", "config/settings.yaml", "normalize"])

    assert exit_code == 0
    # 반환값만으로는 디스패치가 실제로 일어났는지 알 수 없다(명령이 매칭되지
    # 않아도 main()은 그냥 0을 반환한다). normalize_market()의 출력이
    # 실제로 찍혔는지까지 확인해야 오배선을 잡아낸다.
    assert "0종목" in capsys.readouterr().out


# --- 재무 수집과 주가 수집의 분리 --------------------------------------
#
# 주가(pykrx)는 DART 키가 필요 없다. 키 하나가 없다고 수집 전체를 막으면
# 5년치 주가를 받아둘 수 있는데도 아무것도 못 하게 된다.


def test_build_fundamentals_skips_when_flag_set():
    from stockscout.cli import SkippedFundamentals, build_fundamentals

    source, years = build_fundamentals(skip=True, years=[2024, 2025])

    assert isinstance(source, SkippedFundamentals)
    assert years == []


def test_build_fundamentals_skips_when_no_dart_key(tmp_path, monkeypatch):
    """키가 없으면 실패하지 말고 주가만 받는다."""
    from stockscout.cli import SkippedFundamentals, build_fundamentals

    monkeypatch.delenv("DART_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)  # config/.env 가 없는 디렉터리

    source, years = build_fundamentals(skip=False, years=[2024, 2025])

    assert isinstance(source, SkippedFundamentals)
    assert years == []


def test_build_fundamentals_uses_dart_when_key_present(monkeypatch):
    from stockscout.cli import build_fundamentals
    from stockscout.sources.dart import DartSource

    monkeypatch.setenv("DART_API_KEY", "dummy-key-for-test")

    source, years = build_fundamentals(skip=False, years=[2024, 2025])

    assert isinstance(source, DartSource)
    assert years == [2024, 2025]


def test_skipped_fundamentals_would_raise_if_ever_called():
    """years=[] 와 짝지어 쓰이므로 호출될 일이 없다. 계약을 고정해둔다."""
    from stockscout.cli import SkippedFundamentals

    with pytest.raises(AssertionError):
        SkippedFundamentals().fetch_annual("005930.KS", [2025])


def test_parser_collect_skip_financials_flag():
    args = build_parser().parse_args(["collect", "--skip-financials"])

    assert args.skip_financials is True
    assert build_parser().parse_args(["collect"]).skip_financials is False
