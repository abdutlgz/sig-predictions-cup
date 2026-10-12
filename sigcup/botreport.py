"""Bot P&L and whole-account status.

Bot P&L = cash change since the bot started + value of the bot's positions
(pre-existing positions are excluded; they don't trade, so all cash change is the bot's).

    python -m sigcup.botreport
"""
from __future__ import annotations

import csv
from collections import defaultdict

from .bot import DATA, LOG, MANUAL, START, _load
from .client import Client
from .pmfeed import build_map, fair_values


def main() -> None:
    c = Client()
    fair = fair_values(build_map(c))
    manual = set(_load(MANUAL, []))
    start = _load(START, None)
    cash = c.balance()

    def value(p, at_fair: bool) -> float:
        q, f = abs(p["quantity"]), fair.get(p["exchangeId"])
        if not at_fair or f is None:
            return p["marketValue"]
        return q * (f if p["quantity"] > 0 else 1 - f)

    pos = [p for p in c.positions()["positions"] if p["quantity"]]
    groups = {"manual": [p for p in pos if p["exchangeId"] in manual],
              "bot": [p for p in pos if p["exchangeId"] not in manual]}

    print("== Account ==")
    total_sig = cash + sum(p["marketValue"] for p in pos)
    total_fair = cash + sum(value(p, True) for p in pos)
    print(f"cash {cash:,.0f} | account at SIG prices {total_sig:,.0f} | at Polymarket fair {total_fair:,.0f}"
          f" | if held to resolution, expected P&L vs 100k: {total_fair - 100000:+,.0f}")
    lb = c.leaderboard(period="all", limit=3)
    print(f"leaderboard rank {lb.get('myRank')} of {lb.get('total')}")

    for name, ps in groups.items():
        cost = sum(p["costBasis"] for p in ps)
        print(f"\n== {name} positions ({len(ps)}) cost {cost:,.0f} | at SIG {sum(p['marketValue'] for p in ps):,.0f}"
              f" | at fair {sum(value(p, True) for p in ps):,.0f}")
        for p in sorted(ps, key=lambda p: p["marketTitle"]):
            side = "YES" if p["quantity"] > 0 else "NO"
            print(f"  {side:<3} {p['marketTitle'][9:62]:<54} qty {abs(p['quantity']):>6} avg {p['avgCost']:.3f}"
                  f"  SIG {p['marketValue'] - p['costBasis']:+7.0f}  fair {value(p, True) - p['costBasis']:+7.0f}")

    if start:
        bot_pos = groups["bot"]
        sales = _load(DATA / "manual_sales.json", [])
        sold_cash = sum(x["cash"] for x in sales)
        flow = cash - start["cash"] - sold_cash  # cash from selling earlier positions isn't bot profit
        if sales:
            print(f"\n(earlier positions sold by hand: raised {sold_cash:,.0f} vs cost "
                  f"{sum(x['cost'] for x in sales):,.0f} -> {sum(x['cash'] - x['cost'] for x in sales):+,.0f};"
                  f" excluded from bot P&L)")
        print(f"\n== Bot since {start['ts']} ==")
        print(f"cash flow {flow:+,.0f} | P&L at SIG prices {flow + sum(p['marketValue'] for p in bot_pos):+,.0f}"
              f" | P&L at fair {flow + sum(value(p, True) for p in bot_pos):+,.0f}")
        try:
            rows = list(csv.DictReader(open(LOG)))
        except OSError:
            rows = []
        by = defaultdict(lambda: [0, 0.0])
        for r in rows:
            k = (r.get("strategy", "-"), r["kind"])
            by[k][0] += 1
            by[k][1] += float(r["cost"] or 0) * (1 if r["action"] == "sell" else -1)
        for (s, k), (n, cf) in sorted(by.items()):
            print(f"  {s:<6} {k:<6} {n:>4} trades  cash flow {cf:+,.0f}")
        print("  (maker fills from resting quotes are not in the trade log; they show up in cash flow above)")


if __name__ == "__main__":
    main()
