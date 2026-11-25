"""Thin client for the Super Market API (Predictions Cup). Spec: docs/openapi.json"""
from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Any, Iterable, Optional

import requests

BASE_URL = "https://api.thesuper.market/api/v1"
ROOT = Path(__file__).resolve().parent.parent
RETRY_STATUS = {429, 500, 502, 503, 504}
TICK = 0.005


def load_env(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def on_tick(p: float) -> float:
    return round(round(p / TICK) * TICK, 3)


class ApiError(Exception):
    def __init__(self, status: int, body: Any):
        self.status, self.body = status, body
        err = body.get("error", {}) if isinstance(body, dict) else {}
        self.code = err.get("code")
        super().__init__(f"HTTP {status} {self.code}: {err.get('message', body)}")


class Client:
    def __init__(self, api_key: Optional[str] = None, base_url: str = BASE_URL,
                 min_interval: float = 0.05, max_retries: int = 5):
        load_env()
        key = api_key or os.environ.get("SIG_API_KEY")
        if not key:
            raise SystemExit("SIG_API_KEY missing: put it in .env (see .env.example)")
        self.base = base_url.rstrip("/")
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {key}", "Accept": "application/json"})
        self.min_interval, self.max_retries = min_interval, max_retries
        self._last = 0.0
        self._tournament: Optional[dict] = None

    # ---- transport -------------------------------------------------------
    def request(self, method: str, path: str, params: Optional[dict] = None,
                json: Optional[dict] = None) -> Any:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        for attempt in range(self.max_retries + 1):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                r = self.s.request(method, self.base + path, params=params, json=json, timeout=20)
            except requests.RequestException:
                if attempt == self.max_retries:
                    raise
                time.sleep(2 ** attempt * 0.5)
                continue
            # Orders carry an idempotencyKey, so retrying identical POSTs is safe
            # (incl. 502 ORDER_STATUS_UNKNOWN, per the API docs).
            if r.status_code in RETRY_STATUS and attempt < self.max_retries:
                ra = r.headers.get("Retry-After")
                time.sleep(float(ra) if ra and ra.replace(".", "").isdigit() else 2 ** attempt * 0.5)
                continue
            body = r.json() if r.content else None
            if r.status_code >= 400:
                raise ApiError(r.status_code, body)
            return body
        raise RuntimeError("unreachable")

    def get(self, path: str, **params: Any) -> Any:
        return self.request("GET", path, params=params)

    def post(self, path: str, body: dict) -> Any:
        return self.request("POST", path, json=body)

    # ---- account / tournament -------------------------------------------
    def account(self) -> dict:
        return self.get("/account")

    def tournaments(self) -> list:
        return self.get("/tournaments", limit=100)["data"]

    def tournament(self, slug: Optional[str] = None) -> dict:
        """Resolve the competition tournament (env SIG_TOURNAMENT, else best guess)."""
        if self._tournament and not slug:
            return self._tournament
        slug = slug or os.environ.get("SIG_TOURNAMENT") or None
        if not slug:
            ts = self.tournaments()
            if not ts:
                raise SystemExit("No tournaments visible: join the Predictions Cup on the site first")
            active = [t for t in ts if str(t.get("status", "")).lower() in ("active", "open", "live")] or ts
            pick = next((t for t in active if "midterm" in t["name"].lower()), active[0])
            slug = pick["slug"]
        self._tournament = self.get(f"/tournaments/{slug}")
        return self._tournament

    @property
    def tid(self) -> str:
        return self.tournament()["id"]

    @property
    def slug(self) -> str:
        return self.tournament()["slug"]

    def balance(self) -> float:
        for t in self.tournaments():
            if t["id"] == self.tid:
                return float(t.get("myBalance") or 0)
        return 0.0

    # ---- market data -----------------------------------------------------
    def markets(self, status: Optional[str] = None) -> list:
        out, cursor = [], None
        while True:
            page = self.get(f"/tournaments/{self.slug}/markets", limit=100, cursor=cursor, status=status)
            out += page["data"]
            cursor = page["pagination"].get("nextCursor")
            if not page["pagination"].get("hasMore") or not cursor:
                return out

    def prices(self, exchange_ids: Iterable[str]) -> dict:
        ids, out = list(exchange_ids), {}
        for i in range(0, len(ids), 100):
            res = self.get("/exchanges/prices", ids=",".join(ids[i:i + 100]), tournamentId=self.tid)
            out.update({q["exchangeId"]: q for q in res["data"]})
        return out

    def orderbook(self, exchange_id: str, depth: int = 10) -> dict:
        """YES-normalized book: bids desc, asks asc."""
        return self.get(f"/exchanges/{exchange_id}/orderbook", depth=depth, tournamentId=self.tid)

    def relationships(self, type_: Optional[str] = None) -> list:
        out, cursor = [], None
        while True:
            page = self.get("/relationships", type=type_, limit=100, cursor=cursor, tournamentId=self.tid)
            out += page["data"]
            cursor = page["pagination"].get("nextCursor")
            if not page["pagination"].get("hasMore") or not cursor:
                return out

    def constraint_violations(self, min_violation: float = 0.01) -> list:
        return self.get("/relationships/constraints", violationsOnly="true",
                        minViolation=min_violation, tournamentId=self.tid)["data"]

    def leaderboard(self, **params: Any) -> Any:
        return self.get(f"/tournaments/{self.slug}/leaderboard", **params)

    # ---- portfolio -------------------------------------------------------
    def positions(self) -> dict:
        return self.get(f"/tournaments/{self.slug}/portfolio/positions")

    def open_orders(self) -> Any:
        return self.get("/orders", tournamentId=self.tid)

    # ---- trading ---------------------------------------------------------
    def leg(self, exchange_id: str, side: str, action: str, quantity: int,
            price: Optional[float] = None) -> dict:
        o = {"exchangeId": str(exchange_id), "side": side, "action": action,
             "quantity": int(quantity), "tournamentId": self.tid}
        if price is not None:
            o["price"] = on_tick(price)
        return o

    def place_order(self, exchange_id: str, side: str, action: str, quantity: int,
                    price: Optional[float] = None, key: Optional[str] = None) -> dict:
        body = self.leg(exchange_id, side, action, quantity, price)
        body["idempotencyKey"] = key or f"o-{uuid.uuid4()}"
        return self.post("/orders", body)

    def multi_leg(self, legs: list, key: Optional[str] = None,
                  relationship: Optional[str] = None) -> dict:
        """Atomic: all legs fill/rest or none are placed."""
        body = {"legs": legs, "idempotencyKey": key or f"ml-{uuid.uuid4()}"}
        if relationship:
            body["relationshipConstraint"] = relationship
        return self.post("/orders/multi-leg", body)

    def cancel(self, order_id: Any) -> Any:
        return self.request("DELETE", f"/orders/{order_id}")

    def cancel_all(self) -> Any:
        return self.post("/orders/cancel-all", {"tournamentId": self.tid})
