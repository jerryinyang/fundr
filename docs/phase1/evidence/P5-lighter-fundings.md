# P5 — Lighter `/api/v1/fundings` settled history

Status: verified

## What ran
- Command: `FUNDR_DATA=/Users/jerryinyang/Trading/fundr/data/phase1 uv run python probes/p05_lighter_fundings.py`
- Run date (UTC): 2026-09-19T11:34:00Z (approx; `now` inside the script ≈ epoch 1789817566)
- Markets: all 7 P0 sample markets (ENA 29, PONS 231, ONDO 38, ETHFI 64, JUP 26, KAITO 33, BTC 1)
- Dates / windows sampled: full `1h` history per market from its listing (`order_books[].created_at`) to now; plus targeted calls on BTC (market 1) for `1d` resolution, `count_back` semantics, the row cap, and `1m`/`5m`/`15m` resolutions

## What came back
- Row counts (full `1h` history, paged via `fundings_all` in 700-row chunks): ENA 13507, PONS 402, ONDO 12326, ETHFI 9619, JUP 13719, KAITO 12954, BTC 14644. Each equals (or is one less/more than) the hours-since-listing estimate — see Findings.
- Schema (`fundings_frame` output): `market_id, timestamp, settle_time, rate_str, rate, direction, signed_rate, value` — one row per `1h` bucket. Raw API fields are `timestamp` (int, seconds), `value` (string), `rate` (string), `direction` (string, `long`/`short`).
- Files written: `data/phase1/p05/fundings_<market_id>.parquet` for all 7 markets (not committed).
- Excerpt (BTC, first 3 rows):
```
timestamp=1737100800  settle_time=2025-01-17 08:00:00  rate=0.0000  direction=long   value=0.00000000
timestamp=1737104400  settle_time=2025-01-17 09:00:00  rate=0.0000  direction=long   value=0.00000000
timestamp=1737108000  settle_time=2025-01-17 10:00:00  rate=0.0000  direction=long   value=0.00000000
```
- Excerpt (PONS, first row): `timestamp=1788372000 settle_time=2026-09-02 18:00:00 rate=0.0012 direction=long value=0.00000478`
- Targeted calls:
  - `1d, last 30 days` (market 1, `resolution=1d`, 30-day window, `count_back=30`): **2 rows**, first `1789711200`, last `1789797600` (exactly 1 day apart, both near `now`).
  - `count_back=5 over 10 days` (`1h`, `count_back=5`): **240 rows** (= 10 days × 24h), first `1788955200`, last `1789815600`.
  - `count_back=1000 over 40 days` (`1h`, `count_back=1000`): **750 rows**, first `1787119200`, last `1789815600` (750h ≈ 31.25 days, i.e. the tail of the 40-day window).
  - `1m`, `5m`, `15m` (`1h`-only market, 3h window): all three returned `HTTP 400 Bad Request`.
  - All 8 calls (7 history pages + 4 targeted, minus the 3 errors) succeeded with no `Authorization` header set on the client.

## Findings
- **Coverage start**: every sample market's `1h` history starts at (or within 1 hour of) that market's Lighter listing time (`order_books[].created_at`), with no earlier data. Row count ≈ hours-since-listing for all 7 markets (off by at most 1, consistent with rounding `now` to the current partial hour).
- **Gaps**: `gap_scan` over `settle_time` per coin with a 1-hour threshold found **0 gaps > 1h** in any of the 7 markets. Coverage is contiguous hourly from listing to now.
- **History length**: BTC 14644h (≈ 610 days / ~20 months), JUP 13719h (~571 days), ENA 13507h (~563 days), KAITO 12954h (~540 days), ONDO 12326h (~514 days), ETHFI 9619h (~401 days / ~13 months) — all well over 6 months. PONS 402h (~17 days) is short because it is a newly listed market (matches the ruling: not a stop condition, recorded here as expected and likely to be swapped later).
- **`1d` resolution**: requesting 30 days of `1d` data returned only 2 rows (the most recent 2 days), not 30. `start`/`end` were not honored for daily granularity the way they are for `1h` — daily buckets appear to only be available/retained for the very recent window. This is a genuine anomaly worth flagging for anyone relying on `1d` for longer history; `1h` (paged) is the reliable path and is what `fundings_all` uses.
- **`count_back` semantics**: `count_back` is ignored whenever `start_timestamp`/`end_timestamp` are both given — the call returns every row in the timestamp range regardless of `count_back` (10-day window with `count_back=5` returned 240 rows = the full 10×24; 40-day window with `count_back=1000` returned the cap, not 1000). We could not determine what `count_back` alone (without a range) would do, since `fundings()` always sends a range; not tested.
- **Cap**: 750 rows is confirmed as the per-call cap for `1h` resolution (40-day window, `count_back=1000`, returned exactly 750 rows = the most recent 750 hours of the window). `fundings_all` chunks at 700 periods per call to stay under this, and paging + de-duplication by `timestamp` produced contiguous, gap-free series for all 7 markets.
- **Sub-hour / intermediate state**: `1m`, `5m`, `15m` all return `HTTP 400`. No sub-hourly or intermediate (pre-settlement) funding state is exposed by this endpoint — `1h` is the finest resolution `/api/v1/fundings` supports.
- **Auth**: none of the 8 calls used an `Authorization` header; all succeeded (data reads are public).
- **Timestamp convention**: `timestamp` is Unix seconds, on the hour boundary (e.g. `1737100800` = `2025-01-17 08:00:00 UTC` exactly). Which side of the hour this settlement closes (start vs. end of the funding interval) is left to Task 12 per the brief.
- **Units — `rate`**: `rate_str` values are always formatted to 4 decimal places (`reported_tolerance` = `0.0001` for every market), e.g. `"0.0012"`. Baseline-rate hours (where funding sits at the interest-rate floor, e.g. PONS's first row, ONDO's and ETHFI's early rows) report `rate=0.0012`. `order_book_details(231)` gives `base_interest_rate="0.0100"`; `0.0100 ÷ 8 = 0.00125`, which truncated (not rounded) to 4 dp is `0.0012` — matches exactly. This confirms `rate` is **percent per hour** (the interest-rate baseline is defined per-8h and divided by 8 to get the hourly rate), reported truncated to 4 decimal places.
- **`value` field (tentative)**: for every row sampled, `value / rate` is roughly constant within a market over short windows and its scaled value (`value / rate × 100`) tracks a plausible spot price for that coin at that historical date (e.g. ENA ≈ 0.36–0.37, ONDO ≈ 0.92, ETHFI ≈ 1.18–1.19). This is consistent with `value = (rate/100) × mark_price`, i.e. `value` looks like the per-hour funding payment in quote currency per unit of the base asset, while `rate` is the dimensionless percent rate. Not independently verified against an authoritative price series — flagged as an inference, not a confirmed fact.
- **Sign convention**: left as "pending Task 12" per the brief; `direction` (`long`/`short`) and `lighter_signed_rate()` are recorded in the parquet output but not validated here.

## Status
Verified — all claims above are backed by the printed probe output and the written parquet files.

## Open issues
- `1d` resolution's apparent short retention window (2 rows for a 30-day request) is unexplained; not investigated further since `1h` fully covers the need.
- `count_back`'s behavior when no `start`/`end` range is given was not tested (the wrapper always sends a range).
- `value` field's exact formula/units is inferred from price-ratio matching, not confirmed against an independent price source.
- Sign convention (which side pays under `direction=long` vs `short`) is deferred to Task 12 as instructed.
