# P4 — Hyperliquid API cross-check

Status: verified

## What ran
- Command: `FUNDR_DATA=/Users/jerryinyang/Trading/fundr/data/phase1 uv run python probes/p04_hl_api_crosscheck.py`
- Run date (UTC): 2026-09-19
- Markets: 7 P0 sample coins (ENA, PONS, ONDO, ETHFI, JUP, KAITO, BTC) for the settled-funding cross-check; BTC only for the candle-limit check
- Dates / windows sampled: the same `old` (2025-02-21), `mid` (2025-12-05), `recent` (2026-09-18) dates chosen in P1. Per controller ruling, `archive_start` (2023-05-20, P1 coverage history only) is excluded from this loop — a one-line filter in the probe (`dates = {k: v for k, v in dates.items() if k != "archive_start"}`). Candle-limit check: last 5 days and a 1-day window ~29–30 days ago, both `BTC` `1m`.
- A dtype fix was needed to run the brief's code as given: `funding_history_frame` (via `fundr.analysis.epoch_ms`, itself via `pl.from_epoch(..., time_unit="ms")`) always returns microsecond-precision `Datetime` columns regardless of the `time_unit` argument (verified directly: `pl.from_epoch(col, time_unit="ms").schema` → `Datetime(time_unit='us')` on polars 1.44.2) — `time_unit` there governs how the input integer is interpreted, not the output dtype. P1's `profile_<day>.parquet` stores `hour` as ms-precision (via `hl_archive.parse_time`). The two `settle_time`/`hour` columns therefore had mismatched dtypes and `attach_settled`'s join raised `SchemaError`. Fix, scoped to this probe only (not to `fundr.analysis`, which other probes also use): cast `settled`'s `settle_time` to ms before both joins. No change to `fundr/analysis.py` or `fundr/sources/hl_api.py`.

## What came back

Full probe output (`match_stats` dicts and settled-premium excerpts) for all three dates:

```
=== old 20250221: 156 settled rows, tolerance 1e-10
last-in-hour vs closing settlement: {'n': 144, 'n_match': 80, 'rate': 0.5555555555555556, 'mean_signed_error': 9.03027777777772e-08}
last-in-hour vs opening settlement: {'n': 144, 'n_match': 65, 'rate': 0.4513888888888889, 'mean_signed_error': -3.5602687500000004e-06}
settled premium sample: ENA 2025-02-21 00:00:00.115 -0.000229 / 01:00:00.066 -0.000312 / 02:00:00 -0.000559

=== mid 20251205: 156 settled rows, tolerance 1e-10
last-in-hour vs closing settlement: {'n': 144, 'n_match': 36, 'rate': 0.25, 'mean_signed_error': 2.916666666667121e-10}
last-in-hour vs opening settlement: {'n': 144, 'n_match': 26, 'rate': 0.18055555555555555, 'mean_signed_error': -2.6504520833333335e-06}
settled premium sample: ENA 2025-12-05 00:00:00.002 -0.000679 / 01:00:00.035 -0.00067 / 02:00:00.021 -0.000714

=== recent 20260918: 182 settled rows, tolerance 1e-10
last-in-hour vs closing settlement: {'n': 70, 'n_match': 43, 'rate': 0.6142857142857143, 'mean_signed_error': -5.241428571428508e-09}
last-in-hour vs opening settlement: {'n': 70, 'n_match': 34, 'rate': 0.4857142857142857, 'mean_signed_error': -8.703185714285715e-07}
settled premium sample: ENA 2026-09-18 00:00:00.005 0.00023 / 01:00:00.106 0.000112 / 02:00:00.015 0.000509

candles 1m last 5 days: 5042 (5000 cap => ~3.5 days); 30 days ago window: 0
```

Note: `recent` has only 70/70 coin-hours in both alignments (vs 144 on old/mid) because the archived file for 2026-09-18 is a partial day — P1 found it ends at 09:55 UTC, so settled hours after that have no archived counterpart; `drop_nulls`/`inner join` already drop them, they are not a bug.

- Row counts: `settled_<day>.parquet` — old 156 rows, mid 156 rows, recent 182 rows (7 coins × ~24h, minus any coin-hours where a market didn't yet exist that day, e.g. PONS on old/mid).
- Schema (`settled_<day>.parquet`, via `funding_history_frame`): `coin: String, time: Datetime(us), settle_time: Datetime(us), funding_rate: Float64, premium: Float64, funding_rate_str: String`.
- `predicted_fundings.json`: a list of 234 `[coin, [[venue, {fundingRate, nextFundingTime, fundingIntervalHours}], ...]]` entries. Example (`2Z`): `HlPerp` → `{"fundingRate": "-0.0000843902", "nextFundingTime": 1789819200000, "fundingIntervalHours": 1}`, alongside `BinPerp` and `BybitPerp` entries with `fundingIntervalHours: 4` and their own predicted rates. So `predictedFundings` returns, per coin, HL's own next-hour predicted rate plus the same for other venues it tracks, each with that venue's own settlement interval and next-settlement time — not just HL's number.
- Candle check: `candles_recent_1m.json` — 5,042 `BTC` `1m` candles for a nominal 5-day request, spanning `t` from `1789519140000` to `1789821600000` = exactly 3.50 days. `candles_old_1m.json` (1-day window, 29–30 days back) — 0 candles.

## Findings

- **Alignment vs. exact-match rate**: neither Alignment A (last-in-hour vs. the settlement closing that hour) nor Alignment B (last-in-hour vs. the settlement opening that hour) reaches the naive match rate implied by `match_stats`' headline `rate` (55.6/25.0/61.4% for A; 45.1/18.1/48.6% for B) because that number is dominated by trivial baseline-rate hours. Splitting by baseline (settled funding == the interest-rate baseline `0.0000125`) makes this explicit, checked directly on Alignment A:
  - old: 80 baseline coin-hours, 80/80 match; 64 off-baseline coin-hours, **0/64 match**, mean |error| 1.26e-6.
  - mid: 36 baseline coin-hours, 36/36 match; 108 off-baseline coin-hours, **0/108 match**, mean |error| 5.12e-7.
  - recent: 43 baseline coin-hours, 43/43 match; 27 off-baseline coin-hours, **0/27 match**, mean |error| 3.95e-7.
  So every baseline hour matches exactly (trivial — funding pinned at the interest floor when off-clamp premium doesn't move it), and **zero** off-baseline hours match at the API's own reported precision (tolerance `1e-10`, i.e. up to 10 reported decimal places). Off-baseline errors are small (roughly 4e-7 to 1.3e-6, both signs) but real, not floating-point noise.
- **Which alignment is closer**: Alignment A's `mean_signed_error` is near zero on all 3 dates (9.0e-8, 2.9e-10, -5.2e-9); Alignment B's is consistently larger and one-signed (-3.6e-6, -2.7e-6, -8.7e-7). This points to the archived value converging toward the *closing* settlement (A), not the opening one (B) — but "converging toward, small residual error" is not the same as "equals."
- **Reading**: the archived per-minute `funding` field is a **running/continuously-updated rate**, not a value frozen at settlement — it tracks toward the hour's closing settlement (small residual error, ~1e-6 scale, off-baseline hours) rather than toward the previous hour's settlement, but the 1-minute sampling cadence in the archive doesn't catch an exact match to the settled value at the boundary. This is consistent with HL's documented formula (below): funding is an *average* premium index accumulated continuously through the hour, so a snapshot taken up to ~60 seconds before the hour closes will differ slightly from the value HL finalizes at the instant of settlement.
- **`predictedFundings` shape**: per coin, a list of `[venue, {fundingRate, nextFundingTime, fundingIntervalHours}]` across HL and the external venues HL tracks (seen: `HlPerp` interval 1h, `BinPerp`/`BybitPerp` interval 4h). This gives HL's own current predicted (not-yet-settled) rate for the coin, its next settlement time, its interval, and the same fields for competing venues — useful for cross-venue funding comparison but not itself a history.
- **Candle lookback limit**: confirmed real. A request for the last 5 days of `BTC` `1m` candles returned 5,042 rows spanning exactly 3.50 days (not 5 days) — the response is truncated to the venue's retention/cap, not to the requested window. A request for a 1-day window ~29–30 days in the past returned **0** candles — 1-minute candle history beyond roughly the last ~3.5 days is not retrievable via `candleSnapshot`, matching the brief's "0 (or much fewer than 1440) if the limit is real" expectation. (This is a request-serving limit, not necessarily a data-existence limit — not investigated with coarser intervals here.)
- **Units, sign, interval** (from https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding, quoted directly):
  - Interval — **verified (docs + data agree)**: "The funding rate on Hyperliquid is paid every hour." Matches `predictedFundings`' `HlPerp` entry (`fundingIntervalHours: 1`) and the hourly `settle_time` cadence in `fundingHistory`.
  - Sign — **verified (docs + data agree)**: "If the contract's price is higher than the oracle price, the premium and hence the funding rate will be positive, and the long position will pay the short position." So positive `fundingRate`/`funding` = longs pay shorts. Not independently re-derived from data in this probe (would need position-level PnL), so this is a docs claim carried at face value, consistent with the field's sign in the data.
  - Baseline/units — **verified (docs + data agree)**: "Interest rate component is predetermined at 0.01% every 8 hours, which is 0.00125% every hour" → as a fraction, `0.0001 / 8 = 0.0000125` per hour. This is exactly the archive's observed baseline value (P1: BTC's `funding` on the recent date sample row is `0.0000125`) and exactly the value used as the "baseline" cutoff in the split above (`settled == 0.0000125`) — i.e. `fundingRate`/`funding` are plain per-hour fractions (not percentages, not per-8h).
  - Formula — **inferred, docs-stated but not independently re-derived here**: "Funding Rate (F) = Average Premium Index (P) + clamp(interest rate − Premium Index (P), −0.0005, 0.0005)"; premium = `impact_price_difference / oracle_price`. This is deferred to P7/P10 (rebuild test) to confirm against archived premium inputs.
  - Cap — **docs claim, not exercised by this probe's data (no capped hours observed in the sample)**: "Funding on Hyperliquid is capped at 4%/hour."
  - Precision — **verified**: reported tolerance (max decimal places in `fundingRate` strings) is `1e-10` on all 3 dates (up to 10 decimal digits observed, e.g. `-0.0000047026`; most values use 7).

## Target C checkpoint (HL) — interim

Per the design doc's Target C rule: HL is an Outcome A candidate if (a) archived `funding` changes within the hour (P1: yes, confirmed — 57–87% of coin-hours per date) **and** (b) the last in-hour value matches the closing settlement on ≥99% of coin-hours (Alignment A). Condition (b) **does not hold** here: off-baseline match rate is 0% (0/64, 0/108, 0/27) at the API's own reported precision, though the residual error is small (mean |error| ~4e-7 to 1.3e-6). **Interim reading: HL does not clear the Outcome A bar on this evidence** — the archived value is a running estimate that tracks toward, but does not exactly equal, the closing settlement at 1-minute sampling resolution. This is interim; P9's live-watch (finer than 1-minute polling, spanning real settlements) is the check that can confirm whether the *live* value converges exactly to settled at the settlement instant, which this archived-data check cannot resolve by construction (1-minute cadence can't catch a value that only becomes exact in the final seconds).

## Open issues
- Why off-baseline hours never match exactly (residual ~1e-6) is not explained here — could be genuine TWAP-vs-snapshot difference (formula averages premium continuously; archive samples once per minute) or a rounding/precision difference between the archive CSV and the `fundingHistory` API. Deferred to P9 (live, finer-grained polling) and P10 (rebuild test) to distinguish.
- Sign convention and the clamp/formula were taken from docs, not independently re-derived from data in this probe.
- Candle limit was only checked for `1m`/`BTC`; not checked at coarser intervals or other coins.
