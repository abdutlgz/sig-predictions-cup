"""Kalshi as a second fair-value source (public market data, no key needed).

Maps each SIG race to a Kalshi series: Senate -> SENATE{ST}, Governor -> GOVPARTY{ST},
House -> HOUSE{ST}{N}, chambers -> CONTROLS / CONTROLH (2026 event).  Party markets end in
-26-D / -26-R.

    python -m sigcup.kalshi          # compare SIG vs Polymarket vs Kalshi, save data/kalshi_compare.csv
"""
from __future__ import annotations

import csv
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Optional, Tuple

import requests

from .client import ROOT, Client
from .pmfeed import build_map, fair_values

API = "https://api.elections.kalshi.com/trade-api/v2/markets"
STATES = {"Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA", "Colorado": "CO",
          "Connecticut": "CT", "Delaware": "DE", "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID",
          "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA",
          "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN",
          "Mississippi": "MS", "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
          "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY", "North Carolina": "NC",
          "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR", "Pennsylvania": "PA",
          "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX",
          "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA", "West Virginia": "WV",
          "Wisconsin": "WI", "Wyoming": "WY"}
_http = requests.Session()


def series_for(race: str) -> Optional[str]:
    r = re.sub(r"\s+race$", "", race.strip(), flags=re.I)
    if r == "U.S. Senate":
        return "CONTROLS"
    if r == "U.S. House":
        return "CONTROLH"
    m = re.match(r"^([A-Z]{2})-(\d{2})\s+House", r)
    if m:
        return f"HOUSE{m[1]}{int(m[2])}"
    m = re.match(r"^(.+?)\s+(Senate|Governor)$", r)
    if m and m[1] in STATES:
        return ("SENATE" if m[2] == "Senate" else "GOVPARTY") + STATES[m[1]]
    return None


def party_mids(series: str) -> Dict[str, Tuple[float, float]]:
    """party -> (mid, volume) for the 2026 election (tries the KX-prefixed series too)."""
    for name in (series, "KX" + series):
        try:
            ms = _http.get(API, params={"series_ticker": name, "limit": 50}, timeout=15).json().get("markets", [])
        except (requests.RequestException, ValueError):
            ms = []
        out = _parse(ms)
        if out:
            return out
    return {}


def _parse(markets) -> Dict[str, Tuple[float, float]]:
    out = {}
    for m in markets:
        t = m["ticker"]
        if m.get("status") not in ("active", "open"):
            continue
        if "-2028-" in t or not re.search(r"-(26|2026)-(D|R)$", t):
            continue
        try:
            bid, ask = float(m["yes_bid_dollars"]), float(m["yes_ask_dollars"])
        except (KeyError, TypeError, ValueError):
            continue
        if ask <= 0 or ask - bid > 0.10:
            continue
        party = "democratic" if t.endswith("-D") else "republican"
        out[party] = ((bid + ask) / 2, float(m.get("volume_fp") or m.get("volume") or 0))
    return out


def kalshi_fair(pm_map: Dict[str, dict]) -> Dict[str, Tuple[float, float]]:
    """SIG exchange id -> (Kalshi mid, Kalshi volume)."""
    series = {r["race"]: series_for(r["race"]) for r in pm_map.values()}
    uniq = sorted({s for s in series.values() if s})
    with ThreadPoolExecutor(max_workers=8) as pool:
        data = dict(zip(uniq, pool.map(party_mids, uniq)))
    out = {}
    for ex, r in pm_map.items():
        s = series.get(r["race"])
        if s and r["party"] in data.get(s, {}):
            out[ex] = data[s][r["party"]]
    return out


def main() -> None:
    c = Client()
    pm_map = build_map(c)
    pm = fair_values(pm_map)
    ks = kalshi_fair(pm_map)
    q = c.prices(list(pm_map))
    rows = []
    for ex, r in pm_map.items():
        if ex not in pm or ex not in ks or not (q.get(ex) or {}).get("bestAsk"):
            continue
        sig = (q[ex]["bestBid"] + q[ex]["bestAsk"]) / 2
        rows.append({"title": r["title"], "sig": round(sig, 3), "pm": round(pm[ex], 3), "kalshi": round(ks[ex][0], 3),
                     "kalshi_vol": round(ks[ex][1]), "pm_minus_kalshi": round(pm[ex] - ks[ex][0], 3),
                     "sig_minus_pm": round(sig - pm[ex], 3), "sig_minus_kalshi": round(sig - ks[ex][0], 3)})
    with open(ROOT / "data" / "kalshi_compare.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    import statistics as st
    d = [abs(x["pm_minus_kalshi"]) for x in rows]
    print(f"{len(rows)} markets on all three. |Polymarket - Kalshi|: median {st.median(d):.3f}, "
          f">3pts: {sum(x > 0.03 for x in d)}, >5pts: {sum(x > 0.05 for x in d)}")
    agree = [x for x in rows if abs(x["pm_minus_kalshi"]) <= 0.02 and abs(x["sig_minus_pm"]) >= 0.04]
    print(f"Edges CONFIRMED by both (PM & Kalshi agree within 2pts, SIG off by 4+): {len(agree)}")
    dis = sorted([x for x in rows if abs(x["pm_minus_kalshi"]) > 0.03], key=lambda x: -abs(x["pm_minus_kalshi"]))
    print("Biggest Polymarket/Kalshi disagreements (edges here are less certain):")
    for x in dis[:12]:
        print(f"  {x['title'][9:62]:<54} SIG {x['sig']:.3f}  PM {x['pm']:.3f}  Kalshi {x['kalshi']:.3f}"
              f"  (vol {x['kalshi_vol']:,})")


if __name__ == "__main__":
    main()
