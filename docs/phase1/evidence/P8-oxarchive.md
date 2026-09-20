# P8 — 0xArchive coverage (free tier)

Status: verified — Outcome A verdict reached for both venues (see Findings). Neither venue's 0xArchive raw funding series validates as an archived intra-hour Target C source; reasons differ by venue and are given below.

## What ran
- Command (part A): `OXARCHIVE_API_KEY=... FUNDR_DATA=data/phase1 uv run python probes/p08_oxarchive.py` against a partial ~43-min P9 window, plus a throwaway replay snippet (`/private/tmp/claude-501/replay_check.py`, not committed) for Step 4.
- Command (part B): the same probe rerun once `data/phase1/p09/live.jsonl` was final, plus a throwaway comparison script (`/private/tmp/claude-501/step3_compare.py`, not committed) that joins 0xArchive's raw funding against the P9 live log and against each venue's own settled-funding ground truth (`LighterAPI.fundings_all`, `HLInfo.funding_history`).
- Run dates (UTC): part A 2026-09-19 ~13:00–13:07; part B 2026-09-19 ~23:58–00:03 (probe rerun) and immediately after (comparison script; no extra 0xArchive credits — it only reads the already-saved parquet files plus each venue's own free public API).
- Markets: all 7 P0 sample coins (ENA, PONS, ONDO, ETHFI, JUP, KAITO, BTC) for coverage; ENA + BTC for raw funding/OI/trades/L3 pulls and the Step 3 comparison (per the brief's script).
- Dates / windows sampled: the final probe run and Step 3 comparison use the **full P9 window, 2026-09-19 11:27:23 → 16:54:35 UTC (5.45h, ≥5 hourly settlements, 6,777 live records)**. Coverage/catalog calls are not date-scoped and return full history as reported by the vendor.

## What came back
- Row counts / sizes: `symbols.json` ~8 MB (full symbol/exchange catalog, all venues). `lighter_instruments.json` 89 KB, 271 Lighter markets. 14 coverage files (7 coins × 2 venues). `lighter_funding_raw.parquet` and `hl_funding_raw.parquet` cover ENA+BTC over the full 5.45h window: **3,842 Lighter rows, 648 HL rows**. Sample pages: OI/trades pages for ENA on both venues, `lighter_l3_current.json` (full L3 book snapshot). `calls.json`: 27 calls in the part-B run (64 total across the whole session, including part A's 25, two schema-discovery calls, and one earlier crashed attempt).
- Schema: `/v1/{venue}/funding/{coin}` → `{coin, symbol, timestamp, funding_rate}` for Lighter; Hyperliquid adds `premium`. **Lighter's REST funding route has no `premium` field** — a deviation from the brief's script, which assumed both venues return it (handled in code; see `probes/p08_oxarchive.py`). `/v1/lighter/openinterest/{coin}` adds `mark_price, oracle_price, index_price`. `/v1/{venue}/trades/{coin}` → per-fill records. `/v1/lighter/l3orderbook/{coin}` → full current book, one row per resting order.
- Excerpt (Lighter funding): `{"coin": "ENA", "symbol": "ENA", "timestamp": "...", "funding_rate": "0.000064"}`. Excerpt (HL funding): adds `"premium": "..."`.

## Findings

**Coverage per sample market (from `data-quality/coverage`, refreshed part-B numbers):**

| Coin | Lighter funding earliest | Lighter cadence | Lighter completeness | HL funding earliest | HL cadence | HL completeness |
|---|---|---|---|---|---|---|
| ENA | 2025-08-25 | median 10s | 96.9% | 2024-04-02 | median 61s | 100% |
| PONS | 2026-09-02 | median 11s | 97.3% | 2026-08-31 | median 61s | 100% |
| ONDO | 2025-08-25 | median 10s | 95.5% | 2024-01-20 | median 61s | 100% |
| ETHFI | 2025-08-25 | median 10s | 94.3% | 2024-03-21 | median 61s | 100% |
| JUP | 2025-08-25 | median 10s | 91.6% | 2023-12-03 | median 61s | 100% |
| KAITO | 2025-08-25 | median 10s | 84.2% | 2025-02-20 | median 61s | 100% |
| BTC | 2025-08-25 | median 10s | 97.4% | 2023-05-20 | median 61s | 100% |

- Lighter's earliest catalogued date is 2025-08-25 for 6/7 sample coins (PONS newly listed 2026-09-02) — looks like when 0xArchive started ingesting Lighter, not per-market listing dates. HL history goes back to each market's real listing date (2023–2025), consistently 100% complete with zero gaps on every sample coin.
- Every Lighter data type (`funding`, `open_interest`) reports identical earliest/latest/cadence/completeness/gaps for a given coin — 0xArchive sources both from the same underlying Lighter market-stats stream, not independent feeds.

**Units (confirmed).** Lighter's own settled `rate` (per P5 evidence, cross-checked here directly against `/api/v1/fundings`) is **percent per hour** (e.g. `0.0012` = 0.0012%/hr, its baseline value). 0xArchive's raw Lighter `funding_rate` is a **fraction that is numerically identical to Lighter's live `current_funding_rate` (percent) ÷ 100** — confirmed by joining the raw series against the P9 live log: median ratio `ox_funding_rate / current_funding_rate` = **0.01 exactly**, n=466 joined rows. HL: 0xArchive's raw `funding_rate` is **already a fraction, identical to HL's own live `funding` field** — median ratio 1.0 exactly, n=466 joined rows. No conversion is needed for HL; Lighter values must be read as fractions directly (already ÷100 relative to Lighter's native percent display).

**Step 3 decisive test — does 0xArchive's raw funding satisfy Outcome A (moves within the hour, and its last in-hour value equals the closing settlement within tolerance)?**

*Moves-within-hour, full 5.45h window, 12 market-hours total (2 coins × ~6 hours):*
| Venue | Hours with >1 distinct value |
|---|---|
| Lighter | **0 / 12** |
| Hyperliquid | **6 / 12** |

*Last-in-hour value vs the venue's own settled rate at hour-close, all 12 hours and the informative subset (hours where the settled rate actually changed from the previous hour):*
| Venue | Tolerance | All hours match | Informative-subset match |
|---|---|---|---|
| Lighter | 1e-6 (1 ULP of Lighter's 4dp percent format) | 6/12 | **0/5** |
| Hyperliquid | 1e-10 (P10's tolerance) | 7/12 | 1/5 (median abs err 0, max abs err 1.5e-6) |

**Lighter verdict: FAILS Outcome A.** 0xArchive's Lighter `funding_rate` never moves within an hour (0/12) — it is not an intra-hour running signal at all. Direct comparison shows why: within interval `[T-1h, T)` the field holds the *previous* settlement (`funding_rate` = "last completed settlement", per the established P9/P10 framing), then jumps to the new settled value only once settlement `T` posts. The "6/12 all-hours" matches above are an artifact of BTC's funding sitting at a flat interest-rate baseline for several consecutive hours (so the stale previous value happens to equal the new one); the moment the rate actually changes hour-to-hour, the naive same-bucket comparison fails 0/5. **0xArchive is not a source for Lighter's live/intra-hour current funding — it is a (redundant) mirror of Lighter's own settled-funding series**, arriving at ~10s polling cadence rather than genuinely updating intra-hour. Since Lighter's own native `/api/v1/fundings` already provides this settled series with a longer, gap-free history for our sample markets (see P5), 0xArchive adds no capability here for Target C; it could still function as an independent cross-check of settled values, but not as a builder of the running-value target.

**Hyperliquid verdict: NOT VALIDATED (same known limitation as HL's live source, not a 0xArchive-specific failure).** 0xArchive's HL `funding_rate` does move within the hour (6/12, matching known live running-estimate behavior) and its last-in-hour value tracks the closing settlement closely (median error 0, max ~1.5e-6) — but this falls well short of the ≥99%-of-hours-within-tolerance bar the spec requires for Outcome A (only 7/12 = 58% at HL's 1e-10 tolerance, 1/5 on the informative subset). This mirrors the already-established P10 finding that HL's running estimate never exactly equals the settled rate (errors 1e-7–1e-6 against a 1e-10 tolerance) — 0xArchive is faithfully replaying HL's own live feed, with the same intrinsic gap, not introducing a new error source. **0xArchive can reconstruct HL's historical running-funding signal (useful if no other historical source for it exists), but that signal itself does not pass the strict Outcome A equality bar — the same conclusion already reached for HL's live source directly.**

**Other findings:**
- **Lighter trades (`fills`) lag real time**, observed twice: part A showed `finalized_through`/`clamped_to` ~36h behind a real-time request; part B (≈11h later) showed the same field at **2026-09-19T00:12:14Z, ~16.7h behind** the ~16:54 request time. The lag shrank between runs in a way consistent with an offline batch/finalization process rather than a fixed rolling delay, but both calls confirm Lighter trades are not a live feed on this vendor. HL trades for the same windows returned data immediately.
- **L3 order book** (`/v1/lighter/l3orderbook/{coin}`) is a full current snapshot (no time param) with per-order `order_index`, `owner_account_index`, `side`, `price`, `remaining_size`, `original_size` — genuine L3 depth.
- **Replay (Step 4):** connected to `wss://api.0xarchive.io/ws`, authenticated with an `Authorization: Bearer <key>` header (not a query param — browser clients can't set this, server-side only). Sent `{"op": "replay", "channel": "funding", "symbol": "ENA", "start": ..., "end": ..., "speed": 100}`. **Accepted on the free tier.** Findings: (1) requested `speed: 100` was silently clamped to **10×**; (2) the replay payload's nested `data` object **includes `premium` for Lighter**, even though the plain REST `/v1/lighter/funding/{coin}` route doesn't — WS replay and REST catalog schemas disagree for the same underlying data. Messages saved to `data/phase1/p08/replay.json` (not committed).
- **HL fallback coverage:** every HL data type checked was 100% complete with zero gaps across all 7 sample coins — a clean fallback source on this vendor generally, though for the specific Outcome A purpose it inherits HL's own known limitation (above).

## Cost estimate (Step 5)

Credit headers only appear on data-payload routes; `symbols` and `data-quality/coverage` calls are free (no header). Per-route costs (consistent across both runs): `lighter/instruments` ~7–10 credits (full catalog), `funding`/`openinterest`/`trades` pages ~1–2 credits each, `l3orderbook` (full current book) ~4 credits.

**Total credits used across the whole session (both probe runs, setup calls, one crashed attempt): 32.** The account's monthly quota also changed between runs (50,000 → 200,000; remaining 199,968 after part B) — this appears to be a plan/quota change on the vendor's side between sessions, not something this probe did. Both runs stayed far above the 10,000-remaining stop threshold at all times; never BLOCKED.

**Extrapolation to research scale (50 markets × Lighter raw funding + OI + trades) — now narrowed.** Part A flagged a ~1000× spread between two estimation methods because the coverage endpoint's `total_records` field (e.g. Lighter ENA funding: 74.47M) is 900–9,000× larger than its own `cadence.sample_count` (8,373) for the same data type. Part B adds a direct measurement: the actual rows pulled over the 5.45h window (3,842 rows for 2 coins) imply **~10.2s/row/coin**, matching `cadence.median_interval_seconds` almost exactly, and `total_records` for Lighter ENA funding grew by only **4,180** between the two probe runs (~11h9m apart) — almost exactly what a steady 10s cadence predicts (~4,014), not the sub-second rate the absolute total_records figure would otherwise imply. This means: **the low (cadence-based) estimate is the credible one for current/forward-looking ingestion cost; the large absolute `total_records` figure looks like a stale, mis-scaled, or otherwise unreliable field on this vendor and should not be used for budgeting.** (By contrast, Lighter `fills.total_records` for ENA grew by ~13,062 over the same ~11h gap, a rate that roughly matches its own reported cadence — that field looks trustworthy where funding/OI's does not.)

Using the corroborated low estimate: ~2.9M funding rows/market/year (same for OI, shared feed) × 50 markets × 2 data types ≈ 290M rows, plus trades (highly variable per market, using observed per-coin cadences, order 10–50M rows across 50 markets/year) ≈ **~320–340M rows total** ÷ 1,000 rows/page ÷ ~1.5 credits/page ≈ **~480,000–510,000 credits** for a full 50-market, 1-year Lighter funding+OI+trades backfill (revising part A's ~650,000-credit low estimate down modestly with the corroborated cadence).

This exceeds the free tier's 50,000 (or the account's current 200,000) monthly credits, so a full 50-market backfill is not viable on free tier. **Build ($49/mo, 80M credits)** comfortably covers it (~0.6% of the monthly quota). **Pro ($199/mo, 400M credits)** is unnecessary at this scale.

**Free-tier 30-day limit:** data-payload calls (funding/OI/trades/L3) are restricted to roughly the last 30 days regardless of what `coverage` reports as available history. This was not independently tested against dates older than 30 days (all our calls stayed inside "today"). Per the brief, pulling the full 365-day/50-market backfill above requires **Build+ or higher**, independent of the credit math.

## Open issues
- Whether Build's base tier (vs. an explicit "Build+" add-on) actually unlocks data calls older than 30 days was not verified — inferred from the brief and 0xArchive's tier naming, not tested against a live call with `start` >30 days old.
- Lighter trades/fills finalization lag was observed at two different magnitudes (~36h, then ~16.7h) 11 hours apart — consistent with an offline batch process but not characterized precisely (e.g., is it a fixed daily batch time, or a rolling delay that shrinks between batches?).
- The vendor's account-level credit quota changed from 50,000 to 200,000 between the two probe runs; not explained by anything this probe did, and not investigated further (out of scope for P8).
