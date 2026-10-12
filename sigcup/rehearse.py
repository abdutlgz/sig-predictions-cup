"""Election-night rehearsal: the election preset in DRY RUN against live SIG prices, with fake
Polymarket moves injected loop by loop.  Places no orders.  Checks decisions and loop speed.

Scenario (D = Democratic YES probability):
  loop 2 : Georgia Governor swings to R 0.80          -> expect BUY R side on SIG (if SIG lags)
  loop 2 : VA-01 flickers to D 0.90 for ONE loop only  -> expect NO trade (confirmation guard)
  loop 3 : CO-03 goes D 0.92 (our wave bet is NO D)   -> expect SELL our NO D if SIG still pays more
  loop 4 : Georgia Governor called R 0.99             -> expect more buying / then CASHIN once SIG pays
  every  : RI Senate "called" D 0.995, same as today  -> expect NO trade (price did not move)

    python -m sigcup.rehearse [--loops 6]
"""
from __future__ import annotations

import argparse
import time
from typing import Dict

from .bot import PRESETS, Bot
from .client import Client

SCENARIO = [  # (from_loop, to_loop, race, democratic_probability)
    (2, 99, "Georgia Governor", 0.20),
    (2, 2, "VA-01 House race", 0.90),
    (3, 99, "CO-03 House race", 0.92),
    (4, 99, "Georgia Governor", 0.01),
    (1, 99, "Rhode Island Senate", 0.995),
]


def make_hook(pm_map: Dict[str, dict]):
    by_race: Dict[str, Dict[str, str]] = {}
    for ex, r in pm_map.items():
        by_race.setdefault(r["race"], {})[r["party"]] = ex

    def hook(loop: int, fair: Dict[str, float], overrides) -> None:
        notes = []
        for a, b, race, d in SCENARIO:
            if a <= loop <= b and race in by_race:
                legs = by_race[race]
                if "democratic" in legs:
                    fair[legs["democratic"]] = d
                if "republican" in legs:
                    fair[legs["republican"]] = round(1 - d, 3)
                notes.append(f"{race} D={d}")
        print(f"  [fake Polymarket] {'; '.join(notes) or 'no moves'}")
    return hook


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--loops", type=int, default=6)
    a = ap.parse_args()
    bot = Bot(Client(), PRESETS["election"], live=False, include_manual=True)
    bot.fair_hook = make_hook(bot.pm_map)
    print(f"REHEARSAL (dry run, election preset): {bot.cfg}\n")
    for _ in range(a.loops):
        t = time.time()
        bot.step()
        print(f"  -> loop took {time.time() - t:.1f}s (data {bot.fetch_s:.1f}s)\n")


if __name__ == "__main__":
    main()
