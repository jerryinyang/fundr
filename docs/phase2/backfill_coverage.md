# Phase 2b backfill coverage

What was actually collected, and where it is thin. Later sections are added by the QA task.

## Hyperliquid per-minute market state (`hl_asset_ctxs`)

Source: the requester-pays S3 archive `hyperliquid-archive/asset_ctxs/`, one `.csv.lz4` per UTC
day, downloaded 2026-09-22 by `scripts/backfill_hl_archive.py`.

| | |
|---|---|
| Days on disk | 1,218 (`2023-05-20` → `2026-09-19`) |
| Coin-days measured | 194,664 |
| Rows | 275,320,095 coin-minutes |
| Parquet on disk | 9.0 GB (uncommitted, under `data/phase2/hl_asset_ctxs/`) |
| Downloaded | 10.109 GB compressed, 1,218 GETs |
| Spend | **$0.9221** (ledger: `data/phase2/aws_ledger.jsonl`) |

The archive lags real time by about three days: the newest key on 2026-09-22 was `20260919`.

### Permanently missing: 2026-07-08

There is no key for `2026-07-08` in the bucket, and there never has been (checked against both
the live listing and Phase 1's saved listing). It is **missing, not zero** — Phase 7 must not
read the absence of that partition as a day of no activity, no open interest or no funding.

### Completeness

Completeness is measured per coin per day against 1,440 minutes and recorded in
`hl_asset_ctxs_completeness` (`kind=daily`). A day is summarised by its **median** per-coin
completeness, because a coin listed mid-day is legitimately short.

- 1,087 of 1,218 days are at or above 0.99. Mean of the daily medians: 0.9835.
- Overall, 98.22% of the 1,440-minute ideal is present.
- **131 days sit below 0.99**, ranging from 0.108 to 0.99.
- 52 days exceed 1.0, at most 1.0049 (1,447 minutes for a coin). These are a handful of
  duplicate or boundary-straddling timestamps, not extra data; downstream code should
  de-duplicate on `(time, coin)` rather than assume exactly 1,440 rows.

### Short days are truncated, not sampled sparsely

This matters for how the gaps are handled. On every short day inspected, the cadence is a clean
60 seconds from `00:00` and then simply **stops**:

- `2024-10-18`: BTC has 1,038 rows, `00:00` → `17:17`, every gap exactly 60 s.
- `2026-09-15`: BTC has 156 rows, `00:00` → `02:35`, every gap exactly 60 s.

So the missing minutes are a contiguous tail of the day, not scattered holes. Any Phase 7
feature that resamples or forward-fills will silently invent a late-session that did not exist,
unless it respects each day's last observed minute.

### The gaps cluster in late 2024, not in recent days

Phase 1 expected recent days to be the incomplete ones. That is only part of the picture:

| Year | Days below 0.99 |
|---|---|
| 2023 | 4 |
| 2024 | **117** |
| 2025 | 2 |
| 2026 | 8 |

117 of the 131 short days are in 2024, and 102 of those fall in **2024-07 → 2024-11**, where
roughly three weeks of every month are truncated at 0.71–0.85. That window is the single largest
data-quality caveat in this dataset.

The 2026 short days are the recent-lag pattern Phase 1 described:

```
2026-05-30 0.290   2026-08-08 0.481   2026-08-09 0.719   2026-08-27 0.465
2026-08-30 0.357   2026-09-15 0.108   2026-09-16 0.351   2026-09-18 0.414
```

### Quarantine, and what still needs deciding

Short days are quarantined, not frozen: `needs_fetch` skips a day only when its **recorded**
completeness clears 0.99, so a later run re-downloads the thin ones to pick up anything the
venue backfilled. `PERMANENT_SHORT` exempts days that are short by construction —
currently only `2023-05-20`, whose file begins at 02:50:04Z (measured 0.8819).

Two observations from this run:

1. `2023-05-23` was re-fetched from quarantine and came back **byte-identical in shape** —
   29,715 rows, median 0.983. A day that old is not waiting on a backfill.
2. The same is almost certainly true of the whole 2024 cluster, which is two years old.

**Open decision:** as it stands, every future run re-downloads all 131 quarantined days, about
$0.13 of egress each time, for days that will not change. Per the plan's rule ("if a day stays
short for a week it is permanent"), the 2024 window and the older 2023/2025 strays should be
measured once more after a week and then moved into `PERMANENT_SHORT` with their measured
values. That has deliberately **not** been done here — one re-fetch is not a week of evidence.
Only the recent 2026 days are genuine candidates for arriving more complete later.

### Caveat for Phase 7

- `2026-07-08` has no data at all: treat as missing.
- The 2024-07 → 2024-11 window has systematically truncated days; any daily aggregate over it is
  computed on partial sessions.
- Missing minutes are always a tail of the day. Use each day's last observed minute as the
  session end rather than assuming 23:59.
- De-duplicate on `(time, coin)`; a small number of coin-days carry up to 1,447 rows.
