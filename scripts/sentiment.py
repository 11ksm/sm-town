"""
시장 심리 지수 (자체 계산)
- 한국 공포·탐욕지수 (저점 탐지형): 7개 구성지표를 각각 과거 대비 백분위(0~100)로 만들어 평균
- 과열도 지수 (고점 탐지형): 5개 구성지표 평균
수집된 1년치 종목 시세 + 지수 + 환율 + 외국인 수급으로 계산하며, 첫 실행부터 1년 히스토리를 역산합니다.
결과: docs/data/sentiment_history.csv (date, fg, heat, 구성지표별 점수)
"""
import os
import numpy as np
import pandas as pd
import config

HIST_PATH = os.path.join(config.STATE_DIR, "sentiment_history.csv")
FG_LABEL = [(25, "극단적 공포"), (45, "공포"), (55, "중립"), (75, "탐욕"), (101, "극단적 탐욕")]
HEAT_LABEL = [(25, "냉각"), (45, "낮음"), (55, "보통"), (75, "높음"), (101, "과열")]
FG_COMP = {"momentum": "시장 모멘텀", "strength": "주가 강도(신고가-신저가)", "breadth": "시장 폭(상승종목 비율)",
           "volatility": "변동성(역방향)", "risk": "위험선호(코스닥-코스피)", "fx": "원화 강세(환율 역방향)", "foreign": "외국인 순매수"}
HEAT_COMP = {"rsi": "코스피 RSI(14)", "above20": "20일선 상회 종목 비율", "rsi70": "RSI 70 초과 종목 비율",
             "ret20": "코스피 20일 수익률", "turnover": "거래대금(20일/120일)"}


def label(v, table):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "-"
    for th, name in table:
        if v < th:
            return name
    return table[-1][1]


def _pct_expanding(s: pd.Series, min_n=60) -> pd.Series:
    """각 날짜의 값을 그 날짜까지의 과거 분포 대비 백분위로 (미래 정보 미사용)"""
    vals = s.values
    out = np.full(len(vals), np.nan)
    for i in range(len(vals)):
        if i + 1 < min_n or np.isnan(vals[i]):
            continue
        past = vals[max(0, i - 260):i + 1]
        past = past[~np.isnan(past)]
        if len(past) >= min_n:
            out[i] = (past < vals[i]).mean() * 100
    return pd.Series(out, index=s.index)


def _rsi(c: pd.DataFrame, n=14):
    d = c.diff()
    up = d.clip(lower=0).rolling(n).mean()
    dn = (-d.clip(upper=0)).rolling(n).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def _load_wide():
    price_dir = os.path.join(config.DATA_DIR, "prices")
    closes, vols = {}, {}
    if not os.path.isdir(price_dir):
        return None, None
    for f in os.listdir(price_dir):
        if not f.endswith(".csv"):
            continue
        try:
            df = pd.read_csv(os.path.join(price_dir, f))
            dcol = "날짜" if "날짜" in df.columns else df.columns[0]
            idx = pd.to_datetime(df[dcol]).dt.strftime("%Y-%m-%d")
            closes[f[:-4]] = pd.Series(df["종가"].values, index=idx)
            if "거래량" in df.columns:
                vols[f[:-4]] = pd.Series((df["종가"] * df["거래량"]).values, index=idx)
        except Exception:
            continue
    C = pd.DataFrame(closes).sort_index()
    V = pd.DataFrame(vols).sort_index() if vols else None
    return C, V


def _load_series(name, col="종가"):
    p = os.path.join(config.DATA_DIR, name)
    if not os.path.exists(p):
        return None
    df = pd.read_csv(p)
    dcol = "날짜" if "날짜" in df.columns else df.columns[0]
    s = pd.Series(df[col].values, index=pd.to_datetime(df[dcol]).dt.strftime("%Y-%m-%d"))
    return s[~s.index.duplicated()].sort_index()


def compute() -> dict:
    C, V = _load_wide()
    kospi = _load_series("index_KOSPI.csv")
    kosdaq = _load_series("index_KOSDAQ.csv")
    fx = _load_series("fx_krw.csv")
    if C is None or C.empty or kospi is None:
        print("[심리] 시세 데이터 부족 → 계산 생략")
        return {}

    dates = C.index.intersection(kospi.index)
    C = C.loc[dates]
    kospi = kospi.loc[dates]
    comp = pd.DataFrame(index=dates)

    # ---- 공포·탐욕 구성지표 ----
    comp["momentum"] = _pct_expanding(kospi / kospi.rolling(125, min_periods=60).mean() - 1)
    hi = C.rolling(250, min_periods=120).max(); lo = C.rolling(250, min_periods=120).min()
    nh = (C >= hi * 0.995).sum(axis=1); nl = (C <= lo * 1.005).sum(axis=1)
    comp["strength"] = _pct_expanding((nh - nl) / (nh + nl + 1))
    comp["breadth"] = _pct_expanding((C.pct_change(20) > 0).mean(axis=1))
    rv = kospi.pct_change().rolling(20).std()
    comp["volatility"] = 100 - _pct_expanding(rv / rv.rolling(50, min_periods=30).mean())
    if kosdaq is not None:
        kq = kosdaq.reindex(dates).ffill()
        comp["risk"] = _pct_expanding(kq.pct_change(20) - kospi.pct_change(20))
    if fx is not None:
        fxr = fx.reindex(dates).ffill()
        comp["fx"] = 100 - _pct_expanding(fxr.pct_change(20))
    # 외국인 순매수 (최근 20일만 존재) : 5일 누적, ±1.5조 기준 tanh 스케일
    flp = os.path.join(config.DATA_DIR, "investor_flows.csv")
    if os.path.exists(flp):
        try:
            fl = pd.read_csv(flp, dtype={"date": str})
            if "frgn_amount" in fl.columns:
                daily = fl.groupby("date")["frgn_amount"].sum()
                daily.index = pd.to_datetime(daily.index, format="%Y%m%d").strftime("%Y-%m-%d")
                roll = daily.sort_index().rolling(5, min_periods=3).sum()
                comp["foreign"] = (50 + 50 * np.tanh(roll / 1.5e12)).reindex(dates)
        except Exception:
            pass
    fg_cols = [c for c in FG_COMP if c in comp.columns]
    comp["fg"] = comp[fg_cols].mean(axis=1, skipna=True)

    # ---- 과열도 구성지표 ----
    heat = pd.DataFrame(index=dates)
    heat["rsi"] = _pct_expanding(_rsi(kospi))
    heat["above20"] = _pct_expanding((C > C.rolling(20).mean()).mean(axis=1))
    heat["rsi70"] = _pct_expanding((_rsi(C) > 70).mean(axis=1))
    heat["ret20"] = _pct_expanding(kospi.pct_change(20))
    if V is not None:
        tv = V.reindex(dates).sum(axis=1)
        heat["turnover"] = _pct_expanding(tv.rolling(20).mean() / tv.rolling(120, min_periods=60).mean())
    heat_cols = [c for c in HEAT_COMP if c in heat.columns]
    heat["heat"] = heat[heat_cols].mean(axis=1, skipna=True)

    hist = pd.concat([comp, heat.add_prefix("h_")], axis=1)
    hist.index.name = "date"
    hist = hist.dropna(subset=["fg"]).round(1)
    hist["kospi"] = kospi.reindex(hist.index).round(2)
    if kosdaq is not None:
        hist["kosdaq"] = kosdaq.reindex(hist.index).ffill().round(2)
    hist = hist.tail(260)
    os.makedirs(config.STATE_DIR, exist_ok=True)
    hist.to_csv(HIST_PATH, encoding="utf-8-sig")

    last = hist.iloc[-1]
    def at(n):
        return float(hist["fg"].iloc[-1 - n]) if len(hist) > n else None
    def hat(n):
        return float(hist["h_heat"].iloc[-1 - n]) if len(hist) > n else None

    fg_now, heat_now = float(last["fg"]), float(last["h_heat"])
    stats = {
        "new_high": int(nh.iloc[-1]), "new_low": int(nl.iloc[-1]),
        "adv_ratio_20d": round(float((C.pct_change(20) > 0).mean(axis=1).iloc[-1]) * 100, 1),
        "above_ma20": round(float((C > C.rolling(20).mean()).mean(axis=1).iloc[-1]) * 100, 1),
        "kospi": round(float(kospi.iloc[-1]), 2), "kospi_chg": round(float(kospi.pct_change().iloc[-1]) * 100, 2),
        "kosdaq": round(float(kosdaq.reindex(dates).ffill().iloc[-1]), 2) if kosdaq is not None else None,
        "kosdaq_chg": round(float(kosdaq.reindex(dates).ffill().pct_change().iloc[-1]) * 100, 2) if kosdaq is not None else None,
        "n_stocks": int(C.shape[1]),
    }
    payload = {
        "date": hist.index[-1],
        "fg": {"now": fg_now, "label": label(fg_now, FG_LABEL), "prev": at(1), "week": at(5), "month": at(20),
               "components": [{"key": k, "name": FG_COMP[k], "score": (None if pd.isna(last[k]) else float(last[k]))} for k in fg_cols]},
        "heat": {"now": heat_now, "label": label(heat_now, HEAT_LABEL), "prev": hat(1), "week": hat(5), "month": hat(20),
                 "components": [{"key": k, "name": HEAT_COMP[k], "score": (None if pd.isna(last["h_" + k]) else float(last["h_" + k]))} for k in heat_cols]},
        "stats": stats,
        "history": [{"d": d, "fg": float(r["fg"]), "heat": float(r["h_heat"]), "k": float(r["kospi"]) if pd.notna(r["kospi"]) else None}
                    for d, r in hist.iterrows()],
        "reading": reading(fg_now, heat_now),
    }
    print(f"[심리] 한국 공포·탐욕 {fg_now:.0f}({payload['fg']['label']}) · 과열도 {heat_now:.0f}({payload['heat']['label']}) · 히스토리 {len(hist)}일")
    return payload


def reading(fg, heat) -> dict:
    """두 지수 조합 판독 (일반적 해석, 투자 권유 아님)"""
    if fg <= 25 and heat <= 45:
        return {"title": "저점 탐색 구간", "text": "공포 심리가 극단적이고 과열 신호가 없습니다. 역사적으로 분할 매수 관심이 유효했던 조합이지만, 추세 반전 확인(거래량·외국인 수급 전환)이 필요합니다."}
    if fg <= 45 and heat <= 55:
        return {"title": "공포 우세, 과열 없음", "text": "심리는 위축됐지만 지표상 과매도 극단은 아닙니다. 수급이 살아있는 종목 위주로 선별하고, 시장 전체 베팅은 서두르지 않는 구간입니다."}
    if fg >= 75 and heat >= 75:
        return {"title": "과열 경고", "text": "탐욕 심리와 과열 신호가 동시에 극단입니다. 신규 매수보다 이익 실현·비중 조절을 우선 검토하는 조합입니다."}
    if fg >= 55 and heat >= 55:
        return {"title": "상승 추세 진행", "text": "심리와 모멘텀이 함께 강합니다. 추세 추종은 유효하나 과열도가 75를 넘어가면 경계로 전환합니다."}
    if fg >= 55 and heat < 45:
        return {"title": "심리 회복, 과열 미진입", "text": "심리는 개선됐으나 지표상 과열은 아닙니다. 상승 초중반에 흔한 조합으로, 업종 확산(breadth) 여부를 확인합니다."}
    return {"title": "중립 구간", "text": "두 지수 중 하나 이상이 중립대에 있어 방향성 판단을 유보합니다. 개별 종목 스코어링 결과에 더 비중을 두는 구간입니다."}
