#!/usr/bin/env python3
"""전조-눌림목 전략 수치 탐색(학습 2019~2022 / 검증 2023~) + 뉴스 후 매수 시점 분석."""
import bisect
import concurrent.futures as cf
import datetime as dt
import itertools
import json
import multiprocessing as mp
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import precursor as P  # noqa: E402

SPLIT = "2023-01-01"
XS, RETS, DEPTHS, VOLRS = (3, 5, 8, 12), (5, 15, 30), (5, 10, 20), (0.5, 0.3)
LIQS = (1e9, 5e9, 2e10)           # 20일 평균 거래대금 10억 / 50억 / 200억 이상
MAS = (False, True)               # 매수 시점 종가가 120일 이동평균 위
EXITS = [(tp, mh) for tp in (50, 100, 250) for mh in (365, None)]
SIGSETS = list(itertools.product(XS, RETS, DEPTHS, VOLRS))
ETF_D, ETF_C = [], []


def init(d, c):
    global ETF_D, ETF_C
    ETF_D, ETF_C = d, c


def etf_px(d):
    return ETF_C[max(bisect.bisect_right(ETF_D, d) - 1, 0)]


def signals(bars, wk, base, x, minret, depth, volr):
    out, last = [], -99
    i = 13
    n = len(wk)
    while i < n - 1:
        s = wk[i]
        b = base[i]
        if b > 0 and s["v"] >= x * b and s["c"] >= wk[i - 1]["c"] * (1 + minret / 100) and s["c"] > s["o"]:
            high = s["h"]
            for j in range(i + 1, min(i + 9, n)):
                w = wk[j]
                high = max(high, w["h"])
                if w["c"] < s["o"]:
                    break
                if w["c"] <= high * (1 - depth / 100) and w["v"] <= s["v"] * volr:
                    k = w["last"] + 1
                    if k < len(bars) and j - last >= 12:
                        out.append(k)
                        last = j
                    i = j
                    break
        i += 1
    return out


def sim_multi(bars, k):
    """물타기(-10%, -20%) 고정, 여러 매도 규칙을 한 번에 계산. {exit: (invested, pnl, etfPnl)}"""
    entry = bars[k][1]
    qty = P.TRANCHE // entry
    if qty <= 0:
        return None
    inv = qty * entry
    levels = [entry * 0.9, entry * 0.8]
    buys = [(bars[k][0], inv)]
    d0 = dt.date.fromisoformat(bars[k][0])
    res, pending = {}, set(range(len(EXITS)))

    def close(e, px, d):
        ep = etf_px(d)
        res[e] = (inv, qty * px - inv, sum(a * (ep / etf_px(bd) - 1) for bd, a in buys))
        pending.discard(e)

    for i in range(k, len(bars)):
        d, o, h, l, c, _ = bars[i]
        just = False
        while levels and l <= levels[0]:
            px = min(levels.pop(0), o) if i > k else levels.pop(0)
            q = P.TRANCHE // px
            qty, inv, just = qty + q, inv + q * px, True
            buys.append((d, q * px))
        avg = inv / qty
        held = None
        for e in list(pending):
            tp, mh = EXITS[e]
            if not just and h >= avg * (1 + tp / 100):
                close(e, max(avg * (1 + tp / 100), o if i > k else 0), d)
            elif mh:
                if held is None:
                    held = (dt.date.fromisoformat(d) - d0).days
                if held >= mh:
                    close(e, c, d)
        if not pending:
            break
    last = bars[-1]
    for e in list(pending):
        close(e, last[4], last[0])
    return res


def work(item):
    code, bars = item
    if len(bars) < 150:
        return []
    wk = P.weekly(bars)
    base = [0.0] * len(wk)
    for i in range(12, len(wk)):
        base[i] = sum(w["v"] for w in wk[i - 12:i]) / 12
    closes = [b[4] for b in bars]
    cache, rows = {}, []
    for si, (x, r, dp, vr) in enumerate(SIGSETS):
        for k in signals(bars, wk, base, x, r, dp, vr):
            if k not in cache:
                seg = bars[max(0, k - 20):k]
                liq = sum(b[4] * b[5] for b in seg) / len(seg) if len(seg) >= 20 and bars[k][1] >= 1000 else 0
                ma = k >= 120 and bars[k - 1][4] > sum(closes[k - 120:k]) / 120
                cache[k] = (liq, ma, bars[k][0] >= SPLIT, sim_multi(bars, k) if liq >= LIQS[0] else None)
            liq, ma, test, res = cache[k]
            if res:
                rows.append((si, liq, ma, test, res))
    return rows


def news_timing(series):
    bt = json.load(open(os.path.join(P.DATA, "backtest.json"), encoding="utf-8"))
    items = [(r["code"], ev["date"]) for ev in bt["events"] for r in ev["results"]
             if r.get("status") in ("익절", "손절", "기간만료", "보유")]
    for code in {c for c, _ in items} - set(series):
        try:
            series[code] = P.fetch(code)
        except Exception:  # noqa: BLE001
            pass
    horizons = (5, 10, 15, 20, 30, 40, 60)
    cum = {h: [] for h in horizons}
    day0, peak_day, delayed = [], [], {0: [], 5: [], 10: [], 20: [], 30: []}
    used = 0
    for code, date in items:
        bars = series.get(code) or []
        i0 = next((i for i, b in enumerate(bars) if b[0] > date), None)
        if i0 is None or i0 < 2 or i0 + 61 >= len(bars):
            continue
        used += 1
        e = bars[i0][4]
        day0.append((e / bars[i0 - 2][4] - 1) * 100)  # 뉴스 전일 종가 → 뉴스 다음 거래일 종가
        for h in horizons:
            cum[h].append((bars[i0 + h][4] / e - 1) * 100)
        seg = bars[i0:i0 + 61]
        peak_day.append(max(range(len(seg)), key=lambda j: seg[j][2]))
        for dly in delayed:
            if i0 + dly + 30 < len(bars):
                delayed[dly].append((bars[i0 + dly + 30][4] / bars[i0 + dly][4] - 1) * 100)

    def st(xs):
        return {"n": len(xs), "avg": round(statistics.mean(xs), 1), "median": round(statistics.median(xs), 1),
                "winPct": round(sum(x > 0 for x in xs) / len(xs) * 100, 1)} if xs else {"n": 0}
    buckets = {"0~1주": (0, 5), "1~2주": (6, 10), "2~6주": (11, 30), "6~12주": (31, 60)}
    return {"used": used, "total": len(items), "jumpBeforeEntry": st(day0),
            "cumFromNextDay": {f"{h}일": st(cum[h]) for h in horizons},
            "peakTiming": {k: round(sum(a <= p <= b for p in peak_day) / len(peak_day) * 100, 1) for k, (a, b) in buckets.items()} if peak_day else {},
            "hold30ByEntryDelay": {f"{d}일 뒤 매수": st(v) for d, v in delayed.items()}}


def main():
    t0 = time.time()
    stamp = dt.datetime.now(P.KST).strftime("%Y-%m-%d %H:%M KST")
    uni = P.universe()
    series = {}
    with cf.ThreadPoolExecutor(8) as ex:
        futs = {ex.submit(P.fetch, c): c for c in list(uni) + ["069500"]}
        for fu in cf.as_completed(futs):
            try:
                series[futs[fu]] = fu.result()
            except Exception:  # noqa: BLE001
                pass
    etf = series.pop("069500")
    ed, ec = [b[0] for b in etf], [b[4] for b in etf]
    print("수집", len(series), round(time.time() - t0), file=sys.stderr)

    agg = {}
    with mp.Pool(os.cpu_count() or 2, initializer=init, initargs=(ed, ec)) as pool:
        for rows in pool.imap_unordered(work, series.items(), chunksize=20):
            for si, liq, ma, test, res in rows:
                for li, lq in enumerate(LIQS):
                    if liq < lq:
                        continue
                    for mi, need in enumerate(MAS):
                        if need and not ma:
                            continue
                        for e, (inv, pnl, ep) in res.items():
                            a = agg.setdefault((si, li, mi, e), [0, 0.0, 0.0, 0.0, 0, 0.0, 0.0, 0.0])
                            o = 4 if test else 0
                            a[o] += 1
                            a[o + 1] += inv
                            a[o + 2] += pnl
                            a[o + 3] += ep
    print("탐색", len(agg), round(time.time() - t0), file=sys.stderr)

    def part(a, o):
        n, inv, pnl, ep = a[o:o + 4]
        ret = pnl / inv * 100 if inv else None
        er = ep / inv * 100 if inv else None
        return {"n": n, "retPct": round(ret, 1) if ret is not None else None, "etfPct": round(er, 1) if er is not None else None,
                "ratio": round(ret / er, 2) if ret is not None and er and er > 0 else None}

    combos = []
    for (si, li, mi, e), a in agg.items():
        x, r, dp, vr = SIGSETS[si]
        combos.append({"spikeX": x, "spikeRet": r, "depth": dp, "volRatio": vr, "liq": LIQS[li], "aboveMA120": MAS[mi],
                       "targetPct": EXITS[e][0], "maxHold": EXITS[e][1], "train": part(a, 0), "test": part(a, 4)})
    ok = [c for c in combos if c["train"]["n"] >= 100 and c["test"]["n"] >= 50]
    two = lambda p: p["ratio"] is not None and p["ratio"] >= 2 and p["retPct"] > 0  # noqa: E731
    beat = lambda p: p["ratio"] is not None and p["ratio"] > 1 and p["retPct"] > 0  # noqa: E731
    rank = lambda key: sorted([c for c in ok if c[key]["ratio"] is not None], key=lambda c: c[key]["ratio"], reverse=True)  # noqa: E731
    out = {
        "generatedAt": stamp, "seconds": round(time.time() - t0), "universe": len(series), "split": SPLIT,
        "combosTotal": len(combos), "combosEnoughTrades": len(ok),
        "count": {"train2x": sum(two(c["train"]) for c in ok), "test2x": sum(two(c["test"]) for c in ok),
                  "both2x": sum(two(c["train"]) and two(c["test"]) for c in ok),
                  "trainBeat": sum(beat(c["train"]) for c in ok), "testBeat": sum(beat(c["test"]) for c in ok),
                  "bothBeat": sum(beat(c["train"]) and beat(c["test"]) for c in ok)},
        "topByTrain": rank("train")[:15], "topByTest": rank("test")[:15],
        "both2xList": [c for c in ok if two(c["train"]) and two(c["test"])][:30],
        "smallSampleBest": sorted([c for c in combos if c not in ok and c["train"]["n"] + c["test"]["n"] >= 20
                                   and c["test"]["ratio"] and c["train"]["ratio"]],
                                  key=lambda c: min(c["train"]["ratio"], c["test"]["ratio"]), reverse=True)[:10],
        "newsTiming": news_timing(series),
    }
    with open(os.path.join(P.DATA, "precursor_search.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("완료", stamp, out["seconds"], "초")


if __name__ == "__main__":
    main()
