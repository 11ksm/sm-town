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
MOBILE_LISTS = [
    "https://m.stock.naver.com/api/stocks/marketValue/{market}?page={page}&pageSize=100",
    "https://m.stock.naver.com/api/stocks/marketValue/{market}?page={page}&pageSize=100&sortType=marketValue",
]
MOBILE_INTEG = "https://m.stock.naver.com/api/stock/{code}/integration"
DART_CORP = "https://opendart.fss.or.kr/api/corpCode.xml"
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
    """'4,052,150억원' / '1.2조' / '12,345' → float(원 단위는 호출자가 판단)"""
    try:
        t = str(x).replace(",", "").replace("원", "").strip()
        mult = 1.0
        if "조" in t:
            a, _, b = t.partition("조")
            v = float(a) * 1e12 + (float(b.replace("억", "")) * 1e8 if b.replace("억", "").strip() else 0)
            return v
        if t.endswith("억"):
            t, mult = t[:-1], 1e8
        return float(t) * mult
    except Exception:
        return None


def _from_naver_mobile() -> pd.DataFrame:
    sess = requests.Session()
    sess.headers.update(MOBILE_HEADERS)
    rows = []
    for market in config.MARKETS:
        page, got = 1, 0
        while page <= 40:
            j = None
            for url in MOBILE_LISTS:
                try:
                    r = sess.get(url.format(market=market, page=page), timeout=10)
                    if r.status_code == 200 and r.text.strip().startswith(("{", "[")):
                        j = r.json()
                        break
                except Exception:
                    continue
            if j is None:
                if page == 1:
                    print(f"[유니버스] 네이버 모바일 {market} 목록 접근 불가")
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
                for k in ("marketValueFull", "marketValue", "marketSum"):
                    if s.get(k) is not None:
                        v = _num(s.get(k))
                        if v is not None:
                            cap = v if v > 1e9 else v * 1e8   # 억 단위 숫자만 온 경우 보정
                            break
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


# ---------- 3) DART 기업목록 + 네이버 종목별 API ----------
def _from_dart_and_naver() -> pd.DataFrame:
    """DART corpCode(전 상장기업 코드) → 종목별 네이버 모바일 API로 시장/시총 확인. 느리지만 KRX 무관."""
    if not config.DART_API_KEY:
        raise RuntimeError("DART_API_KEY 없음")
    import io, zipfile
    import xml.etree.ElementTree as ET
    r = requests.get(DART_CORP, params={"crtfc_key": config.DART_API_KEY}, timeout=30)
    z = zipfile.ZipFile(io.BytesIO(r.content))
    root = ET.fromstring(z.read(z.namelist()[0]))
    codes = []
    for el in root.iter("list"):
        sc = (el.findtext("stock_code") or "").strip()
        if re.fullmatch(r"\d{6}", sc):
            codes.append((sc, (el.findtext("corp_name") or "").strip()))
    print(f"[유니버스] DART 상장기업 {len(codes)}개 → 네이버 종목별 확인 중 (수 분 소요)")
    sess = requests.Session()
    sess.headers.update(MOBILE_HEADERS)
    MK = {"코스피": "KOSPI", "KOSPI": "KOSPI", "코스닥": "KOSDAQ", "KOSDAQ": "KOSDAQ"}
    rows = []
    for i, (code, cname) in enumerate(codes):
        try:
            j = sess.get(MOBILE_INTEG.format(code=code), timeout=10).json()
            ex = j.get("stockExchangeType") or {}
            market = MK.get(ex.get("name") or ex.get("nameEng") or ex.get("code") or "")
            if market not in config.MARKETS:
                continue
            cap = None
            for info in j.get("totalInfos", []) or []:
                if info.get("code") == "marketValue":
                    cap = _num(info.get("value"))
            name = (j.get("stockName") or cname)
            ind = j.get("industryCodeType") or {}
            rows.append({"ticker": code, "name": name, "market": market,
                         "sector": ind.get("industryGroupKor") or ind.get("name"), "market_cap": cap or 0.0})
        except Exception:
            pass
        if i % 500 == 0 and i > 0:
            print(f"  유니버스 진행 {i}/{len(codes)}")
        time.sleep(0.04)
    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError("DART+네이버 경로 결과 없음")
    return df


# ---------- 4) 캐시 ----------
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
    for name, fn in (("FinanceDataReader", _from_fdr), ("네이버 모바일", _from_naver_mobile), ("DART+네이버", _from_dart_and_naver), ("캐시", _from_cache)):
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
