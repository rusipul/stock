"""정적 사이트 내보내기. Vercel 같은 정적 호스팅에 올릴 파일을 만든다.

수집·분석은 몇 분 걸리는 배치라 서버리스에서 돌릴 수 없다. 그래서 PC에서
analyze까지 마친 뒤, 화면이 읽는 API 응답을 JSON 파일로 굳혀 올린다.

API를 다시 구현하지 않고 create_app()을 메모리 안에서 그대로 호출한다.
그래야 PC의 serve 화면과 공개 화면의 숫자가 같은 코드에서 나온다.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import httpx
from pydantic import BaseModel

from stockscout.web.api import STATIC_DIR, create_app

MARKETS = ("KR", "US")
DATA_DIR = "data"

# app.js가 이 표시를 보고 API 대신 JSON 파일을 읽는다 (app.js의 STATIC).
STATIC_FLAG = "<script>window.STOCKSCOUT_STATIC = true;</script>\n"
APP_SCRIPT = '<script src="/static/app.js"></script>'


class ExportSummary(BaseModel):
    markets: list[str] = []
    securities: int = 0

    def render(self) -> str:
        if not self.markets:
            return "내보낼 스냅샷이 없습니다. stockscout analyze 를 먼저 실행하세요."
        return (f"{', '.join(self.markets)} · {self.securities}종목 내보냄")


def export_site(db_path: Path | str, out_dir: Path | str) -> ExportSummary:
    """db_path의 최신 스냅샷을 out_dir에 정적 사이트로 쓴다.

    out_dir/data는 매번 새로 쓴다 - 유니버스에서 뺀 종목의 옛 파일이 남으면
    공개 사이트에 계속 보인다. out_dir의 다른 파일은 건드리지 않는다.
    """
    return asyncio.run(_export(Path(db_path), Path(out_dir)))


async def _export(db_path: Path, out: Path) -> ExportSummary:
    data = out / DATA_DIR
    if data.exists():
        shutil.rmtree(data)
    summary = ExportSummary()

    transport = httpx.ASGITransport(app=create_app(db_path=db_path))
    async with httpx.AsyncClient(transport=transport, base_url="http://export") as api:
        for market in MARKETS:
            snap = await api.get("/api/snapshot", params={"market": market})
            if snap.status_code == 404:
                continue   # 이 시장은 아직 분석하지 않았다
            snap.raise_for_status()
            rows = await _get(api, "/api/securities", params={"market": market})

            _write(data / market / "snapshot.json", snap.json())
            _write(data / market / "securities.json", _public(rows))
            for row in rows:
                ticker = row["ticker"]
                detail = await _get(api, f"/api/securities/{ticker}")
                prices = await _get(api, f"/api/securities/{ticker}/prices")
                _write(data / "securities" / f"{ticker}.json", _public(detail))
                _write(data / "securities" / f"{ticker}.prices.json", _compact(prices))
            summary.markets.append(market)
            summary.securities += len(rows)

    _copy_page(out)
    return summary


async def _get(api: httpx.AsyncClient, url: str, **kwargs):
    resp = await api.get(url, **kwargs)
    resp.raise_for_status()
    return resp.json()


def _public(body):
    """관심종목은 내 PC의 개인 기록이다. 공개 파일에는 싣지 않는다.

    정적 화면은 관심종목을 방문자 브라우저(localStorage)에 따로 둔다.
    """
    if isinstance(body, list):
        return [_public(item) for item in body]
    return {**body, "in_watchlist": False}


PRICE_DECIMALS = 4


def _compact(body):
    """주가 파일을 줄인다. 화면은 센트까지만 보이는데 yfinance 수정주가는
    148.8123779296875처럼 오고, 원화는 70700.0처럼 .0이 붙는다. 소수 넷째
    자리로 자르고 정수는 정수로 쓴다 - 실측: 공개 사이트 18MB의 대부분이 이 숫자였다.
    분석 데이터(DB)는 건드리지 않는다.
    """
    if isinstance(body, dict):
        return {k: _compact(v) for k, v in body.items()}
    if isinstance(body, list):
        return [_compact(v) for v in body]
    if isinstance(body, float):
        r = round(body, PRICE_DECIMALS)
        return int(r) if r == int(r) else r
    return body


def _write(path: Path, body) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # 공백 없이 쓴다. 주가 5년치가 종목당 1,200점이 넘어 크기가 꽤 줄어든다.
    path.write_text(json.dumps(body, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8")


def _copy_page(out: Path) -> None:
    """index.html은 정적 표시를 얹어 쓰고, app.js·style.css는 그대로 복사한다.

    경로(/static/...)를 serve와 같게 둬서 index.html을 고칠 필요가 없게 한다.
    """
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    if APP_SCRIPT not in html:
        raise RuntimeError(f"index.html에서 {APP_SCRIPT} 를 찾지 못했습니다")
    (out / "static").mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(
        html.replace(APP_SCRIPT, STATIC_FLAG + "  " + APP_SCRIPT), encoding="utf-8")
    for name in ("app.js", "style.css"):
        shutil.copyfile(STATIC_DIR / name, out / "static" / name)
