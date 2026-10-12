"""Election-night race board: every SIG race with its AP race id and poll-closing time (ET),
ordered the way results will arrive.  Saves data/races.csv.

    python -m sigcup.races
"""
from __future__ import annotations

import csv
from typing import Dict, Tuple

from .client import ROOT, Client
from .pmfeed import build_map, fair_values

# Last statewide poll close, Eastern Time, Nov 3 2026 (hours after midnight; 24 = midnight).
# Standard schedules -- verify against each state's SoS before election night.
POLL_CLOSE_ET: Dict[str, float] = {
    "GA": 19, "IN": 19, "KY": 19, "SC": 19, "VA": 19, "VT": 19,
    "NC": 19.5, "OH": 19.5, "WV": 19.5,
    "AL": 20, "CT": 20, "DE": 20, "FL": 20, "IL": 20, "ME": 20, "MD": 20, "MA": 20, "MS": 20,
    "MO": 20, "NH": 20, "NJ": 20, "OK": 20, "PA": 20, "RI": 20, "TN": 20, "DC": 20,
    "AR": 20.5,
    "AZ": 21, "CO": 21, "KS": 21, "LA": 21, "MI": 21, "MN": 21, "NE": 21, "NM": 21, "NY": 21,
    "ND": 21, "SD": 21, "TX": 21, "WI": 21, "WY": 21, "IA": 21,
    "MT": 22, "NV": 22, "UT": 22,
    "CA": 23, "ID": 23, "OR": 23, "WA": 23,
    "HI": 24, "AK": 25,
}
OUT = ROOT / "data" / "races.csv"


def race_details(c: Client, market_id: str) -> Tuple[str, str]:
    n = c.get(f"/markets/{market_id}/nodes", tournamentId=c.tid)["root"].get("contract_details") or {}
    return n.get("usState", ""), str(n.get("raceId", ""))


def main() -> None:
    c = Client()
    pm_map = build_map(c)
    fair = fair_values(pm_map)
    quotes = c.prices(list(pm_map))
    by_title = {m["title"]: m["id"] for m in c.markets(status="open")}
    races: Dict[str, dict] = {}
    for ex, r in pm_map.items():
        row = races.setdefault(r["race"], {"race": r["race"], "market_id": by_title.get(r["title"])})
        tag = r["party"][:3].upper()
        q = quotes.get(ex) or {}
        row[f"{tag}_sig"] = q.get("latestPrice")
        row[f"{tag}_pm"] = round(fair[ex], 3) if ex in fair else None
    for row in races.values():
        st, rid = race_details(c, row.pop("market_id"))
        row["state"], row["ap_race_id"] = st, rid
        row["polls_close_et"] = POLL_CLOSE_ET.get(st)
    rows = sorted(races.values(), key=lambda r: (r["polls_close_et"] or 99, r["race"]))
    cols = ["polls_close_et", "state", "race", "ap_race_id", "DEM_sig", "DEM_pm", "REP_sig", "REP_pm", "IND_sig", "IND_pm"]
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    fmt = lambda v: "  -  " if v is None else f"{v:.3f}"
    last = None
    for r in rows:
        if r["polls_close_et"] != last:
            last = r["polls_close_et"]
            if last is None:
                print("\n== national (chamber control) ==")
            else:
                print(f"\n== polls close {int(last) % 12 or 12}:{int(last % 1 * 60):02d} {'pm' if last < 24 else 'am'} ET ==")
        tossup = r.get("DEM_pm") is not None and 0.2 <= r["DEM_pm"] <= 0.8
        print(f"  {'*' if tossup else ' '} {r['race']:<26} D sig {fmt(r.get('DEM_sig'))} pm {fmt(r.get('DEM_pm'))}"
              f" | R sig {fmt(r.get('REP_sig'))} pm {fmt(r.get('REP_pm'))}")
    print(f"\n{len(rows)} races -> {OUT.relative_to(ROOT)}   (* = competitive, where election night moves most)")


if __name__ == "__main__":
    main()
