"""
DART Open API - 자기주식 취득/소각/처분 공시 수집
GitHub Secrets에 DART_API_KEY 등록 필요 (opendart.fss.or.kr 무료 발급)
"""
import os
import time
from datetime import datetime, timedelta

import pandas as pd
import requests
import config

URL = "https://opendart.fss.or.kr/api/list.json"
KEYWORD_WEIGHTS = [("소각", 100), ("취득", 80), ("처분", 25)]


def _weight(report_nm: str) -> int:
    if "자기주식" not in report_nm and "자사주" not in report_nm:
        return 0
    for kw, w in KEYWORD_WEIGHTS:
        if kw in report_nm:
            return w
    return 0


def fetch_buybacks() -> pd.DataFrame:
    cols = ["ticker", "corp_name", "report_nm", "rcept_dt", "event_raw", "days_ago"]
    os.makedirs(config.DATA_DIR, exist_ok=True)
    path = os.path.join(config.DATA_DIR, "buybacks.csv")

    if not config.DART_API_KEY:
        print("[DART] DART_API_KEY 미설정 → 재료 축 건너뜀 (0점 처리)")
        pd.DataFrame(columns=cols).to_csv(path, index=False, encoding="utf-8-sig")
        return pd.DataFrame(columns=cols)

    today = datetime.today()
    start = today - timedelta(days=config.BUYBACK_LOOKBACK_DAYS)
    rows, page = [], 1
    while True:
        try:
            r = requests.get(URL, params={
                "crtfc_key": config.DART_API_KEY,
                "bgn_de": start.strftime("%Y%m%d"),
                "end_de": today.strftime("%Y%m%d"),
                "pblntf_ty": "B",          # 주요사항보고 (자기주식 결정은 여기에 포함)
                "page_no": page, "page_count": 100,
            }, timeout=10).json()
        except Exception as e:
            print(f"[DART] 요청 실패: {e}")
            break
        if r.get("status") != "000":
            break
        for it in r.get("list", []):
            w = _weight(it.get("report_nm", ""))
            code = (it.get("stock_code") or "").strip()
            if w and code:
                rcept = it.get("rcept_dt", "")
                try:
                    days_ago = (today - datetime.strptime(rcept, "%Y%m%d")).days
                except Exception:
                    days_ago = config.BUYBACK_LOOKBACK_DAYS
                rows.append({"ticker": code.zfill(6), "corp_name": it.get("corp_name"), "report_nm": it.get("report_nm"),
                             "rcept_dt": rcept, "event_raw": w, "days_ago": days_ago})
        if page >= int(r.get("total_page", 1)):
            break
        page += 1
        time.sleep(0.2)

    df = pd.DataFrame(rows, columns=cols)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"[DART] 자사주 관련 공시 {len(df)}건")
    return df


if __name__ == "__main__":
    fetch_buybacks()
