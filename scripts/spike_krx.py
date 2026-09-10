"""pykrx 함수 시그니처와 반환 형태를 확인한다. 일회성 탐색 스크립트.

섹션마다 try/except로 감싸 하나가 실패해도 나머지를 계속 확인한다.
(실제로 KOSPI 200 구성종목 조회는 KRX_ID/KRX_PW 로그인이 없으면 실패한다 — 아래 참고.)
"""

from pykrx import stock

KOSPI200_INDEX = "1028"

print("=== KOSPI 200 구성종목 ===")
try:
    tickers = stock.get_index_portfolio_deposit_file(KOSPI200_INDEX)
    print("종목 수:", len(tickers))
    print("앞 5개:", tickers[:5])
except Exception as exc:
    print("실패:", type(exc).__name__, exc)

print("\n=== 종목명 ===")
print("005930 →", stock.get_market_ticker_name("005930"))

print("\n=== 일봉 (기본값) ===")
df = stock.get_market_ohlcv("20260101", "20260131", "005930")
print("컬럼:", list(df.columns))
print("인덱스 타입:", type(df.index))
print(df.head())

print("\n=== 일봉 (adjusted=True 명시) ===")
try:
    df_adj = stock.get_market_ohlcv("20260101", "20260131", "005930", adjusted=True)
    print("컬럼:", list(df_adj.columns))
    print("기본값과 동일한가:", df.equals(df_adj))
except TypeError as exc:
    print("adjusted 파라미터 없음:", exc)

print("\n=== 일봉 (adjusted=False, 원주가) ===")
try:
    df_raw = stock.get_market_ohlcv("20260101", "20260131", "005930", adjusted=False)
    print("컬럼:", list(df_raw.columns))
    print("기본값과 동일한가:", df.equals(df_raw))
except TypeError as exc:
    print("adjusted 파라미터 없음:", exc)

print("\n=== 장기간 조회로 액면분할 구간 확인 (005930, 2018년 4~6월 — 50:1 액면분할) ===")
try:
    df_split = stock.get_market_ohlcv("20180401", "20180601", "005930")
    print(df_split)
except Exception as exc:
    print("실패:", type(exc).__name__, exc)
