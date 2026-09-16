"""
스코어링 엔진 v2
5개 축을 각각 0~100(백분위)로 만든 뒤 config.WEIGHTS 로 가중 합산합니다.
데이터가 없는 축은 50점(중립) 처리하여 파이프라인이 항상 결과를 내도록 합니다.

축:
  supply      기관+외국인 20일 순매수(시총 대비) + 연속 순매수일
  technical   정배열/RSI/MACD/거래량 + 52주 신고가 근접도 + 시장 대비 상대강도
  event       자사주 취득/소각 공시 (최근일수록 가점)
  fundamental 영업이익증가율/매출액증가율/PER 적정성
  risk        공매도 비중 감소 추이, 신용비율 (낮을수록 가점)
"""
import os
import numpy as np
import pandas as pd
import config


# ---------- 유틸 ----------
def _pct(s: pd.Series) -> pd.Series:
    """0~100 백분위. 전부 NaN이면 50."""
    if s.notna().sum() == 0:
        return pd.Series(50.0, index=s.index)
    r = s.rank(pct=True) * 100
    return r.fillna(50.0)


def _rsi(c: pd.Series, n=14):
    d = c.diff()
    up = d.clip(lower=0).rolling(n).mean()
    dn = (-d.clip(upper=0)).rolling(n).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _macd(c: pd.Series):
    m = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    return m, m.ewm(span=9, adjust=False).mean()


def _read(name, **kw):
    p = os.path.join(config.DATA_DIR, name)
    if not os.path.exists(p):
        return pd.DataFrame()
    try:
        return pd.read_csv(p, dtype={"ticker": str}, **kw)
    except Exception:
        return pd.DataFrame()


# ---------- 각 축 ----------
def technical_and_price(universe: pd.DataFrame) -> pd.DataFrame:
    price_dir = os.path.join(config.DATA_DIR, "prices")
    idx_ret = {}
    for m in config.MARKETS:
        idx = _read(f"index_{m}.csv")
        if not idx.empty and "종가" in idx.columns and len(idx) > 21:
            idx_ret[m] = idx["종가"].iloc[-1] / idx["종가"].iloc[-21] - 1
    mk = universe.set_index("ticker")["market"].to_dict()

    rows = []
    if not os.path.isdir(price_dir):
        return pd.DataFrame(columns=["ticker"])
    for f in os.listdir(price_dir):
        if not f.endswith(".csv"):
            continue
        t = f[:-4]
        try:
            df = pd.read_csv(os.path.join(price_dir, f))
            c = df["종가"].astype(float)
            v = df["거래량"].astype(float) if "거래량" in df.columns else None
            if len(c) < 30:
                continue
            ma5, ma20, ma60 = c.rolling(5).mean(), c.rolling(20).mean(), c.rolling(60).mean()
            rsi = _rsi(c).iloc[-1]
            ml, sl = _macd(c)

            pt = 0
            if len(c) >= 60 and ma5.iloc[-1] > ma20.iloc[-1] > ma60.iloc[-1]:
                pt += 30
            elif ma5.iloc[-1] > ma20.iloc[-1]:
                pt += 15
            if not np.isnan(rsi):
                if 50 <= rsi <= 70: pt += 20
                elif 40 <= rsi < 50: pt += 10
                elif rsi > 80: pt -= 10
            if ml.iloc[-1] > sl.iloc[-1] and ml.iloc[-2] <= sl.iloc[-2]: pt += 15
            elif ml.iloc[-1] > sl.iloc[-1]: pt += 8
            vol_spike = False
            if v is not None and len(v) >= 21:
                base = v.iloc[-21:-1].mean()
                if base > 0 and v.iloc[-1] > base * 1.5:
                    pt += 10; vol_spike = True

            hi52 = c.iloc[-min(len(c), 250):].max()
            near52 = c.iloc[-1] / hi52 if hi52 > 0 else np.nan
            if near52 >= 0.97: pt += 15
            elif near52 >= 0.90: pt += 8

            ret20 = c.iloc[-1] / c.iloc[-21] - 1 if len(c) > 21 else np.nan
            rs = ret20 - idx_ret.get(mk.get(t), 0) if not np.isnan(ret20) else np.nan

            rows.append({"ticker": t, "close": c.iloc[-1], "chg_1d": c.iloc[-1] / c.iloc[-2] - 1,
                         "ret_20d": ret20, "rs_20d": rs, "near_52w": near52, "rsi": rsi,
                         "vol_spike": vol_spike, "tech_pt": pt})
        except Exception:
            continue
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["technical"] = (0.7 * _pct(df["tech_pt"]) + 0.3 * _pct(df["rs_20d"]))
    return df


def supply(universe: pd.DataFrame) -> pd.DataFrame:
    fl = _read("investor_flows.csv")
    if fl.empty:
        return pd.DataFrame(columns=["ticker", "supply", "net_20d", "streak"])
    fl["net_amount"] = pd.to_numeric(fl["net_amount"], errors="coerce").fillna(0)
    daily = fl.groupby(["ticker", "date"])["net_amount"].sum().reset_index()
    total = daily.groupby("ticker")["net_amount"].sum().rename("net_20d")

    def _streak(g):
        s = 0
        for x in g.sort_values("date", ascending=False)["net_amount"]:
            if x > 0: s += 1
            else: break
        return s
    streak = daily.groupby("ticker").apply(_streak).rename("streak")

    df = pd.concat([total, streak], axis=1).reset_index()
    cap = universe.set_index("ticker")["market_cap"]
    df["net_ratio"] = df["net_20d"] / df["ticker"].map(cap).replace(0, np.nan)
    df["supply"] = 0.75 * _pct(df["net_ratio"]) + 0.25 * _pct(df["streak"])
    return df


def event() -> pd.DataFrame:
    bb = _read("buybacks.csv")
    if bb.empty:
        return pd.DataFrame(columns=["ticker", "event", "event_label"])
    bb["decay"] = (1 - bb["days_ago"].clip(0, config.BUYBACK_LOOKBACK_DAYS) / config.BUYBACK_LOOKBACK_DAYS).clip(lower=0.4)
    bb["score"] = bb["event_raw"] * bb["decay"]
    g = bb.sort_values("score", ascending=False).groupby("ticker").first().reset_index()
    g["event_label"] = g["report_nm"].str.replace("주요사항보고서", "").str.strip("() ")
    return g[["ticker", "score", "event_label"]].rename(columns={"score": "event"})


def fundamental() -> pd.DataFrame:
    f = _read("fundamentals.csv")
    if f.empty:
        return pd.DataFrame(columns=["ticker", "fundamental"])
    for c in ["op_growth", "sales_growth", "per", "pbr", "roe"]:
        if c not in f.columns:
            f[c] = np.nan
    per_pt = pd.Series(0.0, index=f.index)
    per_pt[(f["per"] > 0) & (f["per"] <= 12)] = 100
    per_pt[(f["per"] > 12) & (f["per"] <= 25)] = 60
    per_pt[(f["per"] > 25)] = 20
    per_pt[f["per"].isna() | (f["per"] <= 0)] = 30
    f["fundamental"] = (0.4 * _pct(f["op_growth"].clip(-200, 500)) + 0.3 * _pct(f["sales_growth"].clip(-100, 300))
                        + 0.2 * per_pt + 0.1 * _pct(f["roe"]))
    return f[["ticker", "fundamental", "op_growth", "sales_growth", "per", "pbr", "roe", "frgn_rate"]]


def risk() -> pd.DataFrame:
    sh = _read("short_selling.csv")
    cr = _read("credit_ratio.csv")
    parts = []
    if not sh.empty:
        sh["short_ratio"] = pd.to_numeric(sh["short_ratio"], errors="coerce")
        sh = sh.sort_values("date")
        def _trend(g):
            r = g["short_ratio"].dropna()
            if len(r) < 8:
                return np.nan
            recent, prior = r.iloc[-5:].mean(), r.iloc[:-5].mean()
            return prior - recent  # 양수 = 공매도 비중 감소(좋음)
        tr = sh.groupby("ticker").apply(_trend).rename("short_trend")
        lvl = sh.groupby("ticker")["short_ratio"].apply(lambda x: x.iloc[-5:].mean()).rename("short_ratio")
        parts.append(pd.concat([tr, lvl], axis=1))
    if not cr.empty:
        parts.append(cr.set_index("ticker")[["credit_ratio"]])
    if not parts:
        return pd.DataFrame(columns=["ticker", "risk"])
    df = pd.concat(parts, axis=1).reset_index().rename(columns={"index": "ticker"})
    for c in ["short_trend", "short_ratio", "credit_ratio"]:
        if c not in df.columns:
            df[c] = np.nan
    df["risk"] = (0.4 * _pct(df["short_trend"]) + 0.3 * (100 - _pct(df["short_ratio"])) + 0.3 * (100 - _pct(df["credit_ratio"])))
    return df


# ---------- 합산 ----------
def compute(universe: pd.DataFrame, preliminary=False) -> pd.DataFrame:
    base = universe[~universe["excluded"]].copy()
    parts = [technical_and_price(universe), supply(universe), event(), fundamental(), risk()]
    for p in parts:
        if not p.empty:
            base = base.merge(p, on="ticker", how="left")
    for ax in config.WEIGHTS:
        if ax not in base.columns:
            base[ax] = 50.0
        base[ax] = base[ax].fillna(0.0 if ax == "event" else 50.0)

    base["total"] = sum(base[ax] * w for ax, w in config.WEIGHTS.items())
    base = base.dropna(subset=["close"]) if "close" in base.columns else base
    base = base.sort_values("total", ascending=False).reset_index(drop=True)
    base["rank"] = base.index + 1
    base["sector_pct"] = base.groupby("sector")["total"].rank(pct=True, ascending=True) * 100
    base["sector_rank"] = base.groupby("sector")["total"].rank(ascending=False, method="min").astype(int)
    base["sector_size"] = base.groupby("sector")["ticker"].transform("count")

    if not preliminary:
        os.makedirs(config.DATA_DIR, exist_ok=True)
        base.to_csv(os.path.join(config.DATA_DIR, "scores.csv"), index=False, encoding="utf-8-sig")
        print(f"[스코어링] {len(base)}종목 산정 완료")
    return base
