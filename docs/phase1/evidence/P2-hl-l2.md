# P2 — Hyperliquid L2 archive

Status: verified

## What ran
- Command: `FUNDR_DATA=/Users/jerryinyang/Trading/fundr/data/phase1 uv run python probes/p02_hl_l2.py`
- Run date (UTC): 2026-09-19
- Markets: 7 P0 sample coins (ENA, PONS, ONDO, ETHFI, JUP, KAITO, BTC)
- Dates / windows sampled: `hyperliquid-archive` bucket, `market_data/` prefix, newest date `20260918` (bucket's latest), hours `0` and `1` (first two hour folders of that date)

## What came back
- Listing: 1,230 daily prefixes under `market_data/`, first `20230415`, last `20260918` (archive covers ~1,220 days). `20260918` has all 24 hour folders present.
- Hour `0` and hour `1` each have 178 coins with an `l2Book` file; all 7 sample coins present in both hours (no missing coins — the "newest date may be partial" concern from P1 did not reproduce for these two hours).
- Per coin-hour file sizes (compressed, `.lz4`): range 104–237 KB (smallest KAITO ~104–106 KB, largest BTC ~228–237 KB); raw decompressed 1.06–1.11 MB per coin-hour.
- Records per coin-hour: 669 (hour 0) / 670 (hour 1); median spacing between consecutive `raw.data.time` values ≈ 5,388 ms (hour 0) / 5,387 ms (hour 1) — i.e. a new L2 snapshot roughly every 5.4 seconds.
- Depth: 20 levels per side (bid and ask) on every sampled coin-hour, no variation.
- First record excerpt (ENA, hour 0):
```json
{"time": "2026-09-18T00:00:09.296340708", "ver_num": 1, "raw": {"channel": "l2Book", "data": {"coin": "ENA", "time": 1789689608458, "levels": [[{"px": "0.15477", "sz": "11173.0", "n": 2}, ...], [...]]}}}
```

## Findings
- **Structure matched the brief's assumption exactly**: `raw.data.time` and `raw.data.levels` are the real keys — no extraction-key adjustment was needed.
- **Timestamp resolution**: two timestamps per record — an outer `time` (ISO string with microsecond local receipt time) and `raw.data.time` (integer ms epoch, the exchange's own snapshot timestamp, used for spacing above). Snapshot cadence is ~5.4 s, not sub-second — this is a periodic poll/snapshot feed, not a full tick-by-tick book-update stream.
- **Snapshot vs delta**: each record carries the full `levels` array (20 bids + 20 asks) — these are full snapshots, not incremental deltas. No delta-only fields observed.
- **Depth**: fixed at 20 levels per side across all sample coins and both hours.
- **Coin coverage**: all 7 sample coins present with files in both hours; 178 total coins have files per hour (a subset of all HL markets — not every listed market gets an L2 archive file every hour).
- **File sizes and extrapolated cost (7 sample coins × 1 year)**: average compressed size per coin-hour ≈ 152,967 bytes (from the 14 coin-hour samples above). Extrapolating: `152,967 bytes × 24 hours × 365 days × 7 coins ≈ 9.38 GB/year` → egress at $0.09/GB ≈ **$0.84/year**. Request charges (per `HLArchive._charge`: one `list` call per hour plus `head`+`get` per coin-hour, at $0.000005 each) add `(8,760 list calls + 7 × 8,760 × 2 downloads) × $0.000005 ≈ $0.66/year`. **Total ≈ $1.50/year** to mirror L2 books for the 7 sample coins at this ~5.4s cadence, for one full year. (Arithmetic: 152,967 × 24 × 365 × 7 = 9,379,958,340 bytes = 9.38 GB × $0.09 = $0.844; requests: (8,760 + 122,640) × $0.000005 = $0.657; total $1.50.)
- **Gaps**: none observed in the two sampled hours — no missing coins, no missing hour folders, record counts (669–670) consistent with the ~5.4s cadence over an hour (3,600s / 5.4s ≈ 667, matching). Only 2 of 24 hours in 1 of 1,230 dates were sampled, so this is not a full-day or full-history gap scan.

## Conclusion
Usable later as an **optional feature set**. The archive is cheap (~$1.50/year for the full 7-coin sample at ~5.4s snapshot cadence, 20 levels deep, well under the $0.80 probe budget and negligible against a real research budget) and structurally simple (full snapshots, stable schema, matched the brief's assumed keys exactly). The ~5.4s cadence means it's suitable for order-book-shape features (depth, imbalance, spread) sampled every few seconds, not for tick-level microstructure or true delta reconstruction. Not required for the core funding-rate research question; worth revisiting if depth/imbalance features become relevant.

## Open issues
- Only 2 of 24 hours in 1 date were sampled — a full-day or multi-date gap scan (e.g. for the partial-day truncation P1 found in `asset_ctxs`) was not done.
- Whether `raw.data.time` spacing is exactly periodic (fixed interval) vs. jittered around 5.4s was not analyzed beyond the median.
