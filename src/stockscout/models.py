"""공유 데이터 모델.

이 파일은 프로젝트의 바닥이며 stockscout의 어떤 모듈도 import하지 않는다.
sources·normalize·store가 서로를 모른 채 같은 모델로 대화하게 하는 것이 목적이다.
"""

from __future__ import annotations

from pydantic import BaseModel


class Security(BaseModel):
    """종목 마스터 한 건."""

    ticker: str                      # '005930.KS' | 'AAPL'
    market: str                      # 'KR' | 'US'
    name: str
    name_en: str | None = None
    exchange: str | None = None
    sector: str | None = None
    is_financial: bool = False
    source_id: str | None = None     # DART corp_code | SEC CIK
    currency: str
    listed_at: str | None = None
    in_universe: bool = True
