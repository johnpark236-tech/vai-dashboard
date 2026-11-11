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
ADJUSTED = {}


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
    # 감자·병합·분할 등으로 하루 새 가격이 끊긴 경우(상한가 +30%로는 불가능한 변동) 이전 봉을 비율 보정
    for i in range(len(bars) - 1, 0, -1):
        ratio = bars[i]["o"] / bars[i - 1]["c"] if bars[i - 1]["c"] else 1
        if ratio > 1.6 or ratio < 0.5:
            print(f"  [보정] {code} {bars[i]['d']} 가격 불연속 x{ratio:.3f}", file=sys.stderr)
            ADJUSTED.setdefault(code, []).append({"date": bars[i]["d"], "ratio": round(ratio, 4)})
            for bb in bars[:i]:
                for k in ("o", "h", "l", "c"):
                    bb[k] *= ratio
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
    target = entry * (1 + tp / 100)
    stop = entry * (1 + sp / 100) if sp is not None else -1.0  # None = 손절 없음
    deadline = (dt.date.fromisoformat(eb["d"]) + dt.timedelta(days=maxd)).isoformat() if maxd else "9999-12-31"  # None = 무기한
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


TP_GRID = list(range(5, 65, 5))
SP_GRID = [-5, -10, -15, -20, -25, -30]


def auto_tune(pairs, cfg):
    """백테스트 종목으로 익절·손절 조합을 전수 계산해 1억 자본 기준 연 기대수익이 가장 큰 값을 고른다.

    한 칸만 튀는 값(과최적화)을 피하려고 이웃 칸과 평균한 점수로 순위를 매긴다.
    """
    slots = max(1, cfg["capital"] // cfg["budget"])
    grid = {}
    for sp in SP_GRID:
        for tp in TP_GRID:
            c2 = {**cfg, "targetPct": tp, "stopPct": sp}
            rows = [simulate(get_series(code)[1], d, True, c2) for code, d in pairs]
            rows = [r for r in rows if r.get("status") in CLOSED + ("보유",)]
            if not rows:
                continue
            avg_ret = sum(r["retPct"] for r in rows) / len(rows)
            avg_hold = max(1.0, sum(r["holdDays"] for r in rows) / len(rows))
            per_year = min(245.0, slots * 365.0 / avg_hold)  # 하루 1종목, 동시 보유 한도 반영
            sm = summarize(rows)
            grid[(tp, sp)] = {"targetPct": tp, "stopPct": sp, "avgRetPct": round(avg_ret, 2),
                              "avgHoldDays": round(avg_hold, 1), "tradesPerYear": round(per_year),
                              "annualProfit": round(per_year * cfg["budget"] * avg_ret / 100),
                              "target": sm["target"], "stop": sm["stop"], "expired": sm["expired"],
                              "winRate": sm["winRate"]}
    for (tp, sp), g in grid.items():
        ti, si = TP_GRID.index(tp), SP_GRID.index(sp)
        near = [grid[k]["annualProfit"] for k in (
            (tp, sp),
            (TP_GRID[ti - 1], sp) if ti > 0 else None, (TP_GRID[ti + 1], sp) if ti + 1 < len(TP_GRID) else None,
            (tp, SP_GRID[si - 1]) if si > 0 else None, (tp, SP_GRID[si + 1]) if si + 1 < len(SP_GRID) else None,
        ) if k in grid]
        g["score"] = round(sum(near) / len(near))
    ranked = sorted(grid.values(), key=lambda g: g["score"], reverse=True)
    return ranked


def apply_capital(positions, cfg):
    """총 투자금 한도: 매수일 순서로 현금이 모자라면 '자금부족'으로 매수하지 않는다."""
    capital = cfg["capital"]
    accepted = []
    cand = [p for p in positions if p.get("status") in CLOSED + ("보유",)]
    for p in sorted(cand, key=lambda x: (x["entryDate"], x.get("id", ""))):
        d = p["entryDate"]
        cash = capital
        for a in accepted:
            if a.get("exitDate") and a["exitDate"] < d:
                cash += a["pnl"]          # 매도 완료: 원금 회수 + 손익
            else:
                cash -= a["invested"]     # 아직 보유 중
        if cash < p["invested"]:
            p["status"] = "자금부족"
        else:
            accepted.append(p)
    realized = sum(a["pnl"] for a in accepted if a["status"] in CLOSED)
    inv_open = sum(a["invested"] for a in accepted if a["status"] == "보유")
    val_open = sum(a["value"] for a in accepted if a["status"] == "보유")
    cash = capital + realized - inv_open
    return {"capital": capital, "cash": cash, "equity": cash + val_open,
            "equityRetPct": round((cash + val_open - capital) / capital * 100, 2)}


def simulate_add(bars, start, cfg, tp, sp, add_pct=-10):
    """추가매수 연구용: 매수가 대비 add_pct% 하락 시 budget 만큼 1회 추가 매수.

    익절은 평균단가 기준 +tp%, 손절은 최초 매수가 기준 sp%(None=없음). 같은 날 추가매수와
    손절이 겹치면 둘 다 체결(보수적), 추가매수 당일 익절은 인정하지 않는다.
    """
    budget, maxd = cfg["budget"], cfg["maxDays"]
    idx = next((i for i, b in enumerate(bars) if b["d"] > start), None)
    if idx is None:
        return None
    eb = bars[idx]
    entry = eb["c"]
    qty = int(budget // entry)
    if qty <= 0:
        return None
    invested, added = qty * entry, False
    add_lv = entry * (1 + add_pct / 100)
    stop = entry * (1 + sp / 100) if sp is not None else -1.0
    deadline = (dt.date.fromisoformat(eb["d"]) + dt.timedelta(days=maxd)).isoformat()
    status, exit_px, exit_d = "보유", None, None
    for b in bars[idx + 1:]:
        just_added = False
        if not added and b["l"] <= add_lv:
            px = min(add_lv, b["o"])
            q2 = int(budget // px)
            qty, invested, added, just_added = qty + q2, invested + q2 * px, True, True
        avg = invested / qty
        if b["l"] <= stop:
            status, exit_px, exit_d = "손절", min(stop, b["o"]), b["d"]
        elif not just_added and b["h"] >= avg * (1 + tp / 100):
            status, exit_px, exit_d = "익절", max(avg * (1 + tp / 100), b["o"]), b["d"]
        elif b["d"] >= deadline:
            status, exit_px, exit_d = "기간만료", b["c"], b["d"]
        if exit_px is not None:
            break
    last = bars[-1]
    cur = exit_px if exit_px is not None else last["c"]
    return {"status": status, "invested": round(invested), "pnl": round(qty * cur - invested), "added": added,
            "holdDays": (dt.date.fromisoformat(exit_d or last["d"]) - dt.date.fromisoformat(eb["d"])).days}


def add_study(pairs, cfg):
    out = []
    for tp in (10, 15, 20, 30, 40, 50):
        for sp in (-15, -20, -30, None):
            base = [simulate(get_series(c)[1], d, True, {**cfg, "targetPct": tp, "stopPct": sp}) for c, d in pairs]
            base = [r for r in base if r.get("status") in CLOSED + ("보유",)]
            adds = [r for r in (simulate_add(get_series(c)[1], d, cfg, tp, sp) for c, d in pairs) if r]

            def stat(rows):
                inv = sum(r["invested"] for r in rows)
                pnl = sum(r["pnl"] for r in rows)
                return {"pnl": pnl, "invested": inv, "retPct": round(pnl / inv * 100, 2) if inv else None,
                        "target": sum(r["status"] == "익절" for r in rows), "stop": sum(r["status"] == "손절" for r in rows),
                        "expired": sum(r["status"] == "기간만료" for r in rows), "open": sum(r["status"] == "보유" for r in rows),
                        "avgHoldDays": round(sum(r["holdDays"] for r in rows) / len(rows), 1) if rows else None}
            a = stat(adds)
            a["addCount"] = sum(1 for r in adds if r["added"])
            out.append({"targetPct": tp, "stopPct": sp, "noAdd": stat(base), "withAdd": a})
    return out


def main():
    cfg = load("config.json")
    stamp = now_kst().strftime("%Y-%m-%d %H:%M KST")
    events = load("events.json", [])

    # 0) 유효 종목(코드·종목명 확인) 목록
    pairs = []
    for ev in events:
        for st in ev.get("stocks", []):
            src_name, bars, _ = get_series(st["code"])
            if bars and name_ok(st["name"], src_name) is not False:
                pairs.append((st["code"], ev["date"]))

    # 0-1) 익절·손절 자동 설정 (한 번 정하면 auto.json 에 고정. 다시 맞추려면 auto.json 삭제)
    auto = None
    if cfg.get("auto"):
        auto = load("auto.json")
        if not auto or auto.get("budget") != cfg["budget"] or auto.get("capital") != cfg["capital"] or auto.get("maxDays") != cfg["maxDays"]:
            ranked = auto_tune(pairs, cfg)
            if ranked:
                best = ranked[0]
                auto = {"targetPct": best["targetPct"], "stopPct": best["stopPct"], "tunedAt": stamp,
                        "budget": cfg["budget"], "capital": cfg["capital"], "maxDays": cfg["maxDays"],
                        "best": best, "top": ranked[:8], "grid": ranked, "samples": len(pairs)}
                dump("auto.json", auto)
        if auto:
            cfg = {**cfg, "targetPct": auto["targetPct"], "stopPct": auto["stopPct"]}

    # 1) 백테스트: 뉴스일 다음 거래일 종가 매수
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
    # 비교 기준: 같은 기간 KODEX 200 단순 보유 (가격 수익률, 분배금 제외)
    bench = None
    _, bb, _ = get_series("069500")
    first = min((d for _, d in pairs), default=None)
    if bb and first:
        seg = [x for x in bb if x["d"] > first]
        if seg:
            days = (dt.date.fromisoformat(seg[-1]["d"]) - dt.date.fromisoformat(seg[0]["d"])).days
            tot = seg[-1]["c"] / seg[0]["c"] - 1
            peak, mdd = seg[0]["c"], 0.0
            for x in seg:
                peak = max(peak, x["c"])
                mdd = min(mdd, x["c"] / peak - 1)
            bench = {"code": "069500", "name": "KODEX 200", "from": seg[0]["d"], "to": seg[-1]["d"],
                     "startPrice": seg[0]["c"], "endPrice": seg[-1]["c"], "days": days,
                     "totalRetPct": round(tot * 100, 2),
                     "annualRetPct": round(((1 + tot) ** (365 / days) - 1) * 100, 2) if days else None,
                     "maxDrawdownPct": round(mdd * 100, 2)}
    dump("backtest.json", {"generatedAt": stamp, "config": cfg, "auto": auto, "benchmark": bench, "summary": summarize(all_rows),
                           "addStudy": add_study(pairs, cfg), "adjusted": ADJUSTED, "events": events})

    # 2) 실전 가상매매: buyDate 당일(휴장이면 다음 거래일) 종가 매수, 총 투자금 한도 적용
    trades = load("trades.json", [])
    positions = []
    for t in trades:
        print(f"[실전] {t['buyDate']} {t['name']}({t['code']})")
        r = evaluate(t["code"], t["name"], t["buyDate"], False, cfg)
        positions.append({**t, **r})
    ledger = apply_capital(positions, cfg)
    summary = {**summarize(positions), **ledger}
    summary["canBuy"] = ledger["cash"] >= cfg["budget"]
    dump("portfolio.json", {"generatedAt": stamp, "config": cfg, "summary": summary, "positions": positions})
    print("완료:", stamp, "| 익절", cfg["targetPct"], "손절", cfg["stopPct"], "| 현금", ledger["cash"])


if __name__ == "__main__":
    main()
