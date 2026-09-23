"""
KR Stock Radar v2 - 전체 파이프라인
GitHub Actions에서 매일 실행:  python run_pipeline.py
각 단계는 실패해도 다음 단계로 넘어가며, 데이터가 없는 축은 중립 처리됩니다.
"""
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import config
import universe as U
import fetch_prices, fetch_flows, fetch_naver, fetch_dart, fetch_us, scoring, sentiment, build_site


def step(title, fn, *a, **kw):
    print(f"\n=== {title} ===")
    try:
        return fn(*a, **kw)
    except Exception:
        traceback.print_exc()
        print(f"[경고] '{title}' 실패 → 건너뜀")
        return None


def main():
    uni = step("1. 유니버스 구성 (제외종목 필터)", U.build_universe)
    if uni is None:
        raise SystemExit("유니버스 구성 실패 - 중단")

    step("2. 가격/지수 수집", fetch_prices.fetch_all_prices, uni)
    step("3. 기관/외국인 수급 수집 (네이버 모바일 API)", fetch_flows.fetch_investor_flows, uni)
    step("4. 공매도 수집", fetch_flows.fetch_short_selling)
    step("5. 실적/밸류 지표 수집 (네이버 모바일 API)", fetch_naver.fetch_fundamentals, uni)
    step("6. DART 자사주 공시 수집", fetch_dart.fetch_buybacks)
    step("6-1. 미국 지수 / Fear&Greed", fetch_us.fetch_us)

    # 신용비율은 예비 스코어 상위 K종목만 개별 조회 (요청량 절감)
    prelim = step("7. 예비 스코어링", scoring.compute, uni, preliminary=True)
    if prelim is not None and config.CREDIT_RATIO_TOP_K > 0:
        step("8. 실적 보강 (yfinance, 모바일 API 실패 시 상위 K)", fetch_naver.fetch_fundamentals_yf,
             prelim["ticker"].head(config.CREDIT_RATIO_TOP_K).tolist(), uni)

    scores = step("9. 최종 스코어링", scoring.compute, uni)
    if scores is None or scores.empty:
        raise SystemExit("스코어링 실패 - 중단")

    senti = step("9-1. 시장 심리 지수 (한국 공포·탐욕 / 과열도)", sentiment.compute) or {}

    step("10. 사이트 빌드", build_site.build, scores, uni, senti)
    print("\n=== 완료 ===")


if __name__ == "__main__":
    main()
