"""
KR Stock Radar v2 - 설정 파일
값만 바꾸면 전체 파이프라인에 반영됩니다.
"""
import os

# ===== 유니버스 =====
MARKETS = ["KOSPI", "KOSDAQ"]
MIN_MARKET_CAP = 0            # 시가총액 하한(원). 0 = 전체. 실행시간이 길면 30_000_000_000(300억) 권장
EXCLUDE_ADMIN_ISSUES = True   # 관리종목 제외
EXCLUDE_ALERT_LEVELS = ["caution", "warning", "risk"]  # 투자주의/투자경고/투자위험 제외
EXCLUDE_KEYWORDS = ["스팩", "SPAC", "우B", "우C"]  # 종목명 키워드 기반 제외 (스팩·우선주 일부)

# ===== 조회 기간 =====
PRICE_LOOKBACK_DAYS = 380     # 52주 신고가 계산용 (달력일)
INVESTOR_LOOKBACK_DAYS = 20   # 기관/외국인 수급 (거래일)
SHORT_LOOKBACK_DAYS = 20      # 공매도 거래비중 (거래일)
BUYBACK_LOOKBACK_DAYS = 30    # 자사주 공시 유효기간 (달력일)
CREDIT_RATIO_TOP_K = 300      # 신용비율은 예비 스코어 상위 K종목만 개별 조회 (요청량 절감)

# ===== 가중치 (합계 1.0) =====
WEIGHTS = {
    "supply": 0.25,        # 기관/외국인 수급
    "technical": 0.25,     # 기술적지표 (+52주 신고가, 상대강도)
    "event": 0.20,         # 자사주 매입/소각 재료
    "fundamental": 0.15,   # 실적 (영업이익/매출 증가율, PER)
    "risk": 0.15,          # 리스크 (공매도 추이, 신용비율) - 낮을수록 가점
}

# ===== 결과/화면 =====
TOP_N = 50                    # 메인 표 노출 종목 수
EMBED_N = 300                 # 필터/검색용으로 페이지에 내장할 종목 수
NEW_PICK_RANK_JUMP = 20       # "오늘의 신규 포착" 기준: 순위 20계단 이상 급등
HISTORY_KEEP_DAYS = 120       # 히스토리 보관 일수
HISTORY_TRACK_N = 100         # 히스토리에 매일 기록할 상위 종목 수
PERF_HORIZONS = [5, 10, 20]   # 히스토리 성과검증 구간 (거래일)

# ===== 외부 API / 요청 =====
DART_API_KEY = os.environ.get("DART_API_KEY", "")
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
}
REQUEST_SLEEP = 0.15

# ===== 경로 =====
DATA_DIR = "data"                     # 임시 데이터 (커밋 안 함)
DOCS_DIR = "docs"                     # GitHub Pages 배포 폴더
STATE_DIR = os.path.join(DOCS_DIR, "data")   # 히스토리/전일 순위 등 지속 저장 (커밋됨)
