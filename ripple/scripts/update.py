#!/usr/bin/env python3
"""VAI-RIPPLE 가격 갱신 + 백테스트 엔진 (표준 라이브러리만 사용).

입력 : ripple/data/config.json, events.json(과거 사건), trades.json(실전 가상매수)
출력 : ripple/data/backtest.json, portfolio.json

규칙 : 종목당 budget 원 매수 → 고가가 목표가(+targetPct%) 도달 시 익절,
       저가가 손절가(stopPct%) 도달 시 손절(같은 날 둘 다면 손절 우선),
       매수일로부터 달력 maxDays 일 경과 후 첫 거래일 종가에 기간만료 매도.
"""
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
FIXTURE = os.environ.get("RIPPLE_FIXTURE")  # 테스트용: <dir>/<code>.json
KST = dt.timezone(dt.timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}


def now_kst():
    forced = os.environ.get("RIPPLE_NOW")
    if forced:
        return dt.datetime.fromisoformat(forced).replace(tzinfo=KST)
    return dt.datetime.now(KST)


def load(name, default=None):
    path = os.path.join(DATA, name)
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def dump(name, obj):
    with open(os.path.join(DATA, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.write("\n")


def http(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_naver(code):
    raw = http(f"https://fchart.stock.naver.com/sise.nhn?symbol={code}&timeframe=day&count=1000&requestType=0")
    text = None
    for enc in ("euc-kr", "cp949", "utf-8"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("utf-8", "replace")
    text = re.sub(r"<\?xml[^>]*\?>", "", text).strip()
    root = ET.fromstring(text)
    cd = root.find(".//chartdata")
    name = cd.get("name") if cd is not None else None
    bars = []
    for it in root.iter("item"):
        p = it.get("data", "").split("|")
        if len(p) < 6:
            continue
        try:
            d = f"{p[0][:4]}-{p[0][4:6]}-{p[0][6:8]}"
            o, h, l, c, v = (float(x) for x in p[1:6])
        except ValueError:
            continue
        if c > 0:
            bars.append({"d": d, "o": o or c, "h": h or c, "l": l or c, "c": c, "v": v})
    return name, bars


def fetch_yahoo(code):
    start = int(dt.datetime(2023, 1, 1).timestamp())
    end = int(time.time()) + 86400
    for suffix in (".KS", ".KQ"):
        try:
            raw = http(f"https://query1.finance.yahoo.com/v8/finance/chart/{code}{suffix}"
                       f"?period1={start}&period2={end}&interval=1d")
            res = json.loads(raw)["chart"]["result"][0]
            q = res["indicators"]["quote"][0]
            bars = []
            for i, ts in enumerate(res.get("timestamp") or []):
                c = q["close"][i]
                if not c:
                    continue
                d = dt.datetime.fromtimestamp(ts, KST).strftime("%Y-%m-%d")
                bars.append({"d": d, "o": q["open"][i] or c, "h": q["high"][i] or c,
                             "l": q["low"][i] or c, "c": c, "v": q["volume"][i] or 0})
            if bars:
                return None, bars
        except Exception:
            continue
    return None, []


_cache = {}


def get_series(code):
    """(가격소스의 종목명, 일봉 리스트, 소스명) — 실패 시 (None, [], 'none')."""
    if code in _cache:
        return _cache[code]
    name, bars, src = None, [], "none"
    if FIXTURE:
        p = os.path.join(FIXTURE, f"{code}.json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                fx = json.load(f)
            name, bars, src = fx.get("name"), fx["bars"], "fixture"
    else:
        for fn, label in ((fetch_naver, "naver"), (fetch_yahoo, "yahoo")):
            try:
                name, bars = fn(code)
            except Exception as e:  # noqa: BLE001
                print(f"  [{label}] {code} 실패: {e}", file=sys.stderr)
                name, bars = None, []
            if bars:
                src = label
                break
        time.sleep(0.25)
    bars.sort(key=lambda b: b["d"])
    # 장중(15:40 이전)에는 오늘 봉이 미확정이므로 제외
    now = now_kst()
    today = now.strftime("%Y-%m-%d")
    if bars and bars[-1]["d"] == today and (now.hour, now.minute) < (15, 40):
        bars = bars[:-1]
    _cache[code] = (name, bars, src)
    return _cache[code]


def norm(s):
    return re.sub(r"[\s\-_.()㈜]|주식회사", "", (s or "")).lower()


def name_ok(expected, actual):
    if not actual:  # 소스가 이름을 주지 않으면 검증 불가 → 통과시키되 표시
        return None
    a, b = norm(expected), norm(actual)
    return bool(a) and (a in b or b in a)


def simulate(bars, start, strict_after, cfg):
    """start 이후(strict_after=True면 다음 거래일부터) 첫 거래일 종가 매수."""
    budget, tp, sp, maxd = cfg["budget"], cfg["targetPct"], cfg["stopPct"], cfg["maxDays"]
    idx = next((i for i, b in enumerate(bars) if (b["d"] > start if strict_after else b["d"] >= start)), None)
    if idx is None:
        return {"status": "매수대기"}
    eb = bars[idx]
    entry = eb["c"]
    qty = int(budget // entry)
    if qty <= 0:
        return {"status": "매수불가", "entryDate": eb["d"], "entry": entry}
    target, stop = entry * (1 + tp / 100), entry * (1 + sp / 100)
    deadline = (dt.date.fromisoformat(eb["d"]) + dt.timedelta(days=maxd)).isoformat()
    r = {"entryDate": eb["d"], "entry": entry, "qty": qty, "invested": round(qty * entry),
         "target": round(target, 1), "stop": round(stop, 1), "deadline": deadline, "status": "보유"}
    peak, trough, wpeak = entry, entry, entry
    exit_px = exit_d = None
    for b in bars[idx + 1:]:
        if b["d"] <= deadline:
            wpeak = max(wpeak, b["h"])  # 손절과 무관한 86일 창 최고가
        if exit_px is not None:
            if b["d"] > deadline:
                break
            continue
        peak, trough = max(peak, b["h"]), min(trough, b["l"])
        if b["l"] <= stop:
            r["status"], exit_px, exit_d = "손절", min(stop, b["o"]), b["d"]
        elif b["h"] >= target:
            r["status"], exit_px, exit_d = "익절", max(target, b["o"]), b["d"]
        elif b["d"] >= deadline:
            r["status"], exit_px, exit_d = "기간만료", b["c"], b["d"]
    last = bars[-1]
    cur = exit_px if exit_px is not None else last["c"]
    r.update({
        "exitDate": exit_d, "exit": round(exit_px, 1) if exit_px is not None else None,
        "last": last["c"], "lastDate": last["d"],
        "value": round(qty * cur), "pnl": round(qty * (cur - entry)),
        "retPct": round((cur / entry - 1) * 100, 2),
        "peakPct": round((peak / entry - 1) * 100, 2),
        "troughPct": round((trough / entry - 1) * 100, 2),
        "windowPeakPct": round((wpeak / entry - 1) * 100, 2),
        "holdDays": (dt.date.fromisoformat(exit_d or last["d"]) - dt.date.fromisoformat(eb["d"])).days,
    })
    return r


def evaluate(code, name, start, strict_after, cfg):
    src_name, bars, src = get_series(code)
    out = {"code": code, "name": name, "source": src, "sourceName": src_name}
    if not bars:
        out["status"] = "데이터없음"
        return out
    ok = name_ok(name, src_name)
    out["nameVerified"] = ok
    if ok is False:
        out["status"] = "코드불일치"
        return out
    out.update(simulate(bars, start, strict_after, cfg))
    return out


CLOSED = ("익절", "손절", "기간만료")


def summarize(rows):
    valid = [r for r in rows if r.get("status") in CLOSED + ("보유",)]
    closed = [r for r in valid if r["status"] in CLOSED]
    opened = [r for r in valid if r["status"] == "보유"]
    cnt = lambda st: sum(1 for r in valid if r["status"] == st)  # noqa: E731
    avg = lambda xs: round(sum(xs) / len(xs), 2) if xs else None  # noqa: E731
    wins = [r for r in closed if r["pnl"] > 0]
    return {
        "total": len(rows), "valid": len(valid), "excluded": len(rows) - len(valid),
        "open": len(opened), "closed": len(closed),
        "target": cnt("익절"), "stop": cnt("손절"), "expired": cnt("기간만료"),
        "winRate": round(len(wins) / len(closed) * 100, 1) if closed else None,
        "avgRetClosed": avg([r["retPct"] for r in closed]),
        "invested": sum(r["invested"] for r in valid),
        "investedOpen": sum(r["invested"] for r in opened),
        "valueOpen": sum(r["value"] for r in opened),
        "pnlOpen": sum(r["pnl"] for r in opened),
        "pnlClosed": sum(r["pnl"] for r in closed),
        "pnlTotal": sum(r["pnl"] for r in valid),
        "retTotalPct": round(sum(r["pnl"] for r in valid) / sum(r["invested"] for r in valid) * 100, 2) if valid else None,
        "avgWindowPeak": avg([r["windowPeakPct"] for r in valid]),
        "reach20": sum(1 for r in valid if r["windowPeakPct"] >= 20),
        "reach50": sum(1 for r in valid if r["windowPeakPct"] >= 50),
        "reach120": sum(1 for r in valid if r["windowPeakPct"] >= 120),
    }


def main():
    cfg = load("config.json")
    stamp = now_kst().strftime("%Y-%m-%d %H:%M KST")

    # 1) 백테스트: 뉴스일 다음 거래일 종가 매수
    events = load("events.json", [])
    all_rows = []
    for ev in events:
        print(f"[백테스트] {ev['date']} {ev['name']}")
        ev["results"] = []
        for st in ev.get("stocks", []):
            r = evaluate(st["code"], st["name"], ev["date"], True, cfg)
            r["reason"] = st.get("reason", "")
            ev["results"].append(r)
            all_rows.append(r)
        ev.pop("stocks", None)
        ev["summary"] = summarize(ev["results"])
    dump("backtest.json", {"generatedAt": stamp, "config": cfg, "summary": summarize(all_rows), "events": events})

    # 2) 실전 가상매매: buyDate 당일(휴장이면 다음 거래일) 종가 매수
    trades = load("trades.json", [])
    positions = []
    for t in trades:
        print(f"[실전] {t['buyDate']} {t['name']}({t['code']})")
        r = evaluate(t["code"], t["name"], t["buyDate"], False, cfg)
        positions.append({**t, **r})
    dump("portfolio.json", {"generatedAt": stamp, "config": cfg, "summary": summarize(positions), "positions": positions})
    print("완료:", stamp)


if __name__ == "__main__":
    main()
