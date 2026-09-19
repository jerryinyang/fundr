# P10 — Rebuild test

Status: Hyperliquid **verified** (not attemptable, per spec rule); Lighter **pending (part B)**

This note covers the Hyperliquid half only. The Lighter half (0xArchive premium inputs,
`lighter_formula`, and the Lighter verdict) is out of scope for this dispatch and is added
in part B once `lighter_formula.py` exists (Task 16 part B).

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
  after 2025-02-21..24. Recent window: 2,697 rows/coin for all 5 coins (less than the full
  5,760 because the archive's newest day, 2026-09-18, is truncated at 09:55 UTC — P1 finding).
- Input cadence: median 60 s (1 row/coin/minute) — matches P1.
- Formula's own sampling: every 5 s, averaged over the hour (P7, from HL docs; the 5 s samples
  themselves are not public).
- **Cadence vs. formula: 60 s archive vs. 5 s formula sampling** → per the spec's P10 result rule,
  this makes an exact rebuild impossible by construction.
- Baseline check: `settled == BASELINE_HOURLY` (exact float equality) matched 550/604 raw settled
  rows before joining/filtering — a sensible count, so no tolerance-based fallback was needed.
  After joining to hourly-averaged inputs and filtering low-count hours, 340/604 hours in the
  final sample are baseline hours.
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

Pending (part B) — the Lighter half of P10, including `lighter_formula`, 0xArchive premium
downloads, and the Lighter verdict, is added once Task 16 part B (`lighter_formula.py`) is
implemented.
