"""OpenDART 클라이언트. https://opendart.fss.or.kr

응답을 해석하지 않고 원형 그대로 반환한다. 계정과목 해석은 normalize 계층의 몫이다.
"""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile

import httpx

from stockscout.models import RawStatement
from stockscout.sources.base import FatalSourceError, PermanentSourceError, SourceError
from stockscout.sources.ratelimit import DailyQuota, TokenBucket, retry_on_transient

BASE_URL = "https://opendart.fss.or.kr/api"

# DART 응답 status 코드. 공식 가이드 DS003/2019020
# (fnlttSinglAcntAll.json, https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS003&apiId=2019020)
# 이 문서화하는 7종 전부를 이름 붙여 둔다 — 이름 없는 status는 코드 리뷰에서
# "020이 뭐였지"를 매번 스펙에서 찾아야 한다.
STATUS_OK = "000"                    # 정상
STATUS_UNREGISTERED_KEY = "010"      # 미등록 키
STATUS_NO_DATA = "013"               # 조회된 데이터 없음
STATUS_RATE_LIMIT = "020"            # 요청 제한 초과 (일일 한도)
STATUS_INVALID_FIELD = "100"         # 필드 값 오류
STATUS_SERVICE_UNAVAILABLE = "800"   # 서비스 점검 중
STATUS_UNDEFINED_ERROR = "900"       # 정의되지 않은 오류

REPORT_CODE_ANNUAL = "11011"  # 사업보고서

# 주식의 총수 현황(stockTotqySttus.json)의 raw_financials.source 값.
# 재무제표('dart')와 같은 테이블에 들어가되 기본키가 (ticker, fiscal_year,
# source)라 서로 덮어쓰지 않는다.
SOURCE_SHARES = "dart_shares"

# 회계감사인의 명칭 및 감사의견(accnutAdtorNmNdAdtOpinion.json)의
# raw_financials.source 값. 주식수와 같은 이유로 테이블을 늘리지 않는다.
SOURCE_AUDIT = "dart_audit"


class DartServiceUnavailable(ConnectionError):
    """DART가 점검 중이라 응답을 내주지 못한다 (status=800).

    ConnectionError를 상속한 이유: sources/ratelimit.py의 TRANSIENT는
    이미 ConnectionError를 포함하므로, TRANSIENT 튜플 자체를 넓히지
    않고도(= PermissionError 같은 영구 오류까지 재시도 대상으로 끌어들이지
    않고도) 이 예외만 retry_on_transient()의 재시도 대상이 된다. "지금은
    서비스에 연결해 데이터를 받을 수 없다"는 의미도 ConnectionError와
    맞아떨어진다. 800은 정의상 일시적이므로(설계서·공식 가이드 모두
    "점검 중"이라 명시) 재시도할 가치가 있고, 실패하면 결국 TimeoutError
    등과 동일하게 위로 전파된다.
    """


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

    # --- 재무제표 --------------------------------------------------

    @staticmethod
    def _stock_code(ticker: str) -> str:
        """'005930.KS' → '005930'"""
        return ticker.split(".")[0]

    def ensure_corp_codes(self) -> None:
        if not self.corp_codes:
            self.corp_codes = self.fetch_corp_codes()

    def fetch_annual(self, ticker: str, years: list[int]) -> list[RawStatement]:
        self.ensure_corp_codes()
        stock_code = self._stock_code(ticker)
        corp_code = self.corp_codes.get(stock_code)
        if not corp_code:
            # 이 종목만의 문제다 (다른 종목은 corp_code가 있을 수 있다) —
            # 배치를 멈출 근거가 없으므로 PermanentSourceError.
            raise PermanentSourceError(f"{ticker}의 corp_code를 찾을 수 없습니다")

        statements: list[RawStatement] = []
        for year in years:
            payload = self._fetch_one_year(corp_code, year)
            if payload is None:
                continue  # 해당 연도 데이터 없음(013). 정상 상황이다
            statements.append(
                RawStatement(
                    ticker=ticker, fiscal_year=year, source=self.name, payload=payload
                )
            )
        return statements

    def fetch_shares(self, ticker: str, years: list[int]) -> list[RawStatement]:
        """주식의 총수 현황을 연도별로 받는다.

        RawStatement를 재사용하고 source만 달리한다. raw_financials의
        기본키가 (ticker, fiscal_year, source)라 테이블을 늘리지 않아도 된다.
        """
        self.ensure_corp_codes()
        stock_code = self._stock_code(ticker)
        corp_code = self.corp_codes.get(stock_code)
        if not corp_code:
            raise PermanentSourceError(f"{ticker}의 corp_code를 찾을 수 없습니다")

        statements: list[RawStatement] = []
        for year in years:
            payload = self._fetch_shares_one_year(corp_code, year)
            if payload is None:
                continue
            statements.append(RawStatement(
                ticker=ticker, fiscal_year=year,
                source=SOURCE_SHARES, payload=payload,
            ))
        return statements

    def fetch_audit(self, ticker: str, years: list[int]) -> list[RawStatement]:
        """회계감사인의 명칭 및 감사의견을 연도별로 받는다.

        fetch_shares와 같은 모양이다 — 파라미터가 재무제표 API와 같고
        source만 달리해 raw_financials에 담는다 (설계서 §8.1·§17.4).
        """
        self.ensure_corp_codes()
        stock_code = self._stock_code(ticker)
        corp_code = self.corp_codes.get(stock_code)
        if not corp_code:
            raise PermanentSourceError(f"{ticker}의 corp_code를 찾을 수 없습니다")

        statements: list[RawStatement] = []
        for year in years:
            payload = self._fetch_audit_one_year(corp_code, year)
            if payload is None:
                continue
            statements.append(RawStatement(
                ticker=ticker, fiscal_year=year,
                source=SOURCE_AUDIT, payload=payload,
            ))
        return statements

    @retry_on_transient(attempts=3)
    def _fetch_one_year(self, corp_code: str, year: int) -> dict | None:
        """한 회계연도의 연결 재무제표 원본. 데이터가 없으면 None."""
        return self._fetch_json(
            "fnlttSinglAcntAll.json", corp_code, year, {"fs_div": "CFS"}
        )

    @retry_on_transient(attempts=3)
    def _fetch_shares_one_year(self, corp_code: str, year: int) -> dict | None:
        """한 회계연도의 주식의 총수 현황. 데이터가 없으면 None."""
        return self._fetch_json("stockTotqySttus.json", corp_code, year, {})

    @retry_on_transient(attempts=3)
    def _fetch_audit_one_year(self, corp_code: str, year: int) -> dict | None:
        """한 회계연도의 감사인·감사의견. 데이터가 없으면 None."""
        return self._fetch_json(
            "accnutAdtorNmNdAdtOpinion.json", corp_code, year, {}
        )

    def _fetch_json(
        self, endpoint: str, corp_code: str, year: int, extra: dict[str, str]
    ) -> dict | None:
        """DART JSON 엔드포인트 하나를 호출하고 status 7종을 해석한다.

        DART의 모든 공시 API가 crtfc_key·corp_code·bsns_year·reprt_code를
        공유하고 status 코드 체계도 같다. 엔드포인트마다 이 판단을 복사하면
        한 곳만 고쳐지는 일이 생긴다.

        retry_on_transient은 여기에 붙이지 않는다 — 호출자
        (_fetch_one_year·_fetch_shares_one_year)에 이미 붙어 있어서,
        중첩하면 800 하나에 재시도가 3회가 아니라 9회가 된다.

        DART 응답 status 7종(공식 가이드 DS003/2019020)을 각각 다르게
        처리한다. 핵심은 "이 실패가 다음 종목에도 그대로 반복되는가"다:

        - 000(정상): payload를 그대로 반환한다.
        - 013(조회된 데이터 없음): 그 연도만 없는 정상 상황. None.
        - 010(미등록 키)·100(필드 값 오류)·020(요청 제한 초과): 이 종목의
          데이터 문제가 아니라 요청 자체(키·파라미터·일일 한도)의 문제라서
          남은 모든 종목이 정확히 같은 이유로 실패한다. FatalSourceError로
          배치를 멈춘다 — 종목마다 반복해서 기록해봤자 운영자에게 도움이
          안 된다(특히 010: 키를 잘못 넣은 첫 실행에서 42종목이 똑같은
          "DART 오류 010"을 남기고, 정작 무엇을 고쳐야 하는지는 소스를
          읽어야만 알 수 있게 된다).
        - 800(서비스 점검 중): 정의상 일시적이므로 DartServiceUnavailable을
          던져 retry_on_transient()가 재시도하게 한다.
        - 900(정의되지 않은 오류)·그 외 미지정 코드: 원인을 알 수 없으므로
          배치를 멈출 근거가 없다. 이 종목만 SourceError로 실패시킨다.
        """
        self.bucket.wait()
        self.quota.consume()
        resp = self._client.get(
            f"{BASE_URL}/{endpoint}",
            params={
                "crtfc_key": self.api_key,
                "corp_code": corp_code,
                "bsns_year": str(year),
                "reprt_code": REPORT_CODE_ANNUAL,
                **extra,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        status = data.get("status")

        if status == STATUS_OK:
            return data
        if status == STATUS_NO_DATA:
            return None
        if status == STATUS_UNREGISTERED_KEY:
            raise FatalSourceError(
                "DART 인증키가 등록되지 않았습니다. "
                "config/.env 의 DART_API_KEY 를 확인하세요 "
                "(발급: https://opendart.fss.or.kr)"
            )
        if status == STATUS_INVALID_FIELD:
            raise FatalSourceError(
                f"DART 요청 파라미터 오류(필드 값 오류): {data.get('message')}. "
                "crtfc_key/corp_code/bsns_year/reprt_code/fs_div 파라미터를 확인하세요."
            )
        if status == STATUS_RATE_LIMIT:
            raise FatalSourceError(
                f"DART 일일 요청 제한을 초과했습니다: {data.get('message')}. "
                "서버 쪽 일일 한도라 다음 종목으로 넘어가도 풀리지 않습니다 — "
                "내일 다시 시도하세요."
            )
        if status == STATUS_SERVICE_UNAVAILABLE:
            raise DartServiceUnavailable(f"DART 서비스 점검 중: {data.get('message')}")
        # 900(정의되지 않은 오류) 및 스펙에 없는 코드는 원인을 알 수 없으므로
        # 이 종목만 실패시킨다.
        raise SourceError(f"DART 오류 {status}: {data.get('message')}")
