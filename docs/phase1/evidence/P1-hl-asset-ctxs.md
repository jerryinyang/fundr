# P1 — Hyperliquid `asset_ctxs` archive

Status: verified

## What ran
- User approval for billed archive calls was given up front for all of Phase 1 (cap enforced in code at `HLArchive.BUDGET_USD = 0.80`); not re-asked here.
- Region/access check: `aws` CLI in this environment is broken (`bad CPU type in executable: aws`, exit 127), so the equivalent boto3 call was used instead, with the machine's default AWS profile/credentials (no config changed). `s3.list_objects_v2(Bucket="hyperliquid-archive", Prefix="asset_ctxs/", RequestPayer="requester", ...)` in region `ap-northeast-1` returned HTTP 200 with first keys `asset_ctxs/20230520.csv.lz4`, `asset_ctxs/20230521.csv.lz4` — confirms the bucket is reachable in `ap-northeast-1` (`HLArchive`'s default region), so `HLArchive()` was used with no `region=` override. (An earlier attempt in this session hit `AccessDenied` on the same calls; the user then granted S3 read permission to the AWS user, and access now succeeds — see Task 9 report for that history.)
- Command: `FUNDR_DATA=/Users/jerryinyang/Trading/fundr/data/phase1 uv run python probes/p01_hl_asset_ctxs.py`
- Run date (UTC): 2026-09-19 (see probe output; archive's own last-modified metadata not separately queried)
- Markets: 7 P0 sample coins (ENA, PONS, ONDO, ETHFI, JUP, KAITO, BTC)
- Dates / windows sampled (controller ruling, Task 9 follow-up — see Findings): old = `20250221`, mid = `20251205`, recent = `20260918` (bucket's latest file). The archive's actual first file, `20230520` (index 0 of 1217), is recorded separately as `archive_start` and not re-downloaded/re-analysed; its findings from the first P1 run are kept below as coverage history.

## What came back
- Row counts / sizes: old (`20250221`) 267,840 rows / 186 coins; mid (`20251205`) 318,240 rows / 221 coins; recent (`20260918`) 139,464 rows / 234 coins (whole-day files, all coins, before filtering to the 7-coin sample). `archive_start` (`20230520`, coverage history only, not re-downloaded this run): 26,670 rows / 21 coins.
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

- **Row/sample frequency**: one row per coin roughly every **1 minute** — `median_interval` (per coin, via `time.diff()`) is exactly `1m` for every sample coin present, on all 3 analysed dates. `rows per hour (min/median/max)` is `60/60.0/60` on old and mid; on recent it's `56/60.0/60` (PONS's listing day is close to `recent`, so its first few hours are partial — not a gap elsewhere). `gap_scan(..., max_gap=5min)` found **0 gaps > 5 minutes** on any of the 3 dates, so the 1-minute cadence is unbroken wherever a coin has data.
- **Schema and dtypes**: 12 columns, all as listed above — `time` (string, not numeric epoch), `coin` (string), and 10 numeric fields (`Float64`), no `int` or `bool` columns. Schema is identical across old, mid, recent, and the `archive_start` coverage-history date (2023, 2025×2, 2026); no columns added or dropped.
- **Timestamp meaning**: `time` is the snapshot timestamp at which that row's values were read from the exchange state — a per-minute poll, not a settlement or event timestamp. At 1-minute resolution, this is snapshot time, not derived from any settlement boundary.
- **Does `funding` change within the hour**: yes, most of the time. Share of coin-hours where `funding` has more than one distinct value across its ~60 one-minute samples: old 0.632, mid 0.868, recent 0.571 (archive_start, coverage history: 0.909). So on any given date, roughly 55–90% of coin-hours see `funding` change more than once inside the hour (it is not a single value fixed for the whole hour) — this is one of the two funding-related fields recorded per minute, not per settlement.
- **Running vs. settled**: **pending P4.** P1 alone cannot tell whether this per-minute `funding` value is a running/predicted rate or something else; that requires cross-checking against HL's `fundingHistory` (settled) endpoint, done in Task 10 (rebuild test) / P4.
- **Is `premium` intra-hour**: yes, and more consistently than `funding` — share of coin-hours where `premium` changes within the hour is **1.000** on old, mid, recent, and archive_start (every coin, every hour, at least 2 distinct premium values across the ~60 per-minute samples). Premium is the field that changes on essentially every 1-minute sample.
- **OI and impact bid/ask frequency**: `open_interest` changes within the hour with share 1.000 on old and mid, 1.000 on recent (0.364 on `archive_start`, where the sample that day was effectively BTC-only — see coverage history below). `impact_bid_px` and `impact_ask_px` both show share **1.000** on old, mid, recent, and archive_start — these change on essentially every 1-minute sample, same as `mark_px`, `oracle_px`, `mid_px`, and `day_ntl_vlm`.
- **Gaps**: 0 gaps > 5 minutes in the sample coins present, on old, mid, and recent (and on `archive_start` from the earlier run).
- **Stability across the 3 dates and coins**: schema, column set, dtypes, and the ~1-minute cadence are stable across old (2025-02-21), mid (2025-12-05), recent (2026-09-18), and the `archive_start` coverage-history date (2023-05-20) — and across every coin checked. The one thing that is *not* stable is which coins exist at all on a given date (a market-listing fact, not a schema/format instability) — see the dates rule below.
- **Archive coverage start**: `asset_ctxs/20230520.csv.lz4` is the first key in the bucket (1,217 files total) — the archive begins 2023-05-20, with 21 markets that day (`APE, ARB, ATOM, AVAX, BNB, BTC, CRV, DOGE, DYDX, ETH, INJ, LDO, LINK, LTC, MATIC, OP, RNDR, SOL, STX, SUI, kPEPE`). Kept in `data/phase1/p01/dates.json` as `"archive_start"`, separate from the analysed `old`/`mid`/`recent` keys.
- **Upload lag**: latest file is dated 2026-09-18; run date is 2026-09-19 → **1 day** lag between "today" and the newest available archive file.
- **Null counts**: zero nulls in any column, on old, mid, or recent, for the filtered sample rows.

**Controller ruling on P1 date choice (Task 9 follow-up, no P0 sample swap):**

The first P1 run used `old` = the archive's very first file (2023-05-20) and `mid` = the archive's midpoint file (2025-01-17) by pure index, which meant 6 of the 7 sample coins (all but BTC) didn't exist yet on `old`, and 2 of 7 (KAITO, PONS) didn't exist on `mid` — because those are current-OI-rank picks, mostly newer listings, and the archive's very first day only had 21 markets total. Per controller ruling, the fixed 7-coin sample is kept everywhere (no P0 swap); instead **the P1 dates themselves are re-chosen so the sample is actually present**:
- `old` = the first archive date on which every sample coin **except PONS** has data: each coin's HL listing date is read for free from `HLInfo().candle_snapshot(coin, "1d", 0, now_ms)` (first candle's `t`), the latest of those listing dates + 1 day gives a target day, and the probe snaps to the next available archive key on/after that day. Listing dates found: ENA 2024-04-02, ONDO 2024-01-20, ETHFI 2024-03-18, JUP 2023-12-03, KAITO 2025-02-20, BTC 2020-08-19 → target day 2025-02-21, which exists in the listing exactly → `old = asset_ctxs/20250221.csv.lz4`.
- `mid` = the archive key closest to halfway (by calendar time) between the new `old` and `recent` → `mid = asset_ctxs/20251205.csv.lz4`.
- `recent` unchanged → `asset_ctxs/20260918.csv.lz4` (bucket's latest file).
- The archive's actual first file (2023-05-20) is kept as `dates.json["archive_start"]` for coverage-history purposes (first-day findings above) but is **not** re-downloaded or re-analysed by the main loop, which iterates only `old`/`mid`/`recent`.

Result, confirmed directly in the rerun: `old` (2025-02-21) and `mid` (2025-12-05) both show **all 6 non-PONS sample coins present** (`sample coins missing on this date: ['PONS']` on both); `recent` (2026-09-18) has all 7. **PONS appears only on the recent date** — this is a real, permanent fact about PONS (it's a very recently listed market, ~17 days before this run per P8's Lighter-listing evidence), not something any date choice can fix, and is accepted here as a **spec P0-step-5 deviation, by controller ruling: no coin swap**, in favor of picking archive dates around the fixed sample instead of picking a sample around the archive dates.

## Spend
Total AWS spend so far (`HLArchive().spent_usd()`): **$0.00346** (well under the $0.80 cap). Charges: 1 `list` call (paginated listing of `asset_ctxs/`, 1,217 keys) + 3×(`head` + `get`) for the original old/mid/recent daily files (kept, not re-charged) + 2×(`head` + `get`) for the newly chosen old (`20250221`) and mid (`20251205`) daily files (recent `20260918` was already cached from the first run).

## Open issues
- Running vs. settled semantics of `funding` is not resolved here — pending P4/Task 10 (HL API cross-check and rebuild test).
- `parse_time`'s brief-provided stub did not match the archive's real string format; extended in `src/fundr/sources/hl_archive.py` to the exact observed format (`%Y-%m-%dT%H:%M:%SZ`) rather than a generic auto-parse, per the task's explicit allowance to extend `parse_time` for the real format.
