"""
유니버스 구성
- 종목코드/종목명/시장/업종/시가총액 수집 (FinanceDataReader 우선, 실패 시 pykrx)
- 네이버금융에서 관리종목·투자주의/경고/위험 종목을 가져와 제외
결과: data/universe.csv  (ticker, name, market, sector, market_cap, excluded, exclude_reason)
"""
import os
import re
import time
from datetime import datetime

import pandas as pd
import requests
import config

NAVER_EXCLUDE_PAGES = {
    "관리종목": "https://finance.naver.com/sise/management.naver",
    "caution": "https://finance.naver.com/sise/investment_alert.naver?type=caution",
    "warning": "https://finance.naver.com/sise/investment_alert.naver?type=warning",
    "risk": "https://finance.naver.com/sise/investment_alert.naver?type=risk",
}
LABEL_KO = {"caution": "투자주의", "warning": "투자경고", "risk": "투자위험", "관리종목": "관리종목"}


def _latest_bday() -> str:
    from pykrx import stock
    return stock.get_nearest_business_day_in_a_week(datetime.today().strftime("%Y%m%d"))


def _from_fdr() -> pd.DataFrame:
    import FinanceDataReader as fdr
    lst = fdr.StockListing("KRX")          # Code, Name, Market, Marcap, ...
    desc = fdr.StockListing("KRX-DESC")    # Code, Name, Sector, Industry, ...
    lst = lst.rename(columns={"Code": "ticker", "Name": "name", "Market": "market", "Marcap": "market_cap"})
    desc = desc.rename(columns={"Code": "ticker", "Sector": "sector"})
    df = lst.merge(desc[["ticker", "sector"]], on="ticker", how="left")
    df = df[df["market"].isin(config.MARKETS)]
    return df[["ticker", "name", "market", "sector", "market_cap"]]


def _from_pykrx() -> pd.DataFrame:
    from pykrx import stock
    date = _latest_bday()
    frames = []
    for m in config.MARKETS:
        cap = stock.get_market_cap(date, market=m).reset_index()
        ticker_col = next((c for c in cap.columns if c in ("티커", "종목코드")), cap.columns[0])
        cap = cap.rename(columns={ticker_col: "ticker", "시가총액": "market_cap"})
        cap["market"] = m
        frames.append(cap[["ticker", "market_cap", "market"]])
    df = pd.concat(frames, ignore_index=True)
    names = {}
    for t in df["ticker"]:
        try:
            names[t] = stock.get_market_ticker_name(t)
        except Exception:
            names[t] = t
    df["name"] = df["ticker"].map(names)
    df["sector"] = "기타"
    return df[["ticker", "name", "market", "sector", "market_cap"]]


def fetch_exclusions() -> dict:
    """{ticker: 사유} 형태로 반환"""
    result = {}
    targets = []
    if config.EXCLUDE_ADMIN_ISSUES:
        targets.append("관리종목")
    targets += [lv for lv in config.EXCLUDE_ALERT_LEVELS if lv in NAVER_EXCLUDE_PAGES]

    for key in targets:
        url = NAVER_EXCLUDE_PAGES[key]
        try:
            html = requests.get(url, headers=config.REQUEST_HEADERS, timeout=10).text
            codes = set(re.findall(r"code=(\d{6})", html))
            for c in codes:
                result.setdefault(c, LABEL_KO.get(key, key))
            print(f"[유니버스] {LABEL_KO.get(key, key)} {len(codes)}종목 제외 대상")
        except Exception as e:
            print(f"[유니버스] {key} 조회 실패: {e}")
        time.sleep(config.REQUEST_SLEEP)
    return result


def build_universe() -> pd.DataFrame:
    try:
        df = _from_fdr()
        print(f"[유니버스] FinanceDataReader로 {len(df)}종목 로드")
    except Exception as e:
        print(f"[유니버스] FDR 실패({e}) → pykrx 대체")
        df = _from_pykrx()

    df["ticker"] = df["ticker"].astype(str).str.zfill(6)
    df["sector"] = df["sector"].fillna("기타").replace("", "기타")
    df["market_cap"] = pd.to_numeric(df["market_cap"], errors="coerce").fillna(0)

    df["excluded"] = False
    df["exclude_reason"] = ""

    if config.MIN_MARKET_CAP > 0:
        m = df["market_cap"] < config.MIN_MARKET_CAP
        df.loc[m, ["excluded", "exclude_reason"]] = [True, "시총 하한 미달"]

    for kw in config.EXCLUDE_KEYWORDS:
        m = df["name"].astype(str).str.contains(kw, case=False, na=False) & ~df["excluded"]
        df.loc[m, ["excluded", "exclude_reason"]] = [True, f"키워드({kw})"]

    exc = fetch_exclusions()
    m = df["ticker"].isin(exc.keys()) & ~df["excluded"]
    df.loc[m, "excluded"] = True
    df.loc[m, "exclude_reason"] = df.loc[m, "ticker"].map(exc)

    os.makedirs(config.DATA_DIR, exist_ok=True)
    df.to_csv(os.path.join(config.DATA_DIR, "universe.csv"), index=False, encoding="utf-8-sig")
    print(f"[유니버스] 총 {len(df)}종목, 제외 {int(df['excluded'].sum())}종목, 스크리닝 대상 {int((~df['excluded']).sum())}종목")
    return df


if __name__ == "__main__":
    build_universe()
