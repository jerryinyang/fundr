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
- 52 days exceed 1.0, at most 1.0049 (1,447 minutes for a coin), **all of them in
  2023-05-22 → 2023-10-14**. **Corrected 2026-09-22 after independent review:** these are
  *not* duplicate or boundary-straddling timestamps. The written parquet contains **zero
  `(time, coin)` duplicates** and zero rows whose `time` falls outside its partition date.
  The extra rows are **off-grid sub-minute samples** — rows whose `time` carries a non-zero
  seconds component (2023-07-06 has 252 of them, with gaps at 29 s, 30 s, 33 s, 38 s beside
  the usual 60 s). So de-duplicating on `(time, coin)` is a **no-op** and would leave every
  extra row in place while implying the problem was handled. Downstream must instead
  **truncate `time` to the minute and de-duplicate on `(minute, coin)`**, which caps distinct
  minutes per coin at exactly 1,440. Off-grid timestamps also occur on days that total
  exactly 1,440 (2025-09-27 has 213 with no minute collisions), so a clean 60 s cadence is a
  property of the short days examined, not of the dataset as a whole.

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
- Truncate `time` to the minute and de-duplicate on `(minute, coin)`; a small number of
  coin-days carry up to 1,447 rows, and `(time, coin)` alone will not remove them (see the
  correction above).

## Task 8 QA: coverage, gaps, alignment and vendor cross-check

Run with `uv run python scripts/qa_backfill.py` on 2026-09-22. Full per-market tables (worst-ten,
cross-venue pairs, day-by-day archive completeness) are in the uncommitted report at
`data/phase2/qa/coverage_report.md` — this section is the summary of what it found.

### Coverage per dataset, measured against each market's own listing (not the data's own span)

| Dataset | Markets | Rows | Median coverage | Markets with gaps | Short at head | Short at tail |
|---|---|---|---|---|---|---|
| HL funding | 234 | 4,676,365 h | 0.9985 | 141 | 213 | 234 |
| Lighter funding | 235 | 1,585,687 h | 0.9982 | 0 | 0 | 214 |
| Lighter trade candles | 218 | 1,286,265 bars | 0.7995 | 176 | 191 | 197 |
| Lighter mark-price candles | 226 | 1,015,482 bars | 0.7215 | 209 | 222 | 214 |

"Short at tail" for every dataset is dominated by markets that are simply still trading — the
tail anchor is "now" for `active` markets, so a market a few hours behind the last complete hour
at run time reads as tail-short even though nothing is actually missing. The mark/trade-candle
median coverage (0.72–0.80) looks worse than funding because both series measure against the
market's *listing*, and — see below — the two price series routinely start weeks to months
after listing and after each other, not because bars inside the series are missing (the
per-market gap counts for the widely-covered majors like BTC/ETH are 20 gaps over ~14,700 hours,
i.e. spread thin, not clustered).

### Mark-price candles start later than trade candles — confirmed, and worse than the one
### example measured while planning

165 of 218 markets with both series start their mark-price and trade-candle history on
different days. ETH/BTC/SOL/TAO and 9 others all show a **5,087-hour (212-day)** gap: trade
candles from 2025-01-25 16:00Z, mark candles from 2025-08-25 15:00Z. This is larger than the
8-day gap measured on market 138 (AMD) during planning — that gap is real, but it is the small
end of the range, not representative. Any Phase 7 feature that joins trade and mark candles
directly is silently missing up to 212 days of history on 165 of 218 markets unless it treats
the two series' starts independently.

### Cross-venue symbol matching (Phase 1's `kPEPE` vs `1000PEPE` problem, sized)

- Matched on symbol: **100**. Hyperliquid-only: **134**. Lighter-only: **135**.
- This is *not* a measure of assets missing from one venue — the mismatch is dominated by
  denomination prefixes (`kPEPE`/`1000PEPE`) and venue-specific listings (equities, FX and
  commodities exist only on Lighter; older/smaller alts only on HL). Phase 3 owns the alias
  table; this run is the size of the problem it needs to solve — 134 + 135 = 269 unmatched
  symbol-instances out of 469 total, well over half.

### Settlement alignment — Hyperliquid's stamp convention re-tested

| lag (h) | pairs | mean corr | median corr |
|---|---|---|---|
| -1 | 99 | 0.4402 | 0.3846 |
| **0** | **99** | **0.5528** | **0.5368** |
| +1 | 99 | 0.4522 | 0.4041 |

**Verdict: ALIGNED.** Lag 0 has the strongest correlation of the three tested (median 0.537 vs.
0.384 and 0.404 at ±1h), confirming Phase 1's inference that HL's row stamped `T` closes the
hour ending at `T`. Target B and every downstream cross-venue spread rest on this holding, and
it does. (100 symbols matched; the lag scan ran on 99 of them — one pair's series was too short
or degenerate to correlate.)

### Survivorship check

`hl_asset_ctxs` coin roster (234 coins) vs. today's live `meta.universe` from `HLInfo` (234
coins): **`archive_only` is empty, `meta_only` is empty.** HL has not dropped any delisted
coin from `meta` as of 2026-09-22 — a `meta`-built universe currently loses nothing. This is a
point-in-time result, not a permanent guarantee; re-check if `meta` behavior changes.

### Independent vendor cross-check — not run

`vendor_crosscheck` (0xArchive) is implemented and unit-tested (`FakeOX`, all assertions pass),
but the live cross-check against BTC/ENA (handoff §8 action 6) could **not** be executed in this
environment: no `OXARCHIVE_API_KEY` is set, and none is present in the repo's `.env` (checked;
this worktree does not even have a `.env` file). This is an environment gap, not a code defect —
re-run `uv run python scripts/qa_backfill.py --vendor-symbols BTC ENA` once a key is available.

### What a later phase must handle

- Phase 7: features joining Lighter trade and mark candles must not assume a shared start —
  165 of 218 markets disagree, by up to 212 days.
- Phase 3: the alias table for cross-venue symbol matching covers 269 unmatched symbol-instances
  (134 HL-only, 135 Lighter-only) out of 469.
- Phase 3/7: HL funding coverage is 213/234 markets short at the head, 1–24h each (median 13h).
  This is expected noise from the day-granularity listing proxy (the brief allows up to 23h);
  9 coins (AAVE, CFX, COMP, FTM, GMX, SNX, XRP, kBONK, kFLOKI) sit exactly at 24h — right at the
  boundary, not materially longer than a day, but worth a second look if a future run pushes any
  of them further.
- Vendor cross-check remains unexecuted; run it once `OXARCHIVE_API_KEY` exists.
