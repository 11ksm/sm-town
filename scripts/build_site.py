"""
docs/index.html 생성
스코어 결과 + 순위변동 + 신규포착 + 히스토리 성과를 JSON으로 페이지에 내장하고,
필터/검색/정렬/즐겨찾기는 브라우저 JS에서 처리합니다.
"""
import os
import json
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd
import config
import history

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(ROOT, "templates", "index.html")
KST = timezone(timedelta(hours=9))


def _f(x, nd=1):
    try:
        if x is None or (isinstance(x, float) and np.isnan(x)):
            return None
        return round(float(x), nd)
    except Exception:
        return None


def build(scores: pd.DataFrame, universe: pd.DataFrame, senti: dict = None):
    today = datetime.now(KST).strftime("%Y-%m-%d")

    hist = history.load_history()
    prev = history.previous_ranks(hist, today)
    hist = history.append_today(hist, scores, today)
    ih = history.append_index(today)
    perf = history.performance(hist, ih, scores, today)
    snapshots = history.daily_top_snapshots(hist)

    scores = scores.copy()
    scores["prev_rank"] = scores["ticker"].map(prev)
    has_prev = len(prev) > 0

    rows = []
    for _, r in scores.head(config.EMBED_N).iterrows():
        pr = r["prev_rank"]
        if not has_prev:
            change = None
        elif pd.isna(pr):
            change = "NEW"
        else:
            change = int(pr) - int(r["rank"])
        rows.append({
            "t": r["ticker"], "n": r["name"], "m": r["market"], "s": r["sector"],
            "rank": int(r["rank"]), "chg": change,
            "total": _f(r["total"]),
            "ax": [_f(r.get("supply")), _f(r.get("technical")), _f(r.get("event")), _f(r.get("fundamental")), _f(r.get("risk"))],
            "close": _f(r.get("close"), 0), "d1": _f((r.get("chg_1d") or 0) * 100, 2),
            "r20": _f((r.get("ret_20d") or 0) * 100, 1), "n52": _f((r.get("near_52w") or 0) * 100, 0),
            "streak": int(r["streak"]) if pd.notna(r.get("streak")) else 0,
            "ev": r.get("event_label") if isinstance(r.get("event_label"), str) else None,
            "evamt": _f((r.get("event_amount") or 0) / 1e8, 0) if pd.notna(r.get("event_amount")) else None,
            "evpct": _f(r.get("event_cap_pct"), 2) if pd.notna(r.get("event_cap_pct")) else None,
            "opg": _f(r.get("op_growth")), "per": _f(r.get("per")), "cr": _f(r.get("credit_ratio"), 2),
            "sr": int(r["sector_rank"]) if pd.notna(r.get("sector_rank")) else None,
            "ss": int(r["sector_size"]) if pd.notna(r.get("sector_size")) else None,
            "cap": _f(r.get("market_cap", 0) / 1e8, 0),
        })

    # 오늘의 신규 포착: 상위 TOP_N 진입했고 (전일 순위권 밖 or 20계단 이상 급등)
    new_picks = []
    if has_prev:
        for x in rows[:config.TOP_N]:
            if x["chg"] == "NEW" or (isinstance(x["chg"], int) and x["chg"] >= config.NEW_PICK_RANK_JUMP):
                new_picks.append(x["t"])

    us = None
    us_path = os.path.join(config.DATA_DIR, "us_market.json")
    if os.path.exists(us_path):
        try:
            with open(us_path, encoding="utf-8") as f:
                us = json.load(f)
        except Exception:
            us = None

    excluded = universe[universe["excluded"]]
    payload = {
        "updated": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        "date": today,
        "universe_total": int(len(universe)),
        "excluded_total": int(len(excluded)),
        "excluded_breakdown": excluded["exclude_reason"].value_counts().to_dict(),
        "scored_total": int(len(scores)),
        "top_n": config.TOP_N,
        "weights": config.WEIGHTS,
        "sectors": sorted(scores["sector"].dropna().unique().tolist()),
        "rows": rows,
        "new_picks": new_picks,
        "perf": perf,
        "snapshots": snapshots,
        "us": us,
        "senti": {"fg": senti.get("fg"), "heat": senti.get("heat"), "date": senti.get("date")} if senti else None,
    }

    with open(TEMPLATE, encoding="utf-8") as f:
        html = f.read()
    html = html.replace("__DATA__", json.dumps(payload, ensure_ascii=False))

    os.makedirs(config.DOCS_DIR, exist_ok=True)
    with open(os.path.join(config.DOCS_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
    with open(os.path.join(config.STATE_DIR, "latest.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    open(os.path.join(config.DOCS_DIR, ".nojekyll"), "a").close()

    # ---- 서브페이지: 시장심리 ----
    try:
        sp = {"updated": payload["updated"], "us": us, **(senti or {})}
        with open(os.path.join(ROOT, "templates", "sentiment.html"), encoding="utf-8") as f:
            t = f.read()
        with open(os.path.join(config.DOCS_DIR, "sentiment.html"), "w", encoding="utf-8") as f:
            f.write(t.replace("__DATA__", json.dumps(sp, ensure_ascii=False)))
        print("[사이트] sentiment.html 생성")
    except Exception as e:
        print(f"[사이트] sentiment.html 생성 실패: {e}")

    # ---- 서브페이지: 자사주 CHECK ----
    try:
        bp = build_buyback_payload(scores, universe)
        bp["updated"] = payload["updated"]
        with open(os.path.join(ROOT, "templates", "buyback.html"), encoding="utf-8") as f:
            t = f.read()
        with open(os.path.join(config.DOCS_DIR, "buyback.html"), "w", encoding="utf-8") as f:
            f.write(t.replace("__DATA__", json.dumps(bp, ensure_ascii=False)))
        print(f"[사이트] buyback.html 생성 ({bp['total']}건)")
    except Exception as e:
        print(f"[사이트] buyback.html 생성 실패: {e}")


def build_buyback_payload(scores: pd.DataFrame, universe: pd.DataFrame) -> dict:
    db_path = os.path.join(config.STATE_DIR, "buyback_db.csv")
    if not os.path.exists(db_path):
        return {"total": 0, "rows": [], "monthly": [], "summary": {}, "pending": 0}
    db = pd.read_csv(db_path, dtype={"rcept_no": str, "stock_code": str, "rcept_dt": str, "corp_code": str})
    db["amount"] = pd.to_numeric(db["amount"], errors="coerce")
    db["shares"] = pd.to_numeric(db["shares"], errors="coerce")
    db["corrected"] = db["corrected"].astype(str).str.lower().eq("true")
    cap = universe.set_index("ticker")["market_cap"] if universe is not None else pd.Series(dtype=float)
    mk = universe.set_index("ticker")["market"] if universe is not None else pd.Series(dtype=str)
    sc = scores.set_index("ticker") if scores is not None and not scores.empty else pd.DataFrame()
    db["cap_pct"] = db["amount"] / db["stock_code"].map(cap).replace(0, np.nan) * 100
    db["month"] = db["rcept_dt"].str[:6]

    today = datetime.now(KST)
    six = (today - timedelta(days=182)).strftime("%Y%m%d")
    base = db[(~db["corrected"]) & (db["rcept_dt"] >= six)]
    def summ(kinds):
        x = base[base["kind"].isin(kinds)]
        return {"amount": float(x["amount"].fillna(0).sum()), "count": int(len(x)), "corps": int(x["stock_code"].nunique()),
                "no_amount": int(x["amount"].isna().sum())}
    summary = {"acquire": summ(["취득(직접)", "취득(신탁)"]), "cancel": summ(["소각"]), "dispose": summ(["처분"]),
               "cancel_trust": summ(["신탁해지"]), "corps_total": int(base[base["kind"].isin(["취득(직접)", "취득(신탁)", "소각"])]["stock_code"].nunique())}

    monthly = []
    nc = db[~db["corrected"]]
    for m, g in nc.groupby("month"):
        monthly.append({"m": m, "acq_amt": float(g[g["kind"].isin(["취득(직접)", "취득(신탁)"])]["amount"].fillna(0).sum()),
                        "acq_n": int((g["kind"].isin(["취득(직접)", "취득(신탁)"])).sum()),
                        "can_amt": float(g[g["kind"] == "소각"]["amount"].fillna(0).sum()), "can_n": int((g["kind"] == "소각").sum()),
                        "dis_amt": float(g[g["kind"] == "처분"]["amount"].fillna(0).sum()), "dis_n": int((g["kind"] == "처분").sum())})
    monthly = sorted(monthly, key=lambda x: x["m"])[-13:]

    rows = []
    for _, r in db.sort_values("rcept_dt", ascending=False).iterrows():
        t = r["stock_code"]
        rows.append({"no": r["rcept_no"], "d": r["rcept_dt"], "t": t, "n": r["corp_name"], "k": r["kind"], "fix": bool(r["corrected"]),
                     "amt": _f(r["amount"] / 1e8, 1) if pd.notna(r["amount"]) else None,
                     "shr": _f(r["shares"], 0) if pd.notna(r["shares"]) else None,
                     "pct": _f(r["cap_pct"], 2) if pd.notna(r["cap_pct"]) else None,
                     "pp": r["purpose"] if isinstance(r["purpose"], str) else None,
                     "ps": str(r["period_start"]) if pd.notna(r["period_start"]) else None,
                     "pe": str(r["period_end"]) if pd.notna(r["period_end"]) else None,
                     "m": mk.get(t), "cap": _f(cap.get(t, 0) / 1e8, 0) if t in cap.index else None,
                     "rank": int(sc.at[t, "rank"]) if t in sc.index else None,
                     "total": _f(sc.at[t, "total"]) if t in sc.index else None})
    return {"total": int(len(db)), "rows": rows, "monthly": monthly, "summary": summary,
            "pending": int((db["detail_ok"].astype(str).str.lower() != "true").sum()),
            "since": db["rcept_dt"].min() if len(db) else None}
    print(f"[사이트] docs/index.html 생성 ({len(rows)}종목 내장, 신규포착 {len(new_picks)}종목)")
