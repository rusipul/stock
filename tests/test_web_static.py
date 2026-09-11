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
    """app.js가 참조하는 모든 id가 index.html에 있어야 한다.

    빌드 스텝이 없는 프론트엔드에서 가장 흔한 실수는 id를 하나만
    바꾸고 나머지를 안 바꾸는 것이다. 그러면 런타임에만 터진다.
    """
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    ids = set(re.findall(r'\$\(\s*["\']([\w-]+)["\']\s*\)', js))
    assert ids, "app.js에서 $(...) 로 조회하는 id를 찾지 못했습니다"

    for element_id in ids:
        assert f'id="{element_id}"' in html, (
            f'app.js가 참조하는 id="{element_id}" 가 index.html에 없습니다'
        )


def test_static_files_are_valid_utf8_with_expected_korean():
    html_bytes = (STATIC_DIR / "index.html").read_bytes()
    html = html_bytes.decode("utf-8")  # UTF-8이 아니면 여기서 실패한다
    assert "하락 임계치" in html

    css_bytes = (STATIC_DIR / "style.css").read_bytes()
    css_bytes.decode("utf-8")

    js_bytes = (STATIC_DIR / "app.js").read_bytes()
    js = js_bytes.decode("utf-8")
    assert "관심종목" in js or "종목" in js
