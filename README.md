# stockscout

우량주가 최근 몇 달 사이 크게 빠진 종목을 찾는 스크리너.

- 한국: DART 재무·공시 + pykrx 주가 (코스피 42 + 코스닥 30종목)
- 미국: SEC EDGAR 재무·공시 + yfinance 주가 (40종목)
- 걸러내기: 금융업·자본잠식·연속 적자·고부채(한국)·이자 미충당·매출 붕괴·
  감사의견·12개월 자금 여력·거래대금·최근 1년 위험 공시
- 우량주 점수 4축(수익성·성장성·안정성·현금흐름) × 25점. 70점 이상이고
  각 영역 10점 이상
- 등급: 최근 3개월 고점 대비 -25% 이하면 후보. 하락이 멈췄으면 ★★, 아직
  하락 중이면 ★. 같은 기간 시장 지수 하락, 최근 중간보고서 실적, 최근 공시를
  함께 보인다

공유용 요약은 `docs/selection-criteria.md`와 `.png`에 있다.

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
stockscout backtest  --market KR     # 기준 검증: 과거 신호의 20·60·120일 성과
```

`backtest`는 과거 시점마다 그때 보였던 주가·재무(공시일 근사)로 신호를 내고
이후 성과를 아무 조건 없이 산 경우와 비교한다. 유니버스가 지금 남아 있는
대형주라 생존자 편향이 있고 수익률은 거래비용 전이다 - 결과에 함께 적힌다.

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
