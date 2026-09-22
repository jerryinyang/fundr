# Phase 4 targets

What Phase 4 built under `data/phase2/`, what is in it, and what will bite a later phase. This
is the Phase 4 counterpart to [`../phase3/universe.md`](../phase3/universe.md) and follows the
same layout: one section per dataset — what it is, its source, coverage, known gaps, columns,
how to load it, how to refresh it — then a numbered list of everything a later phase must
handle. Every row carries the same three provenance columns as every Phase 2b and Phase 3
dataset (`_source`, `_fetched_at_ms`, `_git_sha`), stamped by `fundr.dataset.stamp`; the
`targets` dataset stamps `_source = "phase4:build_targets"`.

**Sources for this document.** Row counts, schemas, ranges, correlations and shares were
measured directly on the written parquet with polars on 2026-09-23 and are reproducible by
re-reading the files. Everything else is cited to [`decisions.md`](decisions.md) (the decision,
the reasoning and the council record), `data/phase2/qa/basis_drift.md` (the basis measurement,
written by `scripts/measure_basis.py`), or
[`../phase3/universe.md`](../phase3/universe.md). The implementation plan is
[`../superpowers/plans/2026-09-22-phase4-targets.md`](../superpowers/plans/2026-09-22-phase4-targets.md).
Where a figure here disagrees with an older document, the disagreement is called out and the
measurement is what appears.

**Phase 4 produces labels. It produces no model, no feature set and no edge.** Whether these
targets are forecastable at all is the open research question Phases 5–8 exist to ask. Nothing
on this page is evidence that they are.

---

## Read this first: three things that are easy to get wrong

### 1. The target is a **redefinition** of the design's Target B, not an implementation of it

[`../funding_research_design.md`](../funding_research_design.md) defines Target B as the **next
change** in the Hyperliquid–Lighter funding spread, `S_{t+1} − S_t`, and asks whether today's
state predicts the spread's subsequent movement. What Phase 4 built is a different quantity:
the **cumulative level over a hold**, `y_cum_level = Σ_{h=1..H} S_{t+h}`, ranked across the
symbols live that hour.

- **At H = 1 the two are informationally identical.** `S_t` is known at decision time, so
  subtracting it changes the loss function and nothing else.
- **At H = 6, 24 and 72 they are different quantities**, dominated by the persistent component
  of the level (spread lag-1 **0.699**) rather than by its increments (lag-1 **−0.177**).

The reasoning is [`decisions.md`](decisions.md) D1 and it is a good reason — a position is paid
the level hourly, and differencing manufactures a **40.27%** point mass at exactly zero that the
model then has to spend capacity on. But the design's question was **replaced deliberately, not
answered**, and anyone comparing a Phase 5–8 result against the design document must be told so.

### 2. The benchmark is mandatory, and it is not a formality

A level target scores beautifully for free. Hyperliquid's rate level has lag-1 autocorrelation
**0.886** on the concurrent pair-hours, so "same as last hour" is worth roughly **0.79 R²**
having learned nothing at all. Two benchmark columns therefore ship **inside the dataset**
rather than being left for a later phase to remember:

| benchmark | what it is | applies to |
|---|---|---|
| `benchmark_rank` | rank the symbols by **current** `S_t` (or `F_t`) | the cross-venue ranking — D1's boxed correction |
| `benchmark_cum_level` | **`n_hours_used` × today's level** | the level score — D2 |

**A model that does not beat these has demonstrated persistence and nothing else**, whatever its
absolute score. The bar is real, not cosmetic: measured on the written cross-venue partitions,
`benchmark_rank` and `y_rank` agree on only **10.11%** of ranked rows at H = 1, **4.14%** at
H = 6, **2.75%** at H = 24 and **2.33%** at H = 72. There is a lot of room between the benchmark
and the truth — the point is that a model has to actually occupy it.

**`benchmark_cum_level` is `n_hours_used` × the level, never `H` × the level.** Scoring a
two-hour sum against a three-hour prediction hands the model a free win on every short window.

### 3. Nothing here is tradeable on today's evidence, and the document must not imply otherwise

Phase 3 Task 5 measured **net carry at −22 to −32 bp across every size decile and every age
bucket** at a 24-hour hold, net of a measured round-turn near 33 bp and of cross-venue basis
drift ([`../phase3/universe.md`](../phase3/universe.md) item 4). Unconditional carry is
**1.03 bp at 6 h, 3.70 bp at 24 h and 9.78–9.79 bp at 72 h**. These targets are how the
forecastability question gets **asked**; they are not an answer to it.

---

## `targets` — the cumulative funding level over a hold, ranked, 8 partitions

- **What it is**: one row per (symbol, hour) of Phase 3's panel, carrying the funding level a
  hold entered at that hour would actually be paid, its cross-sectional rank inside the hour,
  and both mandatory benchmarks. `view=cross_venue` is **Target B** — the
  Hyperliquid-minus-Lighter spread `S_t` on the concurrent matched panel. `view=per_venue` is
  **Target A** — each venue's own settled rate `F_t`, on every market that venue lists.
- **Layout**: `targets/view=<cross_venue|per_venue>/horizon=<1|6|24|72>/part.parquet`.
  **2 views × 4 horizons = 8 partitions, 217 MB on disk.** D5 makes the horizon a parameter:
  **6 and 24 are the primary runs**, 1 is kept only as the design's original for comparison, and
  72 bounds the long end.
- **Source**: [`scripts/build_targets.py`](../../scripts/build_targets.py), via
  [`src/fundr/targets.py`](../../src/fundr/targets.py). It reads `panel/view=cross_venue` and
  `panel/view=per_venue` from Phase 3 and the two settled-funding datasets, and nothing else.
  Both legs enter on the **common basis** — per-hour signed fraction, positive = longs pay
  shorts — and `assert_common_basis` is called on each before any join.
- **Coverage, as written** (defaults `--horizons 1 6 24 72`):

  | view | rows | symbols | distinct hours | window |
  |---|---|---|---|---|
  | `cross_venue` | 1,039,523 | 105 pairs | 14,702 | 2025-01-17 08:00 → 2026-09-21 21:00 |
  | `per_venue`, `venue=hl` | 3,143,225 | 234 | — | 2025-01-17 00:00 → 2026-09-21 21:00 |
  | `per_venue`, `venue=lighter` | 1,585,687 | 235 | — | 2025-01-17 08:00 → 2026-09-21 22:00 |

  `per_venue` is one partition holding both venues: **4,728,912 rows, 14,711 distinct hours**
  across 369 distinct symbol names. Row counts are identical at every horizon — the horizon
  changes the target, never the row set.

- **How the window length falls off with H** (measured on the written files):

  | view | H | full window (`n = H`) | short (`0 < n < H`) | **no target (`n = 0`)** | file |
  |---|---|---|---|---|---|
  | `cross_venue` | 1 | 1,039,418 | 0 | 105 | 14 MB |
  | `cross_venue` | 6 | 1,038,893 | 525 | 105 | 14 MB |
  | `cross_venue` | 24 | 1,037,003 | 2,415 | 105 | 15 MB |
  | `cross_venue` | 72 | 1,031,963 | 7,455 | 105 | 15 MB |
  | `per_venue` | 1 | 4,728,443 | 0 | 469 | 37 MB |
  | `per_venue` | 6 | 4,726,098 | 2,345 | 469 | 39 MB |
  | `per_venue` | 24 | 4,717,656 | 10,787 | 469 | 40 MB |
  | `per_venue` | 72 | 4,695,144 | 33,299 | 469 | 41 MB |

  The `n = 0` rows are exactly one per series — 105 pairs, 469 (venue, market) series — and they
  are **series tails**, not holes. All **2,002** of Hyperliquid's non-adjacent pairs (1,789
  eight-hour intervals, 213 single-hour holes) sit before this panel's `--start`, so every short
  window here runs off the end of a series. That is a property of the current start date, not of
  the data: move the window back and the holes reappear, which is why the join is built to
  survive them regardless.

- **Known gaps**:
  - **The flags are not in this dataset.** `joint_baseline` and `venue_clamped` are functions in
    `fundr.targets`, not columns on disk. See *What is not in the dataset* below — this is the
    single most likely way a later phase scores a model on venue arithmetic by accident.
  - **The premium columns are not in this dataset either.** `fundr.premium` computes them on
    demand. Same section.
  - **The panel's size ranks are not copied onto these rows.** Join `panel` on `(symbol, hour)`
    for `hl_rank`, `in_hl_top10` and the rest — and when you do, **drop `rank_unavailable` rows**
    rather than testing `hl_rank <= 10` on a null rank, which evaluates false and silently admits
    the giant (`../phase3/universe.md`, "the one thing not to get wrong").
  - **`cross_venue` includes the five denomination-alias pairs** (`kPEPE`/`1000PEPE` and
    friends), **60,951 rows**. Every recorded headline figure in `decisions.md` was measured on
    the **978,572 exact-symbol** pair-hours. Both populations are correct; they are not
    interchangeable, and this page gives both wherever they differ.
  - **The cross-section is narrow early and wide late.** `n_ranked_this_hour` runs from 14 to
    **101** on `cross_venue`, and the first six weeks sit at a median width of 19. A rank of 3
    means something different in each. Never read a rank without it.

- **Columns** (identical across both views except for the value column and `venue`):

  | column | type | what it is |
  |---|---|---|
  | `venue` | str | `hl` or `lighter`. **`per_venue` only.** |
  | `symbol` | str | the canonical (Hyperliquid) name on `cross_venue`; the venue's own name on `per_venue` |
  | `hour` | datetime[ms] | the decision hour `t`, naive UTC |
  | `spread` / `rate` | f64 | the level **at** `t`, on the common basis. `spread` on `cross_venue`, `rate` on `per_venue` |
  | `y_cum_level` | f64 | **the target.** `Σ_{h=1..H}` of the level at `t+1 … t+H`. **Null**, never zero, where no forward hour exists |
  | `n_hours_used` | i64 | how many of the `H` forward hours actually existed, 0 to `H`. Nothing is imputed |
  | `y_rank` | u32 | dense cross-sectional rank of `y_cum_level` inside the hour, **1 = largest**. Null where the target is null |
  | `n_ranked_this_hour` | i64 | the width of that cross-section |
  | `benchmark_cum_level` | f64 | the no-change benchmark: `n_hours_used` × the level at `t` |
  | `benchmark_rank` | u32 | the rank-by-current-level benchmark, over exactly the same rows as `y_rank` |
  | `_source`, `_fetched_at_ms`, `_git_sha` | | provenance |

  **Ties share a rank and do not consume the next one** (`rank("dense")`). That is a large part
  of why D1 ranks rather than regresses: 45.57% of concurrent pair-hours have both venues at
  their own baseline and the spread there takes exactly two values, so ties are the common case.
  Lighter's 1e-6 reporting lattice becomes the tie-break.

  **A row with no target is in neither ranking.** Ranking it last would be imputation under a
  different name, so `y_rank` and `benchmark_rank` are both null there and the two rankings
  always cover exactly the same rows — **1,039,418** on `cross_venue`, **4,728,443** on
  `per_venue`.

  **On `per_venue`, ranks are within a venue.** Hyperliquid's markets are ranked against each
  other and Lighter's against each other — widest hour **234** and **218** respectively, never
  ~450. The two venues share one file, so a naive `.over("hour")` re-rank across the file would
  silently pool them.

- **Load**:

  ```python
  from fundr import dataset
  t = dataset.read_partition("targets", view="cross_venue", horizon=24)

  # or directly
  import polars as pl
  t = pl.read_parquet(dataset.root() / "targets" / "view=cross_venue" / "horizon=24" / "part.parquet")

  # every horizon of one view at once, horizon recovered from the path
  t = pl.read_parquet(dataset.root() / "targets" / "view=cross_venue" / "horizon=*" / "part.parquet",
                      hive_partitioning=True)

  # one venue of Target A
  a = (dataset.read_partition("targets", view="per_venue", horizon=24)
         .filter(pl.col("venue") == "hl"))
  ```

- **Refresh**: free, no network, a few minutes. `uv run python scripts/build_targets.py` rewrites
  all 8 partitions; `--horizons 6 24` writes a subset. It does **not** re-scan the 9 GB
  `hl_asset_ctxs` archive — it reads settled funding and the panel only. The script prints its
  own gates on every run (below); if those miss, the output is not worth using.

- **The gates, printed by the script and reproduced here**:

  | population | adjacent pairs at H = 1 | exact-zero share of `y_cum_level − S_t` | spread level lag-1 |
  |---|---|---|---|
  | exact-symbol pairs | 978,472 | **40.27%** (recorded 40.27%) | **0.6988** (recorded 0.699) |
  | all pairs incl. aliases | 1,039,418 | 40.15% | 0.6959 |

  Both gates are measured on the exact-symbol population because that is the population the
  recorded figures were measured on. **If either misses there, the join or the basis is wrong and
  nothing downstream is worth running.**

---

## What is **not** in the dataset, and must be computed at the point of use

The plan's original dataset sketch listed the two flags and the four premium columns as columns
of `targets`. **They were deliberately not written.** They are functions, so that a flag can
never be silently stale against a re-read market snapshot and so that nothing downstream can
filter on a column it found lying on a row. This is the biggest single difference between this
page and the plan, and it is the thing most likely to be missed.

### The flags: `joint_baseline` and `venue_clamped`

Both come from `fundr.targets.add_venue_flags`, which needs **both venues' rates and each
market's own Lighter parameters** — never BTC's. It returns every input row, unchanged and in
order; it cannot drop one.

```python
import polars as pl
from fundr import dataset, targets

panel  = dataset.read_partition("panel", view="cross_venue").select("symbol", "lighter_symbol", "hour")
params = pl.read_parquet(sorted((dataset.root() / "markets" / "venue=lighter").glob("*/part.parquet"))[-1])

rows = (panel
        .join(hl_rates,      on=["symbol", "hour"],        how="left")   # hl_rate
        .join(lighter_rates, on=["lighter_symbol", "hour"], how="left")  # lighter_rate
        .join(params.select(pl.col("symbol").alias("lighter_symbol"),
                            pl.col("base_interest_rate_pct").alias("lighter_base_rate_pct"),
                            pl.col("funding_clamp_big_pct").alias("lighter_clamp_big_pct")),
              on="lighter_symbol", how="left"))

flagged = targets.add_venue_flags(rows)
skill   = targets.skill_metric_rows(flagged)   # drops joint_baseline — D3
tuning  = targets.tuning_rows(flagged)         # drops both            — D3 + D4
```

`scripts/build_targets.py` builds the same two rate legs with `_leg(...)`, on the common basis
and with `assert_common_basis` already applied; reuse it rather than re-deriving a join.

Measured through `add_venue_flags` itself on 2026-09-23:

| population | rows | `joint_baseline` | `venue_clamped` | `skill_metric_rows` | `tuning_rows` |
|---|---|---|---|---|---|
| exact-symbol pairs | 978,572 | **445,904 (45.57%)** | **142**, 23 symbols | 532,668 (**drops 445,904**) | 532,526 (**drops 446,046**) |
| all pairs incl. aliases | 1,039,523 | 472,641 (45.47%) | 145, 26 symbols | 566,882 (drops 472,641) | 566,737 (drops 472,786) |

**`joint_baseline` is arithmetic, not a market.** Both venues sitting at their own baseline. The
spread there takes exactly **two** values: `+5.0e-7` where Lighter's base rate is 0.01%
(Hyperliquid's untruncated `1.25e-5` against Lighter's `0.00125%` truncated *down* to `0.0012%`)
and `1.25e-5` on the 27 markets whose base rate is 0. A model scored on those hours is being
graded on Lighter's rounding rule. Excluding them un-masks the sign of everything else: mean
spread is **−6.23e-8** overall and **−5.36e-7** without them, 8.6× larger and negative. Reported
separately they are a zero-turnover known-sign carry of about **0.44%/year** — the floor any
model must beat.

**`venue_clamped` is Lighter's outer clamp binding.** 142 hours, 23 symbols, 42 distinct days.
Mean |spread| there is **2.847e-3 = 28.5 bp/h, 149× the overall mean** — but the 142 hours are
only **2.2% of all gross spread**. A risk and capacity question, not a revenue one. Keep them,
fit on them, score on them, report them separately, **do not tune on them**: a squared-error fit
let near them would bend the whole model to chase 2.2% of the gross. Hyperliquid's own outer
clamp never binds at all — **0 of 978,572 hours**, largest observed magnitude 2.26e-2.

### The premium columns

`fundr.premium.lighter_premium(rates, params)` attaches `lighter_premium_point`,
`lighter_premium_lo`, `lighter_premium_hi`, `premium_is_point_identified` and
`funding_clamp_small_eff_pct`; `fundr.premium.hl_premium(df)` passes Hyperliquid's observed
premium through, present on 100% of rows. Both emit **per-hour signed fractions**, Phase 4's
common basis — the formula itself runs in percent, but a 100× mismatch between neighbouring
columns is exactly the silent bug `assert_common_basis` exists to stop.

Measured on 2026-09-23:

| population | rows | point-identified | interval-bounded |
|---|---|---|---|
| exact-symbol matched pair-hours | 978,572 | **356,800 (36.46%)** | 621,772 |
| all matched pair-hours incl. aliases | 1,039,523 | 375,362 (36.11%) | 664,161 |

**This is a link function, not recovered data, and that is the whole warning.** See item 6 below.

---

## The modules

### [`src/fundr/targets.py`](../../src/fundr/targets.py) — the join that cannot bridge a hole, and the assert that cannot be bluffed

- `forward_window(df, h=…)` — attaches the next `h` hours by an **explicit hour-difference
  join**, one equality join per `k`, plus `n_hours_used`. A forward hour that does not exist
  produces a null and is counted; nothing is borrowed from across a hole and nothing is
  interpolated. **Nothing in this phase lags with `shift`.**
- `non_adjacent_pairs(df)` — the consecutive rows a `shift` would have paired. Over Hyperliquid's
  full funding history it returns **2,002** rows: 1,789 at `gap_hours = 8` and 213 at
  `gap_hours = 2`. If it ever returns zero on that history, the grid is being read wrong.
- `assert_common_basis(df)` — raises unless every recognised rate column is a per-hour signed
  fraction. Checked by **value**, not by column name, on three independent tests: scale (any
  magnitude above 0.05), **lattice** (every non-zero value a multiple of 1e-4, which is Lighter's
  four-decimal percent grid — 100.0% of its raw rates sit on it against 0.12% of the same rows'
  fractions), and sign (a wide cross-section with no negative rate at all). A frame carrying no
  recognised rate column raises too, so the assert cannot silently pass something it never looked
  at. **What it cannot catch**: an error that preserves all three — every sign inverted, a
  division by 100 applied twice, or a correct column assembled from the wrong venue or hour.
- `add_venue_flags`, `skill_metric_rows`, `tuning_rows` — above.
- `lighter_baseline(...)`, `lighter_clamp(...)` — each market's own baseline rate and outer-clamp
  ceiling as per-hour signed fractions, from **that market's** published parameters.

### [`src/fundr/premium.py`](../../src/fundr/premium.py) — both venues' premia, on one basis, with a warning label on half of it

Hyperliquid publishes its premium on 100% of rows. Lighter publishes none, but its settlement
formula inverts: off the market's baseline, `P = 8·rate + clamp` above and `P = 8·rate − clamp`
below, in percent; on it, the premium is bounded to the dead-zone interval and
`premium_is_point_identified` is false. The bounds are read off the **settled rate** rather than
quoted as `interest ± clamp`, because truncation lets a premium up to 8e-4 outside the literal
band still settle at the baseline rate — a bound a real premium can sit outside is worse than no
bound.

**Each market's own clamp, never BTC's.** `funding_premium_multiplier` is 100 on 137 markets, 50
on 96 and 1 on 2; `base_interest_rate_pct` is 0.0100 on 119, 0.0032 on 89 and 0.0000 on 27. F6
(settled 2026-09-22 against Lighter's public `market_stats` websocket) confirmed
`clamp_small = 0.05` as the **half**-band on all matched crypto pairs — 100/136 exact and
**10/13 in the band where the rival 0.025 reading scored 0/13** — and separately found that the
multiplier-50 equity/RWA markets fit the **halved** bound better (MAE 0.00022 against 0.00065).
No matched pair is multiplier-50, so **Target B is untouched**; Target A's Lighter view spans 96
such markets, so it is not. `effective_clamp_small_pct` is the only clamp the module will apply,
it is derived from each market's own row, and the value actually used is emitted beside the
premium. There is no crypto-calibrated constant in the file to reach for by accident.

### [`scripts/build_targets.py`](../../scripts/build_targets.py) and [`scripts/measure_basis.py`](../../scripts/measure_basis.py)

`build_targets.py` writes the 8 partitions and prints the gates. `measure_basis.py` writes
[`../../data/phase2/qa/basis_drift.md`](../../data/phase2/qa/basis_drift.md) — the cross-venue
mark-basis measurement, summarised in item 8 below. Both are free and offline:

```
uv run python scripts/build_targets.py                 # all 8 partitions
uv run python scripts/build_targets.py --horizons 6 24  # the primary runs only
uv run python scripts/measure_basis.py                  # rewrites data/phase2/qa/basis_drift.md
uv run pytest tests/ -q                                 # 366 tests
```

---

## What a later phase must handle

1. **Say that Target B was redefined, every time a Target B result is reported.** The design asks
   for the next *change*; `y_cum_level` is the cumulative *level* over a hold. Identical at
   H = 1, **different quantities at H ≥ 6**. A reader comparing a Phase 5–8 result against
   `funding_research_design.md` is otherwise misled. (`decisions.md` D1, first boxed correction.)

2. **Score against the benchmark or do not report the score.** `benchmark_rank` for the
   cross-venue ranking, `benchmark_cum_level` for any level score, no-change for per-venue.
   A level target is worth ~0.79 R² for free off a lag-1 of 0.886. Measured on the written files,
   `benchmark_cum_level` correlates with `y_cum_level` at **0.865 (H=1) / 0.752 / 0.626 / 0.474**
   on the Hyperliquid per-venue view and **0.817 / 0.732 / 0.618 / 0.484** on Lighter's. Those
   are the numbers a model has to beat, and they cost nothing.

3. **`benchmark_cum_level` is `n_hours_used` × the level, not `H` × the level.** If a later phase
   rebuilds the benchmark, it must rebuild it that way. Scoring a short window against a
   full-length prediction is a free win, and at H = 72 it would apply to 7,455 cross-venue rows
   and 33,299 per-venue ones.

4. **A tail row carries a NULL target, never a zero.** `sum_horizontal` over an all-null window
   returns `0.0`, which would read as "the hold paid nothing" and is indistinguishable from a
   real flat hold. The script guards this with `when(n_hours_used > 0)`. **Any code that
   `fill_null(0)`s `y_cum_level` has silently invented 105 (cross-venue) or 469 (per-venue) flat
   holds per horizon** and, if the window is ever moved back before the current `--start`, 2,002
   more beside Hyperliquid's holes.

5. **The flags are flags, never filters — and they are not on the rows.** Compute them with
   `add_venue_flags` and exclude at the point a metric is computed, using `skill_metric_rows`
   (drops `joint_baseline`, **445,904** exact-symbol rows) or `tuning_rows` (drops both,
   **446,046**). Scoring on joint-baseline hours grades a model on Lighter's four-decimal
   rounding rule rather than on a market, and it is **45.57%** of the panel. Deleting the clamp
   hours from the dataset instead would fit a model on a sample conditioned on the event never
   happening.

6. **The premium column is a link function, not new data.** `P = 8·rate ± clamp` is a **strictly
   monotone transform of `signed_rate_fraction`**, a column already on disk, and a monotone map
   cannot change a rank: Spearman against Hyperliquid's observed premium is **identical to four
   decimals (0.6233)** for the inverted premium and for the raw rate, and the raw rate's Pearson
   is the **higher** of the two (**0.8575** against **0.8450**), over the 337,905 off-baseline
   hours on markets carrying the 0.01% base rate. **Feeding both `lighter_premium_point` and
   `signed_rate_fraction` to one fit is feeding one variable twice.**
   [`../phase1/handoff.md`](../phase1/handoff.md) §2 — no *independent* historical Lighter
   premium observation exists — **stands as written**, and this page does not amend it. What the
   inversion buys is scale and structure: the rate in the units of the quantity that drives it,
   with the dead zone made explicit. Do not write a test asserting the point estimates respect
   the dead-zone band: branch selection is `rate > baseline` against `rate < baseline` and the
   band condition reduces to exactly that inequality, so the check passes on any data whatsoever.

7. **Hyperliquid's `0.0000125` is a plateau, not a floor, and nothing is censored.** The settled
   rate sits exactly on it 59.86% of the time but is strictly **below** it 34.00% of the time and
   **negative** 23.98% — rates pass straight through. It is a dead zone made by the *inner*
   clamp, which cancels the premium whenever the premium lies inside a band of width 0.001, and
   the rate is fully observed. **No censoring correction belongs anywhere downstream.** What is
   not identified inside the band is the premium, and Hyperliquid publishes that on 100% of rows.

8. **The basis measurement is a range with a cleaning rule, and it is measured on marks.**
   From [`../../data/phase2/qa/basis_drift.md`](../../data/phase2/qa/basis_drift.md):

   | quantity | measured |
   |---|---|
   | sd(Δbasis) at 24 h | **19.5–24.8 bp** across every frozen-filtered cleaning rule; **37.2 bp** with nothing excluded |
   | median \|24 h drift\| | **7.50 bp** cleaned, 7.59 bp unfiltered — the stable statistic, it barely moves under any rule |
   | share of 24 h holds exceeding ±18.91 bp | **17.2–17.7%**, and 5.0% on BTC/ETH/SOL |
   | share exceeding the hold's **own** carry | **81.1–81.3%** — the harsher and more honest comparison |
   | does it compound? | **No.** sd grows as H^0.01–H^0.16 against H^0.5 for a random walk |

   **The old 22.3 bp is not reproducible under any single convention** and must not be quoted as
   a point estimate. **Every number here is mark against mark**, off two different venue
   references (Hyperliquid oracle, Lighter index), so part of it is a marking convention rather
   than realizable P&L. The realizable figure needs **traded** prices, which are not on disk.
   That is why `decisions.md` F2 stays open and Phase 9 sizes nothing until it resolves.

9. **The young-market basis figure is a range, and only a range.** Week-1 24-hour drift sd is
   **198.9 bp unfiltered, 117.3 bp with frozen marks out, 79.9 bp with XPL and MON also out**
   (measured on the correct age origin, `../superpowers/plans/2026-09-22-phase3-universe.md`,
   boxed note above Task 5), and **140.2 bp** under Task 5's own filter
   (`basis_drift.md` §7). All four are correct for their rule; **none is *the* number**, and
   **124.4 and 246 are both wrong** — the first used the wrong age origin, the second is one
   cleaning rule quoted as if it were all of them. What is robust and should be quoted instead:
   **3 months+ is 17.7 bp under every rule**, **BTC/ETH/SOL 9.9 bp**, and the young-vs-mature
   gradient holds regardless. Age must come from `panel.pair_age_hours` — the pair's first
   *concurrent funding* hour — **never** from the first hour of the mark-joined panel, which
   starts 2025-08-25 and relabels mature pairs as week-1.

10. **Label every selective-entry carry number as selective, and name the rule.** Unconditional
    carry across every hold on the panel is **1.03 bp at 6 h, 3.70 bp at 24 h and 9.78–9.79 bp
    at 72 h** (0.19 bp at 1 h, 20.05 bp at 168 h). The **6.21 bp at 6 h and 31.33 bp at 72 h** in
    circulation are **widest-decile entry**, and the **18.91 bp at 24 h** used as the basis
    exceedance threshold is **widest-spread entry** — both in sample, gross of costs, from a rule
    with no forecasting in it. Against a round turn near 33 bp, unconditional 6-hour carry is
    short by a factor of about 32, not about 5.

11. **Lighter's outer-clamp ceiling is per market — `funding_clamp_big_pct / 8` — and a
    large rate is usually not a violation.** The parameter is 4.0 on 227 markets (ceiling
    0.5 %/h, every one of the matched pairs), 20.0 on ARC (2.5 %/h), 16.0 on RIVER (2.0 %/h),
    0.02 on five Korean equities (0.0025 %/h) and 0.0 on MKR. **ARC's observed maximum of
    1.454 %/h and RIVER's 0.9924 %/h sit under their own wider ceilings and are not violations.**
    **Six markets do breach theirs** and their published parameters cannot be trusted — SAMSUNG
    and HANMI at 0.5 %/h, KRCOMP 0.4347, HYUNDAI 0.4045, SKHYNIX 0.3814 against a published
    0.0025 %/h, and MKR at 0.0636 %/h against a published clamp of 0. **None of them is a matched
    pair, so Target B is exact** (observed maximum 5.0000e-03 to five figures); this is a
    **Target A only** problem. A market whose published clamp is not positive flags nothing,
    which is why believing MKR's zero would otherwise flag all 4,463 of its hours as clamped.

12. **Read each Lighter market's own funding parameters before any Lighter-wide statement.** For
    Target B this is nearly harmless — all 100 matched markets carry premium multiplier 100 and
    96 of 100 carry base rate 0.01. For **Target A it is not**: the per-venue Lighter view spans
    89 markets at base rate 0.0032 and 96 at multiplier 50. A baseline, dead zone or clamp
    calibrated on the matched set is wrong outside it.

13. **Never `shift` across a funding series.** Hyperliquid's full history carries **1,789
    eight-hour intervals** (funding was 8-hourly until 2023-06-08) and **213 single-hour holes
    across 141 of 234 markets**, out of 4,676,131 intervals; a one-row shift pairs rates 2 or 8
    hours apart on **2,002** occasions and the result reads as noise. All 2,002 sit before this
    panel's start today — that is the start date's doing, not the data's. Lighter has **zero**
    irregular intervals in 1,585,452.

14. **Join on `signed_rate_fraction`, never on Lighter's raw `rate`.** Used as-is it is 100× too
    large and unsigned. Phase 1 named this the single most likely silent bug in Phase 4, and
    `assert_common_basis` is the guard — call it on any frame you assemble yourself, not only on
    the ones this phase wrote.

15. **No cross-sectional statistic without `n_ranked_this_hour`, and no pooled standard error
    without clustering on the hour.** The cross-venue width runs from 14 to 101. 100 symbols in
    one hour are one observation under a market-wide funding shock; the textbook standard error
    overstates precision by roughly a factor of 32
    ([`../phase3/universe.md`](../phase3/universe.md) item 8).

16. **Split every headline result on `in_hl_top10`.** The top-10 cohort is ~13.6% of cross-venue
    rows and it is the **cheapest and best-hedged** part of the panel — 24.3 bp round turn and
    18.5 bp of 24-hour basis drift against 37.9 and 91.2 in the smallest decile. A pooled number
    hides which cohort it came from (`../phase3/universe.md` item 4).

17. **Frozen and stale markets are still in every row of this dataset, and no `is_stale` flag
    exists yet.** Hyperliquid's `mark_px` and funding freeze on a delisted market rather than
    stopping; the 5 Hyperliquid-delisted matched symbols are **19,498 pair-hours (1.99%)**. They
    must **stay** — deleting them reintroduces the survivorship bias the panel avoids — but a
    frozen price must not enter a price-derived statistic. The flag is still owed
    (`decisions.md`, hygiene §1).

---

## Known disagreements between documents

Recorded so they are not re-propagated.

1. **The plan's `targets` column list is not the schema that was written.** The plan's *Dataset
   layout* section lists `joint_baseline`, `venue_clamped`, `hl_premium`,
   `lighter_premium_point`, `lighter_premium_lo`, `lighter_premium_hi` and
   `premium_is_point_identified` as columns of `targets`. **None of them is on disk**; all are
   computed on demand by `fundr.targets` and `fundr.premium`. `build_targets.py`'s own docstring
   records the deviation. The written schema is the one in the table above.

2. **Task 6 Step 2 of the plan asks for `docs/phase1/handoff.md` §2 to be corrected. It must
   not be.** That step was written when the premium inversion was believed to overturn Phase 1's
   claim. It does not: the inversion is a monotone relabelling of a column already on disk, so it
   is not an *independent* observation, which is all §2 ever claimed. The DISPUTED box and the
   "F6 RUN AND SETTLED" box in [`decisions.md`](decisions.md), the `premium.py` module docstring
   and F5 all say §2 stands. **`handoff.md` was left unchanged and Step 2 is recorded here as
   superseded**, not silently skipped.

3. **Six Lighter markets breach their published outer clamp, not seven.** `targets.py`'s
   `lighter_clamp` docstring says "seven markets ... the five Korean equities plus HANMI ... and
   MKR". **HANMI is one of the five** — the markets with `funding_clamp_big_pct = 0.02` are
   exactly SAMSUNG, HYUNDAI, KRCOMP, SKHYNIX and HANMI — so the correct count is **5 + MKR = 6**.
   Measured 2026-09-23 over all 235 Lighter markets. The docstring's substance (which markets,
   what magnitudes, Target A only) is right; only the count is off by one.

4. **D6's basis block is superseded by Task 5's report and by F2's updated row.** D6's original
   numbers (24 h drift sd 22.3 bp, level lag-1 0.9342, week 1 at 90.0 bp) were measured on a
   `lighter_mark_candles` series missing 337,412 bars, and its week-1 figure additionally used
   the wrong age origin — `basis_drift.md` §7 reproduces exactly where it came from. Read D6's
   boxed re-measurement and F2's row, not D6's opening paragraphs. **22.3 bp must not be quoted
   as a point estimate.**

5. **Two lag-1 figures for Hyperliquid's rate level are both correct, on different
   populations.** **0.886** is measured on the 978,572 concurrent pair-hours (reproduced here at
   0.8855); **0.865** is the same statistic over the whole per-venue Hyperliquid panel of
   3,142,991 adjacent pairs. Lighter's are 0.867 concurrent and 0.817 per-venue. Quote the one
   that matches the population being scored, and say which.

6. **Two joint-baseline counts are in circulation and both are right.** **445,904 (45.57%)** is
   the exact-symbol figure every recorded number in `decisions.md` rests on; **472,641 (45.47%)**
   is the same flag over the full written `cross_venue` partition, which adds the 60,951
   alias-pair rows. Likewise `venue_clamped`: **142 hours / 23 symbols** exact-symbol,
   **145 / 26** on the full partition.
