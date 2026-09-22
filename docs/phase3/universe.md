# Phase 3 universe and panel

What Phase 3 built under `data/phase2/`, what is in it, and what will bite a later phase. This
is the Phase 3 counterpart to [`../phase2/datasets.md`](../phase2/datasets.md) and follows the
same layout: one section per dataset — what it is, its source, coverage, known gaps, columns,
how to load it, how to refresh it — then a numbered list of everything a later phase must
handle. Every row carries the same three provenance columns as every Phase 2b dataset
(`_source`, `_fetched_at_ms`, `_git_sha`), stamped by `fundr.dataset.stamp`; both datasets here
stamp `_source = "phase3:build_universe"`.

**Sources for this document.** Row counts, schemas, ranges and shares were measured directly on
the written parquet with polars on 2026-09-22 and are reproducible by re-reading the files.
Everything else is cited to [`decisions.md`](decisions.md) (the decision, the reasoning and the
council record), `data/phase2/qa/universe_diagnostics.md` (the size-rule measurement, written by
`scripts/universe_diagnostics.py` — uncommitted by design, regenerate rather than trusting a
stale copy), or `data/phase2/qa/cross_venue_overlap.md`. The implementation plan is
[`../superpowers/plans/2026-09-22-phase3-universe.md`](../superpowers/plans/2026-09-22-phase3-universe.md).
Where a figure here disagrees with an older document, the disagreement is called out and the
measurement is what appears.

**Nothing here is a target.** Phase 3 built the universe and the panel spine. Targets are Phase
4; features are Phase 7.

---

## Read this first: Phase 3 ships no size gate

The research design's universe is "perpetual markets outside the top 10 by open interest".
**Phase 3 does not delete those rows. It flags them.** Every market's point-in-time size rank is
a column — `hl_rank`, `lighter_rank`, `in_hl_top10`, `in_lighter_top10` — and no row was removed
for being large.

That was originally a conditional override, with the gated panel kept as the reporting default
until the rule could be measured. **It has now been measured, and the answer is that no size
gate belongs at any threshold** (`decisions.md` F1, answered 2026-09-22). The binding rule is
lifted. Nothing downstream filters on size.

One obligation replaced it, and it is not optional: **every headline result in Phases 5–8 must
be reported split on `in_hl_top10`.** Not because the rule is right — because the two cohorts
differ on trading cost and hedge quality by 1.5–5×, and a pooled number hides which side it came
from. The top-10 cohort is **141,290 of the 1,034,687 cross-venue rows that have a Hyperliquid
rank (13.66%)**.

---

## `universe` — point-in-time size rank, one partition per venue

- **What it is**: one row per market per hour, with the size that market had at or before that
  hour and its rank against every market live on its own venue that hour. Two partitions,
  `venue=hl` and `venue=lighter`, because the two venues are ranked on different measures.
- **Source**: `scripts/build_universe.py`, via
  [`src/fundr/universe.py`](../../src/fundr/universe.py). Hyperliquid size is
  `open_interest × mark_px` from `hl_asset_ctxs`, taken as the **median of the hour's minutes**
  (one minute can be a stale print or a spike). Lighter size is the **rolling 24-hour sum of
  `lighter_candles.quote_volume`, offset one hour** so the window labelling hour `T` completed
  before `T`. Size means **notional**, never base units: ranking base units would rank 1 BTC
  against 1,000,000 PEPE.
- **Coverage, as written** (defaults `--pool venue --rebalance daily --start 2025-01-17`):

  | partition | rows | markets | window | rank unavailable | rank carried forward |
  |---|---|---|---|---|---|
  | `venue=hl` | 3,132,523 | 234 | 2025-01-17 00:00 → 2026-09-19 23:00 | 955 (0.03%) | 5,544 (0.18%) |
  | `venue=lighter` | 1,607,229 | 218 | 2025-01-18 08:00 → 2026-09-21 22:00 | 1,655 (0.10%) | 0 |

- **Known gaps**:
  - **The Lighter partition covers 218 of Lighter's 235 markets**, not all of them. 17 markets
    have no trade-candle history at all (`datasets.md`, `lighter_candles`), so there is no volume
    to rank them on. They are still present in the `panel`, flagged unrankable — see caveat 1.
  - **The Hyperliquid partition ends two days before the Lighter one**, because it is bounded by
    the S3 archive (`hl_asset_ctxs`, last day 2026-09-19) rather than by the funding API.
  - **Where a rank is carried forward, it is carried at most 24 hours** (`MAX_STALE_HOURS`).
    Beyond that the hour is `rank_unavailable` with a null rank, not a rank. Of the 5,544
    carried Hyperliquid rows, the carry is a median of 13 hours old and never more than 24.
  - Before carry-forward, `decisions.md` D1 measured **17,156 concurrent pair-hours (1.8%)** with
    no Hyperliquid open-interest row to rank against, and 2,323 (0.24%) on the Lighter side after
    the candle re-collection. The counts in the table above are what survives the carry.
- **Columns**: `venue`, `symbol`, `hour`, `size_measure` (`oi_notional` on Hyperliquid,
  `trailing_quote_volume_24h` on Lighter), `size_value`, `size_rank` (1 is the largest; null
  wherever `rank_unavailable`), `rank_basis` (`daily` as written), `rank_is_carried_forward`,
  `rank_stale_hours` (0 when fresh; a daily basis's own one-day lag is **not** counted as
  staleness), `rank_unavailable`, plus provenance.
- **Load**: `dataset.read_partition("universe", venue="hl")` — or
  `pl.read_parquet(dataset.root() / "universe" / "venue=*" / "part.parquet")` for both.
- **Refresh**: free, no network. `uv run python scripts/build_universe.py` rewrites both
  partitions and both panel views together. `--pool venue|matched`, `--rebalance
  hourly|daily|monthly`, `--start`, `--end` are all overridable; the defaults are the recorded
  decisions D1, D2 and D5. It re-scans `hl_asset_ctxs` (9 GB), so it is not instant.

## `panel` — the research spine, two views

- **What it is**: the hours each target is actually defined on, with both venues' size ranks
  attached and neither gating. `view=cross_venue` is the concurrent matched intersection, for
  Target B. `view=per_venue` is every market on each venue, matched or not, for Target A. Target
  *values* are not here — that is Phase 4.
- **Source**: `scripts/build_universe.py`. The spine is the **settled funding grid** of each
  venue, not a live roster, which is why delisted markets are present and survivorship is clean
  (5 of the matched symbols are Hyperliquid-delisted today, 4 Lighter-inactive).
- **Coverage, as written**:

  | view | rows | markets | distinct hours | window |
  |---|---|---|---|---|
  | `cross_venue` | 1,039,523 | 105 pairs | 14,702 | 2025-01-17 08:00 → 2026-09-21 21:00 |
  | `per_venue`, `venue=hl` | 3,143,225 | 234 | 14,710 | 2025-01-17 00:00 → 2026-09-21 21:00 |
  | `per_venue`, `venue=lighter` | 1,585,687 | 235 | 14,703 | 2025-01-17 08:00 → 2026-09-21 22:00 |

  `cross_venue` reproduces `handoff.md` §3 exactly: 978,572 exact-symbol pair-hours plus 60,951
  from the five denomination-alias pairs.
- **Known gaps**:
  - **`cross_venue`**: 4,836 rows (0.47%) have no usable Hyperliquid rank and 3,170 (0.30%) no
    usable Lighter rank. Those are the rows to drop from a rank-conditioned statistic, never to
    test against 10.
  - **`per_venue`, Lighter**: 46,353 rows (2.92%) have no Lighter rank, and **33,255 of them are
    the 17 markets with no candle history at all** — they will never be rankable from data now on
    disk. `hl_rank` is null on every Lighter row by design (see caveat 2).
  - **`per_venue`, Hyperliquid**: 11,657 rows (0.37%) have no Hyperliquid rank.
  - **The cross-section is narrow early and wide late, and this is the single biggest trap in a
    pooled statistic.** Over the first six weeks the median live width is **19 pairs** (minimum
    14, maximum 26, 1,000 hours); from 2026 on it is **96** (maximum 101). `pairs_live_this_hour`
    is on every row for exactly this reason.
- **Columns** (identical across both views, so one loader reads either): `view`, `venue`
  (`cross`, `hl` or `lighter`), `symbol` (the canonical, i.e. Hyperliquid, name),
  `lighter_symbol`, `hour`, `hl_rank`, `lighter_rank`, `in_hl_top10`, `in_lighter_top10`,
  `rank_unavailable`, `hl_rank_unavailable`, `lighter_rank_unavailable`, `pairs_live_this_hour`,
  `pair_age_hours`, `size_multiplier`, `lighter_size_multiplier`, `is_alias_pair`, plus
  provenance.
- **Load**: `dataset.read_partition("panel", view="cross_venue")` — or
  `pl.read_parquet(dataset.root() / "panel" / "view=cross_venue" / "part.parquet")`.
- **Refresh**: free, same command as `universe` above — they are written in one run.

---

## The one thing not to get wrong: `rank_unavailable` is row-governing

**`rank_unavailable` follows the leg that governs the row**, so that `filter(~rank_unavailable)`
is safe to write on any row of either view:

| row | governed by |
|---|---|
| `view=cross_venue` | the Hyperliquid leg — D1 ranks a pair on Hyperliquid open-interest notional |
| `view=per_venue`, `venue=hl` | the Hyperliquid leg |
| `view=per_venue`, `venue=lighter` | the **Lighter** leg |

`hl_rank_unavailable` and `lighter_rank_unavailable` travel beside it and are never ambiguous: a
statistic about one venue conditions on **that venue's own column**, not on `rank_unavailable`.

**Why this is spelled out.** An earlier draft defined `rank_unavailable` as "no usable
Hyperliquid rank" on every row. A Lighter-only market has no Hyperliquid rank to fail closed on
— 130 of the 235 Lighter markets are venue exclusives, and D3 explicitly rejected ranking Lighter
off Hyperliquid's crowding — so that definition read **true on all 1,585,687 per-venue Lighter
rows**, and the obvious filter would have silently deleted the whole of Target A's Lighter panel.
Not an error, not a warning: an empty frame. Two tests now guard it,
`test_rank_unavailable_follows_the_leg_that_governs_the_row` and
`test_the_natural_filter_does_not_delete_target_as_lighter_panel` in
`tests/test_build_universe.py`.

**And the rule the flag exists for: drop unavailable rows, never test their rank.** A null rank
makes `hl_rank <= 10` evaluate **false**, which *admits* the giant instead of excluding it — and
the archive's short days cluster, so the contamination is correlated rather than random. That is
why `in_hl_top10` is **null** where the rank is unavailable and never `False`. The equivalence
holds on every row of both views: `in_hl_top10` is null exactly where `hl_rank_unavailable`, and
`in_lighter_top10` exactly where `lighter_rank_unavailable`. That pair of equivalences is the
fail-closed guarantee.

```python
panel = dataset.read_partition("panel", view="cross_venue")
usable = panel.filter(~pl.col("rank_unavailable"))   # right: drops the rows with no rank
wrong  = panel.filter(pl.col("hl_rank") <= 10)       # wrong: a null rank silently passes as not-top-10
```

## The alias table and the denomination multipliers

Five hand-checked pairs, in [`src/fundr/alias.py`](../../src/fundr/alias.py), worth **+60,951
concurrent pair-hours (+6.2%)**: `kBONK↔1000BONK`, `kFLOKI↔1000FLOKI`, `kPEPE↔1000PEPE`,
`kSHIB↔1000SHIB`, `NOT↔1000NOT`. **Nothing fuzzy is allowed.** At 0.8 similarity six further
candidates appear and all six are false — `HMSTR`/`MSTR` is Hamster Kombat against MicroStrategy,
`ME`/`GME` is Magic Eden against GameStop. Any one of them silently corrupts a spread and nothing
downstream would catch it.

**The multiplier is per venue leg, not per pair.** Measured 2026-09-22 over 772 common hours:

| pair | `size_multiplier` (Hyperliquid) | `lighter_size_multiplier` | rows |
|---|---|---|---|
| `kBONK` / `1000BONK` | 1000 | 1000 | 14,352 |
| `kFLOKI` / `1000FLOKI` | 1000 | 1000 | 14,352 |
| `kPEPE` / `1000PEPE` | 1000 | 1000 | 14,702 |
| `kSHIB` / `1000SHIB` | 1000 | 1000 | 14,352 |
| **`NOT` / `1000NOT`** | **1** | **1000** | 3,193 |

The four `k` markets already agree — both venues quote 1000 tokens. The fifth does not:
Hyperliquid lists `NOT`, one token, not `kNOT`, so the legs differ by exactly 1000×. An unscaled
difference of the two marks on that pair reads as a 100,000 bp basis.

**Neither multiplier is applied to either size measure, and both travel anyway.** Open-interest
notional and quote volume are both **notional**, so they are denomination-invariant — scaling a
`kPEPE` size by 1000 and its price by 1/1000 leaves the product unchanged, and the ranking is
correct without rescaling. The columns are on the row so that a later **price or size** feature
cannot silently mis-scale a `k`/`1000` market. Funding *rates* are unaffected, which is why
Targets A and B are safe either way.

## `pair_age_hours`

Hours since the pair's (or market's) first concurrent hour, as an explicit hour difference.
Measured over the pair's whole history, **not** from `--start`, so sweeping the start date does
not manufacture a fresh young cohort.

It exists because basis risk is concentrated almost entirely in young markets, and Phase 3's own
size rule steers the universe toward exactly that cohort — the two pull in opposite directions,
and without this column the interaction cannot be measured. Distribution on `cross_venue`:

| bucket | rows | share |
|---|---|---|
| week 1 (< 168 h) | 17,640 | 1.70% |
| weeks 2–4 (168–719 h) | 57,037 | 5.49% |
| months 1–3 (720–2,159 h) | 145,596 | 14.01% |
| 3 months+ (≥ 2,160 h) | 819,250 | 78.81% |

**Excluding each market's first month costs 7.2% of panel rows** (measured 7.18% here, 7.2% in
`universe_diagnostics.md` §5). The plan's earlier "~8.6%" figure is superseded.

---

## What a later phase must handle

1. **Drop `rank_unavailable` rows from rank-conditioned statistics; never test their rank
   against 10.** A null rank evaluates false, which admits the giant instead of excluding it.
   See the section above — this is the single most important thing on this page.

2. **`hl_rank` is null on every per-venue Lighter row, and that is correct, not a defect.**
   Lighter is not ranked off Hyperliquid's crowding (D3 rejected it: Target B *is* the venue
   spread, so ranking both legs off one venue's positioning assumes away the quantity being
   forecast). Use `lighter_rank` on a Lighter row.

3. **No size gate exists, and none should be added.** `decisions.md` F1 is answered: there is no
   break at rank 10 (gap **0.108 ± 0.086** clustered, t = 1.25) and no threshold from 3 to 50
   whose gap survives. Persistence by size decile is non-monotone, spread **0.279**, strongest
   D7 and weakest D10 — and the four build knobs (pool × rebalance × start × Lighter leg) move
   the same headline by **0.291** across 24 cells, *more* than size does. The rank columns stay
   because H2 still wants open interest as a **predictor**, and because cost and basis risk are
   strongly size-ordered even though forecastability is not.

4. **Split every headline result on `in_hl_top10`.** The top-10 cohort is ~13.6% of cross-venue
   rows and it is the **cheapest and best-hedged** part of the panel, not a nuisance. From
   `universe_diagnostics.md` §4 (D10 is the **largest** decile):

   | | D1 (smallest) | D5 | D9 | D10 (largest) |
   |---|---|---|---|---|
   | round-turn cost (bp) | 37.9 | 31.4 | 27.1 | **24.3** |
   | 24h basis drift sd (bp) | 91.2 | 23.2 | 37.9 | **18.5** |
   | 24h carry (bp) | 6.21 | 3.71 | 2.92 | 2.16 |
   | **net 24h (bp)** | **−31.7** | **−27.7** | **−24.2** | **−22.1** |

   Both gradients are monotone in size and both run against the design's rule. A pooled number
   hides which cohort it came from.

5. **Use the unconditional carry figures, and label the selective-entry ones as such.** Measured
   across *every* hold on the panel: **1.03 bp at 6h, 3.70 bp at 24h, 9.78 bp at 72h**
   (`universe_diagnostics.md` §6, which prints 1.032 / 3.700 / 9.784). The **6.21 bp at 6h and
   31.33 bp at 72h** recorded earlier in Phase 3 and Phase 4 are **selective-entry** numbers —
   reachable only by restricting entry to hours whose spread is already in the widest decile, in
   sample, with no forecasting. The same diagnostics measure widest-decile entry at **4.25 bp at
   6h, 12.61 bp at 24h, 27.45 bp at 72h**. Against a round turn of ~33 bp at the median
   non-top-10 name, unconditional 6-hour carry is short by a factor of ~32, not ~5. Every
   downstream use of 6.21/31.33 must say it is conditional on selection, or use the unconditional
   figures.

6. **Lighter's size measure is a volume proxy, not open interest.** Lighter publishes no
   open-interest history anywhere, so `trailing_quote_volume_24h` is the best available stand-in
   and it **cannot be validated on the venue it is applied to** (F2 stays open). It *can* be
   checked on Hyperliquid, which has both: over 108,362 coin-days the two top-10 sets share a
   median **8 of 10** members, daily Spearman correlation is **0.873** overall but **0.807**
   inside the true top 30 — i.e. worst precisely at the boundary where a rule would act. The
   "98.06% verdict agreement" figure is **not** honest and must not be quoted: with a median 177
   live coins per day, a ranking that knows nothing scores **89.34%**.

7. **The basis figures are mark against mark, off two different venue references.** Part of the
   level is convention rather than realizable P&L. They also cover only the **804,723 pair-hours
   (77.4%)** with a Lighter mark candle — that series starts 2025-08-25 and nothing before it can
   be measured. Treat a basis number as an upper bound on how much is real.

8. **No cross-sectional statistic without `pairs_live_this_hour`.** The live width runs from 14
   to 101 pairs; the first six weeks are 1,000 hours at a median width of 19. The whole early
   window — everything before 2025-07-29 — is **12.0% of rows but 31.8% of distinct hours**
   (`decisions.md` D5), so it is cheap to keep and expensive to let drive a pooled average. 100
   symbols in one hour are one observation under a market-wide funding shock
   — standard errors belong clustered on the hour, which costs roughly a **factor of 32** in
   precision against the textbook error.

9. **Phase 3 ships no `is_stale` flag, and frozen markets are still in every figure.**
   Hyperliquid's `mark_px` and funding freeze on a delisted market rather than stopping: `AI`
   contributes 460 concurrent pair-hours in which Hyperliquid's rate *and* premium each take
   exactly one value while Lighter's rate takes 317. The 5 Hyperliquid-delisted matched symbols
   are **19,498 pair-hours (1.99%)**. They must **stay** in the panel — deleting them
   reintroduces the survivorship bias the panel avoids — but a frozen price must not enter a
   price-derived statistic. The flag is still owed (`decisions.md`, hygiene §1).

10. **Read each Lighter market's own funding parameters before making any Lighter-wide
    statement.** For Target B this is nearly harmless — all 100 matched markets carry premium
    multiplier 100 and 96 of 100 carry base rate 0.01. For **Target A it is not**: the per-venue
    Lighter view spans 89 markets at base rate 0.0032 and 96 at multiplier 50. A baseline, dead
    zone or clamp calibrated on the matched set is wrong outside it (`decisions.md`, hygiene §2).

11. **Never `shift` across a funding series to build a lag.** Hyperliquid's full history carries
    **1,789 eight-hour intervals** (funding was 8-hourly until 2023-06-08) and **213 single-hour
    holes across 141 of 234 markets**. All of it predates the Lighter window, so Target B is
    untouched — but Target A's per-venue view runs on that history, and a one-row shift would
    silently pair rates 2 or 8 hours apart on 2,002 occasions. Everything in Phase 3 joins on an
    explicit hour difference; a later phase must do the same. Lighter has **zero** irregular
    intervals in 1,585,452.

12. **Row counts drop in a specific order, and the three figures in circulation are all
    correct.** `cross_venue` holds **1,039,523** rows. Requiring a real neighbour on each side —
    an inner join on an explicit hour difference, which drops the hour beside a hole instead of
    reaching across it — leaves **1,039,313**, the "usable pair-hours" in
    `universe_diagnostics.md`'s header. Also requiring an available governing rank leaves
    **1,034,593**, the row count in that report's knob-grid default cell. Reproducible by
    re-reading the panel.

13. **The giant regressors are a function, not a dataset.** D7 keeps BTC, ETH, SOL, XRP and HYPE
    as market-wide regressors even where they are flagged out as observations, because they are
    the best available proxy for the common funding factor. `fundr.universe.giant_regressors()`
    returns them, one row per hour, joinable by `hour` — but **nothing writes them to disk**. A
    later phase must call it.

---

## Known disagreements between documents

Recorded so they are not re-propagated.

1. **The unmatched symbols are not "dominated by denomination prefixes."** `datasets.md` item 1
   and `handoff.md` §6 both said so; both now carry a correction. Exactly **5** recoverable pairs
   exist; the remaining **259** unmatched symbols are genuine venue exclusives — Lighter lists
   equities, FX and commodities, Hyperliquid lists older alts. 105 pairs is very nearly the
   ceiling, not a floor.

2. **Hyperliquid's `0.0000125` is a plateau, not a floor, and nothing is censored.** The settled
   rate sits exactly on it 59.86% of the time but is strictly **below** it 34.00% of the time and
   negative 23.98% — rates pass straight through. It is a dead zone made by the *inner* clamp,
   and the rate is fully observed. `datasets.md` item 9 and `backfill_coverage.md` still use the
   word "floor"; [`../phase4/decisions.md`](../phase4/decisions.md) has the measurement. No
   censoring correction belongs anywhere downstream.

3. **First-month exclusion costs 7.2% of panel rows, not ~8.6%.** The plan's boxed note above
   Task 5 has the older figure; `universe_diagnostics.md` §5 and this panel both measure 7.2%.

4. **Two measurements of week-1 basis drift are in circulation and they differ by ~2×.** The
   plan's boxed note above Task 5 reports a 24-hour drift sd of **124.4 bp** in week 1 over
   804,800 holds; `universe_diagnostics.md` §5 reports **246.1 bp** on this panel. The two used
   different Hyperliquid mark conventions — the diagnostics take the hour's **last** print to
   align with Lighter's candle close, and note that a median-of-the-hour mark measures
   price-timing noise instead of basis. Prefer the diagnostics figure, and do not average them.

5. **Selective-entry carry is quoted twice with different values.** `decisions.md`'s boxed carry
   correction gives widest-decile entry as 3.94 / 11.57 / 25.26 bp at 6/24/72h from an
   independent re-measurement; `universe_diagnostics.md` §6 gives 4.25 / 12.61 / 27.45 bp from
   the committed script. The **unconditional** figures agree to the second decimal (1.03 / 3.70 /
   9.78, with the boxed table rounding 9.784 up to 9.79), and those are the ones to use. Both
   selective-entry readings are far short of a ~33 bp round turn either way.
