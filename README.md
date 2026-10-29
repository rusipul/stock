# stockscout

한국·미국 우량주 가운데 5년 평균·고점 대비 크게 빠진 종목을 찾는 스크리너.

- 한국: DART 재무 + pykrx 주가 (KOSPI 42종목)
- 미국: SEC EDGAR 재무 + yfinance 주가 (40종목)
- 우량주 점수 4축(수익성·성장성·안정성·현금흐름) × 25점, 하드필터, 등급 ★★/★

설계는 `docs/superpowers/specs/2026-09-10-stock-screener-design.md`에 있다.

## 설치

```
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"
```

`config/.env`에 비밀값을 넣는다 (`config/.env.example` 참고).

```
DART_API_KEY=...                         # 한국: https://opendart.fss.or.kr
SEC_USER_AGENT=stockscout you@example.com  # 미국: 연락처가 없으면 SEC가 403
```

## 실행

```
stockscout universe  --market KR     # config/universe.yaml -> DB
stockscout collect   --market KR     # 재무·주가 수집 (증분)
stockscout normalize --market KR     # 원본 -> 공통 재무 형식
stockscout analyze   --market KR     # 스냅샷 생성
stockscout serve                     # http://127.0.0.1:8000
```

미국은 `--market US`. 종목을 더하려면 `config/universe.yaml`에 한 줄을 넣고
위 명령을 다시 돌린다.

## 공개 사이트 (Vercel)

수집·분석은 PC에서 하고, 결과만 정적 사이트로 내보내 올린다.

```
stockscout export            # site/ 에 화면 + JSON
git add site && git commit -m "data: 스냅샷 갱신" && git push
```

Vercel 프로젝트를 이 저장소에 연결해 두면 push마다 다시 배포된다.
설정은 `vercel.json`(출력 폴더 `site`, 빌드 없음, 검색엔진 색인 차단)이다.
공개 사이트에서 관심종목은 방문자 브라우저에만 저장된다.

## 테스트

```
.venv/Scripts/python.exe -m pytest
```
