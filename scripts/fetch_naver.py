"""
실적·밸류 지표 수집
1순위: 네이버 모바일 API (전 종목, 빠름)  https://m.stock.naver.com/api/stock/{code}/integration
2순위: yfinance (모바일 API 차단 시, 예비순위 상위 K종목만)
결과: data/fundamentals.csv  (ticker, per, pbr, eps, dividend_yield, frgn_rate, roe, op_growth, sales_growth, sector_hint)
"""
import os
import time

import pandas as pd
import requests
import config

INTEG_URL = "https://m.stock.naver.com/api/stock/{code}/integration"
MOBILE_HEADERS = {**config.REQUEST_HEADERS, "Referer": "https://m.stock.naver.com/", "Accept": "application/json"}
KEYMAP = {"per": "per", "pbr": "pbr", "eps": "eps", "dividendYield": "dividend_yield", "foreignRatio": "frgn_rate",
          "roe": "roe", "estimatedPer": "per_fwd"}


def _num(x):
    try:
        v = str(x).replace(",", "").replace("%", "").replace("배", "").replace("원", "").strip()
        return float(v) if v not in ("", "-", "N/A") else None
    except Exception:
        return None


def _parse_integration(j: dict) -> dict:
    out = {}
    for info in j.get("totalInfos", []) or []:
        code = info.get("code")
        if code in KEYMAP:
            out[KEYMAP[code]] = _num(info.get("value"))
    ind = j.get("industryCodeType") or {}
    out["sector_hint"] = ind.get("industryGroupKor") or ind.get("name")
    # 연간 실적 증가율 (있으면)
    try:
        ann = [x for x in (j.get("financeInfo", {}) or {}).get("rowList", []) if x.get("title") in ("영업이익", "매출액")]
        for row in ann:
            cols = [c for c in row.get("columns", {}).values()] if isinstance(row.get("columns"), dict) else []
            vals = [_num(c.get("value")) for c in cols if isinstance(c, dict)]
            vals = [v for v in vals if v is not None]
            if len(vals) >= 2 and vals[-2] not in (0, None):
                g = (vals[-1] - vals[-2]) / abs(vals[-2]) * 100
                out["op_growth" if row["title"] == "영업이익" else "sales_growth"] = g
    except Exception:
        pass
    return out


def fetch_fundamentals(universe: pd.DataFrame = None) -> pd.DataFrame:
    if universe is None:
        universe = pd.read_csv(os.path.join(config.DATA_DIR, "universe.csv"), dtype={"ticker": str})
    tickers = universe.loc[~universe["excluded"], "ticker"].tolist()
    os.makedirs(config.DATA_DIR, exist_ok=True)
    path = os.path.join(config.DATA_DIR, "fundamentals.csv")

    sess = requests.Session()
    sess.headers.update(MOBILE_HEADERS)
    try:
        probe = sess.get(INTEG_URL.format(code="005930"), timeout=10).json()
        ok = bool(probe.get("totalInfos"))
    except Exception as e:
        ok = False
        print(f"[실적] 모바일 API 접근 실패: {e}")

    rows = []
    if ok:
        failed = 0
        for i, t in enumerate(tickers):
            try:
                d = _parse_integration(sess.get(INTEG_URL.format(code=t), timeout=10).json())
                d["ticker"] = t
                rows.append(d)
            except Exception:
                failed += 1
            if i % 500 == 0 and i > 0:
                print(f"  실적 진행 {i}/{len(tickers)}")
            time.sleep(0.05)
        print(f"[실적] 네이버 모바일 API {len(rows)}종목 (실패 {failed})")
    else:
        print("[실적] yfinance 대체 경로 (예비 스코어 상위 K종목) — fetch_fundamentals_yf 에서 처리")

    df = pd.DataFrame(rows)
    if df.empty:
        df = pd.DataFrame(columns=["ticker", "per", "pbr", "eps", "dividend_yield", "frgn_rate", "roe", "op_growth", "sales_growth", "sector_hint"])
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return df


def fetch_fundamentals_yf(tickers, universe: pd.DataFrame) -> pd.DataFrame:
    """모바일 API가 막힌 경우: yfinance로 상위 K종목만 (느리므로 K 제한)"""
    path = os.path.join(config.DATA_DIR, "fundamentals.csv")
    existing = pd.read_csv(path, dtype={"ticker": str}) if os.path.exists(path) else pd.DataFrame()
    if not existing.empty and existing["per"].notna().sum() > 50:
        return existing  # 이미 수집됨
    try:
        import yfinance as yf
    except Exception:
        return existing
    mk = universe.set_index("ticker")["market"].to_dict()
    rows = []
    for i, t in enumerate(tickers):
        sym = f"{t}.KS" if mk.get(t) == "KOSPI" else f"{t}.KQ"
        try:
            info = yf.Ticker(sym).info or {}
            rows.append({"ticker": t, "per": info.get("trailingPE"), "pbr": info.get("priceToBook"),
                         "roe": (info.get("returnOnEquity") or 0) * 100 if info.get("returnOnEquity") is not None else None,
                         "op_growth": (info.get("earningsGrowth") or 0) * 100 if info.get("earningsGrowth") is not None else None,
                         "sales_growth": (info.get("revenueGrowth") or 0) * 100 if info.get("revenueGrowth") is not None else None,
                         "dividend_yield": (info.get("dividendYield") or 0) * 100 if info.get("dividendYield") is not None else None})
        except Exception:
            pass
        if i % 50 == 0 and i > 0:
            print(f"  yfinance 진행 {i}/{len(tickers)}")
        time.sleep(0.2)
    df = pd.DataFrame(rows)
    if not df.empty:
        df.to_csv(path, index=False, encoding="utf-8-sig")
        print(f"[실적] yfinance {df['per'].notna().sum()}/{len(df)}종목 확보")
    return df


def fetch_credit_ratio(tickers) -> pd.DataFrame:
    """네이버 PC 페이지 차단으로 신용비율 수집 불가 → 생략"""
    print("[신용비율] 네이버 PC 페이지 해외 접속 차단으로 생략")
    return pd.DataFrame(columns=["ticker", "credit_ratio"])


if __name__ == "__main__":
    fetch_fundamentals()
