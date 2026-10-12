# API connections

## 1. SIG "Super Market" API (trading)
Client: [`sigcup/client.py`](../sigcup/client.py)

- **Base URL:** `https://sig.thesuper.market/api/v1`. The OpenAPI spec is at `/api/v1/openapi.json`, with an interactive reference at `/api/v1/docs` (available to participants; not redistributed here).
- **Auth:** an API key created under *My Profile → API Keys* (scopes `read` + `trade`), sent as `Authorization: Bearer <key>`. It is stored in `.env` as `SIG_API_KEY`. Keys are bound to the organization, so the competition tournament is the default context. The client still passes `tournamentId` explicitly on every call.
- **Rate limits** are per account (all keys share one budget). The client spaces requests and retries `429/5xx` with backoff, honoring `Retry-After`. Run one bot at a time.

**Endpoints used:**
| Purpose | Endpoint |
|---|---|
| Tournament, balance | `GET /tournaments`, `GET /tournaments/{slug}` |
| Markets (237, one exchange each) | `GET /tournaments/{slug}/markets` (cursor paging) |
| Quotes for many markets at once | `GET /exchanges/prices?ids=…` (≤100 per call) |
| Order book (YES-normalized) | `GET /exchanges/{id}/orderbook?depth=…` |
| Price history (5m/1h candles) | `GET /exchanges/{id}/price-history` |
| Settlement details (AP race id, "Party Winner") | `GET /markets/{id}/nodes` |
| Orders | `POST /orders`, `POST /orders/multi-leg` (atomic, ≤10 legs), `DELETE /orders/{id}`, `POST /orders/cancel-all`, `GET /orders?status=open` |
| Portfolio | `GET /tournaments/{slug}/portfolio/positions`, `…/pnl` |
| Leaderboard | `GET /tournaments/{slug}/leaderboard?period=all\|7d\|1d` |
| Linked-market checks (none configured) | `GET /relationships`, `GET /relationships/constraints` |

**Order semantics that mattered:**
- Prices are on a **0.005 tick** between 0.005 and 0.995. A contract pays 1 if right.
- **Selling what you don't hold becomes buying the opposite side** (`sell YES @p ≡ buy NO @1−p`). Buying the side opposite your position nets it out and credits cash at once.
- **Resting buys don't hold back cash;** cash moves only on fills. Self-trade prevention is on.
- Every order needs an `idempotencyKey`. A `502 ORDER_STATUS_UNKNOWN` is retried with the same key.
- Position quantity is signed: a NO position shows as negative YES.

## 2. Polymarket (fair value)
Feed: [`sigcup/pmfeed.py`](../sigcup/pmfeed.py), mapping in [`fairvalue.py`](../sigcup/fairvalue.py). No API key needed.

| Purpose | Endpoint |
|---|---|
| Find the race's event | Gamma `GET https://gamma-api.polymarket.com/events?slug=…`. Slug patterns: `{state}-senate-election-winner`, `{state}-governor-winner-2026`, `{st}-{nn}-house-election-winner`, `which-party-will-win-the-{senate,house}-in-2026`; `public-search` as a fallback |
| Live prices (all markets, 3 batch calls) | CLOB `POST https://clob.polymarket.com/midpoints` and `/spreads` |
| History for backtests | CLOB `GET /prices-history?market=<token>&startTs=…&fidelity=5` |

228 of 237 SIG markets are mapped (`data/pm_map.json`). The rest are Alaska and California races without a Polymarket "winner" event. Markets with a Polymarket spread wider than 6 pts (10 on election night) are skipped as unreliable.

## 3. Kalshi (cross-check)
Feed: [`sigcup/kalshi.py`](../sigcup/kalshi.py). No API key needed for market data.

- `GET https://api.elections.kalshi.com/trade-api/v2/markets?series_ticker=…`
- **Series pattern:** `SENATE{ST}`, `GOVPARTY{ST}`, `HOUSE{ST}{N}`, `CONTROLS` / `CONTROLH`. Some use a `KX` prefix (e.g. `KXGOVPARTYAK`). Party markets end in `-26-D` / `-26-R`.
- **Coverage:** ~26 races. Agreement with Polymarket: median 1 pt, never more than 3.
- **Used for:** skipping trades where the two disagree by more than 3 pts, refreshed every 5 minutes in the background. Not used on election night (too slow to refresh).
