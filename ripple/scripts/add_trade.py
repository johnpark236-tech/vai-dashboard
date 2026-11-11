#!/usr/bin/env python3
"""실전 가상매수 1건을 trades.json 에 추가한다.

사용: python3 ripple/scripts/add_trade.py <trade.json>
하루 1종목 규칙(같은 buyDate 중복 금지)과 필수 항목을 검사한다.
"""
import datetime as dt
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "data", "trades.json")
REQUIRED = ["buyDate", "code", "name", "market", "sector", "newsDate", "newsTitle", "newsUrl",
            "newsSummary", "causepath", "reason", "risk", "scores"]


def main():
    with open(sys.argv[1], encoding="utf-8") as f:
        t = json.load(f)
    missing = [k for k in REQUIRED if not t.get(k)]
    if missing:
        sys.exit(f"필수 항목 누락: {missing}")
    if not re.fullmatch(r"\d{6}", str(t["code"])):
        sys.exit("종목코드는 숫자 6자리여야 합니다")
    for k in ("buyDate", "newsDate"):
        dt.date.fromisoformat(t[k])
    if not str(t["newsUrl"]).startswith("http"):
        sys.exit("newsUrl 은 실제 기사 주소여야 합니다")
    sc = t["scores"]
    for k in ("event", "biz", "total"):
        if not isinstance(sc.get(k), (int, float)):
            sys.exit(f"scores.{k} 숫자 필요")
    with open(PATH, encoding="utf-8") as f:
        trades = json.load(f)
    if any(x["buyDate"] == t["buyDate"] for x in trades):
        sys.exit(f"{t['buyDate']} 에는 이미 매수 기록이 있습니다 (하루 1종목)")
    active_codes = {x["code"] for x in trades[-60:]}
    if t["code"] in active_codes:
        sys.exit(f"{t['code']} 는 최근 60건 안에 이미 매수한 종목입니다. 다른 종목을 고르세요")
    t["id"] = f"T{len(trades) + 1:04d}"
    trades.append(t)
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(trades, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print("추가 완료:", t["id"], t["name"], t["code"])


if __name__ == "__main__":
    main()
