"""Snapshot all tournament markets + quotes into data/snapshots.db and data/latest.csv.

Usage:
    python -m sigcup.snapshot               # one snapshot, prints table
    python -m sigcup.snapshot --watch 300   # every 5 min
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import time
from typing import Optional, Sequence

from .client import ROOT, Client

DB = ROOT / "data" / "snapshots.db"
CSV = ROOT / "data" / "latest.csv"

SCHEMA = """
CREATE TABLE IF NOT EXISTS markets (
  market_id TEXT, exchange_id TEXT PRIMARY KEY, title TEXT, option TEXT,
  status TEXT, categories TEXT, settlement_date TEXT, settled_with TEXT);
CREATE TABLE IF NOT EXISTS quotes (
  ts INTEGER, exchange_id TEXT, last REAL, bid REAL, ask REAL, spread REAL,
  PRIMARY KEY (ts, exchange_id));
"""


def snapshot(c: Client) -> list:
    markets = c.markets()
    rows = []
    for m in markets:
        for ex in m.get("exchanges", []):
            rows.append((m["id"], ex["id"], m["title"], ex.get("option"), m.get("status"),
                         ",".join(m.get("categories") or []), m.get("settlementDate"), m.get("settledWith")))
    quotes = c.prices([r[1] for r in rows])
    ts = int(time.time())
    DB.parent.mkdir(exist_ok=True)
    with sqlite3.connect(DB) as db:
        db.executescript(SCHEMA)
        db.executemany("INSERT OR REPLACE INTO markets VALUES (?,?,?,?,?,?,?,?)", rows)
        db.executemany("INSERT OR REPLACE INTO quotes VALUES (?,?,?,?,?,?)",
                       [(ts, i, q.get("latestPrice"), q.get("bestBid"), q.get("bestAsk"), q.get("spread"))
                        for i, q in quotes.items()])
    out = []
    for r in rows:
        q = quotes.get(r[1], {})
        out.append({"market_id": r[0], "exchange_id": r[1], "title": r[2], "option": r[3], "status": r[4],
                    "last": q.get("latestPrice"), "bid": q.get("bestBid"), "ask": q.get("bestAsk"),
                    "spread": q.get("spread")})
    with open(CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0]) if out else ["title"])
        w.writeheader()
        w.writerows(out)
    return out


def main(argv: Optional[Sequence[str]] = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", type=float, default=0)
    args = ap.parse_args(argv)
    c = Client()
    print(f"Tournament: {c.tournament()['name']}  balance: {c.balance():,.0f}")
    while True:
        rows = snapshot(c)
        print(f"[{time.strftime('%H:%M:%S')}] {len(rows)} exchanges -> {CSV.relative_to(ROOT)}")
        if not args.watch:
            fmt = lambda v: "  -  " if v is None else f"{v:.3f}"
            for r in sorted(rows, key=lambda r: r["title"]):
                print(f"  {r['title'][:55]:<56}{fmt(r['bid'])} / {fmt(r['ask'])}  last {fmt(r['last'])}")
            break
        time.sleep(args.watch)


if __name__ == "__main__":
    main()
