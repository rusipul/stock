"""OpenDART 클라이언트. https://opendart.fss.or.kr

응답을 해석하지 않고 원형 그대로 반환한다. 계정과목 해석은 normalize 계층의 몫이다.
"""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile

import httpx

from stockscout.models import RawStatement
from stockscout.sources.base import PermanentSourceError, SourceError
from stockscout.sources.ratelimit import DailyQuota, TokenBucket, retry_on_transient

BASE_URL = "https://opendart.fss.or.kr/api"

# DART 응답 status 코드
STATUS_OK = "000"
STATUS_NO_DATA = "013"
STATUS_RATE_LIMIT = "020"

REPORT_CODE_ANNUAL = "11011"  # 사업보고서


def parse_corp_codes(xml_bytes: bytes) -> dict[str, str]:
    """CORPCODE.xml → {6자리 종목코드: 8자리 corp_code}.

    stock_code가 비어 있는 비상장사는 제외한다.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise ValueError(f"CORPCODE XML을 파싱할 수 없습니다: {exc}") from exc

    mapping: dict[str, str] = {}
    for item in root.iter("list"):
        stock_code = (item.findtext("stock_code") or "").strip()
        corp_code = (item.findtext("corp_code") or "").strip()
        if stock_code and corp_code:
            mapping[stock_code] = corp_code

    if not mapping:
        raise ValueError("CORPCODE XML에 상장사가 하나도 없습니다")
    return mapping


class DartSource:
    """FundamentalSource 프로토콜 구현."""

    name = "dart"

    def __init__(
        self,
        api_key: str,
        daily_limit: int = 20_000,
        rate_per_sec: float = 5.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key
        self.quota = DailyQuota(daily_limit)
        self.bucket = TokenBucket(rate_per_sec)
        self._client = client or httpx.Client(timeout=30)
        self.corp_codes: dict[str, str] = {}

    # --- 종목코드 매핑 -------------------------------------------------

    @retry_on_transient(attempts=3)
    def fetch_corp_codes(self) -> dict[str, str]:
        """전체 상장사의 종목코드 → corp_code 매핑을 받는다 (호출 1건)."""
        self.bucket.wait()
        self.quota.consume()
        resp = self._client.get(
            f"{BASE_URL}/corpCode.xml", params={"crtfc_key": self.api_key}
        )
        resp.raise_for_status()
        try:
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                xml_bytes = zf.read(zf.namelist()[0])
        except zipfile.BadZipFile as exc:
            # 키가 잘못되면 ZIP이 아니라 XML 오류 메시지가 온다
            raise PermanentSourceError(
                f"corpCode 응답이 ZIP이 아닙니다. API 키를 확인하세요. "
                f"응답 앞부분: {resp.content[:200]!r}"
            ) from exc
        return parse_corp_codes(xml_bytes)

    def fetch_annual(self, ticker: str, years: list[int]) -> list[RawStatement]:
        raise NotImplementedError("Task 8에서 구현한다")
