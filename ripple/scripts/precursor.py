#!/usr/bin/env python3
"""전조(대량거래) → 눌림목 → 2차 랠리 전략 백테스트. 표준 라이브러리만 사용.

전조  : 주봉 거래량이 직전 12주 평균의 SPIKE_X배 이상이고 그 주가 +5% 이상 양봉
눌림목: 전조 후 1~8주 안에, 전조 이후 고점 대비 -7% 이상 눌리고(종가),
        그 주 거래량이 전조 주의 50% 이하이며, 종가가 전조 주 시가 위(지지 유지)
매수  : 눌림목 확인 다음 거래일 시가에 1천만원
물타기: 최초 매수가 대비 -10%, -20% 에 각 1천만원 (최대 3천만원)
매도  : 평균단가 대비 +목표% 도달 시 (기본 +250%). 미도달이면 계속 보유(평가).
"""
import bisect
import concurrent.futures as cf
import datetime as dt
import json
import os
import re
import statistics
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
FIXTURE = os.environ.get("RIPPLE_FIXTURE_P")
KST = dt.timezone(dt.timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
BARS = 2000
TRANCHE = 10_000_000
SPIKE_X = 3.0
EXCLUDE = re.compile(r"스팩|리츠|KODEX|TIGER|KBSTAR|ARIRANG|HANARO|KOSEF|ACE |SOL |RISE |PLUS |ETN|인버스|레버리지|\d+호$|우$|우B$|우C$|\(전환\)")


def http(url, timeout=25):
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                return r.read()
        except Exception:  # noqa: BLE001
            if attempt == 2:
                raise
            time.sleep(1.5 * (attempt + 1))


def decode(raw):
    for enc in ("euc-kr", "cp949", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def universe():
    out = {}
    for sosok, market in ((0, "KOSPI"), (1, "KOSDAQ")):
        for page in range(1, 60):
            html = decode(http(f"https://finance.naver.com/sise/sise_market_sum.naver?sosok={sosok}&page={page}"))
            rows = re.findall(r'code=(\d{6})"\s+class="tltle">([^<]+)</a>', html)
            if not rows:
                break
            for code, name in rows:
                name = name.strip()
                if not EXCLUDE.search(name):
                    out[code] = (name, market)
            time.sleep(0.15)
    return out


def fetch(code):
    text = re.sub(r"<\?xml[^>]*\?>", "", decode(http(
        f"https://fchart.stock.naver.com/sise.nhn?symbol={code}&timeframe=day&count={BARS}&requestType=0"))).strip()
    bars = []
    for it in ET.fromstring(text).iter("item"):
        p = it.get("data", "").split("|")
        try:
            d = f"{p[0][:4]}-{p[0][4:6]}-{p[0][6:8]}"
            o, h, l, c, v = (float(x) for x in p[1:6])
        except (ValueError, IndexError):
            continue
        if c > 0:
            bars.append([d, o or c, h or c, l or c, c, v])
    bars.sort()
    for i in range(len(bars) - 1, 0, -1):  # 감자·병합·분할 불연속 보정
        r = bars[i][1] / bars[i - 1][4] if bars[i - 1][4] else 1
        if r > 1.6 or r < 0.5:
            for b in bars[:i]:
                for k in (1, 2, 3, 4):
                    b[k] *= r
    return bars


def weekly(bars):
    weeks = []
    for i, b in enumerate(bars):
        y, w, _ = dt.date.fromisoformat(b[0]).isocalendar()
        if weeks and weeks[-1]["k"] == (y, w):
            x = weeks[-1]
            x["h"], x["l"], x["c"], x["v"], x["last"] = max(x["h"], b[2]), min(x["l"], b[3]), b[4], x["v"] + b[5], i
        else:
            weeks.append({"k": (y, w), "o": b[1], "h": b[2], "l": b[3], "c": b[4], "v": b[5], "first": i, "last": i})
    return weeks


def find_signals(bars):
    """(매수일 인덱스, 전조주 정보) 목록. 한 종목에서 매수 간격은 최소 12주."""
    wk = weekly(bars)
    sigs, last_entry_week = [], -99
    i = 13
    while i < len(wk) - 1:
        base = sum(w["v"] for w in wk[i - 12:i]) / 12
        s = wk[i]
        if base > 0 and s["v"] >= SPIKE_X * base and s["c"] >= wk[i - 1]["c"] * 1.05 and s["c"] > s["o"]:
            high = s["h"]
            for j in range(i + 1, min(i + 9, len(wk))):
                w = wk[j]
                high = max(high, w["h"])
                if w["c"] < s["o"]:
                    break  # 전조 주 시가 이탈 → 무효
                if w["c"] <= high * 0.93 and w["v"] <= s["v"] * 0.5:
                    k = w["last"] + 1  # 다음 거래일 시가 매수
                    if k < len(bars) and j - last_entry_week >= 12:
                        sigs.append({"k": k, "spikeDate": bars[s["first"]][0], "mult": round(s["v"] / base, 1),
                                     "spikeRet": round((s["c"] / wk[i - 1]["c"] - 1) * 100, 1),
                                     "pullback": round((w["c"] / high - 1) * 100, 1), "weeksAfter": j - i})
                        last_entry_week = j
                    i = j
                    break
        i += 1
    return sigs


def liquid(bars, k):
    seg = bars[max(0, k - 20):k]
    return len(seg) >= 20 and bars[k][1] >= 1000 and sum(b[4] * b[5] for b in seg) / len(seg) >= 1e9


def simulate(bars, k, tp, adds, stop, maxhold):
    entry = bars[k][1]
    qty = TRANCHE // entry
    if qty <= 0:
        return None
    inv = qty * entry
    levels = [entry * 0.9, entry * 0.8] if adds else []
    buys = [(bars[k][0], inv)]
    status, xpx, xi = "보유", None, None
    d0 = dt.date.fromisoformat(bars[k][0])
    for i in range(k, len(bars)):
        d, o, h, l, c, _ = bars[i]
        just = False
        while levels and l <= levels[0]:
            px = min(levels.pop(0), o) if i > k else levels.pop(0)
            q = TRANCHE // px
            qty, inv, just = qty + q, inv + q * px, True
            buys.append((d, q * px))
        avg = inv / qty
        if stop is not None and l <= entry * (1 + stop / 100):
            status, xpx, xi = "손절", min(entry * (1 + stop / 100), o if i > k else entry), i
        elif not just and h >= avg * (1 + tp / 100):
            status, xpx, xi = "익절", max(avg * (1 + tp / 100), o if i > k else 0), i
        elif maxhold and (dt.date.fromisoformat(d) - d0).days >= maxhold:
            status, xpx, xi = "기간만료", c, i
        if xi is not None:
            break
    end = xi if xi is not None else len(bars) - 1
    cur = xpx if xpx is not None else bars[end][4]
    return {"status": status, "invested": inv, "pnl": qty * cur - inv, "buys": buys, "exitDate": bars[end][0],
            "entryDate": bars[k][0], "entry": entry, "exit": cur, "adds": len(buys) - 1,
            "days": (dt.date.fromisoformat(bars[end][0]) - d0).days,
            "minPct": (min(b[3] for b in bars[k:end + 1]) / entry - 1) * 100}


def etf_pnl(etf_dates, etf_close, buys, exit_date):
    def px(d):
        i = bisect.bisect_right(etf_dates, d) - 1
        return etf_close[max(i, 0)]
    return sum(amt * (px(exit_date) / px(d) - 1) for d, amt in buys)


def forward(bars, k, n):
    if k + n >= len(bars):
        return None
    seg = bars[k:k + n + 1]
    e = bars[k][1]
    return (max(b[2] for b in seg) / e - 1) * 100, (min(b[3] for b in seg) / e - 1) * 100, (seg[-1][4] / e - 1) * 100


def pct(xs, f):
    return round(sum(1 for x in xs if f(x)) / len(xs) * 100, 1) if xs else None


def fwd_stats(rows):
    if not rows:
        return {"n": 0}
    mx, mn, end = [r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows]
    return {"n": len(rows), "avgEndPct": round(statistics.mean(end), 1), "medEndPct": round(statistics.median(end), 1),
            "up": pct(end, lambda x: x > 0), "max50": pct(mx, lambda x: x >= 50), "max100": pct(mx, lambda x: x >= 100),
            "max250": pct(mx, lambda x: x >= 250), "dd20": pct(mn, lambda x: x <= -20), "dd40": pct(mn, lambda x: x <= -40)}


def main():
    t0 = time.time()
    stamp = dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    if FIXTURE:
        with open(FIXTURE, encoding="utf-8") as f:
            fx = json.load(f)
        uni = {c: (v["name"], "TEST") for c, v in fx.items()}
        series = {c: v["bars"] for c, v in fx.items()}
    else:
        uni = universe()
        print("종목 수", len(uni), file=sys.stderr)
        series = {}
        with cf.ThreadPoolExecutor(8) as ex:
            futs = {ex.submit(fetch, c): c for c in list(uni) + ["069500"]}
            for fu in cf.as_completed(futs):
                try:
                    series[futs[fu]] = fu.result()
                except Exception as e:  # noqa: BLE001
                    print("실패", futs[fu], e, file=sys.stderr)
    etf = series.pop("069500", None) or []
    etf_dates, etf_close = [b[0] for b in etf], [b[4] for b in etf]

    variants = [(tp, adds, stop, mh) for tp in (30, 50, 100, 150, 250) for adds in (True, False)
                for stop in (None, -30) for mh in (None, 180, 365)]
    agg = {v: [] for v in variants}
    base_trades, sig_fwd, base_fwd, by_year, lg = [], {126: [], 252: []}, {126: [], 252: []}, {}, []
    n_sig = 0
    for code, bars in series.items():
        if len(bars) < 150:
            continue
        name = uni.get(code, ("?", "?"))[0]
        for k in range(100, len(bars), 20):  # 비교 기준: 같은 유동성 조건의 임의 시점
            if liquid(bars, k):
                for n in (126, 252):
                    f = forward(bars, k, n)
                    if f:
                        base_fwd[n].append(f)
        for s in find_signals(bars):
            k = s["k"]
            if not liquid(bars, k):
                continue
            n_sig += 1
            for n in (126, 252):
                f = forward(bars, k, n)
                if f:
                    sig_fwd[n].append(f)
            for v in variants:
                r = simulate(bars, k, *v)
                if not r:
                    continue
                e = etf_pnl(etf_dates, etf_close, r["buys"], r["exitDate"]) if etf else 0
                agg[v].append((r["invested"], r["pnl"], r["status"], r["days"], e, r["adds"]))
                if v == (250, True, None, None):
                    t = {"code": code, "name": name, **s, "entryDate": r["entryDate"], "entry": round(r["entry"]),
                         "status": r["status"], "exitDate": r["exitDate"], "exit": round(r["exit"]), "adds": r["adds"],
                         "invested": round(r["invested"]), "pnl": round(r["pnl"]),
                         "retPct": round(r["pnl"] / r["invested"] * 100, 1), "days": r["days"],
                         "minPct": round(r["minPct"], 1), "etfPnl": round(e)}
                    t.pop("k")
                    base_trades.append(t)
                    y = by_year.setdefault(r["entryDate"][:4], {"n": 0, "invested": 0, "pnl": 0, "etfPnl": 0, "hit": 0})
                    y["n"] += 1
                    y["invested"] += round(r["invested"])
                    y["pnl"] += round(r["pnl"])
                    y["etfPnl"] += round(e)
                    y["hit"] += r["status"] == "익절"
                    if code == "011070":
                        lg.append(t)

    def summ(rows):
        inv, pnl, e = sum(r[0] for r in rows), sum(r[1] for r in rows), sum(r[4] for r in rows)
        rets = [r[1] / r[0] * 100 for r in rows]
        return {"n": len(rows), "invested": round(inv), "pnl": round(pnl), "retPct": round(pnl / inv * 100, 2) if inv else None,
                "etfPnl": round(e), "etfRetPct": round(e / inv * 100, 2) if inv else None,
                "target": sum(r[2] == "익절" for r in rows), "stop": sum(r[2] == "손절" for r in rows),
                "expired": sum(r[2] == "기간만료" for r in rows), "open": sum(r[2] == "보유" for r in rows),
                "winPct": pct(rets, lambda x: x > 0), "medRetPct": round(statistics.median(rets), 1) if rets else None,
                "avgDays": round(statistics.mean(r[3] for r in rows)) if rows else None,
                "avgAdds": round(statistics.mean(r[5] for r in rows), 2) if rows else None,
                "lossBelow30": pct(rets, lambda x: x <= -30)}

    grid = [{"targetPct": v[0], "adds": v[1], "stopPct": v[2], "maxHold": v[3], **summ(agg[v])} for v in variants]
    base_trades.sort(key=lambda t: t["pnl"], reverse=True)
    out = {
        "generatedAt": stamp, "seconds": round(time.time() - t0),
        "rules": __doc__, "universe": len(series), "signals": n_sig,
        "period": {"from": min((b[0][0] for b in series.values() if b), default=None),
                   "to": max((b[-1][0] for b in series.values() if b), default=None)},
        "base": next(g for g in grid if (g["targetPct"], g["adds"], g["stopPct"], g["maxHold"]) == (250, True, None, None)),
        "grid": grid, "byYear": dict(sorted(by_year.items())),
        "forward": {"signal6m": fwd_stats(sig_fwd[126]), "random6m": fwd_stats(base_fwd[126]),
                    "signal12m": fwd_stats(sig_fwd[252]), "random12m": fwd_stats(base_fwd[252])},
        "lgInnotek": lg, "top": base_trades[:25], "bottom": base_trades[-25:],
        "openNow": sorted([t for t in base_trades if t["status"] == "보유"], key=lambda t: t["entryDate"], reverse=True)[:40],
    }
    with open(os.path.join(DATA, "precursor.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("완료", stamp, "종목", len(series), "신호", n_sig, "초", out["seconds"])


if __name__ == "__main__":
    main()
