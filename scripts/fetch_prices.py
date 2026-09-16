"""
가격/거래량 데이터 수집 (pykrx)
- 스크리닝 대상 종목의 OHLCV (52주 신고가 계산을 위해 약 1년치)
- 코스피/코스닥 지수 (상대강도 계산용)
"""
import os
import time
from datetime import datetime, timedelta

import pandas as pd
import config

INDEX_CODES = {"KOSPI": "1001", "KOSDAQ": "2001"}


def fetch_all_prices(universe: pd.DataFrame):
    from pykrx import stock

    end = datetime.today()
    start = end - timedelta(days=config.PRICE_LOOKBACK_DAYS)
    s, e = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")

    out_dir = os.path.join(config.DATA_DIR, "prices")
    os.makedirs(out_dir, exist_ok=True)

    # 지수
    for m, code in INDEX_CODES.items():
        try:
            idx = stock.get_index_ohlcv(s, e, code)
            idx.to_csv(os.path.join(config.DATA_DIR, f"index_{m}.csv"), encoding="utf-8-sig")
        except Exception as ex:
            print(f"[가격] {m} 지수 조회 실패: {ex}")

    tickers = universe.loc[~universe["excluded"], "ticker"].tolist()
    print(f"[가격] 대상 {len(tickers)}종목 수집 시작")
    failed = 0
    for i, t in enumerate(tickers):
        try:
            df = stock.get_market_ohlcv(s, e, t)
            if df is not None and not df.empty:
                df.to_csv(os.path.join(out_dir, f"{t}.csv"), encoding="utf-8-sig")
        except Exception:
            failed += 1
        if i % 250 == 0 and i > 0:
            print(f"  진행 {i}/{len(tickers)}")
        time.sleep(0.04)
    print(f"[가격] 완료 (실패 {failed}건)")


if __name__ == "__main__":
    u = pd.read_csv(os.path.join(config.DATA_DIR, "universe.csv"), dtype={"ticker": str})
    fetch_all_prices(u)
