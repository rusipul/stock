import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stockscout.store.db import connect, init_db
from stockscout.web.api import STATIC_DIR, create_app


@pytest.fixture
def client(tmp_path):
    path = tmp_path / "t.db"
    with connect(path) as conn:
        init_db(conn)
    return TestClient(create_app(db_path=path))


def test_static_files_exist():
    for name in ("index.html", "style.css", "app.js"):
        assert (STATIC_DIR / name).exists(), f"{name} 이 없습니다"


def test_index_references_its_assets():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert "/static/style.css" in html
    assert "/static/app.js" in html


def test_assets_are_served(client):
    for path in ("/static/style.css", "/static/app.js"):
        assert client.get(path).status_code == 200, path


def test_chartjs_is_pinned_to_an_exact_version():
    """CDN 스크립트에 버전을 고정한다.

    latest를 쓰면 어느 날 갑자기 차트가 깨지고, 그때 원인을 찾기
    어렵다. 빌드 스텝이 없으므로 lockfile도 없다.
    """
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert "chart.js" in html.lower()
    assert "latest" not in html.lower()


def test_index_has_the_two_panels():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert 'id="list-panel"' in html
    assert 'id="detail-panel"' in html


def test_javascript_parses():
    """실제로 파싱해서 문법 오류를 잡는다.

    괄호 균형 세기 같은 대용품은 문자열 안의 괄호에 속고, 정작 빠진
    쉼표 같은 진짜 문법 오류는 못 잡는다. 이 프로젝트에는 빌드 스텝이
    없어서 문법 오류가 브라우저에서만 드러나므로, 여기서 막는다.

    node가 없는 환경에서는 skip한다 — 이 저장소는 node를 요구하지 않는다.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node 가 없어 JS 문법 검사를 건너뛴다")

    result = subprocess.run(
        [node, "--check", str(STATIC_DIR / "app.js")],
        capture_output=True, text=True,
    )

    assert result.returncode == 0, result.stderr


def test_app_js_defines_every_function_it_calls_from_an_event_handler():
    """이벤트 핸들러(onclick/oninput/onchange)가 부르는 함수는 모두
    정의돼 있어야 한다.

    렌더 함수 하나만 아직 정의되지 않아도 클릭 시점에만 터지는
    ReferenceError가 되어, 실제로 눌러보기 전에는 못 잡는다
    (이 작업에서 selectTicker가 처음에 겪었던 문제다).
    """
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    # 핸들러는 두 형태로 쓰인다: `() => { ... };` (블록) 와
    # `() => selectTicker(row.ticker);` (중괄호 없는 단일 표현식).
    # 후자를 빼먹으면 이 작업에서 실제로 있었던 문제(선택만 늦게
    # 정의된 selectTicker)를 놓친다.
    handler_pattern = re.compile(
        r"\.on(?:click|input|change)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>\s*"
        r"(?:\{(?P<block>.*?)\}\s*;|(?P<expr>[^\n{][^\n]*);)",
        re.DOTALL,
    )
    handler_bodies = [
        m.group("block") if m.group("block") is not None else m.group("expr")
        for m in handler_pattern.finditer(js)
    ]
    assert handler_bodies, "app.js에서 이벤트 핸들러 할당을 찾지 못했습니다"

    called_names = set()
    for body in handler_bodies:
        # 점 앞의 이름은 제외한다 — e.stopPropagation() 같은 메서드 호출이지
        # app.js가 정의해야 할 최상위 함수가 아니다.
        called_names |= set(re.findall(r"(?<![\w.])([a-zA-Z_]\w*)\s*\(", body))

    defined_names = set(re.findall(r"function\s+(\w+)\s*\(", js))

    # 제어문 키워드와 브라우저/JS 내장 전역은 app.js가 정의할 대상이 아니다.
    not_user_defined = {
        "if", "for", "while", "switch", "catch", "function", "return",
        "Number", "String", "Boolean", "Array", "Object", "Math", "JSON",
        "Date", "Set", "Map", "Promise", "parseInt", "parseFloat",
        "encodeURIComponent", "decodeURIComponent", "fetch", "console",
    }
    missing = called_names - defined_names - not_user_defined
    assert not missing, f"이벤트 핸들러가 부르지만 정의되지 않은 함수: {missing}"

    for name in ("selectTicker", "toggleWatch", "renderList", "renderHitCount"):
        assert name in defined_names, f"{name} 함수 정의를 찾을 수 없습니다"


def test_index_has_an_element_for_every_id_app_js_looks_up():
    """app.js가 참조하는 모든 id가 index.html에 있거나, app.js 자신이
    런타임에 그 id를 가진 요소를 만들어 붙여야 한다.

    빌드 스텝이 없는 프론트엔드에서 가장 흔한 실수는 id를 하나만
    바꾸고 나머지를 안 바꾸는 것이다. 그러면 런타임에만 터진다.

    우측 상세 패널의 차트 캔버스(price-chart)처럼 정적 HTML이 아니라
    app.js가 innerHTML로 직접 만들어 붙이는 요소도 있다 — 그런 id는
    index.html에는 없는 게 정상이므로, app.js 안에 id="..." 마크업이
    있는지로 대신 확인한다. 단순히 통과시키기 위해 검사를 없애면
    실제로 있는 id를 만들지 않는 버그(오타 등)를 못 잡으므로 그렇게
    하지 않는다.
    """
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    ids = set(re.findall(r'\$\(\s*["\']([\w-]+)["\']\s*\)', js))
    assert ids, "app.js에서 $(...) 로 조회하는 id를 찾지 못했습니다"

    for element_id in ids:
        in_html = f'id="{element_id}"' in html
        in_js_markup = f'id="{element_id}"' in js
        assert in_html or in_js_markup, (
            f'app.js가 참조하는 id="{element_id}" 가 index.html에도, '
            f'app.js가 직접 만들어 붙이는 마크업에도 없습니다'
        )


def test_static_files_are_valid_utf8_with_expected_korean():
    html_bytes = (STATIC_DIR / "index.html").read_bytes()
    html = html_bytes.decode("utf-8")  # UTF-8이 아니면 여기서 실패한다
    assert "고점 대비 하락" in html

    css_bytes = (STATIC_DIR / "style.css").read_bytes()
    css_bytes.decode("utf-8")

    js_bytes = (STATIC_DIR / "app.js").read_bytes()
    js = js_bytes.decode("utf-8")
    assert "관심종목" in js or "종목" in js


def test_app_js_renders_the_detail_panel():
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    for needed in ("renderDetail", "drawChart", "rationale", "financials"):
        assert needed in js, f"{needed} 이 app.js 에 없습니다"


def test_app_js_explains_missing_financials_with_commands():
    """빈 패널은 '준비 중'이 아니라 사용자가 할 수 있는 일을 말한다."""
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "opendart.fss.or.kr" in js
    assert "stockscout collect" in js
    assert "준비 중" not in js


def test_app_js_dart_guide_lists_every_pipeline_command():
    """DART 안내문이 collect 하나만 말하고 끝나지 않는지 확인한다.

    normalize·analyze를 빼먹으면 사용자가 collect만 실행하고 화면이
    그대로인 이유를 알 수 없다.
    """
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    guide = js.split("DART_GUIDE")[1].split("selectTicker")[0]

    for cmd in ("stockscout collect", "stockscout normalize", "stockscout analyze"):
        assert cmd in guide, f"{cmd} 가 DART 안내문에 없습니다"


def test_app_js_uses_server_computed_reference_lines():
    """차트 기준선을 프론트에서 다시 계산하지 않는다.

    같은 평균을 두 곳에서 계산하면 창 정의나 반올림이 갈려
    차트와 판정 근거가 어긋난다.

    "drawChart"만으로 자르면 renderDetail 안의 호출부(drawChart(prices))가
    먼저 걸려서, 검사 구간이 정작 drawChart의 함수 본문이 아니라
    그 사이에 낀 다른 함수들(kpi, sectionTitle 등)의 앞부분이 되어버린다.
    함수 정의 자체("function drawChart")를 기준으로 잘라야 본문을 본다.
    """
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "avg_price_5y" in js
    body = js.split("function drawChart")[1].split("function")[0]
    assert "reduce" not in body


# --- 계획 C: 점수 분해 ------------------------------------------------

def js_text() -> str:
    return (STATIC_DIR / "app.js").read_text(encoding="utf-8")


def _function_body(js: str, name: str) -> str:
    """"function <name>"부터 중괄호 깊이가 0으로 돌아오는 지점까지.

    split으로 대충 자르면 호출부를 집어 엉뚱한 곳을 검사한다 - 계획 D에서
    drawChart 검사가 실제로 그랬다 (test_app_js_uses_server_computed_reference_lines).
    """
    start = js.index(f"function {name}(")
    depth = 0
    for i in range(js.index("{", start), len(js)):
        if js[i] == "{":
            depth += 1
        elif js[i] == "}":
            depth -= 1
            if depth == 0:
                return js[start:i + 1]
    raise AssertionError(f"{name}의 본문을 찾지 못했습니다")


def test_app_js_renders_a_score_breakdown():
    js = js_text()

    assert "function scoreBars(" in js
    assert "점수 분해" in _function_body(js, "renderDetail")


def test_score_bars_scale_by_max_points_not_a_hardcoded_25():
    """배점은 설정에서 온다. 사용자가 YAML을 고치면 막대도 따라야 한다."""
    body = _function_body(js_text(), "scoreBars")

    assert "max_points" in body
    assert "25" not in body


def test_score_bars_label_an_unmeasurable_axis():
    body = _function_body(js_text(), "scoreBars")

    assert "AXIS_UNMEASURABLE" in body
    assert "측정 불가" in body


def test_score_bars_expand_per_metric_with_details():
    """설계서 §11.2: 막대 클릭 시 지표별 상세 펼침. 브라우저의 details가 준다."""
    body = _function_body(js_text(), "scoreBars")

    assert "<details" in body and "<summary" in body


def test_every_scoring_status_has_a_label():
    """서버가 내는 지표 상태마다 화면 문구가 있어야 한다.

    없으면 그 지표는 '0.0 / 10'으로 표시돼 사업이 나쁜 것처럼 읽힌다.
    END_NONPOSITIVE는 실데이터(삼성SDI)에서 나온 상태라 계획에 없었다.
    """
    from stockscout import models

    js = js_text()
    labels = js.split("const METRIC_STATUS_LABELS")[1].split("};")[0]
    statuses = [v for k, v in vars(models).items()
                if k.startswith("STATUS_") and v != models.STATUS_OK]

    for status in statuses:
        assert f"{status}:" in labels, f"{status}의 화면 문구가 없다"


def test_detail_skips_the_breakdown_when_there_is_no_score():
    body = _function_body(js_text(), "renderDetail")

    assert "if (d.score_detail)" in body


# --- 계획 C: 프론트 등급 판정 (설계서 §11.1) ---------------------------
#
# 등급은 프론트가 슬라이더로 즉석 판정한다(§6.1·§11.3). 서버의
# grade_signal과 같은 규칙이어야 한다 - 품질이 먼저고, 점수가 기준에
# 못 미치면 아무리 떨어져도 등급이 없다.

def _run_grade_cases(cases):
    """app.js의 gradeOf를 node에서 실제로 돌린다. 문자열 검사로는 규칙을 못 본다."""
    import json as _json

    node = shutil.which("node")
    if node is None:
        pytest.skip("node 가 없어 등급 판정 실행 검사를 건너뛴다")

    source = (_function_body(js_text(), "drawdownOf") + "\n"
              + _function_body(js_text(), "gradeOf"))
    script = (
        "const state = { minScore: 70, dropThreshold: -0.25, months: 3,"
        " snapshot: { has_scores: true } };\n"
        f"{source}\n"
        f"const cases = {_json.dumps(cases)};\n"
        "console.log(JSON.stringify(cases.map(gradeOf)));"
    )
    result = subprocess.run([node, "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return _json.loads(result.stdout)


def _row(score=80.0, drop=-0.30, stabilized=True, passed=True, months=3):
    return {"passed_filters": passed, "score_total": score,
            "drawdown": {str(months): {"drop_from_high": drop,
                                       "stabilized": stabilized}}}


def test_grade_of_matches_the_server_rule():
    """analysis/grading.py의 grade_signal과 같은 규칙이다."""
    grades = _run_grade_cases([
        _row(),                          # 크게 빠졌고 멈췄다 -> 별 둘
        _row(stabilized=False),          # 크게 빠졌지만 아직 하락 중 -> 별 하나
        _row(score=42.0),                # 많이 떨어졌지만 우량주가 아니다
        _row(score=70.0),                # 경계: 70점은 통과
        _row(passed=False, score=None),  # 제외 종목
        _row(drop=-0.10),                # 우량주지만 별로 안 빠졌다
        _row(drop=-0.25),                # 경계: -25%는 '이하'
        _row(months=6),                  # 3개월 값이 없다(이 기능 이전 스냅샷 등)
    ])

    assert grades == ["STRONG", "WATCH", None, "STRONG", None, None, "STRONG", None]


def test_score_slider_regrades_without_a_server_round_trip():
    body = _function_body(js_text(), "wireControls")
    handler = body.split('$("score-threshold").oninput')[1].split("};")[0]

    assert "state.minScore" in handler
    assert "savePrefs()" in handler
    assert "fetch" not in handler and "reload" not in handler


def test_min_score_survives_a_reload():
    """§11.1: 슬라이더 값은 localStorage에 저장해 다음 방문에 복원한다."""
    js = js_text()

    assert "minScore" in _function_body(js, "loadPrefs")
    assert "minScore" in _function_body(js, "savePrefs")


def test_grade_chips_appear_once_scores_exist():
    body = _function_body(js_text(), "renderChips")

    assert '"★★"' in body and '"★"' in body
    assert "has_scores" in body


def test_hit_count_reports_both_grades():
    """§11.1: '현재 기준 -> ★★ N종목 · ★ M종목'."""
    body = _function_body(js_text(), "renderHitCount")

    assert "STRONG" in body and "WATCH" in body


# --- 계획 E: 시장 탭 --------------------------------------------------


def test_index_has_market_tabs():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert 'id="market-tabs"' in html


def test_every_api_list_call_carries_the_market():
    js = js_text()

    assert "market: state.market" in _function_body(js, "reload")
    assert "/api/snapshot?market=" in _function_body(js, "loadMarket")


def test_money_shows_dollars_with_cents_for_us():
    """원화 formatter로 달러를 찍으면 $230.55가 231이 된다."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node 가 없어 금액 형식 실행 검사를 건너뛴다")

    script = (
        'const state = { market: "US" };\n'
        f"{_function_body(js_text(), 'money')}\n"
        "console.log(money(230.55)); state.market = 'KR'; console.log(money(60000));"
    )
    out = subprocess.run([node, "-e", script], capture_output=True, text=True)

    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["$230.55", "60,000"]


def test_no_amount_is_formatted_as_won_directly():
    """금액은 전부 money()를 거친다. 원화 전용 formatter가 남아 있으면 미국 화면이 원화로 찍힌다."""
    assert "won(" not in js_text()


def test_a_market_without_a_snapshot_does_not_wipe_the_page():
    """미국을 아직 분석하지 않았으면 탭은 남기고 무엇을 실행할지 말한다."""
    body = _function_body(js_text(), "loadMarket")

    assert "document.body.innerHTML" not in body
    assert "catch" in body


def test_the_chosen_market_survives_a_reload():
    js = js_text()

    assert "stockscout.market" in _function_body(js, "loadPrefs")
    assert "stockscout.market" in _function_body(js, "savePrefs")


def test_financial_amounts_are_in_millions_of_dollars_for_us():
    """10-K의 관례대로 백만 달러로 쓴다. $82,584,000,000.00은 읽을 수 없다."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node 가 없어 금액 형식 실행 검사를 건너뛴다")

    js = js_text()
    script = (
        'const state = { market: "US" };\n'
        f"{_function_body(js, 'money')}\n{_function_body(js, 'amount')}\n"
        "console.log(amount(82584000000)); console.log(amount(null));"
        "state.market = 'KR'; console.log(amount(279604799000000));"
    )
    out = subprocess.run([node, "-e", script], capture_output=True,
                         text=True, encoding="utf-8")

    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["$82,584M", "—", "279,604,799,000,000"]


def test_the_financials_table_formats_amounts_with_amount():
    body = _function_body(js_text(), "financialsTable")

    assert "money(" not in body
    assert body.count("amount(") == 3


# --- 정적 사이트 모드 (Vercel) -----------------------------------------


def _run_node(script):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node 가 없어 실행 검사를 건너뛴다")
    out = subprocess.run([node, "-e", script], capture_output=True,
                         text=True, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return out.stdout


def test_static_filter_and_sort_follow_the_server_rules():
    """정적 화면은 서버 대신 브라우저가 검색·정렬한다. 규칙이 api.list_securities와 같아야 한다."""
    rows = (
        '[{"ticker":"B","name":"베타","drawdown":{"3":{"drop_from_high":-0.1}}},'
        '{"ticker":"A","name":"알파","drawdown":{}},'
        '{"ticker":"C","name":"Charlie","drawdown":{"3":{"drop_from_high":-0.5}}}]'
    )
    script = (
        "const state = { months: 3 };\n"
        f"{_function_body(js_text(), 'drawdownOf')}\n"
        f"{_function_body(js_text(), 'filterAndSort')}\n"
        f"const rows = {rows};\n"
        "const t = (q, s) => filterAndSort(rows, q, s).map(r => r.ticker).join('');\n"
        "console.log(t('', 'drop'), t('', 'name'), t('', 'ticker'), t('char', 'drop'), t('b', 'drop'));"
    )

    # 하락률 오름차순, 하락률 없는 종목은 뒤 / 이름순 / 코드순 / 이름 검색 / 코드 검색
    assert _run_node(script).split() == ["CBA", "CBA", "ABC", "C", "B"]


def test_static_mode_reads_exported_files():
    js = js_text()

    assert "STOCKSCOUT_STATIC" in js
    assert "data/${state.market}/snapshot.json" in _function_body(js, "loadMarket")
    assert "data/${state.market}/securities.json" in _function_body(js, "reload")
    assert ".prices.json" in _function_body(js, "selectTicker")


def test_static_watchlist_lives_in_the_browser():
    """공개 사이트는 읽기 전용이다. 관심종목은 방문자 브라우저에 둔다."""
    body = _function_body(js_text(), "toggleWatch")

    assert "STATIC" in body
    assert "stockscout.watchlist" in js_text()


# --- 하락 기간 슬라이더 (1·3·6·12개월) ------------------------------------


def _grade_at(months, rows):
    import json as _json

    js = js_text()
    script = (
        f"const state = {{ minScore: 70, dropThreshold: -0.25, months: {months},"
        " snapshot: { has_scores: true } };\n"
        f"{_function_body(js, 'drawdownOf')}\n{_function_body(js, 'gradeOf')}\n"
        f"console.log(JSON.stringify({_json.dumps(rows)}.map(gradeOf)));"
    )
    return _json.loads(_run_node(script))


def test_the_period_slider_changes_which_drop_is_graded():
    """1개월로는 별로 안 빠졌지만 6개월로 보면 크게 빠진 종목."""
    row = {"passed_filters": True, "score_total": 80.0, "drawdown": {
        "1": {"drop_from_high": -0.05, "stabilized": True},
        "6": {"drop_from_high": -0.40, "stabilized": True},
    }}

    assert _grade_at(1, [row]) == [None]
    assert _grade_at(6, [row]) == ["STRONG"]


def test_index_has_the_period_slider():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert 'id="period"' in html
    assert 'id="window-years"' not in html


def test_the_period_and_threshold_survive_a_reload():
    js = js_text()

    for key in ("stockscout.ddMonths", "stockscout.ddThreshold"):
        assert key in _function_body(js, "loadPrefs")
        assert key in _function_body(js, "savePrefs")


def test_the_period_slider_regrades_without_a_server_round_trip():
    body = _function_body(js_text(), "wireControls")
    handler = body.split('$("period").oninput')[1].split("};")[0]

    assert "getJSON" not in handler and "fetch" not in handler
    assert "renderList" in handler


def test_slider_defaults_come_from_the_snapshot():
    """서버가 판정한 기준(3개월·-25%)이 화면의 기본값이어야 등급이 일치한다."""
    body = _function_body(js_text(), "loadMarket")

    assert "drawdown_months" in body and "drawdown_threshold" in body


def test_the_detail_shows_the_market_drop_and_whether_it_stopped():
    body = _function_body(js_text(), "renderDetail")

    assert "market_drop" in body
    assert "stabilized" in body


def test_a_weak_axis_blocks_the_grade_on_screen_too():
    """서버의 grading.weak_axes와 같다. 미달 축이 있으면 등급이 없다."""
    weak = dict(_row(), weak_axes=["stability"])

    assert _run_grade_cases([_row(), weak]) == ["STRONG", None]
