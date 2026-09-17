"""
수급 데이터 수집 (네이버 모바일 API — GitHub 서버에서 접근 가능한 경로)
KRX 정보데이터시스템은 해외 IP를 차단하므로 pykrx 수급 함수는 사용하지 않습니다.

API: https://m.stock.naver.com/api/stock/{code}/trend?pageSize=N
     → 일자별 종가, 외국인 순매수량, 기관 순매수량
     순매수금액 ≈ (외국인 순매수량 + 기관 순매수량) × 종가
"""
import os
import time

import pandas as pd
import requests
import config

TREND_URL = "https://m.stock.naver.com/api/stock/{code}/trend?pageSize={n}"
MOBILE_HEADERS = {**config.REQUEST_HEADERS, "Referer": "https://m.stock.naver.com/", "Accept": "application/json"}


def _num(x):
    try:
        return float(str(x).replace(",", "").replace("+", ""))
    except Exception:
        return 0.0


def _probe(sess) -> bool:
    try:
        r = sess.get(TREND_URL.format(code="005930", n=3), timeout=10)
        j = r.json()
        return isinstance(j, list) and len(j) > 0 and ("bizdate" in j[0] or "bizDate" in j[0])
    except Exception as e:
        print(f"[수급] 모바일 API 접근 실패: {e}")
        return False


def fetch_investor_flows(universe: pd.DataFrame = None) -> pd.DataFrame:
    if universe is None:
        universe = pd.read_csv(os.path.join(config.DATA_DIR, "universe.csv"), dtype={"ticker": str})
    tickers = universe.loc[~universe["excluded"], "ticker"].tolist()

    sess = requests.Session()
    sess.headers.update(MOBILE_HEADERS)
    if not _probe(sess):
        print("[수급] 데이터 없음 → 수급 축 중립 처리")
        return pd.DataFrame()

    rows, failed = [], 0
    for i, t in enumerate(tickers):
        try:
            j = sess.get(TREND_URL.format(code=t, n=config.INVESTOR_LOOKBACK_DAYS), timeout=10).json()
            for it in j:
                date = str(it.get("bizdate") or it.get("bizDate") or "")
                if not date:
                    continue
                close = _num(it.get("closePrice"))
                fq = _num(it.get("foreignerPureBuyQuant"))
                oq = _num(it.get("organPureBuyQuant"))
                rows.append({"ticker": t, "date": date, "net_amount": (fq + oq) * close,
                             "frgn_amount": fq * close, "inst_amount": oq * close})
        except Exception:
            failed += 1
        if i % 500 == 0 and i > 0:
            print(f"  수급 진행 {i}/{len(tickers)}")
        time.sleep(0.05)

    if not rows:
        print("[수급] 데이터 없음")
        return pd.DataFrame()
    out = pd.DataFrame(rows)
    os.makedirs(config.DATA_DIR, exist_ok=True)
    out.to_csv(os.path.join(config.DATA_DIR, "investor_flows.csv"), index=False, encoding="utf-8-sig")
    print(f"[수급] {out['ticker'].nunique()}종목 × {out['date'].nunique()}거래일 수집 (실패 {failed})")
    return out


def fetch_short_selling():
    """KRX 공매도 데이터는 해외 IP 차단으로 수집 불가. 리스크 축은 가격 기반 지표(변동성·낙폭·유동성)로 대체."""
    print("[공매도] KRX 해외 접속 차단으로 생략 → 리스크 축은 변동성·낙폭·유동성으로 산정")
    return pd.DataFrame()


if __name__ == "__main__":
    fetch_investor_flows()
