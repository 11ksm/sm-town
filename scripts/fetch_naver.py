"""
네이버금융 데이터 수집
1) 시가총액 페이지(항목 커스터마이즈)로 전 종목의 실적/밸류 지표 일괄 수집
   - 영업이익증가율, 매출액증가율, PER, PBR, ROE, 외국인비율
2) 신용비율은 종목별 페이지에서만 제공 → 예비 스코어 상위 K종목만 개별 조회

주의: 네이버 페이지 구조가 바뀌면 파싱이 실패할 수 있습니다. 실패 시 해당 축은 중립(50점) 처리되어
파이프라인 전체는 계속 동작합니다.
"""
import os
import re
import time
from io import StringIO

import pandas as pd
import requests
import config

FIELD_SUBMIT = "https://finance.naver.com/sise/field_submit.naver"
MARKET_SUM = "https://finance.naver.com/sise/sise_market_sum.naver"
ITEM_MAIN = "https://finance.naver.com/item/main.naver?code={code}"

# 네이버 항목 ID (최대 6개 선택 가능)
FIELDS = ["operating_profit_increasing_rate", "sales_increasing_rate", "per", "pbr", "roe", "frgn_rate"]
COL_MAP = {
    "영업이익증가율": "op_growth",
    "매출액증가율": "sales_growth",
    "PER": "per",
    "PBR": "pbr",
    "ROE": "roe",
    "외국인비율": "frgn_rate",
}
SOSOK = {"KOSPI": 0, "KOSDAQ": 1}


def _to_num(s):
    return pd.to_numeric(s.astype(str).str.replace(",", "").str.replace("%", "").replace({"N/A": None, "-": None}), errors="coerce")


def fetch_fundamentals() -> pd.DataFrame:
    sess = requests.Session()
    sess.headers.update(config.REQUEST_HEADERS)
    rows = []
    for market, sosok in SOSOK.items():
        if market not in config.MARKETS:
            continue
        try:
            sess.post(FIELD_SUBMIT, data={"menu": "market_sum", "returnUrl": f"{MARKET_SUM}?sosok={sosok}", "fieldIds": FIELDS}, timeout=10)
        except Exception as e:
            print(f"[네이버] 항목 설정 실패: {e}")
        page = 1
        while page <= 80:
            try:
                html = sess.get(f"{MARKET_SUM}?sosok={sosok}&page={page}", timeout=10).text
            except Exception:
                break
            codes = re.findall(r'href="/item/main\.naver\?code=(\d{6})"', html)
            if not codes:
                break
            try:
                tables = pd.read_html(StringIO(html))
                tbl = next(t for t in tables if "종목명" in t.columns)
            except Exception:
                break
            tbl = tbl.dropna(subset=["종목명"]).reset_index(drop=True)
            codes = list(dict.fromkeys(codes))  # 순서 유지 중복 제거
            if len(codes) != len(tbl):
                codes = codes[:len(tbl)]
            tbl = tbl.iloc[:len(codes)].copy()
            tbl["ticker"] = codes
            keep = {k: v for k, v in COL_MAP.items() if k in tbl.columns}
            sub = tbl[["ticker"] + list(keep.keys())].rename(columns=keep)
            rows.append(sub)
            page += 1
            time.sleep(config.REQUEST_SLEEP)
        print(f"[네이버] {market} {page - 1}페이지 수집")

    if not rows:
        print("[네이버] 재무지표 수집 실패 → 실적 축 중립 처리")
        return pd.DataFrame(columns=["ticker"] + list(COL_MAP.values()))

    df = pd.concat(rows, ignore_index=True).drop_duplicates("ticker")
    for c in COL_MAP.values():
        if c in df.columns:
            df[c] = _to_num(df[c])
    os.makedirs(config.DATA_DIR, exist_ok=True)
    df.to_csv(os.path.join(config.DATA_DIR, "fundamentals.csv"), index=False, encoding="utf-8-sig")
    print(f"[네이버] 재무지표 {len(df)}종목 완료")
    return df


def fetch_credit_ratio(tickers) -> pd.DataFrame:
    """종목별 메인 페이지에서 신용비율(%) 추출"""
    sess = requests.Session()
    sess.headers.update(config.REQUEST_HEADERS)
    out = []
    pat = re.compile(r"신용비율.*?<em>\s*([\d.,]+)\s*%?", re.S)
    for i, t in enumerate(tickers):
        try:
            html = sess.get(ITEM_MAIN.format(code=t), timeout=10).text
            m = pat.search(html)
            val = float(m.group(1).replace(",", "")) if m else None
        except Exception:
            val = None
        out.append({"ticker": t, "credit_ratio": val})
        time.sleep(config.REQUEST_SLEEP)
        if i % 100 == 0 and i > 0:
            print(f"  신용비율 진행 {i}/{len(tickers)}")
    df = pd.DataFrame(out)
    df.to_csv(os.path.join(config.DATA_DIR, "credit_ratio.csv"), index=False, encoding="utf-8-sig")
    ok = df["credit_ratio"].notna().sum()
    print(f"[네이버] 신용비율 {ok}/{len(df)}종목 확보")
    return df


if __name__ == "__main__":
    fetch_fundamentals()
