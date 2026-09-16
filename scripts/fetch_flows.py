"""
수급 데이터 수집 (pykrx, 시장 전체를 날짜별 1회 호출로 수집 → 효율적)
- 기관합계/외국인 종목별 순매수거래대금 (최근 N거래일)
- 공매도 거래량/거래비중 (최근 N거래일)
"""
import os
import time
from datetime import datetime, timedelta

import pandas as pd
import config


def _date_candidates(n_extra=15):
    d = datetime.today()
    out = []
    for _ in range(max(config.INVESTOR_LOOKBACK_DAYS, config.SHORT_LOOKBACK_DAYS) + n_extra):
        out.append(d.strftime("%Y%m%d"))
        d -= timedelta(days=1)
    return out


def _std_ticker_col(df: pd.DataFrame) -> pd.DataFrame:
    df = df.reset_index()
    col = next((c for c in df.columns if c in ("티커", "종목코드")), df.columns[0])
    return df.rename(columns={col: "ticker"})


def fetch_investor_flows():
    from pykrx import stock
    frames, used = [], 0
    for date in _date_candidates():
        if used >= config.INVESTOR_LOOKBACK_DAYS:
            break
        day = []
        for m in config.MARKETS:
            for inv in ["기관합계", "외국인"]:
                try:
                    d = stock.get_market_net_purchases_of_equities_by_ticker(date, date, m, inv)
                except Exception:
                    continue
                if d is None or d.empty:
                    continue
                d = _std_ticker_col(d)
                amt = next((c for c in d.columns if "순매수거래대금" in c), None)
                if amt is None:
                    continue
                d = d.rename(columns={amt: "net_amount"})
                d["date"], d["investor"] = date, inv
                day.append(d[["ticker", "net_amount", "date", "investor"]])
        if day:
            frames.append(pd.concat(day, ignore_index=True))
            used += 1
        time.sleep(0.1)
    if not frames:
        print("[수급] 데이터 없음")
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    out["ticker"] = out["ticker"].astype(str).str.zfill(6)
    os.makedirs(config.DATA_DIR, exist_ok=True)
    out.to_csv(os.path.join(config.DATA_DIR, "investor_flows.csv"), index=False, encoding="utf-8-sig")
    print(f"[수급] {used}거래일 수집 완료")
    return out


def fetch_short_selling():
    from pykrx import stock
    frames, used = [], 0
    for date in _date_candidates():
        if used >= config.SHORT_LOOKBACK_DAYS:
            break
        day = []
        for m in config.MARKETS:
            try:
                d = stock.get_shorting_volume_by_ticker(date, market=m)
            except Exception:
                continue
            if d is None or d.empty:
                continue
            d = _std_ticker_col(d)
            ratio = next((c for c in d.columns if "비중" in c), None)
            vol = next((c for c in d.columns if c == "공매도"), None)
            if ratio is None:
                continue
            d = d.rename(columns={ratio: "short_ratio"})
            if vol:
                d = d.rename(columns={vol: "short_volume"})
            else:
                d["short_volume"] = 0
            d["date"] = date
            day.append(d[["ticker", "short_ratio", "short_volume", "date"]])
        if day:
            frames.append(pd.concat(day, ignore_index=True))
            used += 1
        time.sleep(0.1)
    if not frames:
        print("[공매도] 데이터 없음 (리스크 축은 중립 처리)")
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    out["ticker"] = out["ticker"].astype(str).str.zfill(6)
    out.to_csv(os.path.join(config.DATA_DIR, "short_selling.csv"), index=False, encoding="utf-8-sig")
    print(f"[공매도] {used}거래일 수집 완료")
    return out


if __name__ == "__main__":
    fetch_investor_flows()
    fetch_short_selling()
