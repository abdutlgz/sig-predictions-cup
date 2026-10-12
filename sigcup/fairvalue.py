"""Fair-value sheet: SIG Predictions Cup prices vs Polymarket (deep, real-money market).

For every "Will the <Party> Party win the <Race>?" market, finds the matching Polymarket
"Election Winner" event and compares prices.
  edge_yes = PM_mid - SIG_ask   (> 0: SIG YES is cheap -> buy YES)
  edge_no  = SIG_bid - PM_mid   (> 0: SIG YES is rich  -> buy NO)

Usage:
    python -m sigcup.fairvalue                 # table of edges >= 2 pts, saves data/fairvalue.csv
    python -m sigcup.fairvalue --min-edge 0.04
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Sequence

import requests

from .arb import parse_party
from .client import ROOT, Client

GAMMA = "https://gamma-api.polymarket.com"
CSV_PATH = ROOT / "data" / "fairvalue.csv"
_http = requests.Session()


def _slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def pm_slugs(race: str) -> List[str]:
    """SIG race name -> candidate Polymarket event slugs."""
    r = re.sub(r"\s+race$", "", race.strip(), flags=re.I)
    if r.lower() in ("u.s. senate", "u.s. house"):  # chamber control
        return [f"which-party-will-win-the-{r.lower()[5:]}-in-2026"]
    m = re.match(r"^([A-Z]{2})-(\d{2})\s+House", r, re.I)
    if m:
        return [f"{m[1].lower()}-{m[2]}-house-election-winner"]
    m = re.match(r"^(.+?)\s+(Senate|Governor)$", r, re.I)
    if m:
        st, office = _slugify(m[1]), m[2].lower()
        if office == "senate":
            return [f"{st}-senate-election-winner", f"{st}-senate-election-winner-2026"]
        return [f"{st}-governor-winner-2026", f"{st}-governor-election-winner"]
    return []


def fetch_event(race: str) -> Optional[dict]:
    for slug in pm_slugs(race):
        try:
            d = _http.get(f"{GAMMA}/events", params={"slug": slug}, timeout=15).json()
        except (requests.RequestException, ValueError):
            continue
        if d:
            return d[0]
    # Fallback: search
    try:
        q = re.sub(r"\s+race$", "", race, flags=re.I) + " Election Winner"
        d = _http.get(f"{GAMMA}/public-search", params={"q": q, "limit_per_type": 5}, timeout=15).json()
        for e in d.get("events", []):
            if e.get("title", "").lower().startswith(q.lower().replace(" election winner", "")) and \
                    "winner" in e.get("title", "").lower() and "primary" not in e.get("title", "").lower():
                return _http.get(f"{GAMMA}/events", params={"slug": e["slug"]}, timeout=15).json()[0]
    except (requests.RequestException, ValueError, IndexError):
        pass
    return None


def pm_party_prices(event: dict) -> Dict[str, dict]:
    """party ('democratic'|'republican') -> {mid, bid, ask, volume, question}"""
    out = {}
    for m in event.get("markets", []):
        q = m.get("question", "")
        ql = q.lower()
        if "democrat" in ql:
            party = "democratic"
        elif "republican" in ql:
            party = "republican"
        else:
            continue
        if m.get("closed"):
            continue
        bid, ask = m.get("bestBid"), m.get("bestAsk")
        try:
            last = float(json.loads(m.get("outcomePrices") or "[]")[0])
        except (ValueError, IndexError, TypeError):
            last = None
        mid = (bid + ask) / 2 if bid is not None and ask is not None and 0 < ask - bid <= 0.1 else last
        if mid is None:
            continue
        out[party] = {"mid": mid, "bid": bid, "ask": ask, "volume": float(m.get("volume") or 0), "question": q}
    return out


def build_sheet(c: Client) -> List[dict]:
    markets = c.markets(status="open")
    rows = []
    for m in markets:
        parsed = parse_party(m["title"])
        if parsed:
            party = {"democrat": "democratic", "gop": "republican"}.get(parsed[1].lower(), parsed[1].lower())
            rows.append({"race": parsed[0], "party": party,
                         "title": m["title"], "exchange_id": m["exchanges"][0]["id"]})
    races = sorted({r["race"] for r in rows})
    with ThreadPoolExecutor(max_workers=8) as ex:
        events = dict(zip(races, ex.map(fetch_event, races)))
    quotes = c.prices([r["exchange_id"] for r in rows])
    for r in rows:
        q = quotes.get(r["exchange_id"], {})
        r["sig_bid"], r["sig_ask"] = q.get("bestBid"), q.get("bestAsk")
        ev = events.get(r["race"])
        pm = pm_party_prices(ev).get(r["party"]) if ev else None
        r["pm_event"] = ev["slug"] if ev else None
        r["pm_mid"] = round(pm["mid"], 4) if pm else None
        r["pm_volume"] = round(pm["volume"]) if pm else None
        r["edge_yes"] = round(r["pm_mid"] - r["sig_ask"], 4) if pm and r["sig_ask"] is not None else None
        r["edge_no"] = round(r["sig_bid"] - r["pm_mid"], 4) if pm and r["sig_bid"] is not None else None
        best = max([e for e in (r["edge_yes"], r["edge_no"]) if e is not None], default=None)
        r["best_edge"] = best
        r["action"] = None if best is None else ("BUY YES" if best == r["edge_yes"] else "BUY NO")
    return rows


def main(argv: Optional[Sequence[str]] = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-edge", type=float, default=0.02)
    ap.add_argument("--min-volume", type=float, default=10000, help="min Polymarket $ volume to trust its price")
    args = ap.parse_args(argv)
    rows = build_sheet(Client())
    CSV_PATH.parent.mkdir(exist_ok=True)
    with open(CSV_PATH, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    matched = [r for r in rows if r["pm_mid"] is not None]
    unmatched = sorted({r["race"] for r in rows if r["pm_mid"] is None
                        and r["party"] in ("democratic", "republican")})
    print(f"{len(rows)} SIG markets, {len(matched)} matched to Polymarket -> {CSV_PATH.relative_to(ROOT)}")
    if unmatched:
        print(f"unmatched races ({len(unmatched)}): {', '.join(unmatched[:20])}{' ...' if len(unmatched) > 20 else ''}")
    hits = sorted([r for r in matched if (r["best_edge"] or 0) >= args.min_edge
                   and (r["pm_volume"] or 0) >= args.min_volume], key=lambda r: -r["best_edge"])
    print(f"\n{'action':<8} {'market':<58}{'SIG bid/ask':>13}{'PM':>7}{'edge':>7}{'PM vol':>11}")
    for r in hits:
        print(f"{r['action']:<8} {r['title'][9:66]:<58}{r['sig_bid']:>6.3f}/{r['sig_ask']:<6.3f}"
              f"{r['pm_mid']:>7.3f}{r['best_edge']:>7.3f}{r['pm_volume']:>11,}")


if __name__ == "__main__":
    main()
