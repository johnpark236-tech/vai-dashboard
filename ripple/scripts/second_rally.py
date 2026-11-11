#!/usr/bin/env python3
"""거래량 급증 1차 랠리 이후 2차 랠리(1차 고점 돌파)까지 걸린 기간 조사.

1차 랠리 : 일 거래량 ≥ 20일 평균의 X배 & 당일 +5% 이상으로 시작, 시작 전 종가 대비 고점 +30% 이상
1차 종료 : 고점 대비 종가 -15% 이상 하락한 날
2차 랠리 : 이후 고가가 1차 고점을 넘어선 날 (고점 후 250거래일까지 관찰)
"""
import concurrent.futures as cf
import datetime as dt
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import precursor as P  # noqa: E402

WIN = 250
BUCKETS = [("2주 이내", 1, 10), ("2~6주", 11, 30), ("6~12주", 31, 60), ("3~6개월", 61, 120), ("6~12개월", 121, 250)]


def scan(bars, x):
    out = []
    n = len(bars)
    vol = [b[5] for b in bars]
    i = 21
    while i < n - 1:
        avg = sum(vol[i - 20:i]) / 20
        if not (avg > 0 and vol[i] >= x * avg and bars[i][4] >= bars[i - 1][4] * 1.05 and P.liquid(bars, i)):
            i += 1
            continue
        base = bars[i - 1][4]
        peak, pi, e = bars[i][2], i, None
        for j in range(i, n):
            if bars[j][2] > peak:
                peak, pi = bars[j][2], j
            if bars[j][4] <= peak * 0.85:
                e = j
                break
        if e is None:
            break
        if peak / base - 1 < 0.30 or pi + WIN >= n:
            i = e + 1
            continue
        low, li, b, reb = bars[e][3], e, None, None
        for j in range(pi + 1, pi + WIN + 1):
            if j > e:
                if reb is None and bars[j][2] >= low * 1.3:
                    reb = j
            if bars[j][3] < low or j <= e:
                if bars[j][3] <= low:
                    low, li = bars[j][3], j
            if j > e and bars[j][2] > peak:
                b = j
                break
        rec = {"rally1": (peak / base - 1) * 100, "rally1Days": pi - i, "endDays": e - pi,
               "depth": (low / peak - 1) * 100, "year": bars[pi][0][:4]}
        ec = bars[e][4]
        for h in (20, 60, 120):
            rec[f"hold{h}"] = (bars[e + h][4] / ec - 1) * 100 if e + h < n else None
        if reb is not None:
            rec["reboundDays"] = reb - pi
        if b is not None:
            rec["days"] = b - pi
            rec["lowToBreak"] = b - li
            p2 = peak
            for j in range(b, n):
                p2 = max(p2, bars[j][2])
                if bars[j][4] <= p2 * 0.85:
                    break
            rec["rally2"] = (p2 / peak - 1) * 100
            fill = max(peak, bars[b][1])
            rec["afterBreak60"] = (bars[b + 60][4] / fill - 1) * 100 if b + 60 < n else None
        out.append(rec)
        i = e + 1
    return out


def med(xs):
    return round(statistics.median(xs), 1) if xs else None


def summarize(recs):
    n = len(recs)
    hit = [r for r in recs if "days" in r]
    days = [r["days"] for r in hit]
    q = statistics.quantiles(days, n=4) if len(days) >= 4 else [None] * 3
    s = {"n": n, "secondRally": len(hit), "secondRallyPct": round(len(hit) / n * 100, 1) if n else None,
         "daysMedian": med(days), "daysQ1": q[0], "daysQ3": q[2],
         "lowToBreakMedian": med([r["lowToBreak"] for r in hit]),
         "rebound30Pct": round(sum("reboundDays" in r for r in recs) / n * 100, 1) if n else None,
         "reboundDaysMedian": med([r["reboundDays"] for r in recs if "reboundDays" in r]),
         "rally1Median": med([r["rally1"] for r in recs]), "rally1DaysMedian": med([r["rally1Days"] for r in recs]),
         "depthMedianAll": med([r["depth"] for r in recs]), "depthMedianHit": med([r["depth"] for r in hit]),
         "rally2Median": med([r["rally2"] for r in hit]),
         "rally2Over30Pct": round(sum(r["rally2"] >= 30 for r in hit) / n * 100, 1) if n else None,
         "rally2Over100Pct": round(sum(r["rally2"] >= 100 for r in hit) / n * 100, 1) if n else None,
         "buckets": {name: {"count": sum(a <= d <= b for d in days),
                            "pctOfAll": round(sum(a <= d <= b for d in days) / n * 100, 1) if n else None}
                     for name, a, b in BUCKETS}}
    s["buckets"]["1년 내 없음"] = {"count": n - len(hit), "pctOfAll": round((n - len(hit)) / n * 100, 1) if n else None}
    for h in (20, 60, 120):
        xs = [r[f"hold{h}"] for r in recs if r.get(f"hold{h}") is not None]
        s[f"buyAtPullback{h}"] = {"median": med(xs), "avg": round(statistics.mean(xs), 1) if xs else None,
                                  "winPct": round(sum(v > 0 for v in xs) / len(xs) * 100, 1) if xs else None}
    xs = [r["afterBreak60"] for r in hit if r.get("afterBreak60") is not None]
    s["buyAtBreakout60"] = {"median": med(xs), "avg": round(statistics.mean(xs), 1) if xs else None,
                            "winPct": round(sum(v > 0 for v in xs) / len(xs) * 100, 1) if xs else None}
    return s


def main():
    t0 = time.time()
    stamp = dt.datetime.now(P.KST).strftime("%Y-%m-%d %H:%M KST")
    uni = P.universe()
    series = {}
    with cf.ThreadPoolExecutor(8) as ex:
        futs = {ex.submit(P.fetch, c): c for c in uni}
        for fu in cf.as_completed(futs):
            try:
                series[futs[fu]] = fu.result()
            except Exception:  # noqa: BLE001
                pass
    out = {"generatedAt": stamp, "universe": len(series), "rules": __doc__, "byVolume": {}}
    for x in (3, 5, 10):
        recs = [r for bars in series.values() if len(bars) > 300 for r in scan(bars, x)]
        s = summarize(recs)
        if x == 5:
            s["byDepth"] = {name: summarize([r for r in recs if lo < -r["depth"] <= hi])
                            for name, lo, hi in (("눌림 15~25%", 0, 25), ("눌림 25~40%", 25, 40), ("눌림 40% 초과", 40, 1000))}
            for v in s["byDepth"].values():
                v.pop("buckets", None)
            s["byRally1"] = {name: {k: v for k, v in summarize([r for r in recs if lo <= r["rally1"] < hi]).items() if k != "buckets"}
                             for name, lo, hi in (("1차 +30~50%", 30, 50), ("1차 +50~100%", 50, 100), ("1차 +100% 이상", 100, 1e9))}
            s["byYear"] = {y: {k: summarize([r for r in recs if r["year"] == y])[k] for k in ("n", "secondRallyPct", "daysMedian")}
                           for y in sorted({r["year"] for r in recs})}
        out["byVolume"][f"{x}배"] = s
    out["seconds"] = round(time.time() - t0)
    with open(os.path.join(P.DATA, "second_rally.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("완료", stamp, out["seconds"])


if __name__ == "__main__":
    main()
