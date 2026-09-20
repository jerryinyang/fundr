# P10 — Rebuild test

Status: Hyperliquid **verified** (not attemptable, per spec rule); Lighter **verified** (not attemptable — no premium input exists to attempt with)

This note covers both venues. Hyperliquid ran the full rebuild pipeline (see below). Lighter
could not run the pipeline at all: there is no historical premium input anywhere to feed it, so
the probe stops after a minimal, cheap check of the vendor response and writes a
`not_attemptable` verdict directly, rather than crashing partway through a rebuild that was
never possible.

## Hyperliquid

### What ran
- Command: `FUNDR_DATA=/Users/jerryinyang/Trading/fundr/data/phase1 uv run python probes/p10_rebuild.py --venue hl`
- Run date (UTC): 2026-09-19
- Markets: the 5 mid-cap P0 sample coins (`role == "mid"`): ENA, PONS, ONDO, ETHFI, JUP
- Dates / windows sampled: two 4-day windows of the `asset_ctxs` archive —
  old: 2025-02-21 .. 2025-02-24 (adjacent to P1's `old` date), recent: 2026-09-15 .. 2026-09-18
  (adjacent to P1's `recent` date, shifted back 3 days to stay inside the archive). All 8 days
  were already present in the archive listing (no gaps). Settled funding for the same coins/windows
  came from `HLInfo().funding_history`.

### What came back
- Inputs: `asset_ctxs` archive rows (1 row/coin/minute, per P1), filtered to the 5 sample coins.
  Old window: 5,760 rows/coin (4 full days at 1-min cadence) for ENA/ONDO/ETHFI/JUP; PONS has
  **0 rows in the old window** — expected, PONS was listed ~17 days before this run (P1/P8), long
  after 2025-02-21..24. Recent window: 2,697 rows/coin for all 5 coins — well short of the full
  5,760, and **not just from the newest day's known truncation**. Per-day row counts (identical
  for every one of the 5 sample coins; max possible per day is 1,440 at 1-min cadence), all
  starting cleanly at `00:00:00` with no internal gaps > ~1 minute, and each simply stopping at
  the listed last timestamp:

  | day (2026) | rows | last timestamp (UTC) |
  |---|---|---|
  | 09-15 | 156/1440 | 02:35:00 |
  | 09-16 | 505/1440 | 08:24:00 |
  | 09-17 | 1,440/1440 | 23:59:00 (full day) |
  | 09-18 | 596/1440 | 09:55:00 |

  Only 09-18 is the "newest file, upload lag" case noted in P1. 09-15 and 09-16 are far more
  incomplete than that and are **not** the newest file at download time — this looks like the
  archive's recent daily files can be badly incomplete for reasons beyond simple upload lag
  (hypothesis: backfill lag or an outage in whatever populates that day's file; not verified
  against any Hyperliquid-side status source). See the added note in P1's evidence file. These
  per-day gaps, not just the 09-18 truncation, are what strip most of the recent window down to
  2,697 rows/coin before the completeness filter (which drops hours below 90% of expected samples).
- Input cadence: median 60 s (1 row/coin/minute) — matches P1.
- Formula's own sampling: every 5 s, averaged over the hour (P7, from HL docs; the 5 s samples
  themselves are not public).
- **Cadence vs. formula: 60 s archive vs. 5 s formula sampling** → per the spec's P10 result rule,
  this makes an exact rebuild impossible by construction.
- Baseline check: `settled == BASELINE_HOURLY` (exact float equality) matched 550/873 raw settled
  rows (`st`, the full set fetched from `HLInfo().funding_history` before any join or
  completeness filter) ≈ 63% — a sensible count, so no tolerance-based fallback was needed. After
  joining to hourly-averaged premium inputs and filtering out low-count hours, a different,
  smaller population remains: 604 coin-hours total, of which 340 are baseline hours.
- Tolerance: one unit in the last reported decimal place of `fundingRate` = `1e-10` (same as P7).
- Match rates (after joining coarse hourly-averaged premium to settled, 604 coin-hours total,
  hours with fewer than 90% of expected 1-min samples dropped):
  - All market-hours: 332/604 match within tolerance (55.0%).
  - Off-baseline market-hours: 0/264 match within tolerance (0.0%).
  - Mean signed error, all: -2.95e-07. Mean signed error, off-baseline: -6.87e-07.
  - Mean absolute error, off-baseline: 4.3e-06; all: 1.9e-06.
- Verdict:
  ```
  {"verdict": "not_attemptable", "raw_verdict": "fail",
   "all": {"n": 604, "n_match": 332, "rate": 0.5497, "mean_signed_error": -2.95e-07},
   "off_baseline": {"n": 264, "n_match": 0, "rate": 0.0, "mean_signed_error": -6.87e-07},
   "tol": 1e-10, "attemptable": false, "input_cadence": "0:01:00",
   "windows": [["2025-02-21","2025-02-22","2025-02-23","2025-02-24"],
               ["2026-09-15","2026-09-16","2026-09-17","2026-09-18"]]}
  ```
  Saved at `data/phase1/p10/verdict_hl.json`. `raw_verdict` is `"fail"`, not `"pass"` — so this is
  not the "practical rebuild despite not_attemptable" case the plan asked to flag prominently;
  a coarse rebuild from the 1-minute archive misses even loosely.
- Largest off-baseline mismatches (absolute error, top 5 of 264):

  | coin | hour (UTC) | p_avg (1-min mean) | rebuilt | settled | abs error |
  |---|---|---|---|---|---|
  | ENA | 2025-02-21 23:00 | -0.004444 | -0.000493 | -0.000511 | 1.8e-05 |
  | ENA | 2025-02-21 16:00 | -0.002146 | -0.000206 | -0.000188 | 1.7e-05 |
  | ENA | 2025-02-24 22:00 | -0.001721 | -0.000153 | -0.000168 | 1.5e-05 |
  | ETHFI | 2025-02-24 22:00 | -0.000776 | -0.000035 | -0.00005 | 1.5e-05 |
  | ETHFI | 2025-02-22 16:00 | -0.000546 | -0.000006 | 0.000009 | 1.5e-05 |

  Likely cause (hypothesis, not verified): the formula's true input is the average of ~720
  5-second premium samples per hour; the archive only gives ~60 1-minute snapshots. Premium
  changes on essentially every sample (P1: share of coin-hours where `premium` changes within
  the hour = 1.000), so the 1-minute average is a noisy proxy for the 5-second average, especially
  in hours where premium moves sharply (e.g. clamp-active hours, which is where all the largest
  errors above occur — clamp-active hours amplify small premium-average errors less than
  in-band hours because the clamp compresses the mapping, yet errors are still ~1.5e-5, two to
  three orders of magnitude above the 1e-10 tolerance). This is a sampling-resolution gap, not a
  formula error — `hl_formula.hourly_rate` itself reproduces settled funding exactly from HL's
  own reported hourly premium (P7: 245/245 off-baseline, 494/494 all).

### Spend
- AWS spend before this run (P1 + earlier probes): ≈ $0.0075.
- AWS spend after this run: ≈ $0.0118 (delta ≈ $0.0043 for the 6 new archive days downloaded —
  4 old-window days were already cached from P1/P7, plus 2 additional recent-window days).
  Well under the $0.80 cap; no `BudgetExceeded`.

### Findings
- HL's hourly funding **cannot be exactly rebuilt** from the public `asset_ctxs` archive: the
  archive's 1-minute cadence is coarser than the formula's 5-second premium sampling, so per the
  spec's P10 result rule this venue is **not_attemptable** by construction, independent of the
  match rate observed.
- The coarse rebuild itself also fails outright on rate (`raw_verdict: "fail"`): 0% of off-baseline
  hours land within tolerance, with absolute errors ~1e-5–2e-5 — several orders of magnitude above
  the 1e-10 tolerance, though small in absolute funding-rate terms.
- PONS has no data in the old window, as expected (very recent listing) — consistent with P1's
  PONS finding; not a new data-quality issue.
- HL's Target C decision (per the design's Target C rule) is therefore **not Outcome C**
  (reconstructed and validated) via this coarse archive; it depends on whether `asset_ctxs`'
  per-minute `funding`/`premium` fields otherwise support Outcome A (historical, via last-in-hour
  value equal to settled) — a separate question from this rebuild test.

### Open issues
- Whether a better-than-naive rebuild (e.g. weighting or a different aggregation of the 1-minute
  samples) narrows the gap was not explored — the spec's not-attemptable rule already settles the
  venue's status regardless.
- Exact HL 5-second premium samples remain unpublished; this probe used the only public premium
  granularity available (1-minute).

## Lighter

### What ran
- Command: `FUNDR_DATA=/Users/jerryinyang/Trading/fundr/data/phase1 uv run python probes/p10_rebuild.py --venue lighter`
- Run date (UTC): 2026-09-20
- The Lighter branch of `probes/p10_rebuild.py`, as written in the Task 17 brief, assumed a
  historical premium input was reachable via 0xArchive's `/v1/lighter/funding/{coin}` route (the
  same route the Hyperliquid side calls, which does carry a `premium` field for HL). It does not
  for Lighter. Rather than let the probe run its full pipeline and crash on a missing `premium`
  column partway through (after spending most of the credit budget on data that could never be
  used), the probe now makes one minimal, cheap request first — `GET /v1/lighter/funding/ENA`
  with `limit=1` — to check the vendor response directly, and writes a `not_attemptable` verdict
  immediately if `premium` is absent, instead of proceeding.

### Inputs sought, and why each is unavailable
Three possible sources of historical Lighter premium were considered; none work:
1. **Lighter's own API.** P6 (`docs/phase1/evidence/P6-lighter-market-state.md`, "Premium
   history"): "None found natively as a 'premium' series. `/api/v1/markPriceCandles` gives
   historical mark price; `/api/v1/candles` gives historical trade price. A premium series (mark
   − index, or similar) would have to be derived by combining `markPriceCandles` with a
   historical index-price source — no such index-price-history endpoint was found among the
   endpoints checked." So Lighter's live API has no historical premium series, and no
   index-price history to derive one from either.
2. **0xArchive's REST route** (`/v1/lighter/funding/{coin}`). P8 (`docs/phase1/evidence/P8-oxarchive.md`):
   "Lighter's REST funding route has no `premium` field — a deviation from the brief's script,
   which assumed both venues return it... Hyperliquid adds `premium`." Confirmed live again here:
   the probe's minimal check returned `{"coin", "symbol", "timestamp", "funding_rate"}` for
   Lighter — no `premium` key, matching P8 exactly.
3. **0xArchive's WebSocket replay.** P8 also found (Step 4) that the *replay* channel's nested
   `data` object does include a `premium` field for Lighter, unlike the plain REST route — but
   flagged this as "untested" beyond that one observation, and it is a live-replay stream of
   recent data (clamped to 10× realtime on the free tier), not an archived historical series
   reachable for arbitrary past windows the way the HL `asset_ctxs` archive is. Using it here
   would mean building and validating a new, unverified data path under this task's minimal-change
   constraint and outside the controller's ruling — not attempted.
4. **The P9 live recording** (`data/phase1/p09/live.jsonl`). This is real Lighter premium data
   (used by P7 to confirm the formula) but it spans only ~3h52m of wall-clock time on one day —
   nowhere near the two 4-day windows this probe's design calls for, and it predates the "old"
   and "recent" windows this probe would need to compare against different market regimes. It is
   the *only* premium history that exists for Lighter anywhere (P7's own framing), which is
   exactly why it cannot double as an archive.

No substitute input was invented; per the controller's ruling, the probe reports the missing
input and stops.

### Verdict
```json
{
  "verdict": "not_attemptable",
  "attemptable": false,
  "reason": "No historical Lighter premium input exists to rebuild from. 0xArchive's REST Lighter funding route (/v1/lighter/funding/{coin}) has no premium field (P8) -- confirmed live here: sample keys were ['coin', 'funding_rate', 'symbol', 'timestamp']. Lighter's own API has no native historical premium series either (P6: no premium/index-price-history endpoint to derive one from). The only Lighter premium data that exists anywhere is the ~3h52m P9 live websocket recording, which is far short of the two 4-day archive windows this probe needs and is not a substitute for an archive.",
  "missing_input": "historical Lighter premium",
  "checked": {"path": "/v1/lighter/funding/ENA", "sample_keys": ["coin", "funding_rate", "symbol", "timestamp"]},
  "input_cadence": null,
  "all": null,
  "off_baseline": null,
  "tol": null,
  "windows": [
    ["2026-08-22T00:39:56.914", "2026-08-26T00:39:56.914"],
    ["2026-09-14T00:39:56.914", "2026-09-18T00:39:56.914"]
  ]
}
```
Saved at `data/phase1/p10/verdict_lighter.json`. No rebuild statistics (`all`, `off_baseline`,
`tol`, `input_cadence`) exist because the pipeline never ran past the input check.

### What would be needed to attempt this later
A **prospective recorder** capturing Lighter's `market_stats` websocket `premium` field (the
running hourly average — P7's key finding, not a per-minute spot sample) at, say, once per
minute, continuously, for at least the two 4-day windows this design calls for. This is exactly
the kind of data P9's live recording captured for ~3h52m; running the same recorder
uninterrupted for days would produce the missing input. No amount of querying existing vendor
history (Lighter's own API, or 0xArchive) can substitute, because none of them retain this field
historically — it only exists as of "now" on the live feed.

### Spend
- 0xArchive: one call, `GET /v1/lighter/funding/ENA` with `limit=1` (the account's cumulative
  credits-used counter read 30 after the call; this task's own delta is 1 page ≈ 1–2 credits per
  P8's per-route cost table). Well under any budget; no other calls made.

### Deviation from the spec
The spec's rebuild design calls for two 4-day windows per venue (old and recent), matching P1's
market-regime split. For Lighter this could not happen: not only were the archive windows
unreachable (no premium field to pull), but even if 0xArchive's REST route did carry `premium`,
its free-tier data-payload calls are restricted to roughly the last 30 days (P8, "Free-tier
30-day limit"), which would rule out an "old" window matching HL's Feb-2025 window anyway. The
windows recorded in the verdict JSON (both inside the last 30 days, computed the same way as the
0xArchive windows the brief's original code would have used) are therefore vestigial — carried
over from the unreached code path for schema consistency, not evidence of an attempted rebuild.

### Findings
- Lighter's hourly funding **cannot be rebuilt at all** from any currently available historical
  source — not "coarser than needed" (HL's case) but **absent entirely**. No historical premium
  series exists on Lighter's own API (P6), 0xArchive's REST catalog (P8), or anywhere else, except
  the ~3h52m P9 live recording, which is too short to serve as an archive and does not cover the
  two required 4-day windows.
- This is a stronger and more definitive "not attemptable" than HL's cadence-mismatch case: HL's
  archive exists and is merely too coarse (60s vs. the formula's 5s sampling); Lighter's archive
  does not exist at all for this field.
- Per the design's Target C rule, Lighter's funding-rebuild path is not Outcome C for the same
  reason it can never run: there is no historical premium input to reconstruct from, at any
  granularity.
