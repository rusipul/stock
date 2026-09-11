from datetime import date

import pytest

from stockscout.cli import build_parser, build_universe, fiscal_years, main, price_window
from stockscout.models import (
    AnalysisResult,
    FilterOutcome,
    PriceStats,
    Security,
    Snapshot,
    Valuation,
)
from stockscout.sources.krx import KrxUniverse
from stockscout.sources.universe import YamlUniverse
from stockscout.store.db import connect, init_db
from stockscout.store.repository import Repository


@pytest.fixture
def repo(tmp_path):
    with connect(tmp_path / "t.db") as conn:
        init_db(conn)
        yield Repository(conn)


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

def test_price_window_excludes_today():
    """장중 수집 시 오늘 봉은 확정 종가가 아니다. 어제까지만 받는다."""
    start, end = price_window(years=5, today=date(2026, 9, 11))

    assert end == date(2026, 9, 10)
    assert start == date(2021, 9, 10)


def test_price_window_handles_leap_day():
    """2/29에 5년을 빼면 존재하지 않는 날짜가 된다."""
    start, end = price_window(years=5, today=date(2024, 3, 1))

    assert end == date(2024, 2, 29)   # 윤일이 종료일이 된다
    assert start == date(2019, 2, 28)  # 2019년에는 2/29가 없다


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


# --- I6: --tickers 의 6자리 코드가 조용히 0건으로 새는 버그 -----------------
#
# DB의 ticker는 항상 거래소 접미사가 붙는다('005930.KS'). 그런데 증권사
# 화면에 찍히는 코드는 접미사가 없는 6자리('005930')다. collect_market()은
# 문자열을 그대로 비교하므로, 정규화하지 않으면 대상 0종목·조용한 성공(exit 0)이
# 된다. resolve_tickers()가 KR에서는 krx.to_ticker()로 맞추고, DB에 없거나
# in_universe=0인 티커는 이름을 밝혀 경고한다.


def test_resolve_tickers_normalizes_kr_plain_code(repo, capsys):
    from stockscout.cli import resolve_tickers

    repo.upsert_security(
        Security(ticker="005930.KS", market="KR", name="삼성전자", currency="KRW")
    )

    tickers = resolve_tickers(repo, market="KR", tickers=["005930"])

    assert tickers == ["005930.KS"]
    assert "경고" not in capsys.readouterr().out


def test_resolve_tickers_leaves_already_suffixed_kr_code_unchanged(repo):
    from stockscout.cli import resolve_tickers

    repo.upsert_security(
        Security(ticker="005930.KS", market="KR", name="삼성전자", currency="KRW")
    )

    tickers = resolve_tickers(repo, market="KR", tickers=["005930.KS"])

    assert tickers == ["005930.KS"]


def test_resolve_tickers_warns_on_unknown_ticker(repo, capsys):
    """오타 등 목록에 없는 티커는 이름을 밝혀 경고해야 한다. 조용히 넘어가면 안 된다."""
    from stockscout.cli import resolve_tickers

    resolve_tickers(repo, market="KR", tickers=["999999"])

    out = capsys.readouterr().out
    assert "999999.KS" in out


def test_resolve_tickers_warns_on_out_of_universe_ticker(repo, capsys):
    """DB에는 있지만 in_universe=0이면 collect_market()이 건너뛴다. 그 사실도 알려야 한다."""
    from stockscout.cli import resolve_tickers

    repo.upsert_security(
        Security(
            ticker="005930.KS", market="KR", name="삼성전자",
            currency="KRW", in_universe=False,
        )
    )

    resolve_tickers(repo, market="KR", tickers=["005930"])

    out = capsys.readouterr().out
    assert "005930.KS" in out


def test_resolve_tickers_does_not_append_ks_suffix_for_non_kr_market(repo):
    """to_ticker()는 무조건 .KS를 붙인다. KR이 아닌 시장에는 적용하면 안 된다."""
    from stockscout.cli import resolve_tickers

    tickers = resolve_tickers(repo, market="US", tickers=["AAPL"])

    assert tickers == ["AAPL"]


def test_resolve_tickers_passes_through_none(repo):
    from stockscout.cli import resolve_tickers

    assert resolve_tickers(repo, market="KR", tickers=None) is None


def test_main_collect_normalizes_plain_kr_ticker_before_dispatch(tmp_path, monkeypatch):
    """--tickers 005930 이 collect_market()에는 '005930.KS'로 도달해야 한다.

    수정 전에는 args.tickers가 그대로 전달되어 DB 티커('005930.KS')와
    매칭되지 않고, 대상 0종목으로 조용히 끝난다.
    """
    from stockscout.pipeline.collect import CollectSummary

    db_path = tmp_path / "scout.db"
    with connect(db_path) as conn:
        init_db(conn)
        Repository(conn).upsert_security(
            Security(ticker="005930.KS", market="KR", name="삼성전자", currency="KRW")
        )

    captured = {}

    def fake_collect_market(
        repo, market, fundamentals, prices, years, price_start, price_end,
        tickers=None, force=False,
    ):
        captured["tickers"] = tickers
        return CollectSummary(total=0)

    monkeypatch.setattr("stockscout.cli.collect_market", fake_collect_market)

    exit_code = main([
        "--db", str(db_path), "--config", "config/settings.yaml",
        "collect", "--market", "KR", "--tickers", "005930", "--skip-financials",
    ])

    assert exit_code == 0
    assert captured["tickers"] == ["005930.KS"]


# --- analyze 명령 -----------------------------------------------------


def test_parser_analyze_command():
    args = build_parser().parse_args(["analyze"])

    assert args.command == "analyze"
    assert args.market == "KR"
    assert args.note is None
    assert args.top == 20


def test_parser_analyze_accepts_note_and_top():
    args = build_parser().parse_args(
        ["analyze", "--note", "임계치 실험", "--top", "5"])

    assert args.note == "임계치 실험"
    assert args.top == 5


def test_main_analyze_on_empty_db_returns_zero(tmp_path, capsys):
    from stockscout.cli import main

    code = main(["--db", str(tmp_path / "t.db"), "--config",
                 "config/settings.yaml", "analyze"])

    assert code == 0
    assert "0종목 분석" in capsys.readouterr().out


# --- analyze 명령: 순위표 출력 ------------------------------------------
#
# render_ranking()을 파이프라인 전체를 돌리지 않고 검증하기 위해,
# Repository.save_snapshot()으로 직접 스냅샷을 만든다. AnalysisResult를
# 손으로 조립해 넣는다.


def _analysis_result(
    ticker: str,
    passed: bool = True,
    drop_vs_avg_5y: float = -0.1,
    price_current: float = 100.0,
    avg_price_5y: float = 150.0,
    drop_vs_high: float = -0.2,
) -> AnalysisResult:
    return AnalysisResult(
        ticker=ticker,
        filters=FilterOutcome(failures=[] if passed else ["FINANCIAL_SECTOR"]),
        stats=PriceStats(
            price_current=price_current,
            avg_price={1: avg_price_5y, 3: avg_price_5y, 5: avg_price_5y},
            high_5y=avg_price_5y * 1.5,
            drop_vs_avg={1: drop_vs_avg_5y, 3: drop_vs_avg_5y, 5: drop_vs_avg_5y},
            drop_vs_high=drop_vs_high,
        ),
        valuation=Valuation(),
    )


def _save_snapshot(repo: Repository, results: list[AnalysisResult]) -> int:
    snapshot = Snapshot(
        run_at="2026-01-01T00:00:00+00:00", config_hash="deadbeef",
        config_json="{}", note=None,
    )
    return repo.save_snapshot(snapshot, results)


def test_render_ranking_excludes_filter_failing_stocks(repo):
    from stockscout.cli import render_ranking

    repo.upsert_security(Security(ticker="AAA", market="KR", name="AAA전자", currency="KRW"))
    repo.upsert_security(Security(ticker="BBB", market="KR", name="BBB은행", currency="KRW"))

    snapshot_id = _save_snapshot(repo, [
        _analysis_result("AAA", passed=True, drop_vs_avg_5y=-0.3),
        _analysis_result("BBB", passed=False, drop_vs_avg_5y=-0.9),
    ])

    output = render_ranking(repo, snapshot_id, top=20, window=5)

    assert "AAA전자" in output
    assert "BBB은행" not in output


def test_render_ranking_sorts_ascending_by_drop_rate(repo):
    """가장 많이 떨어진 종목이 맨 위로 온다 (drop_vs_avg는 음수이므로 오름차순 정렬)."""
    from stockscout.cli import render_ranking

    repo.upsert_security(Security(ticker="AAA", market="KR", name="가종목", currency="KRW"))
    repo.upsert_security(Security(ticker="BBB", market="KR", name="나종목", currency="KRW"))
    repo.upsert_security(Security(ticker="CCC", market="KR", name="다종목", currency="KRW"))

    snapshot_id = _save_snapshot(repo, [
        _analysis_result("AAA", drop_vs_avg_5y=-0.1),
        _analysis_result("BBB", drop_vs_avg_5y=-0.5),
        _analysis_result("CCC", drop_vs_avg_5y=-0.3),
    ])

    output = render_ranking(repo, snapshot_id, top=20, window=5)

    # -0.5(나종목) -> -0.3(다종목) -> -0.1(가종목) 순으로 나와야 한다.
    positions = [output.index(name) for name in ("나종목", "다종목", "가종목")]
    assert positions == sorted(positions)


def test_render_ranking_reports_no_passing_stocks(repo):
    from stockscout.cli import render_ranking

    repo.upsert_security(Security(ticker="AAA", market="KR", name="AAA전자", currency="KRW"))

    snapshot_id = _save_snapshot(repo, [
        _analysis_result("AAA", passed=False),
    ])

    output = render_ranking(repo, snapshot_id, top=20, window=5)

    assert "(통과 종목 없음)" in output


def test_render_ranking_respects_top(repo):
    from stockscout.cli import render_ranking

    results = []
    for i in range(5):
        ticker = f"T{i}"
        repo.upsert_security(
            Security(ticker=ticker, market="KR", name=f"종목{i}", currency="KRW")
        )
        results.append(_analysis_result(ticker, drop_vs_avg_5y=-0.1 * (i + 1)))

    snapshot_id = _save_snapshot(repo, results)

    output = render_ranking(repo, snapshot_id, top=2, window=5)

    data_lines = output.splitlines()[2:]  # 머리글 줄과 구분선(---) 다음부터
    assert len(data_lines) == 2


def test_main_analyze_top_zero_suppresses_ranking_table(tmp_path, monkeypatch, capsys):
    """--top 0 이면 요약과 스냅샷 저장은 그대로 하되 순위표는 찍지 않는다."""
    from stockscout.pipeline.analyze import AnalyzeSummary

    def fake_analyze_market(repo, market, config, note=None):
        return AnalyzeSummary(snapshot_id=1, analyzed=1, passed=1, excluded=0)

    monkeypatch.setattr("stockscout.cli.analyze_market", fake_analyze_market)

    exit_code = main([
        "--db", str(tmp_path / "t.db"), "--config", "config/settings.yaml",
        "analyze", "--top", "0",
    ])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert out == "1종목 분석 · 통과 1 · 제외 0\n스냅샷 #1 저장\n"


def test_parser_serve_command():
    args = build_parser().parse_args(["serve"])

    assert args.command == "serve"
    assert args.host == "127.0.0.1"
    assert args.port == 8000


def test_parser_serve_accepts_host_and_port():
    args = build_parser().parse_args(["serve", "--host", "0.0.0.0", "--port", "9000"])

    assert args.host == "0.0.0.0"
    assert args.port == 9000
