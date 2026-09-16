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


def build(scores: pd.DataFrame, universe: pd.DataFrame):
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
    print(f"[사이트] docs/index.html 생성 ({len(rows)}종목 내장, 신규포착 {len(new_picks)}종목)")
