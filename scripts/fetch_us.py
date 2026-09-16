"""
미국시장 브리핑 데이터 수집
- 주요 지수/변동성/금리/환율 (yfinance)
- CNN Fear & Greed Index
- CNBC 주요 헤드라인 (RSS, 실패 시 생략)
- 규칙 기반 한국어 리뷰 문장
결과: data/us_market.json  (실패 항목은 None, 페이지는 계속 생성됨)
"""
import os
import json
import xml.etree.ElementTree as ET

import requests
import config

TICKERS = {
    "S&P 500": "^GSPC",
    "나스닥": "^IXIC",
    "다우": "^DJI",
    "필라델피아 반도체": "^SOX",
    "VIX": "^VIX",
    "미 10년물(%)": "^TNX",
    "원/달러": "KRW=X",
}
FNG_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
CNBC_RSS = "https://www.cnbc.com/id/100003114/device/rss/rss.html"
FNG_KO = {"extreme fear": "극단적 공포", "fear": "공포", "neutral": "중립", "greed": "탐욕", "extreme greed": "극단적 탐욕"}


def fetch_indices() -> dict:
    out = {}
    try:
        import yfinance as yf
    except Exception as e:
        print(f"[미국] yfinance 없음: {e}")
        return {k: None for k in TICKERS}
    for name, sym in TICKERS.items():
        try:
            h = yf.Ticker(sym).history(period="10d", interval="1d")
            c = h["Close"].dropna()
            if len(c) < 2:
                out[name] = None
                continue
            last, prev = float(c.iloc[-1]), float(c.iloc[-2])
            out[name] = {"last": round(last, 2), "chg": round(last - prev, 2),
                         "pct": round((last / prev - 1) * 100, 2), "date": c.index[-1].strftime("%m/%d")}
        except Exception as e:
            print(f"[미국] {name} 실패: {e}")
            out[name] = None
    return out


def fetch_fng():
    try:
        j = requests.get(FNG_URL, headers=config.REQUEST_HEADERS, timeout=10).json()["fear_and_greed"]
        return {"score": round(float(j["score"])), "label_ko": FNG_KO.get(str(j.get("rating", "")).lower(), j.get("rating", "")),
                "prev_close": round(float(j.get("previous_close", 0))), "week_ago": round(float(j.get("previous_1_week", 0))),
                "month_ago": round(float(j.get("previous_1_month", 0)))}
    except Exception as e:
        print(f"[미국] Fear&Greed 실패: {e}")
        return None


def fetch_headlines(n=4) -> list:
    try:
        xml = requests.get(CNBC_RSS, headers=config.REQUEST_HEADERS, timeout=10).text
        root = ET.fromstring(xml)
        items = root.findall(".//item")[:n]
        return [{"title": (i.findtext("title") or "").strip(), "link": (i.findtext("link") or "").strip()} for i in items]
    except Exception as e:
        print(f"[미국] 헤드라인 실패: {e}")
        return []


def make_review(idx: dict, fng) -> list:
    s = []
    core = [(k, idx[k]) for k in ("S&P 500", "나스닥", "다우") if idx.get(k)]
    if core:
        ups = sum(1 for _, v in core if v["pct"] > 0)
        avg = sum(v["pct"] for _, v in core) / len(core)
        tone = "3대 지수 동반 상승" if ups == len(core) == 3 else "3대 지수 동반 하락" if ups == 0 and len(core) == 3 else "주요 지수 상승" if ups == len(core) else "주요 지수 하락" if ups == 0 else "지수 혼조"
        mag = "강한 " if abs(avg) >= 1.5 else "" if abs(avg) >= 0.4 else "소폭 "
        s.append(f"{mag}{tone} — " + ", ".join(f"{k} {v['pct']:+.2f}%" for k, v in core))
    sox = idx.get("필라델피아 반도체")
    if sox and abs(sox["pct"]) >= 0.8:
        s.append(f"반도체지수 {sox['pct']:+.2f}% → 국내 반도체·장비 업종에 {'우호적' if sox['pct'] > 0 else '부담'}")
    vix = idx.get("VIX")
    if vix:
        v = vix["last"]
        s.append(f"VIX {v:.1f} — " + ("변동성 안정 구간" if v < 15 else "보통 수준" if v < 20 else "경계 구간, 위험자산 선호 약화" if v < 30 else "위험회피 심리 강함"))
    tnx = idx.get("미 10년물(%)")
    if tnx and abs(tnx["chg"]) >= 0.05:
        s.append(f"미 10년물 {tnx['last']:.2f}% ({tnx['chg']*100:+.0f}bp) — 금리 {'상승, 성장주 밸류에이션 부담' if tnx['chg'] > 0 else '하락, 성장주에 우호적'}")
    krw = idx.get("원/달러")
    if krw and abs(krw["pct"]) >= 0.3:
        s.append(f"원/달러 {krw['last']:,.0f}원 ({krw['pct']:+.2f}%) — 외국인 수급에 {'부정적' if krw['pct'] > 0 else '긍정적'}")
    if fng:
        d = fng["score"] - fng["prev_close"]
        s.append(f"Fear&Greed {fng['score']} '{fng['label_ko']}' (전일 대비 {d:+d})" + (" — 과열 주의" if fng["score"] >= 75 else " — 역발상 관심 구간" if fng["score"] <= 25 else ""))
    return s or ["미국 시장 데이터를 가져오지 못했습니다."]


def fetch_us():
    idx = fetch_indices()
    fng = fetch_fng()
    data = {"indices": idx, "fng": fng, "headlines": fetch_headlines(), "review": make_review(idx, fng)}
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(os.path.join(config.DATA_DIR, "us_market.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print(f"[미국] 지수 {sum(1 for v in idx.values() if v)}/{len(idx)}, F&G {'OK' if fng else '없음'}")
    for line in data["review"]:
        print("   ·", line)
    return data


if __name__ == "__main__":
    fetch_us()
