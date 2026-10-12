"""Where do we stand: leaderboard percentiles and how efficiently capital is used.

    python -m sigcup.standing
"""
from __future__ import annotations

from .client import Client


def main() -> None:
    c = Client()
    lb = c.leaderboard(period="all", limit=1)
    tot, me = lb["total"], lb["myRank"]
    print(f"rank {me} of {tot}")
    for pct in (0.01, 0.05, 0.10, 0.20, 0.30, 0.50):
        r = max(1, int(tot * pct))
        x = c.leaderboard(period="all", limit=1, offset=r - 1)["leaderboard"][0]
        print(f"  top {pct:>4.0%} = rank {r:>4}: pnl {x['pnl']:>10,.0f}  trades {x['tradesCount']}")
    l7 = c.leaderboard(period="7d", limit=100)
    pn = [round(x["pnl"]) for x in l7["leaderboard"]]
    print(f"7d pnl at ranks 1/10/50/100: {[pn[i] for i in (0, 9, 49, 99) if i < len(pn)]}"
          f"  (our 7d rank {l7['myRank']})")

    pos = [p for p in c.positions()["positions"] if p["quantity"]]
    q = c.prices([p["exchangeId"] for p in pos])
    by = {}
    for p in pos:
        by.setdefault(p["marketTitle"].split(" win the ")[-1].rstrip("?"), []).append(p)
    print("\nHedged pairs (NO on both parties): capital, locked profit, value if sold now")
    tc = tl = ts = 0.0
    for race, ps in sorted(by.items()):
        if len(ps) != 2 or not all(p["quantity"] < 0 for p in ps):
            continue
        n = min(abs(p["quantity"]) for p in ps)
        cost = sum(p["costBasis"] / abs(p["quantity"]) * n for p in ps)
        sell = sum(n * (1 - ((q.get(p["exchangeId"]) or {}).get("bestAsk") or 1)) for p in ps)
        tc, tl, ts = tc + cost, tl + n - cost, ts + sell
        print(f"  {race:<24} cost {cost:>7,.0f}  locked {n - cost:>+5,.0f} ({(n - cost) / cost:+.1%})"
              f"  sell now {sell:>7,.0f} ({sell - cost:+,.0f})")
    print(f"  TOTAL capital {tc:,.0f}  locked {tl:+,.0f} ({tl / max(tc, 1):+.1%})  sell now {ts:,.0f} ({ts - tc:+,.0f})")


if __name__ == "__main__":
    main()
