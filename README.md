# KR Stock Radar v2 — 수원WM 데일리 종목 스크리닝

코스피·코스닥 전 종목을 매일 아침 08:30(KST) 자동으로 5개 축으로 스코어링하고,
필터·검색·즐겨찾기·히스토리 성과검증까지 제공하는 GitHub Pages 홈페이지입니다.

## 스코어링 (config.py 에서 가중치 조정)

| 축 | 기본 비중 | 내용 | 출처 |
|---|---|---|---|
| 수급 | 25% | 기관+외국인 20일 순매수(시총 대비) + 연속 순매수일 | KRX(pykrx) |
| 기술 | 25% | 정배열·RSI·MACD·거래량 급증 + 52주 신고가 근접도 + 시장 대비 상대강도 | KRX(pykrx) |
| 재료 | 20% | 자기주식 취득/소각 공시 (최근일수록 가점, 소각 > 취득 > 처분) | DART Open API |
| 실적 | 15% | 영업이익증가율·매출액증가율·PER 적정성·ROE | 네이버금융 |
| 리스크 | 15% | 공매도 비중 감소 추이·공매도 수준·신용비율 (낮을수록 가점) | KRX, 네이버금융 |

각 축은 전 종목 백분위(0~100)로 정규화 후 가중 합산합니다. 데이터가 없는 축은 50점(중립) 처리되어 어떤 소스가 일시 장애여도 페이지는 항상 갱신됩니다.

## 실무 기능
- **자동 제외**: 관리종목, 투자주의/경고/위험, 스팩 (네이버금융 리스트 기준)
- **오늘의 신규 포착**: TOP50 신규 진입 또는 20계단 이상 급등 종목만 모아 보기
- **순위 변동**: 전일 대비 ▲▼ / NEW 표시
- **업종 내 순위**: 전체 순위 외에 업종 내 상대 순위 표시, 업종 필터
- **히스토리 · 성과검증**: 5/10/20거래일 전 상위10 종목의 이후 평균 수익률 vs 코스피, 일별 상위10 기록
- **즐겨찾기**: ★ 클릭 (해당 브라우저에만 저장, 서버 전송 없음)
- **종목명 클릭** → 네이버금융 종목 페이지

## 설치 (5단계)

1. **저장소 생성**: github.com → New repository → 이름 예) `kr-stock-radar`, **Public** → 이 폴더 전체 업로드
   (Add file → Upload files 로 드래그해도 됩니다. `.github` 폴더가 빠지지 않았는지 확인)
2. **Actions 권한**: Settings → Actions → General → Workflow permissions → **Read and write permissions** 저장
3. **DART 키**: https://opendart.fss.or.kr 무료 가입 → 인증키 발급 → Settings → Secrets and variables → Actions → New repository secret → Name `DART_API_KEY`
4. **Pages**: Settings → Pages → Source: Deploy from a branch → Branch `main`, 폴더 `/docs` 저장
   → 주소: `https://본인계정.github.io/kr-stock-radar/`
5. **첫 실행**: Actions 탭 → "KR Stock Radar - Daily Update" → Run workflow (전 종목 1년치 시세 수집으로 20~60분 소요)

## 실행 시간 줄이기
`config.py`의 `MIN_MARKET_CAP = 30_000_000_000` (300억) 처럼 시총 하한을 두면 초소형주가 제외되어 실행이 크게 빨라집니다.
`CREDIT_RATIO_TOP_K` 를 줄이면 네이버 개별 요청도 줄어듭니다.

## 폴더 구조
```
config.py               가중치·기간·필터 설정
run_pipeline.py         전체 실행 (각 단계 실패 시 건너뛰고 계속)
scripts/
  universe.py           종목/업종/시총 + 관리종목·투자주의 제외
  fetch_prices.py       OHLCV 1년치 + 코스피/코스닥 지수
  fetch_flows.py        기관/외국인 순매수, 공매도
  fetch_naver.py        실적/PER/ROE (전종목), 신용비율 (상위 K)
  fetch_dart.py         자사주 공시
  scoring.py            5축 스코어링 + 업종 상대순위
  history.py            히스토리·순위변동·성과검증
  build_site.py         docs/index.html 생성
templates/index.html    화면 (필터/검색/정렬/즐겨찾기/탭)
docs/                   GitHub Pages 배포 폴더
docs/data/              히스토리 (커밋되어 누적됨)
```

## 주의
- pykrx·FinanceDataReader는 비공식 라이브러리이며, 네이버 페이지 구조도 바뀔 수 있습니다. 특정 축이 갑자기 전부 50점이면 해당 수집 단계 로그(Actions 탭)를 확인하세요.
- 히스토리 성과검증은 실행일 수가 누적되어야 표시됩니다 (5거래일 이상).
- 본 도구는 투자 판단 보조용이며 매매 권유가 아닙니다. 고객 제안 전 최신 공시·시세를 반드시 재확인하세요.
