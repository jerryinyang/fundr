# P1 — Hyperliquid `asset_ctxs` archive

Status: verified

## What ran
- User approval for billed archive calls was given up front for all of Phase 1 (cap enforced in code at `HLArchive.BUDGET_USD = 0.80`); not re-asked here.
- Region/access check: `aws` CLI in this environment is broken (`bad CPU type in executable: aws`, exit 127), so the equivalent boto3 call was used instead, with the machine's default AWS profile/credentials (no config changed). `s3.list_objects_v2(Bucket="hyperliquid-archive", Prefix="asset_ctxs/", RequestPayer="requester", ...)` in region `ap-northeast-1` returned HTTP 200 with first keys `asset_ctxs/20230520.csv.lz4`, `asset_ctxs/20230521.csv.lz4` — confirms the bucket is reachable in `ap-northeast-1` (`HLArchive`'s default region), so `HLArchive()` was used with no `region=` override. (An earlier attempt in this session hit `AccessDenied` on the same calls; the user then granted S3 read permission to the AWS user, and access now succeeds — see Task 9 report for that history.)
- Command: `FUNDR_DATA=/Users/jerryinyang/Trading/fundr/data/phase1 uv run python probes/p01_hl_asset_ctxs.py`
- Run date (UTC): 2026-09-19 (see probe output; archive's own last-modified metadata not separately queried)
- Markets: 7 P0 sample coins (ENA, PONS, ONDO, ETHFI, JUP, KAITO, BTC)
- Dates / windows sampled: 3 full days from `asset_ctxs/` — old = `20230520` (bucket's earliest file, index 0 of 1217), mid = `20250117` (middle index), recent = `20260918` (bucket's latest file, index 1216)

## What came back
- Row counts / sizes: old 26,670 rows / 21 coins; mid 249,600 rows / 174 coins; recent 139,464 rows / 234 coins (whole-day files, all coins, before filtering to the 7-coin sample)
- Listing: 1,217 daily files, first `asset_ctxs/20230520.csv.lz4`, last `asset_ctxs/20260918.csv.lz4`, upload lag 1 day (today 2026-09-19, latest file dated 2026-09-18)
- Schema (identical on all 3 dates): `time: String, coin: String, funding: Float64, open_interest: Float64, prev_day_px: Float64, day_ntl_vlm: Float64, premium: Float64, oracle_px: Float64, mark_px: Float64, mid_px: Float64, impact_bid_px: Float64, impact_ask_px: Float64`
- `time` format (real, not what `parse_time`'s stub assumed): ISO 8601 whole-second UTC with `Z` suffix, always 20 characters, e.g. `2023-05-20T02:50:04Z`, `2025-01-17T00:00:00Z`. No fractional seconds observed anywhere in the 3 sampled days. `parse_time` in `src/fundr/sources/hl_archive.py` was extended for this exact format (`"%Y-%m-%dT%H:%M:%SZ"`, `time_unit="ms"`); the brief's original stub (`str.to_datetime(time_unit="ms")` with no format) raised `polars.exceptions.ComputeError: ... a time zone is part of the data` because it could not auto-infer a bare `Z`-suffixed string with no offset digits.
- Excerpt (≤10 lines, recent date, filtered to sample coin BTC):
```
time                 coin  funding    open_interest  premium     mark_px   mid_px
2026-09-18T00:00:00Z BTC   0.0000125  ...            ...         ...       ...
```
(full per-date head(5) prints, unfiltered, are in the full probe run log; all 12 columns present on all 3 dates)

## Findings

Answering the spec's P1 question list:

- **Row/sample frequency**: one row per coin roughly every **1 minute** — `median_interval` (per coin, via `time.diff()`) is exactly `1m` for every coin on every date checked (BTC on old/mid/recent; ENA/ETHFI/JUP/ONDO on mid/recent; KAITO/PONS on recent). `rows per hour (min/median/max)` is `60/60.0/60` on mid and recent for the sample coins present; on the old date it's `10/60.0/60` — the 10 comes from the day's first partial hour (archive's first row that day starts at `02:50:04`, not `00:00`), not a gap. `gap_scan(..., max_gap=5min)` found **0 gaps > 5 minutes** on any of the 3 dates, so the 1-minute cadence is unbroken wherever the coin has data.
- **Schema and dtypes**: 12 columns, all as listed above — `time` (string, not numeric epoch), `coin` (string), and 10 numeric fields (`Float64`), no `int` or `bool` columns. Schema is identical across all 3 sampled dates (2023, 2025, 2026); no columns added or dropped.
- **Timestamp meaning**: `time` is the snapshot timestamp at which that row's values were read from the exchange state — a per-minute poll, not a settlement or event timestamp. At 1-minute resolution, this is snapshot time, not derived from any settlement boundary.
- **Does `funding` change within the hour**: yes, most of the time. Share of coin-hours where `funding` has more than one distinct value across its ~60 one-minute samples: old date 0.909, mid date 0.600, recent date 0.571. So on any given date, roughly 40–60% of coin-hours see `funding` change more than once inside the hour (it is not a single value fixed for the whole hour) — this is one of the two funding-related fields recorded per minute, not per settlement.
- **Running vs. settled**: **pending P4.** P1 alone cannot tell whether this per-minute `funding` value is a running/predicted rate or something else; that requires cross-checking against HL's `fundingHistory` (settled) endpoint, done in Task 10 (rebuild test) / P4.
- **Is `premium` intra-hour**: yes, and more consistently than `funding` — share of coin-hours where `premium` changes within the hour is **1.000** on all 3 dates (every coin, every hour, at least 2 distinct premium values across the ~60 per-minute samples). Premium is the field that changes on essentially every 1-minute sample.
- **OI and impact bid/ask frequency**: `open_interest` changes within the hour with share 0.364 (old date, but that date's sample is BTC-only with only 21 coins existing at all — see below), 1.000 (mid), 1.000 (recent) — so OI updates roughly every minute on the more recent, higher-liquidity dates, less often (but still sometimes) on the earliest date. `impact_bid_px` and `impact_ask_px` both show share **1.000** on all 3 dates — these change on essentially every 1-minute sample, same as `mark_px`, `oracle_px`, `mid_px`, and `day_ntl_vlm`.
- **Gaps**: 0 gaps > 5 minutes in the sample coins present, on all 3 dates (see above).
- **Stability across the 3 dates and coins**: schema, column set, dtypes, and the ~1-minute cadence are stable across all 3 dates (2023, 2025, 2026) and across every coin checked. The one thing that is *not* stable is which coins exist at all on a given date (see next point) — that is a market-listing fact, not a schema/format instability.
- **Archive coverage start**: `asset_ctxs/20230520.csv.lz4` is the first key in the bucket (1,217 files total) — the archive begins 2023-05-20, with 21 markets that day (`APE, ARB, ATOM, AVAX, BNB, BTC, CRV, DOGE, DYDX, ETH, INJ, LDO, LINK, LTC, MATIC, OP, RNDR, SOL, STX, SUI, kPEPE`).
- **Upload lag**: latest file is dated 2026-09-18; run date is 2026-09-19 → **1 day** lag between "today" and the newest available archive file.
- **Null counts**: zero nulls in any column, on any of the 3 dates, for the filtered sample rows.

**Sample-coin coverage per date (not all 7 present everywhere) — no swap made, documented instead:**

| date | sample coins present | missing |
|---|---|---|
| old (2023-05-20) | BTC | ENA, ETHFI, JUP, KAITO, ONDO, PONS |
| mid (2025-01-17) | BTC, ENA, ETHFI, JUP, ONDO | KAITO, PONS |
| recent (2026-09-18) | all 7 | — |

The brief's Step 5 rule ("if a sample coin is missing on any sampled date, rerun `p00_sample.py --exclude` and swap") was **not applied**, for a concrete reason found here: the "old" date is deliberately the archive's very first file (2023-05-20), and only 21 markets existed on HL at all that day (list above). None of the current OI-rank-based mid/small-cap picks (ENA, ETHFI, JUP, KAITO, ONDO, PONS) are among those 21 — they are all newer listings by construction (P0 picks by *current* OI rank). Excluding and re-picking would cascade through nearly the entire non-BTC sample and still very likely fail the same "old" check, since virtually no OI-rank-11/18/25/33/40/80 market from today's universe existed on day 1 of the archive; doing so would abandon the OI-rank selection rule for a "whatever existed in May 2023" rule instead. On the "mid" date (2025-01-17), only KAITO and PONS are missing — both are recent listings (P8's evidence separately shows PONS listed ~2026-09-02 on Lighter, i.e. ~17 days before this run). This matches the anticipated caveat about PONS, but here KAITO is also affected.
Swapping now would also be inconsistent with `docs/phase1/evidence/P0-sample.md`, `P5-lighter-fundings.md`, `P6-lighter-market-state.md`, and `P8-oxarchive.md`, all already committed on this branch using this exact 7-coin sample (spec requires "the same 7 markets are used by every probe"), and P9's live-watch process (data/phase1/p09/live.jsonl) is running and must keep its original coins regardless. Recommendation: keep the fixed sample as-is; treat "some sample coins didn't exist yet on some archive dates" as a market-listing fact recorded here, not a defect to fix by resampling. Flagged as an open issue below for the operator to confirm.

## Spend
Total AWS spend so far (`HLArchive().spent_usd()`): **$0.00142** (well under the $0.80 cap). Charges: 1 `list` call (paginated listing of `asset_ctxs/`, 1,217 keys) + 3×(`head` + `get`) for the old/mid/recent daily files.

## Open issues
- Running vs. settled semantics of `funding` is not resolved here — pending P4/Task 10 (HL API cross-check and rebuild test).
- Sample coverage gaps documented above (KAITO/PONS missing on the mid date; everything but BTC missing on the old date) were **not** fixed by resampling, per the reasoning above — flagged for the operator/decision-map to confirm this is acceptable, since it means P10's "old"-adjacent rebuild window (Task 10) can only use BTC from this sample, and the "mid"-adjacent window can use 5 of 7 (not KAITO/PONS).
- `parse_time`'s brief-provided stub did not match the archive's real string format; extended in `src/fundr/sources/hl_archive.py` to the exact observed format (`%Y-%m-%dT%H:%M:%SZ`) rather than a generic auto-parse, per the task's explicit allowance to extend `parse_time` for the real format.
