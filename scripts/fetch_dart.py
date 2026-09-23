"""
DART 자사주 공시 누적 DB (docs/data/buyback_db.csv)
- 공시 목록(list.json)에서 자기주식 취득/소각/처분/신탁 공시를 최근 BUYBACK_DB_DAYS 일 범위로 수집
- 금액·수량·목적은 DART 구조화 API(취득/처분/신탁체결/해지)로 보강, 소각은 공시 원문에서 정규식 추출
- 매일 신규 공시만 추가하고, 미수집 상세는 매 실행마다 BUYBACK_DETAIL_PER_RUN 건씩 채움 (첫 실행은 여러 날에 걸쳐 완성)
- 스코어링용 data/buybacks.csv 도 함께 생성 (최근 BUYBACK_LOOKBACK_DAYS 일)
"""
import io
import os
import re
import time
import zipfile
from datetime import datetime, timedelta

import pandas as pd
import requests
import config

LIST_URL = "https://opendart.fss.or.kr/api/list.json"
DOC_URL = "https://opendart.fss.or.kr/api/document.xml"
API = {
    "취득(직접)": "https://opendart.fss.or.kr/api/tsstkAqDecsn.json",
    "처분": "https://opendart.fss.or.kr/api/tsstkDpDecsn.json",
    "취득(신탁)": "https://opendart.fss.or.kr/api/tsstkAqTrctrCnsDecsn.json",
    "신탁해지": "https://opendart.fss.or.kr/api/tsstkAqTrctrCcDecsn.json",
}
DB_PATH = os.path.join(config.STATE_DIR, "buyback_db.csv")
DB_COLS = ["rcept_no", "rcept_dt", "corp_code", "corp_name", "stock_code", "kind", "corrected",
           "amount", "shares", "purpose", "method", "period_start", "period_end", "detail_ok"]
KIND_WEIGHT = {"소각": 100, "취득(직접)": 85, "취득(신탁)": 75, "처분": 25, "신탁해지": 15}


def classify(report_nm: str):
    n = report_nm.replace(" ", "")
    if "자기주식" not in n and "자사주" not in n:
        return None
    if "소각" in n: return "소각"
    if "신탁계약해지" in n or ("신탁" in n and "해지" in n): return "신탁해지"
    if "신탁" in n: return "취득(신탁)"
    if "취득" in n: return "취득(직접)"
    if "처분" in n: return "처분"
    return None


def _num(x):
    try:
        v = float(str(x).replace(",", "").strip())
        return v if v > 0 else None
    except Exception:
        return None


def load_db() -> pd.DataFrame:
    if os.path.exists(DB_PATH):
        db = pd.read_csv(DB_PATH, dtype={"rcept_no": str, "corp_code": str, "stock_code": str, "rcept_dt": str,
                                          "period_start": str, "period_end": str})
        for c in DB_COLS:
            if c not in db.columns:
                db[c] = None
        return db[DB_COLS]
    return pd.DataFrame(columns=DB_COLS)


def fetch_list(bgn: str, end: str) -> pd.DataFrame:
    rows, page = [], 1
    while True:
        try:
            r = requests.get(LIST_URL, params={"crtfc_key": config.DART_API_KEY, "bgn_de": bgn, "end_de": end,
                                               "pblntf_ty": "B", "page_no": page, "page_count": 100}, timeout=15).json()
        except Exception as e:
            print(f"[DART] 목록 요청 실패: {e}")
            break
        if r.get("status") != "000":
            break
        for it in r.get("list", []):
            kind = classify(it.get("report_nm", ""))
            code = (it.get("stock_code") or "").strip()
            if kind and code:
                rows.append({"rcept_no": it["rcept_no"], "rcept_dt": it["rcept_dt"], "corp_code": it["corp_code"],
                             "corp_name": it["corp_name"], "stock_code": code.zfill(6), "kind": kind,
                             "corrected": "정정" in it.get("report_nm", "")})
        if page >= int(r.get("total_page", 1)):
            break
        page += 1
        time.sleep(0.15)
    return pd.DataFrame(rows)


def _extract_struct(rec: dict, kind: str) -> dict:
    """구조화 API 응답 1건에서 금액/수량/목적/기간 추출 (필드명 차이를 흡수)"""
    out = {}
    if kind in ("취득(신탁)", "신탁해지"):
        for k in ("ctr_prc", "ctr_prc_bfcc"):
            if rec.get(k):
                out["amount"] = _num(rec[k]); break
        out["period_start"], out["period_end"] = rec.get("ctr_pd_bgd"), rec.get("ctr_pd_edd")
        out["purpose"] = rec.get("ctr_pp") or rec.get("aq_pp")
        out["method"] = rec.get("ctr_cns_int") or "신탁"
    else:
        amt = sum((_num(v) or 0) for k, v in rec.items() if "prc" in k and ("ostk" in k or "estk" in k))
        shr = sum((_num(v) or 0) for k, v in rec.items() if re.search(r"(aqpln|dppln)_stk_(ostk|estk)", k))
        out["amount"] = amt or None
        out["shares"] = shr or None
        out["purpose"] = rec.get("aq_pp") or rec.get("dp_pp")
        out["method"] = rec.get("aq_mth") or rec.get("dp_mth")
        out["period_start"] = rec.get("aqexpd_bgd") or rec.get("dpprpd_bgd")
        out["period_end"] = rec.get("aqexpd_edd") or rec.get("dpprpd_edd")
    return out


def fill_struct_details(db: pd.DataFrame, budget: int) -> pd.DataFrame:
    """취득/처분/신탁 공시의 금액 등 보강. 기업별로 기간 조회 1회 → 해당 기업 모든 공시 매칭"""
    need = db[(db["detail_ok"] != True) & (db["kind"].isin(API.keys()))]
    if need.empty:
        return db
    corps = need.groupby(["corp_code", "kind"]).agg(bgn=("rcept_dt", "min"), end=("rcept_dt", "max")).reset_index()
    done = 0
    for _, c in corps.iterrows():
        if done >= budget:
            break
        try:
            r = requests.get(API[c["kind"]], params={"crtfc_key": config.DART_API_KEY, "corp_code": c["corp_code"],
                                                    "bgn_de": c["bgn"], "end_de": c["end"]}, timeout=15).json()
            recs = r.get("list", []) if r.get("status") == "000" else []
        except Exception:
            recs = []
        by_rcept = {rec.get("rcept_no"): rec for rec in recs}
        idx = db[(db["corp_code"] == c["corp_code"]) & (db["kind"] == c["kind"]) & (db["detail_ok"] != True)].index
        for i in idx:
            rec = by_rcept.get(db.at[i, "rcept_no"])
            if rec:
                for k, v in _extract_struct(rec, c["kind"]).items():
                    db.at[i, k] = v
            db.at[i, "detail_ok"] = True   # 매칭 실패도 재시도 방지 (정정공시 등)
        done += 1
        time.sleep(0.12)
    print(f"[DART] 구조화 상세 {done}건 기업 조회 (남은 기업 {max(0, len(corps) - done)})")
    return db


def fill_cancel_details(db: pd.DataFrame, budget: int) -> pd.DataFrame:
    """소각 공시: 원문에서 소각 예정 금액/주식수 추출"""
    need = db[(db["detail_ok"] != True) & (db["kind"] == "소각")]
    done = 0
    for i, row in need.iterrows():
        if done >= budget:
            break
        try:
            r = requests.get(DOC_URL, params={"crtfc_key": config.DART_API_KEY, "rcept_no": row["rcept_no"]}, timeout=20)
            z = zipfile.ZipFile(io.BytesIO(r.content))
            txt = ""
            for n in z.namelist():
                txt += z.read(n).decode("utf-8", errors="ignore")
            txt = re.sub(r"<[^>]+>", " ", txt)
            m = re.search(r"소각\s*예정\s*금액[^0-9]{0,40}?([\d,]{4,})", txt)
            s = re.search(r"소각할?\s*주식의?\s*(?:종류\s*와?\s*)?수[^0-9]{0,60}?([\d,]{3,})", txt)
            p = re.search(r"소각\s*(?:목적|사유)[^가-힣]{0,20}([가-힣A-Za-z0-9 ,()·]{4,40})", txt)
            db.at[i, "amount"] = _num(m.group(1)) if m else None
            db.at[i, "shares"] = _num(s.group(1)) if s else None
            db.at[i, "purpose"] = p.group(1).strip() if p else "주식 소각"
            db.at[i, "method"] = "소각"
        except Exception:
            pass
        db.at[i, "detail_ok"] = True
        done += 1
        time.sleep(0.15)
    if done:
        print(f"[DART] 소각 원문 상세 {done}건")
    return db


def fetch_buybacks() -> pd.DataFrame:
    os.makedirs(config.DATA_DIR, exist_ok=True)
    os.makedirs(config.STATE_DIR, exist_ok=True)
    out_path = os.path.join(config.DATA_DIR, "buybacks.csv")
    cols = ["ticker", "corp_name", "report_nm", "rcept_dt", "event_raw", "days_ago", "amount", "kind", "rcept_no"]

    if not config.DART_API_KEY:
        print("[DART] DART_API_KEY 미설정 → 재료 축 0점 처리")
        pd.DataFrame(columns=cols).to_csv(out_path, index=False, encoding="utf-8-sig")
        return pd.DataFrame(columns=cols)

    db = load_db()
    today = datetime.today()
    if db.empty:
        bgn = (today - timedelta(days=config.BUYBACK_DB_DAYS)).strftime("%Y%m%d")
        print(f"[DART] 첫 수집: {bgn} ~ 오늘 공시 목록 (수 분 소요)")
    else:
        last = pd.to_datetime(db["rcept_dt"].max(), format="%Y%m%d", errors="coerce")
        bgn = (last - timedelta(days=3)).strftime("%Y%m%d") if pd.notna(last) else (today - timedelta(days=30)).strftime("%Y%m%d")
    new = fetch_list(bgn, today.strftime("%Y%m%d"))
    if not new.empty:
        new = new[~new["rcept_no"].isin(db["rcept_no"])]
        new["detail_ok"] = False
        for c in DB_COLS:
            if c not in new.columns:
                new[c] = None
        db = pd.concat([db, new[DB_COLS]], ignore_index=True)
        print(f"[DART] 신규 공시 {len(new)}건 추가 (DB 총 {len(db)}건)")

    # 상세 보강 (실행당 예산)
    db = fill_struct_details(db, config.BUYBACK_DETAIL_PER_RUN)
    db = fill_cancel_details(db, max(50, config.BUYBACK_DETAIL_PER_RUN // 3))

    # 오래된 것 정리 & 저장
    cutoff = (today - timedelta(days=config.BUYBACK_DB_DAYS)).strftime("%Y%m%d")
    db = db[db["rcept_dt"].astype(str) >= cutoff].sort_values("rcept_dt", ascending=False).reset_index(drop=True)
    db.to_csv(DB_PATH, index=False, encoding="utf-8-sig")
    pending = int((db["detail_ok"] != True).sum())
    print(f"[DART] DB {len(db)}건 저장, 상세 미수집 {pending}건")

    # 스코어링용 최근 N일
    recent_cut = (today - timedelta(days=config.BUYBACK_LOOKBACK_DAYS)).strftime("%Y%m%d")
    rec = db[(db["rcept_dt"].astype(str) >= recent_cut) & (~db["corrected"].astype(str).str.lower().eq("true"))].copy()
    rec["event_raw"] = rec["kind"].map(KIND_WEIGHT).fillna(0)
    rec["days_ago"] = (today - pd.to_datetime(rec["rcept_dt"], format="%Y%m%d", errors="coerce")).dt.days
    rec = rec.rename(columns={"stock_code": "ticker"})
    rec["report_nm"] = rec["kind"].map({"소각": "자기주식소각결정", "취득(직접)": "자기주식취득결정",
                                       "취득(신탁)": "자기주식취득신탁계약체결결정", "처분": "자기주식처분결정",
                                       "신탁해지": "자기주식취득신탁계약해지결정"})
    rec[cols].to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"[DART] 최근 {config.BUYBACK_LOOKBACK_DAYS}일 스코어링 대상 {len(rec)}건")
    return rec[cols]


if __name__ == "__main__":
    fetch_buybacks()
