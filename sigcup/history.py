"""Download 5-minute price history for every mapped market: SIG candles + Polymarket prices.

    python -m sigcup.history            # -> data/hist/sig_5m.csv, data/hist/pm_5m.csv
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pandas as pd
import requests

from .client import ROOT, ApiError, Client
from .pmfeed import build_map

START = "2026-10-01T16:00:00Z"
OUT = ROOT / "data" / "hist"


def sig_candles(c: Client, ex: str) -> pd.DataFrame:
    rows, frm = [], START
    while True:
        try:
            h = c.get(f"/exchanges/{ex}/price-history", tournamentId=c.tid, resolution="5m",
                      limit=1000, **{"from": frm})
        except ApiError as e:
            print(f"  sig {ex}: {e}")
            break
        cs = h.get("candles") or []
        new = [x for x in cs if not rows or x["time"] > rows[-1]["time"]]
        rows += new
        if len(cs) < 1000 or not new:
            break
        frm = cs[-1]["time"]
    df = pd.DataFrame(rows)
    if not df.empty:
        df["exchange"] = ex
    return df


def pm_prices(token: str, ex: str) -> pd.DataFrame:
    start = int(datetime.fromisoformat(START.replace("Z", "+00:00")).timestamp())
    for _ in range(3):
        try:
            r = requests.get("https://clob.polymarket.com/prices-history",
                             params={"market": token, "startTs": start, "endTs": int(time.time()), "fidelity": 5},
                             timeout=20)
            h = r.json().get("history", [])
            df = pd.DataFrame(h)
            if not df.empty:
                df["time"] = pd.to_datetime(df["t"], unit="s", utc=True)
                df["exchange"] = ex
            return df
        except (requests.RequestException, ValueError):
            time.sleep(2)
    return pd.DataFrame()


def main() -> None:
    c = Client()
    pm_map = build_map(c)
    OUT.mkdir(parents=True, exist_ok=True)
    t = time.time()
    sig = [sig_candles(c, ex) for ex in pm_map]  # sequential: shared SIG rate limit
    sig = pd.concat([d for d in sig if not d.empty])
    sig.to_csv(OUT / "sig_5m.csv", index=False)
    print(f"SIG: {len(sig):,} candles for {sig.exchange.nunique()} exchanges ({time.time() - t:.0f}s)")
    with ThreadPoolExecutor(max_workers=6) as pool:
        pm = list(pool.map(lambda kv: pm_prices(kv[1]["pm_token"], kv[0]),
                           [(ex, r) for ex, r in pm_map.items() if r.get("pm_token")]))
    pm = pd.concat([d for d in pm if not d.empty])
    pm.to_csv(OUT / "pm_5m.csv", index=False)
    print(f"Polymarket: {len(pm):,} points for {pm.exchange.nunique()} markets ({time.time() - t:.0f}s total)")


if __name__ == "__main__":
    main()
