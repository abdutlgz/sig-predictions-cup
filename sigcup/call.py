"""Election-night race calls -> data/overrides.json (the bot treats them as fair value).

    python -m sigcup.call "Georgia Senate" democratic      # D wins: D=0.99, every other party=0.01
    python -m sigcup.call "NH-01" republican --prob 0.95    # strong lean, not a call
    python -m sigcup.call --list
    python -m sigcup.call --clear "Georgia Senate"
    python -m sigcup.call --clear-all
"""
from __future__ import annotations

import argparse
import json
from typing import Optional, Sequence

from .bot import OVERRIDES
from .client import Client
from .pmfeed import build_map


def main(argv: Optional[Sequence[str]] = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("race", nargs="?", help="race name substring, e.g. 'Georgia Senate', 'NH-01', 'U.S. House'")
    ap.add_argument("winner", nargs="?", help="democratic | republican | independent")
    ap.add_argument("--prob", type=float, default=0.99, help="probability for the winner (default 0.99)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--clear", action="store_true")
    ap.add_argument("--clear-all", action="store_true")
    a = ap.parse_args(argv)

    ov = json.loads(OVERRIDES.read_text()) if OVERRIDES.exists() else {}
    if a.clear_all:
        ov = {}
    elif a.race:
        pm_map = build_map(Client())
        hits = {r["title"]: r for r in pm_map.values() if a.race.lower() in r["race"].lower()}
        races = {r["race"] for r in hits.values()}
        if len(races) != 1:
            raise SystemExit(f"'{a.race}' matches {len(races)} races: {sorted(races)[:10]}")
        for title, r in hits.items():
            if a.clear:
                ov.pop(title, None)
            else:
                win = a.winner and r["party"].startswith(a.winner.lower()[:3])
                ov[title] = a.prob if win else round(1 - a.prob, 3)
    if not a.list:
        OVERRIDES.parent.mkdir(exist_ok=True)
        OVERRIDES.write_text(json.dumps(ov, indent=1))
    for k, v in sorted(ov.items()):
        print(f"  {v:>5.2f}  {k}")
    print(f"{len(ov)} overrides active")


if __name__ == "__main__":
    main()
