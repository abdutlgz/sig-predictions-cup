"""Live fair values from Polymarket's order book (fast batch midpoints).

Builds (once, cached in data/pm_map.json) a map: SIG exchange id -> Polymarket YES token,
then fetches all midpoints in a few batch calls (~0.3s each).
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Optional

import requests

from .arb import parse_party
from .client import ROOT, Client
from .fairvalue import fetch_event

CLOB = "https://clob.polymarket.com"
MAP_PATH = ROOT / "data" / "pm_map.json"
_http = requests.Session()


def _party_tokens(event: dict) -> Dict[str, str]:
    out = {}
    for m in event.get("markets", []):
        ql = m.get("question", "").lower()
        party = "democratic" if "democrat" in ql else "republican" if "republican" in ql else None
        if not party or m.get("closed") or not m.get("clobTokenIds"):
            continue
        out[party] = json.loads(m["clobTokenIds"])[0]  # YES token
    return out


def build_map(c: Client, refresh: bool = False) -> Dict[str, dict]:
    if MAP_PATH.exists() and not refresh:
        return json.loads(MAP_PATH.read_text())
    rows = {}
    for m in c.markets(status="open"):
        p = parse_party(m["title"])
        if p:
            party = {"democrat": "democratic"}.get(p[1].lower(), p[1].lower())
            rows[m["exchanges"][0]["id"]] = {"title": m["title"], "race": p[0], "party": party}
    races = sorted({r["race"] for r in rows.values()})
    with ThreadPoolExecutor(max_workers=8) as ex:
        events = dict(zip(races, ex.map(fetch_event, races)))
    for r in rows.values():
        ev = events.get(r["race"])
        r["pm_slug"] = ev["slug"] if ev else None
        r["pm_token"] = _party_tokens(ev).get(r["party"]) if ev else None
    MAP_PATH.parent.mkdir(exist_ok=True)
    MAP_PATH.write_text(json.dumps(rows, indent=1))
    return rows


def _batch(path: str, tokens: list) -> dict:
    out = {}
    for i in range(0, len(tokens), 100):
        r = _http.post(f"{CLOB}/{path}", json=[{"token_id": t} for t in tokens[i:i + 100]], timeout=10)
        r.raise_for_status()
        out.update(r.json())
    return out


def fair_values(pm_map: Dict[str, dict], max_spread: float = 0.06) -> Dict[str, float]:
    """SIG exchange id -> Polymarket mid (YES probability). Skips wide/illiquid books."""
    tokens = [r["pm_token"] for r in pm_map.values() if r.get("pm_token")]
    mids = _batch("midpoints", tokens)
    try:
        spreads = _batch("spreads", tokens)
    except requests.RequestException:
        spreads = {}
    out = {}
    for ex, r in pm_map.items():
        t = r.get("pm_token")
        if t and t in mids:
            if t in spreads and float(spreads[t]) > max_spread:
                continue
            out[ex] = float(mids[t])
    return out


if __name__ == "__main__":
    c = Client()
    old = json.loads(MAP_PATH.read_text()) if MAP_PATH.exists() else {}
    n_old = sum(1 for r in old.values() if r.get("pm_token"))
    m = build_map(c, refresh=True)
    if sum(1 for r in m.values() if r.get("pm_token")) < n_old - 5:  # refresh got worse: keep the old map
        MAP_PATH.write_text(json.dumps(old, indent=1))
        print(f"refresh mapped fewer markets than before ({n_old}); kept the previous mapping")
        m = old
    t = time.time()
    fv = fair_values(m)
    print(f"mapped {sum(1 for r in m.values() if r.get('pm_token'))}/{len(m)} markets; "
          f"{len(fv)} usable fair values in {time.time() - t:.2f}s")
