"""
유니버스 구성 (종목/시장/업종/시총 + 제외종목)
경로 우선순위:
  1) FinanceDataReader KRX 상장정보  (가끔 404/차단)
  2) 네이버 모바일 API 시가총액 목록 (GitHub 서버에서 안정적으로 접근 가능)
  3) 직전 성공 시 저장한 캐시 docs/data/universe_cache.csv (모든 경로 실패 시)
성공하면 캐시를 갱신합니다.
결과: data/universe.csv  (ticker, name, market, sector, market_cap, excluded, exclude_reason)
"""
import os
import re
import time

import pandas as pd
import requests
import config

CACHE_PATH = os.path.join(config.STATE_DIR, "universe_cache.csv")
MOBILE_LIST = "https://m.stock.naver.com/api/stocks/marketValue/{market}?page={page}&pageSize=100"
MOBILE_HEADERS = {**config.REQUEST_HEADERS, "Referer": "https://m.stock.naver.com/", "Accept": "application/json"}

NAVER_EXCLUDE_PAGES = {
    "관리종목": "https://finance.naver.com/sise/management.naver",
    "caution": "https://finance.naver.com/sise/investment_alert.naver?type=caution",
    "warning": "https://finance.naver.com/sise/investment_alert.naver?type=warning",
    "risk": "https://finance.naver.com/sise/investment_alert.naver?type=risk",
}
LABEL_KO = {"caution": "투자주의", "warning": "투자경고", "risk": "투자위험", "관리종목": "관리종목"}


# ---------- 1) FinanceDataReader ----------
def _from_fdr() -> pd.DataFrame:
    import FinanceDataReader as fdr
    lst = fdr.StockListing("KRX")
    lst = lst.rename(columns={"Code": "ticker", "Name": "name", "Market": "market", "Marcap": "market_cap"})
    lst = lst[lst["market"].isin(config.MARKETS)].copy()
    lst["sector"] = None
    try:
        desc = fdr.StockListing("KRX-DESC")
        if "Sector" not in desc.columns and "Industry" in desc.columns:
            desc["Sector"] = desc["Industry"]
        desc = desc.rename(columns={"Code": "ticker", "Sector": "sector"})[["ticker", "sector"]]
        lst = lst.drop(columns=["sector"]).merge(desc, on="ticker", how="left")
        lst["sector"] = lst["sector"].where(~lst["sector"].astype(str).str.contains("부$", na=False), None)
    except Exception as e:
        print(f"[유니버스] 업종(KRX-DESC) 실패: {e} → 실적 수집 시 업종 힌트로 보완")
    return lst[["ticker", "name", "market", "sector", "market_cap"]]


# ---------- 2) 네이버 모바일 API ----------
def _num(x):
    try:
        return float(str(x).replace(",", "").strip())
    except Exception:
        return None


def _from_naver_mobile() -> pd.DataFrame:
    sess = requests.Session()
    sess.headers.update(MOBILE_HEADERS)
    rows = []
    for market in config.MARKETS:
        page, got = 1, 0
        while page <= 40:
            try:
                j = sess.get(MOBILE_LIST.format(market=market, page=page), timeout=10).json()
            except Exception as e:
                print(f"[유니버스] 네이버 모바일 {market} p{page} 실패: {e}")
                break
            stocks = j.get("stocks") if isinstance(j, dict) else j
            if not stocks:
                break
            for s in stocks:
                code = str(s.get("itemCode") or s.get("code") or "").zfill(6)
                if not re.fullmatch(r"\d{6}", code):
                    continue
                # 시총: marketValue(억 단위 문자열) 또는 marketValueFull(원)
                cap = None
                if s.get("marketValueFull") is not None:
                    cap = _num(s.get("marketValueFull"))
                if cap is None and s.get("marketValue") is not None:
                    v = _num(s.get("marketValue"))
                    cap = v * 1e8 if v is not None else None
                rows.append({"ticker": code, "name": s.get("stockName") or s.get("name"), "market": market,
                             "sector": None, "market_cap": cap or 0.0})
                got += 1
            page += 1
            time.sleep(config.REQUEST_SLEEP)
        print(f"[유니버스] 네이버 모바일 {market} {got}종목")
    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError("네이버 모바일 목록 비어있음")
    return df.drop_duplicates("ticker")


# ---------- 3) 캐시 ----------
def _from_cache() -> pd.DataFrame:
    if not os.path.exists(CACHE_PATH):
        raise RuntimeError("캐시 없음")
    df = pd.read_csv(CACHE_PATH, dtype={"ticker": str})
    print(f"[유니버스] ⚠ 모든 소스 실패 → 직전 캐시 {len(df)}종목 사용")
    return df[["ticker", "name", "market", "sector", "market_cap"]]


# ---------- 제외 종목 ----------
def _fdr_admin() -> dict:
    out = {}
    try:
        import FinanceDataReader as fdr
        for key in ("KRX-ADMINISTRATIVE", "KRX-ADMIN"):
            try:
                df = fdr.StockListing(key)
                col = next((c for c in df.columns if c.lower() in ("code", "symbol", "종목코드")), None)
                if col is not None:
                    for c in df[col].astype(str).str.zfill(6):
                        out[c] = "관리종목"
                    break
            except Exception:
                continue
    except Exception:
        pass
    if out:
        print(f"[유니버스] 관리종목(FDR) {len(out)}종목")
    return out


def _cached_exclusions() -> dict:
    """소스가 죽었을 때 직전 캐시의 관리종목 사유를 재사용"""
    if not os.path.exists(CACHE_PATH):
        return {}
    df = pd.read_csv(CACHE_PATH, dtype={"ticker": str})
    if "exclude_reason" not in df.columns:
        return {}
    m = df["exclude_reason"].isin(["관리종목", "투자주의", "투자경고", "투자위험"])
    return dict(zip(df.loc[m, "ticker"], df.loc[m, "exclude_reason"]))


def fetch_exclusions() -> dict:
    result = _fdr_admin()
    targets = (["관리종목"] if config.EXCLUDE_ADMIN_ISSUES else []) + [lv for lv in config.EXCLUDE_ALERT_LEVELS if lv in NAVER_EXCLUDE_PAGES]
    for key in targets:
        try:
            html = requests.get(NAVER_EXCLUDE_PAGES[key], headers=config.REQUEST_HEADERS, timeout=10).text
            codes = set(re.findall(r"code=(\d{6})", html))
            for c in codes:
                result.setdefault(c, LABEL_KO.get(key, key))
            if codes:
                print(f"[유니버스] {LABEL_KO.get(key, key)}(네이버) {len(codes)}종목")
        except Exception:
            pass
        time.sleep(config.REQUEST_SLEEP)
    if not any(v == "관리종목" for v in result.values()):
        cached = _cached_exclusions()
        if cached:
            print(f"[유니버스] 관리종목 소스 실패 → 캐시 {len(cached)}종목 재사용")
            for k, v in cached.items():
                result.setdefault(k, v)
    return result


# ---------- 메인 ----------
def build_universe() -> pd.DataFrame:
    df = None
    for name, fn in (("FinanceDataReader", _from_fdr), ("네이버 모바일", _from_naver_mobile), ("캐시", _from_cache)):
        try:
            df = fn()
            if df is not None and len(df) > 500:
                print(f"[유니버스] {name} 경로로 {len(df)}종목 로드")
                break
            df = None
        except Exception as e:
            print(f"[유니버스] {name} 실패: {e}")
    if df is None:
        raise SystemExit("유니버스 구성 실패 - 모든 소스 불가")

    df = df.copy()
    df["ticker"] = df["ticker"].astype(str).str.zfill(6)
    df["market_cap"] = pd.to_numeric(df["market_cap"], errors="coerce").fillna(0)
    df["sector"] = df["sector"].where(df["sector"].notna(), None)

    # 이전 캐시에서 업종 보완 (이번 소스에 업종이 없을 때)
    if os.path.exists(CACHE_PATH):
        try:
            old = pd.read_csv(CACHE_PATH, dtype={"ticker": str}).set_index("ticker")["sector"]
            miss = df["sector"].isna()
            df.loc[miss, "sector"] = df.loc[miss, "ticker"].map(old)
        except Exception:
            pass
    df["sector"] = df["sector"].fillna("기타").replace({"": "기타", "nan": "기타"})

    df["excluded"], df["exclude_reason"] = False, ""
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
    os.makedirs(config.STATE_DIR, exist_ok=True)
    df.to_csv(os.path.join(config.DATA_DIR, "universe.csv"), index=False, encoding="utf-8-sig")
    df.to_csv(CACHE_PATH, index=False, encoding="utf-8-sig")  # 다음 실행 대비 캐시 갱신
    print(f"[유니버스] 총 {len(df)}종목, 제외 {int(df['excluded'].sum())}종목, 스크리닝 대상 {int((~df['excluded']).sum())}종목")
    return df


if __name__ == "__main__":
    build_universe()
