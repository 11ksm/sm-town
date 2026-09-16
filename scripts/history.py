"""
히스토리 관리 (docs/data/ 에 저장 → 커밋되어 다음 실행에서도 유지됨)
- rank_history.csv : 날짜별 상위 종목 순위/점수/종가
- 전일 순위와 비교하여 순위변동/신규진입 계산
- 과거 N거래일 전 상위 10종목의 이후 수익률 vs 지수 (성과 검증)
"""
import os
from datetime import datetime

import numpy as np
import pandas as pd
import config

HIST_PATH = os.path.join(config.STATE_DIR, "rank_history.csv")
INDEX_HIST = os.path.join(config.STATE_DIR, "index_history.csv")


def load_history() -> pd.DataFrame:
    if os.path.exists(HIST_PATH):
        h = pd.read_csv(HIST_PATH, dtype={"ticker": str, "date": str})
        return h
    return pd.DataFrame(columns=["date", "ticker", "name", "rank", "total", "close"])


def previous_ranks(hist: pd.DataFrame, today: str) -> pd.Series:
    prev_dates = sorted(d for d in hist["date"].unique() if d < today)
    if not prev_dates:
        return pd.Series(dtype=float)
    last = hist[hist["date"] == prev_dates[-1]]
    return last.set_index("ticker")["rank"]


def append_today(hist: pd.DataFrame, scores: pd.DataFrame, today: str) -> pd.DataFrame:
    top = scores.head(config.HISTORY_TRACK_N)[["ticker", "name", "rank", "total", "close"]].copy()
    top.insert(0, "date", today)
    hist = hist[hist["date"] != today]
    hist = pd.concat([hist, top], ignore_index=True)
    keep = sorted(hist["date"].unique())[-config.HISTORY_KEEP_DAYS:]
    hist = hist[hist["date"].isin(keep)]
    os.makedirs(config.STATE_DIR, exist_ok=True)
    hist.to_csv(HIST_PATH, index=False, encoding="utf-8-sig")
    return hist


def _index_close_today():
    p = os.path.join(config.DATA_DIR, "index_KOSPI.csv")
    if os.path.exists(p):
        idx = pd.read_csv(p)
        if "종가" in idx.columns and len(idx):
            return float(idx["종가"].iloc[-1])
    return np.nan


def append_index(today: str) -> pd.DataFrame:
    v = _index_close_today()
    if os.path.exists(INDEX_HIST):
        ih = pd.read_csv(INDEX_HIST, dtype={"date": str})
    else:
        ih = pd.DataFrame(columns=["date", "kospi"])
    if not np.isnan(v):
        ih = ih[ih["date"] != today]
        ih = pd.concat([ih, pd.DataFrame([{"date": today, "kospi": v}])], ignore_index=True)
        ih = ih.sort_values("date").tail(config.HISTORY_KEEP_DAYS)
        ih.to_csv(INDEX_HIST, index=False, encoding="utf-8-sig")
    return ih


def performance(hist: pd.DataFrame, ih: pd.DataFrame, scores: pd.DataFrame, today: str) -> list:
    """N거래일 전 상위10 종목이 오늘까지 낸 평균 수익률 vs 코스피"""
    dates = sorted(hist["date"].unique())
    if today not in dates:
        return []
    ti = dates.index(today)
    cur_close = scores.set_index("ticker")["close"] if "close" in scores.columns else pd.Series(dtype=float)
    idx = ih.set_index("date")["kospi"] if not ih.empty else pd.Series(dtype=float)
    out = []
    for h in config.PERF_HORIZONS:
        if ti - h < 0:
            out.append({"horizon": h, "date": None, "top10_ret": None, "kospi_ret": None, "hit_rate": None, "n": 0})
            continue
        d = dates[ti - h]
        past = hist[(hist["date"] == d) & (hist["rank"] <= 10)]
        rets = []
        for _, r in past.iterrows():
            c0, c1 = r["close"], cur_close.get(r["ticker"], np.nan)
            if pd.notna(c0) and pd.notna(c1) and c0 > 0:
                rets.append(c1 / c0 - 1)
        k_ret = None
        if d in idx.index and today in idx.index and idx[d] > 0:
            k_ret = float(idx[today] / idx[d] - 1)
        out.append({
            "horizon": h, "date": d,
            "top10_ret": float(np.mean(rets)) if rets else None,
            "kospi_ret": k_ret,
            "hit_rate": float(np.mean([x > 0 for x in rets])) if rets else None,
            "n": len(rets),
        })
    return out


def daily_top_snapshots(hist: pd.DataFrame, n_days=15, top=10) -> list:
    """히스토리 탭용: 최근 n일간 일별 상위 종목 목록"""
    out = []
    for d in sorted(hist["date"].unique())[-n_days:][::-1]:
        day = hist[(hist["date"] == d) & (hist["rank"] <= top)].sort_values("rank")
        out.append({"date": d, "items": [{"ticker": r["ticker"], "name": r["name"], "rank": int(r["rank"]),
                                          "total": round(float(r["total"]), 1)} for _, r in day.iterrows()]})
    return out
