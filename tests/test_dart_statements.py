"""DART 재무제표 수집과 status 코드 처리.

공식 가이드(DS003/2019020, fnlttSinglAcntAll.json)가 문서화하는 응답
status는 7종이다: 000 정상 / 010 미등록 키 / 013 조회된 데이터 없음 /
020 요청 제한 초과 / 100 필드 값 오류 / 800 서비스 점검 중 /
900 정의되지 않은 오류.

이 파일은 그 7종이 각각 올바른 결과로 이어지는지 검증한다. 특히 010·100·020은
"이 종목만"이 아니라 "요청 자체"의 문제라서 나머지 모든 종목도 동일하게
실패한다 — FatalSourceError로 배치를 멈춰야 한다(개별 SourceError로
종목마다 반복 기록하면 안 된다). 800은 일시적이므로 재시도 대상이다.
"""

from __future__ import annotations

import httpx
import pytest

from stockscout.sources.base import FatalSourceError, PermanentSourceError, SourceError
from stockscout.sources.dart import DartSource


def make_source(handler, **kwargs) -> DartSource:
    """httpx MockTransport로 네트워크를 대체한다. tests/test_dart_corpcode.py와
    같은 패턴이며, TokenBucket 대기로 테스트가 느려지지 않도록 rate_per_sec을
    넉넉히 준다."""
    transport = httpx.MockTransport(handler)
    kwargs.setdefault("rate_per_sec", 1000.0)
    return DartSource("dummy-key", client=httpx.Client(transport=transport), **kwargs)


def json_response(status: str, message: str = "", **extra) -> httpx.Response:
    body = {"status": status, "message": message}
    body.update(extra)
    return httpx.Response(200, json=body)


# --- fetch_annual (통합 경로) ----------------------------------------


def test_fetch_annual_returns_raw_statement_per_year():
    payload = {
        "status": "000",
        "list": [
            {"sj_div": "BS", "account_id": "ifrs-full_Assets", "account_nm": "자산총계",
             "thstrm_amount": "100"},
        ],
    }

    def handler(request):
        return json_response("000", list=payload["list"])

    src = make_source(handler)
    src.corp_codes = {"005930": "00126380"}

    stmts = src.fetch_annual("005930.KS", [2023, 2024])

    assert len(stmts) == 2
    assert {s.fiscal_year for s in stmts} == {2023, 2024}
    assert all(s.source == "dart" for s in stmts)
    assert all(s.ticker == "005930.KS" for s in stmts)
    assert stmts[0].payload["list"], "payload에 계정 목록이 그대로 들어 있어야 한다"


def test_fetch_annual_skips_years_with_no_data():
    """013(조회된 데이터 없음)은 그 회계연도만 없는 정상 상황이다.
    다른 연도는 계속 반환되어야 한다."""

    def handler(request):
        year = request.url.params["bsns_year"]
        if year == "2020":
            return json_response("013", "조회된 데이터가 없습니다")
        return json_response("000", list=[{"sj_div": "BS", "account_id": "x",
                                            "account_nm": "y", "thstrm_amount": "1"}])

    src = make_source(handler)
    src.corp_codes = {"005930": "00126380"}

    stmts = src.fetch_annual("005930.KS", [2020, 2024])

    assert [s.fiscal_year for s in stmts] == [2024]


def test_fetch_annual_raises_permanent_on_unknown_ticker():
    """corp_code를 못 찾는 것은 이 종목만의 문제다 (다른 종목은 corp_code가
    있을 수 있으므로) — FatalSourceError가 아니라 PermanentSourceError.

    corp_codes를 빈 딕셔너리로 두면 ensure_corp_codes()가 "아직 안
    불러왔다"고 보고 실제 fetch_corp_codes() 호출을 시도해버리므로,
    999999가 빠진 비어있지 않은 매핑을 준다 — corp_code 조회 자체가
    아니라 "이 종목이 매핑에 없을 때"만 검증한다."""
    src = make_source(lambda r: json_response("000", list=[]))
    src.corp_codes = {"005930": "00126380"}

    with pytest.raises(PermanentSourceError, match="corp_code"):
        src.fetch_annual("999999.KS", [2024])


def test_ticker_to_stock_code_strips_suffix():
    src = make_source(lambda r: json_response("000"))

    assert src._stock_code("005930.KS") == "005930"
    assert src._stock_code("005930") == "005930"


# --- status 코드 7종 (직접, _fetch_one_year) --------------------------


def test_status_000_returns_payload_untouched():
    payload_list = [{"sj_div": "IS", "account_id": "ifrs-full_Revenue",
                      "account_nm": "매출액", "thstrm_amount": "1000"}]
    src = make_source(lambda r: json_response("000", list=payload_list))

    data = src._fetch_one_year("00126380", 2024)

    assert data["status"] == "000"
    assert data["list"] == payload_list


def test_status_013_returns_none():
    src = make_source(lambda r: json_response("013", "조회된 데이터가 없습니다"))

    assert src._fetch_one_year("00126380", 2024) is None


def test_status_010_unregistered_key_raises_fatal_and_names_the_fix():
    """010은 API 키 자체가 등록되지 않았다는 뜻이라 나머지 종목도 전부
    똑같이 실패한다. 메시지만 보고도(소스 코드를 읽지 않고도) 운영자가
    무엇을 고쳐야 하는지 알 수 있어야 한다 — DART_API_KEY와 config/.env를
    이름으로 콕 집어야 한다."""
    src = make_source(lambda r: json_response("010", "등록되지 않은 키입니다"))

    with pytest.raises(FatalSourceError) as exc_info:
        src._fetch_one_year("00126380", 2024)

    message = str(exc_info.value)
    assert "DART_API_KEY" in message
    assert "config/.env" in message


def test_status_100_field_error_raises_fatal():
    """100(필드 값 오류)은 요청 파라미터 자체가 잘못됐다는 뜻이라
    다음 종목도 동일한 파라미터 구성으로 똑같이 실패한다."""
    src = make_source(lambda r: json_response("100", "필드의 부적절한 값입니다"))

    with pytest.raises(FatalSourceError):
        src._fetch_one_year("00126380", 2024)


def test_status_020_rate_limit_raises_fatal_not_permanent():
    """020(요청 제한 초과)은 서버 쪽 일일 한도라서 다음 종목으로 넘어가도
    풀리지 않는다. 예전 코드는 PermanentSourceError(종목 단위)로 잘못
    처리했다 — 여기서는 FatalSourceError여야 한다."""
    src = make_source(lambda r: json_response("020", "요청 제한을 초과하였습니다"))

    with pytest.raises(FatalSourceError) as exc_info:
        src._fetch_one_year("00126380", 2024)

    assert not isinstance(exc_info.value, PermanentSourceError)


def test_status_900_undefined_error_raises_plain_source_error():
    """900(정의되지 않은 오류)은 원인을 알 수 없다 — 배치를 통째로
    멈출 근거가 없으므로 이 종목만 실패시킨다."""
    src = make_source(lambda r: json_response("900", "정의되지 않은 오류가 발생하였습니다"))

    with pytest.raises(SourceError) as exc_info:
        src._fetch_one_year("00126380", 2024)

    assert not isinstance(exc_info.value, FatalSourceError)


def test_unknown_status_raises_plain_source_error():
    src = make_source(lambda r: json_response("777", "?"))

    with pytest.raises(SourceError) as exc_info:
        src._fetch_one_year("00126380", 2024)

    assert not isinstance(exc_info.value, FatalSourceError)


# --- 800: 서비스 점검 중 → 재시도 ------------------------------------


def test_status_800_is_retried_and_eventually_succeeds(monkeypatch):
    """서비스 점검은 일시적이다. 두 번 실패하고 세 번째에 성공하면
    _fetch_one_year는 예외 없이 최종 payload를 반환해야 한다."""
    monkeypatch.setattr("stockscout.sources.ratelimit.time.sleep", lambda s: None)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return json_response("800", "서비스 점검중입니다")
        return json_response("000", list=[])

    src = make_source(handler)

    data = src._fetch_one_year("00126380", 2024)

    assert data["status"] == "000"
    assert calls["n"] == 3


def test_status_800_gives_up_after_configured_attempts(monkeypatch):
    """_fetch_one_year는 @retry_on_transient(attempts=3)로 감싸여 있다.
    계속 800만 오면 정확히 3번 시도하고 포기해야 한다."""
    monkeypatch.setattr("stockscout.sources.ratelimit.time.sleep", lambda s: None)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return json_response("800", "서비스 점검중입니다")

    src = make_source(handler)

    with pytest.raises(ConnectionError):
        src._fetch_one_year("00126380", 2024)

    assert calls["n"] == 3
