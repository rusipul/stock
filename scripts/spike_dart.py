"""DART 실제 응답을 확인하고 픽스처를 만든다. 일회성 탐색 스크립트."""

import io
import sys
import zipfile

import httpx

from stockscout.config import load_secrets

BASE = "https://opendart.fss.or.kr/api"


def dump_corpcode() -> None:
    key = load_secrets().dart_api_key
    resp = httpx.get(f"{BASE}/corpCode.xml", params={"crtfc_key": key}, timeout=60)
    print("status:", resp.status_code, "content-type:", resp.headers.get("content-type"))
    print("first 200 bytes:", resp.content[:200])
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        print("zip 안의 파일:", zf.namelist())
        xml = zf.read(zf.namelist()[0]).decode("utf-8")
    print("xml 앞부분:\n", xml[:1200])


def dump_statement(corp_code: str = "00126380", year: int = 2024) -> None:
    """00126380 = 삼성전자"""
    key = load_secrets().dart_api_key
    resp = httpx.get(
        f"{BASE}/fnlttSinglAcntAll.json",
        params={
            "crtfc_key": key,
            "corp_code": corp_code,
            "bsns_year": str(year),
            "reprt_code": "11011",   # 사업보고서(연간)
            "fs_div": "CFS",         # 연결
        },
        timeout=60,
    )
    data = resp.json()
    print("status:", data.get("status"), data.get("message"))
    items = data.get("list", [])
    print("항목 수:", len(items))
    seen = set()
    for it in items:
        key_ = (it.get("sj_div"), it.get("account_id"), it.get("account_nm"))
        if key_ not in seen:
            seen.add(key_)
            print(f"  {it.get('sj_div'):4} | {it.get('account_id'):60} | "
                  f"{it.get('account_nm'):30} | {it.get('thstrm_amount')}")


if __name__ == "__main__":
    if sys.argv[1:] and sys.argv[1] == "statement":
        dump_statement()
    else:
        dump_corpcode()
