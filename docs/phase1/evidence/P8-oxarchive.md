# P8 — 0xArchive coverage (free tier)

Status: unverified (Step 3, the live-log comparison, is pending part B — see Open issues)

## What ran
- Command: `OXARCHIVE_API_KEY=... FUNDR_DATA=data/phase1 uv run python probes/p08_oxarchive.py`, plus a throwaway replay snippet (`/private/tmp/claude-501/replay_check.py`, not committed) for Step 4.
- Run date (UTC): 2026-09-19, ~13:00–13:07.
- Markets: all 7 P0 sample coins (ENA, PONS, ONDO, ETHFI, JUP, KAITO, BTC) for coverage; ENA + BTC for raw funding/OI/trades/L3 pulls (per the brief's script).
- Dates / windows sampled: raw-data calls used `start`/`end` = the P9 live-log window available at run time, **2026-09-19 11:27:23 → 12:10:29 UTC (~43 minutes)** — the P9 live watch was only ~1 hour into its 4-hour run, so this is a partial window, not the full P9 span. Coverage/catalog calls are not date-scoped and returned full history.

## What came back
- Row counts / sizes: `symbols.json` 7.9 MB (full symbol/exchange catalog — includes non-sample markets and venues, e.g. `hip4`). `lighter_instruments.json` 89 KB, 271 Lighter markets. 14 coverage files (7 coins × 2 venues), 1.5–10 KB each. `lighter_funding_raw.parquet` (8 KB) and `hl_funding_raw.parquet` (3.5 KB) cover ENA+BTC over the ~43-min window: 402 Lighter rows, 70 HL rows. Sample pages: `lighter_oi_page.json` (24 KB), `hl_oi_page.json` (39 KB), `lighter_trades_page.json` (273 B, **empty**), `hl_trades_page.json` (58 KB), `lighter_l3_current.json` (79 KB, full L3 book snapshot with `order_index`/`owner_account_index`/price/size per order). `calls.json`: 25 calls this run (37 total across the session including two setup/schema-discovery calls and one earlier crashed attempt).
- Schema: `/v1/{venue}/funding/{coin}` → `{coin, symbol, timestamp, funding_rate}` for Lighter; Hyperliquid adds `premium`. **Lighter's REST funding route has no `premium` field** — a deviation from the brief's script, which assumed both venues return it (see Open issues / Deviations). `/v1/lighter/openinterest/{coin}` → adds `mark_price, oracle_price, index_price`. `/v1/{venue}/trades/{coin}` → per-fill records (`side, price, size, tx_hash, trade_id, order_id, user_address, direction, fee`). `/v1/lighter/l3orderbook/{coin}` → full current book, one row per resting order.
- Excerpt (Lighter funding, `/v1/lighter/funding/ENA`, `limit=3`):
  ```
  {"coin": "ENA", "symbol": "ENA", "timestamp": "2026-09-18T12:01:43.945Z", "funding_rate": "0.000012"}
  ```
  Excerpt (HL funding, same call shape): adds `"premium": "0.0007409539"`.

## Findings

**Coverage per sample market (from `data-quality/coverage`):**

| Coin | Lighter funding earliest | Lighter funding cadence (sample) | Lighter completeness | HL funding earliest | HL cadence | HL completeness |
|---|---|---|---|---|---|---|
| ENA | 2025-08-25 | median 10s, p95 13s | 95.1% | 2024-04-02 | median 61s | 100% |
| PONS | 2026-09-02 | median 11s, p95 15s | 97.9% | 2026-08-31 | median 61s | 100% |
| ONDO | 2025-08-25 | median 10s, p95 13s | 94.5% | 2024-01-20 | median 61s | 100% |
| ETHFI | 2025-08-25 | median 10s, p95 13s | 95.2% | 2024-03-21 | median 61s | 100% |
| JUP | 2025-08-25 | median 10s, p95 15s | 90.5% | 2023-12-03 | median 61s | 100% |
| KAITO | 2025-08-25 | median 10s, p95 19s | 85.0% | 2025-02-20 | median 61s | 100% |
| BTC | 2025-08-25 | median 10s, p95 11s | 97.9% | 2023-05-20 | median 61s | 100% |

- Lighter's earliest catalogued date is 2025-08-25 for every market except PONS (newly listed, 2026-09-02) — this looks like when 0xArchive started ingesting Lighter, not per-market listing dates. HL history goes back to each market's real HL listing date (2023–2025), consistently 100% complete with no gaps in any sample coin. HL is the more reliable/longer-history venue on this vendor.
- Every Lighter data type (`funding`, `open_interest`) reports **identical earliest/latest/cadence/completeness/gaps** for a given coin — 0xArchive appears to source both from the same underlying Lighter market-stats stream, not independent feeds. `orderbook`, `l3_orderbook`, and `fills` (trades) are distinct feeds with their own cadence (60s, ~245s, and highly variable respectively).
- **Coverage endpoint's `total_records` field is inconsistent with its own `cadence.sample_count`** by a factor of ~900–9,000× (e.g., Lighter ENA funding: `total_records=74,469,933` vs `sample_count=8,211`; HL ENA funding: `total_records=1,282,960` vs `sample_count=1,423`). `sample_count` matches the reported median-10s-cadence story; `total_records` implies sub-second-level events. Not resolved here — flagged as an open issue since it changes the Step-5 cost estimate by ~1000×.
- **Lighter trades (`fills`) lag real time.** A request for the last hour of Lighter ENA trades returned zero rows, with `meta.finalized_through` and `clamped_to` both set to **2026-09-18T00:12:41Z — about 36 hours before the request**. HL trades for the same window returned data immediately with per-fill detail (wallet, tx hash, fee). This is a real latency/finalization gap on the Lighter side of this vendor, independent of free-tier limits.
- **Units — `funding_rate`.** Lighter's own native format (per P5 evidence) reports `rate` as **percent per hour, truncated to 4 dp** (e.g. `0.0012` = 0.0012%/hr). 0xArchive's raw Lighter `funding_rate` for the same coin/period (`0.000012`) is exactly `0.0012% ÷ 100` — i.e. **0xArchive stores Lighter funding as a fraction**, matching the brief's anticipated ~0.01 ratio. This is inferred from the P5 baseline-rate identity, not yet cross-checked minute-by-minute against the P9 live log (that join is Step 3, pending part B).
- **Intra-hour movement (partial-window signal only, not the full Step 3 comparison):** over the ~43-minute window pulled so far, Lighter raw `funding_rate` did not change within any hour bucket (0/1 hours moved) while HL's did (1/2 hours moved). With under an hour of data this is not a reliable signal either way — needs the full P9 window (part B).
- **L3 order book** (`/v1/lighter/l3orderbook/{coin}`) is a full current snapshot (no time param) with per-order `order_index`, `owner_account_index`, `side`, `price`, `remaining_size`, `original_size` — genuine L3 depth, not aggregated L2.
- **Replay (Step 4):** connected to `wss://api.0xarchive.io/ws` (documented at `/websocket/connection.md`), authenticated with `Authorization: Bearer <key>` header (not a query param — browsers can't do this, so server-side only). Sent:
  ```json
  {"op": "replay", "channel": "funding", "symbol": "ENA", "start": <10 min ago - 10 min>, "end": <10 min ago>, "speed": 100}
  ```
  **Accepted on the free tier.** Response events: `replay_started` (echoes channel/symbol/start/end and the *actual* speed applied) then one `historical_data` message per event, e.g.:
  ```json
  {"type": "historical_data", "channel": "funding", "coin": "ENA", "symbol": "ENA", "timestamp": 1789818983412,
   "data": {"coin": "ENA", "fundingRate": "0.0000652046", "premium": "0.0007854226", "timestamp": 1789818983412}}
  ```
  Two findings: (1) the requested `speed: 100` (100×) was **silently clamped to 10×** — the vendor caps replay speed regardless of tier as tested; (2) the replay payload's `data` object **includes `premium` for Lighter**, even though the plain REST `/v1/lighter/funding/{coin}` route does not — the WS replay and REST catalog schemas disagree for the same underlying data. Full messages saved to `data/phase1/p08/replay.json` (2 messages captured in a 20–30s listen window at 10× speed; the 10-minute window would take ~60s wall-clock to fully replay at that cap).
- **HL fallback coverage:** every HL data type checked (funding, open_interest, orderbook, fills) was 100% complete with zero gaps across all 7 sample coins — HL is a clean fallback source on this vendor if Lighter's gaps/lag are disqualifying.

## Cost estimate (Step 5)

Credit headers only appear on data-payload routes (`instruments`, `funding`, `openinterest`, `trades`, `l3orderbook`); `symbols` and `data-quality/coverage` calls returned **no credit header at all** (free). Observed `x-credits-used` deltas this run:

| Route | Credits |
|---|---|
| `/v1/lighter/instruments` (full catalog, 271 markets) | 7 |
| `/v1/lighter/funding/ENA` (1 page, ~230 rows, 43-min window) | 2 |
| `/v1/lighter/funding/BTC` (1 page, fewer rows) | 1 |
| `/v1/hyperliquid/funding/{ENA,BTC}` (1 page each) | 1 each |
| `/v1/{venue}/openinterest/ENA` | 1 each |
| `/v1/{venue}/trades/ENA` | 1 each |
| `/v1/lighter/l3orderbook/ENA` (full current book) | 4 |

Total credits used across this whole session (setup calls + one crashed attempt + this run): **20 of 50,000** monthly free-tier credits (`x-credits-remaining: 49,980`). Well under the 10,000-remaining stop threshold.

**Extrapolation to research scale (50 markets × Lighter raw funding + OI + trades), two ways — arithmetic shown, not reconciled (see the `total_records` vs `sample_count` finding above):**

- *Low estimate*, using the cadence-implied row count (median ~10s interval ⇒ ~2.9M rows/market/year for funding, same for OI since they share a feed; trades assumed similar order of magnitude): ≈ 50 × 3 × 2.9M rows ≈ 435M rows ÷ 1,000 rows/page ≈ 435,000 pages × ~1.5 credits/page (average of the 1–2 credit/page rates above) ≈ **~650,000 credits**.
- *High estimate*, using the coverage endpoint's own `total_records` for ENA as a per-market proxy (74.5M funding + 74.5M OI + 18.6M fills ≈ 167.5M rows for **one** market) × 50 markets ÷ 1,000 rows/page × ~1.5 credits/page ≈ **~12.6 million credits**.

Both figures exceed the free tier's 50,000 credits/month by 13×–250×, so a full 50-market backfill is not viable on free tier regardless of which estimate is right. Against paid plans: **Build ($49/mo, 80M credits)** comfortably covers even the high estimate (12.6M ≈ 16% of the monthly quota), with room to spare for OI/trades on Hyperliquid too. **Pro ($199/mo, 400M credits)** is unnecessary at this scale unless the true row counts are closer to the high estimate *and* full L3/orderbook history is also wanted.

**Free-tier 30-day limit:** data-payload calls (funding/OI/trades/L3) are restricted to roughly the last 30 days regardless of what the `coverage` endpoint reports as available (coverage shows full history back to each market's listing date, but we did not test — and did not need to test, since all our calls stayed inside today — whether a data call with `start` older than 30 days is rejected). Per the brief and 0xArchive's own tier structure, **any of the "50 markets × 365 days" backfill above requires Build+ or higher** to actually pull data older than 30 days; Build's base tier alone may not unlock the lookback even though it has the credits.

## Open issues
- Step 3 (compare 0xArchive raw funding to the P9 live log, minute-by-minute) is **pending part B**: the P9 live watch had under an hour recorded when this probe ran; the controller will resume this task after the full 4-hour window completes (~15:40 UTC) to rerun against the complete window and finalize the units/intra-hour findings.
- `total_records` vs `cadence.sample_count` inconsistency (900–9,000×) in the coverage endpoint is unresolved and materially changes the Step 5 credit estimate; worth a small confirming pull (e.g., paginate one market's full funding history and count actual pages/credits) before committing budget.
- Whether Build's base tier (vs. an explicit "Build+" add-on) actually unlocks data calls older than 30 days was not verified — inferred from the brief, not tested against the vendor's own plan docs.
- Lighter trades/fills finalization lag (~36h observed) is based on a single empty-result call; not repeated at a different time of day to see if it's constant.
