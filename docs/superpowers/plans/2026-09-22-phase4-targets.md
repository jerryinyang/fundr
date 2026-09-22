# Phase 4 Target Construction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the forecasting targets Phases 5–11 are scored on — Target A (per-venue funding) and Target B (the Hyperliquid–Lighter funding spread), both as **expected cumulative level over a parameterised hold, ranked across symbols**, with the venue-arithmetic artifacts flagged out of every skill score — plus the two measurements that decide whether the primary target is funding or funding net of price basis.

**Architecture:** A `fundr.targets` module builds target columns from Phase 3's panel and Phase 2b's funding datasets; a `fundr.premium` module recovers Lighter's historical premium by inverting the published settlement formula and exposes both venues' premia as the feature pathway; one script writes the target datasets and one measures the cross-venue price basis. Nothing here models anything — Phase 4 produces labels, Phase 6 produces baselines.

**Tech Stack:** Python 3.13, uv, **polars only — never pandas**, pytest.

**Spec:** none. Written from `docs/funding_research_design.md` (Phase 4, "Construct the Targets"; Targets A/B/C), `docs/phase1/handoff.md` §3 and §5, `docs/phase2/handoff.md`, and — for every judgement call — [`../../phase4/decisions.md`](../../phase4/decisions.md), which records each decision, its reasoning, and the council session behind it.

**Depends on:** [`2026-09-22-phase3-universe.md`](2026-09-22-phase3-universe.md) Tasks 1–3. Phase 4 reads `panel/view=cross_venue` and `panel/view=per_venue` and nothing else from Phase 3.

## Global Constraints

- Python 3.13 via `uv`. Run everything as `uv run ...`.
- **polars only, never pandas.**
- **Never write to `data/phase1/`.** Phase 4 writes only under `data/phase2/`.
- Per-row provenance via `fundr.dataset.stamp`, as Phase 2b.
- **Common basis: per-hour signed fraction, positive = longs pay shorts.** Hyperliquid's `funding_rate` is already that; Lighter's is `signed_rate / 100`. **Join on `signed_rate_fraction`, never on Lighter's raw `rate`** — used as-is it is 100× too large and unsigned. Phase 1 named this the single most likely silent bug in Phase 4. Assert it in code.
- **Never build a lag with `shift`.** Hyperliquid's settled grid is not uniform: **1,789 eight-hour intervals** (funding was 8-hourly until 2023-06-08) and **213 single-hour holes across 141 of 234 markets**, out of 4,676,131 intervals. A one-row shift silently pairs rates 2 or 8 hours apart on 2,002 occasions. Join on an explicit hour difference and drop non-adjacent pairs. Lighter has **zero** irregular intervals in 1,585,452.
- **The meaningful resolution of any spread is 1e-6, not 1e-10** — Lighter truncates to four decimal places of percent. 3.26% of non-zero spread changes fall below Lighter's own reporting resolution.
- Free. Phase 4 spends nothing.

## Measured facts (2026-09-22, this plan's own measurements, over the 978,572 concurrent pair-hours)

**The structure of the rates**
- Hyperliquid's settled rate is exactly `0.0000125` on **59.86%** of hours, **strictly below it on 34.00%**, and **negative on 23.98%**. It is a **plateau, not a floor** — the inner clamp cancels the premium exactly whenever the premium lies inside a band of width 0.001. Nothing is censored: the rate is fully observed, and Hyperliquid publishes the premium for **100%** of rows, which varies continuously inside the dead zone (548,808 distinct values among 585,752 dead-zone hours, sd 2.21e-4).
- Lighter sits at its market's reported baseline on **63.54%** of hours.
- **Hyperliquid's outer ±0.04 clamp never binds: 0 of 978,572** (largest magnitude 2.26e-2). **Lighter's binds on 142 hours (0.0145%)**, 23 symbols, 139 distinct hours, 42 distinct days, worst day 12. Mean |spread| there is **2.847e-3 = 28.5 bp/h, 149× the overall mean**, but only **2.2% of all gross spread**.
- **45.57% of hours (445,904) have both venues at baseline**, and the spread there takes exactly **two** values: `+5.0e-7` and `1.25e-5`. Mean `+5.029e-7`. That is Lighter truncating `0.00125%` to `0.0012%` — arithmetic, not economics. Mean spread overall is **−6.23e-8**; **excluding** those hours it is **−5.36e-7**, 8.6× larger and negative.

**The targets**
- Exact-zero share: ΔF Hyperliquid **54.01%**, ΔF Lighter **60.41%**, Δspread **40.27%**. Conditional on Hyperliquid sitting at baseline, next hour's rate is identical **90.14%** of the time — but unconditionally the do-nothing answer is right only 54.01%.
- Lag-1 autocorrelation: levels `F_HL` **0.886**, `F_Lighter` **0.867**, spread **0.699**, Hyperliquid premium **0.896**. Changes: ΔF_HL **−0.040**, ΔF_Li **−0.133**, Δspread **−0.177**, Δpremium **−0.042**.
- Spread level sd **8.04e-5 = 0.804 bp/h**; half-life **1.98 h** pooled, **2.23 h** per-pair median; sign-run median **2 h**, 95th percentile 29 h. **Between-symbol variance is only 1.90% of Var(spread)** and lag-1 on symbol-demeaned spread is **0.6985** against 0.7042 raw — **there is no durable per-symbol spread to sit in; you must round-trip.**
- **A log ratio is impossible**: at least one venue's rate is ≤ 0 on **33.61%** of hours.
- **The cross-section is where the size is.** Pooled mean |spread| on non-top-10 pairs is 0.1966 bp/h. Selecting each hour's widest symbol: **2.562 bp/h (13.0×)**; widest 5: 1.136 (5.8×); widest 10: 0.775 (3.9×). Cumulative gross carry entering the widest: **1.83 bp (1h), 7.56 (6h), 18.91 (24h), 35.99 (72h), 61.64 (168h)** — in-sample, gross, from a rule with no forecasting in it.
- **Perfect-sign annual ceiling is 16.7%/year gross on notional** (E|S| = 1.909e-5/h × 8760). Not 56% — the half-normal constant 0.798 does not apply to a distribution with a 45.57% spike; measured E|S|/sd = **0.2374**.

**The premium, which is more available than Phase 1 recorded**
- **Lighter's hourly premium is point-identified on 356,800 off-baseline hours (36.46%)** by inverting the published formula: above baseline `P = 8·rate + 0.05`, below baseline `P = 8·rate − 0.05`, in percent. On the **337,905** rows whose market carries the 0.01% base rate, **zero** implied values violate the band the formula requires. The implied Lighter premium correlates **0.845** with Hyperliquid's observed premium. On the remaining 63.54% the premium is **bounded to a known interval**. **This contradicts `docs/phase1/handoff.md` §2**, which says no historical Lighter premium exists anywhere.
- It recovers the hour's **final average** premium, not the intra-hour path — **Target C is unaffected and still needs the recorder.**

**Is this trade even hedged?**
- Hyperliquid-minus-Lighter **mark basis**, hour-close against hour-close, 564,391 hours / 96 symbols from 2025-08-25: level mean **+3.36 bp**, lag-1 **0.9342**, per-symbol sd median **13.4 bp** (BTC 4.9 bp). **Change over a hold: sd 19.5 bp (1h), 22.3 (24h), 27.4 (72h), 39.8 (168h)** — exceeding the entire 24-hour carry from the widest spread on **17.7% of 24-hour holds**. Measured on *mark* prices; the venues mark off different references (Hyperliquid oracle, Lighter index), so part is convention rather than realizable P&L.

**Costs are unknown and must stay that way until measured**
- Lighter's `maker_fee`/`taker_fee` read **0.0 on all 235 markets**; Hyperliquid's market snapshot has **no fee column at all**. No bid/ask, no depth, no historical fee series exists on disk. **No cost model may rest on these fields.**

## Scope decisions recorded here, deliberately

1. **Targets are levels over a hold, not one-hour differences.** The design's `F_{t+1} − F_t` is replaced by `E[Σ_{h=1..H} S_{t+h}]`, ranked across symbols. Reasoning in `decisions.md` D1 — five of seven council advisors independently reached "you are paid the level, not the change", and the 40.27% atom in the spread change is manufactured by differencing.
2. **The premium is a feature pathway, not a rival target.** The settled rate is an exact function of the premium, so forecasting the premium and forecasting the rate are the same model. Build the premium columns; do not build a premium target.
3. **No hurdle model.** Given the premium, "will it move" is deterministic. The useful part of that argument — that a squared-error fit returns `P(move) × E[y | move]` — belongs at Phase 9's sizing stage.
4. **Target C is not built here.** Prospective-only, exactly as Phase 1 decided; the premium inversion does not change it. Phase 11's business.
5. **No modelling, no baselines, no features beyond the premium pathway.** Phase 4 produces labels.
6. **The cost model is not built here** and cannot be — the inputs do not exist on disk. Task 5 measures the one economic quantity that *can* be measured today.

## File map

| File | Responsibility |
|---|---|
| `src/fundr/premium.py` | Lighter premium inversion + interval bounds; Hyperliquid premium passthrough; the shared band constants |
| `src/fundr/targets.py` | the adjacency-safe hour join, cumulative-level targets at any `H`, cross-sectional ranking, the artifact and clamp flags |
| `scripts/build_targets.py` | writes the `targets` dataset for both views and a list of `H` values |
| `scripts/measure_basis.py` | the cross-venue mark-basis drift measurement (Task 5) |
| `docs/phase4/targets.md` | what was built, what is in it, what will bite |
| `tests/test_premium.py`, `test_targets.py`, `test_build_targets.py`, `test_measure_basis.py` | offline tests, fixtures only |

## Dataset layout (all under `data/phase2/`)

```
data/phase2/
  targets/view=<per_venue|cross_venue>/horizon=<H>/part.parquet
  qa/basis_drift.md            # written by scripts/measure_basis.py
```

`targets` columns: `symbol`, `hour`, `venue` (per-venue view only), `spread` / `rate`, `y_cum_level` (the target), `y_rank` (cross-sectional rank of `y_cum_level` within the hour), `n_hours_used` (how many of the `H` forward hours actually existed), `joint_baseline` (bool), `venue_clamped` (bool), `hl_premium`, `lighter_premium_point`, `lighter_premium_lo`, `lighter_premium_hi`, `premium_is_point_identified` (bool), plus provenance.

## Execution order

Task 1 (adjacency-safe joins and the unit assertions) first — everything else is wrong without it, and the two silent-bug hazards both live here. Task 2 builds the premium columns, including the inversion that is this phase's genuinely new capability. Task 3 builds the targets. Task 4 flags the artifact and clamp hours, which is what makes every later score honest. Task 5 measures the basis, which decides whether the target built in Task 3 is the right one at all. Task 6 documents.

---

### Task 1: Adjacency-safe hour joins and the unit assertions

**Files:** create `src/fundr/targets.py`, `tests/test_targets.py`

**Interfaces produced:**
- `forward_window(df, *, h: int, on="hour", by="symbol") -> pl.DataFrame` — attaches the next `h` hours by an **explicit hour-difference join**, plus `n_hours_used`. Never `shift`.
- `assert_common_basis(df) -> None` — raises if any Lighter rate entered without passing through the signing/÷100 function. Phase 1 called this the most likely silent bug in Phase 4; make it impossible rather than documented.

**Why this task is first.** Two silent corruptions live here and neither would ever surface as an error. (a) Hyperliquid's grid has 1,789 eight-hour intervals and 213 single-hour holes; a `shift`-based lag pairs rates 2 or 8 hours apart on 2,002 occasions and the result just looks like noise. (b) Lighter's raw `rate` is an unsigned percent; used as-is it is 100× too large and sign-free, and a spread built on it would be dominated by a units bug.

- [x] **Step 1: write the failing tests.** Feed a fixture with a deliberate 2-hour hole and assert the row spanning it is dropped or flagged, never silently paired. Feed a fixture with an 8-hour gap and assert the same. Assert `assert_common_basis` raises on a frame carrying Lighter's raw `rate`. Assert `n_hours_used < h` at a series' tail.
- [x] **Step 2: run, expect failure.**
- [x] **Step 3: implement**, then re-run. 16 tests in `tests/test_targets.py`; suite 250 → 266, all green.
- [x] **Verify:** over the real Hyperliquid history the join reports exactly **2,002** non-adjacent pairs (1,789 eight-hour plus 213 single-hour holes). If it reports zero, the join is silently bridging them. **Measured: 2,002 — 1,789 at 8h and 213 at 2h across 141 of 234 markets.** `forward_window(h=1)` independently leaves those same 2,002 rows with `n_hours_used = 0` rather than pairing them, beside 234 legitimate series tails. Lighter: 0 non-adjacent pairs in 1,585,452 intervals.

### Task 2: The premium pathway, including Lighter's historical premium

**Files:** create `src/fundr/premium.py`, `tests/test_premium.py`

**Interfaces produced:**
- `hl_premium(...)` — passthrough of `hl_funding.premium`, present on 100% of rows.
- `lighter_premium(rates, params) -> pl.DataFrame` — for each hour, either a point estimate or an interval:
  - **off baseline** → `P = 8·rate + clamp_small` above baseline, `P = 8·rate − clamp_small` below, in percent, with the market's own `funding_clamp_small_pct` and `base_interest_rate_pct` (**never BTC's** — the parameters are per market: multiplier 100/50/1 on 137/96/2 markets, base rate 0.0100/0.0032/0.0000 on 119/89/27).
  - **at baseline** → `lo`/`hi` bounding the dead zone, `premium_is_point_identified = False`.

> ### ⚠️ Corrected 2026-09-22 — this is NOT new information, and F6 is already settled
>
> This section previously called the inversion "the phase's new capability" that "contradicts a
> Phase 1 finding", citing zero band violations on 337,905 rows and a 0.845 correlation with
> Hyperliquid's premium. **The formula below is correct and should be implemented as written.
> The framing was wrong on three counts:**
>
> 1. **It recovers no information.** `P = 8·rate ± clamp_small` is a *strictly monotone transform*
>    of `signed_rate_fraction`, a column already on disk. Spearman against Hyperliquid's premium
>    is **identical to four decimals (0.6233)** for the inverted premium and for the raw rate, and
>    the raw rate's Pearson is **higher** (0.8575 vs 0.8450). A monotone map cannot change rank
>    order. **`docs/phase1/handoff.md` §2 stands** — it claims no *independent* historical premium
>    observation exists, and re-deriving one from the rate through the venue's own formula is not
>    an independent observation.
> 2. **The "zero band violations" evidence is a tautology.** Branch selection is
>    `rate > baseline` vs `rate < baseline`; the band condition reduces to exactly that same
>    inequality. It cannot fail on any data. Do not cite it, and do not write a test that asserts
>    it as though it were evidence.
> 3. **F6 is DONE — do not re-run it, and do not wait on the recorder.** It was settled on
>    2026-09-22 via Lighter's *public* websocket (`market_stats`), which serves `premium`,
>    `current_funding_rate`, `base_interest_rate` and both clamps in one message. Scored forward
>    against the venue's own published rate over 234 markets: `clamp_small = 0.05` as the
>    **half-band** (what `lighter_formula.py` ships, and what this task's formula uses) gives
>    100/136 exact on crypto and **10/13 in the disputed band**; the rival 0.025 reading gives
>    89/136 and **0/13**. The shipped reading is right for all 100 matched pairs. See the
>    "F6 RUN AND SETTLED" box in `docs/phase4/decisions.md`.
>
> **What the inversion is still good for:** expressing the rate on the premium's scale, which may
> be the more natural regressor and makes the dead-zone structure explicit. Implement it, and
> document it as a **link function / reparameterisation**, never as recovered data.
>
> **One real finding, and it matters for Target A only.** The multiplier-50 equity/RWA markets fit
> the *halved* bound better (MAE 0.00022 against 0.00065), consistent with the effective clamp
> scaling with `funding_premium_multiplier`. No matched pair is multiplier-50, so **Target B is
> untouched** — but Target A's Lighter per-venue view spans **96** such markets. Do not apply the
> crypto-calibrated bound there; use each market's own scaled clamp and assert it.

- [ ] **Step 1: write the failing tests.** Round-trip: take Phase 1's confirmed formula, feed a known premium, settle it, invert it, and assert the premium comes back within the truncation width (~8e-4 percent). Assert a market with `base_interest_rate_pct = 0.0032` inverts with its own parameters, not BTC's. Assert a baseline hour returns an interval and `premium_is_point_identified = False`. Assert every point estimate falls **outside** the market's dead-zone band.
- [ ] **Step 2: run, expect failure.**
- [ ] **Step 3: implement**, then re-run.
- [ ] **Verify:** on the real data, **356,800** off-baseline hours invert and the correlation with Hyperliquid's premium is **0.845**. **Do not verify "zero band violations"** — that check is a tautology and has been withdrawn. **Do not re-run F6** — it is settled (see the box above). Instead assert the redundancy explicitly: Spearman of the inverted premium against Hyperliquid's premium equals that of the raw `signed_rate_fraction` to four decimals, so a reader cannot later mistake the column for new data.

### Task 3: The targets

**Files:** create `scripts/build_targets.py`, `tests/test_build_targets.py`

For each view and each `H` in `{1, 6, 24, 72}` (1 kept only as the design's original, for comparison):

- `y_cum_level = Σ_{h=1..H} S_{t+h}` for the cross-venue view; `Σ_{h=1..H} F_{t+h}` per venue for the per-venue view.
- `y_rank` — the cross-sectional rank of `y_cum_level` among the symbols live in hour `t`.
- `n_hours_used` — never impute a missing forward hour; carry the count and let Phase 6 decide.

**Why the level and why ranked** is in `decisions.md` D1. In short: the trade holds a position and receives the differential hourly, so the level is the P&L; `S_t` is known at decision time so the change carries identical information with a 40.27% atom bolted on; and Phase 9 must choose *which* of ~100 symbols to hold, which is a ranking problem. Ranking also makes the atom harmless — tied zeros are ties — and turns Lighter's 1e-6 lattice into a tie-break.

**The trap to guard against.** A level target scores beautifully for free: Hyperliquid's rate level has lag-1 autocorrelation 0.886, so "same as last hour" is worth roughly 0.79 R². Every score this phase hands downstream must be **against the no-change benchmark**, never a raw R². Emit the benchmark column alongside the target so a later phase cannot forget.

- [ ] **Step 1: write the failing tests.** Assert `y_cum_level` at `H=1` equals next hour's spread. Assert a series tail gives `n_hours_used < H` and is not imputed. Assert `y_rank` is dense within an hour and ties are handled explicitly. Assert the no-change benchmark column is present.
- [ ] **Step 2: run, expect failure.**
- [ ] **Step 3: implement**, then re-run.
- [ ] **Verify:** at `H=1` on the cross-venue view, the exact-zero share of `y_cum_level − S_t` reproduces the measured **40.27%**; the spread level's own lag-1 reproduces **0.699**. If either misses, the join or the basis is wrong.

### Task 4: Flag the arithmetic, flag the tail — what makes every later score honest

**Files:** modify `src/fundr/targets.py`, extend `tests/test_targets.py`

Two flags, both emitted, neither used to delete a row.

**`joint_baseline`** — both venues at their own baseline. **45.57% of hours (445,904).** The spread there takes exactly two values, `+5.0e-7` and `1.25e-5`, because Lighter truncates `0.00125%` down to `0.0012%`. That is a rounding rule, not a market. A model scored on those hours is being graded on arithmetic. **Every Phase 6–8 skill metric excludes them**; they may be reported separately as a zero-turnover known-sign carry of about **0.44%/year**, which is the floor any model must beat. This also un-masks the real sign: mean spread is −6.23e-8 overall but **−5.36e-7** excluding them.

**`venue_clamped`** — Lighter's outer clamp binding. **142 hours, 23 symbols, 42 distinct days.** Mean |spread| **28.5 bp/h, 149× the overall mean**, but only **2.2% of all gross spread**. Keep them (dropping them fits on a sample conditioned on the event never happening), exclude them from hyperparameter selection (a squared-error fit would bend the whole model to chase them), report them separately. They are a risk and capacity question, not a revenue one.

- [ ] **Step 1: write the failing tests.** Assert a fixture hour with both venues at baseline gets `joint_baseline = True` and a spread of exactly `5e-7` (or `1.25e-5` where Lighter's base rate is 0). Assert the flags never remove rows. Assert a per-market base rate of 0 is handled.
- [ ] **Step 2: run, expect failure.**
- [ ] **Step 3: implement**, then re-run.
- [ ] **Verify:** `joint_baseline` is true on **445,904** rows (45.57%) with exactly **two** distinct spread values among them; `venue_clamped` is true on **142** rows across **23** symbols.

### Task 5: Is this trade hedged? — the measurement that could change the target

**Files:** create `scripts/measure_basis.py`, `tests/test_measure_basis.py`

A delta-neutral cross-venue position's P&L is the funding differential **plus** the change in the price basis over the hold. Nobody had measured the second term until this plan was written, and it is the same size as the first.

Writes `data/phase2/qa/basis_drift.md`: per-symbol and pooled distribution of `(hl_mark − lighter_mark) / mid` in basis points, its persistence, and the distribution of its **change** over holds of 1, 6, 24, 72 and 168 hours, against the measured carry at the same horizons.

**Two implementation traps, both hit while measuring this.**

- **Compare like with like.** A first pass compared Hyperliquid's hour-*median* mark against Lighter's hour-*close* candle and produced a basis sd of 95 bp with essentially zero autocorrelation — a timing artifact, not a venue basis. Comparing hour-close against hour-close gives sd 57.5 bp pooled, per-symbol median 13.4 bp, and lag-1 **0.9342**. **If the autocorrelation comes out near zero, the alignment is wrong; stop.**
- **Label it correctly.** This is measured on *mark* prices, and the venues mark off different references — Hyperliquid oracle, Lighter index. Part of the basis is therefore convention rather than realizable P&L, and the realizable figure needs **traded** prices, which are not on disk. Say so in the report; do not let a mark-based number be quoted as a P&L number.

- [ ] **Step 1: write the failing tests** — assert hour-close against hour-close; assert the report carries the mark-convention caveat; assert `lighter_mark_candles`' later start (2025-08-25, 212 days after the trade candles) is handled rather than silently truncating the join.
- [ ] **Step 2: run, expect failure.**
- [ ] **Step 3: implement**, then run on the real data.
- [ ] **Verify:** the report reproduces sd(Δbasis) = **22.3 bp at 24 hours** and **17.7%** of 24-hour holds exceeding 18.91 bp, with lag-1 of the level at **0.9342**.
- [ ] **Step 4: record the consequence** in `docs/phase4/decisions.md` F2. Under ~6 bp sd at 24h on traded prices, the funding target above stands with `H = 24`. Near 22 bp, the primary target becomes funding **net of basis**, and Phase 9 sizes nothing until it exists.

### Task 6: Document what was built

**Files:** create `docs/phase4/targets.md`; modify `docs/phase1/handoff.md`

- [ ] **Step 1:** write `docs/phase4/targets.md` in the style of `docs/phase2/datasets.md`.
- [ ] **Step 2: correct `docs/phase1/handoff.md` §2**, which states that no historical Lighter premium exists on Lighter's API, on 0xArchive REST, or anywhere else. True for the intra-hour path; false for the hour's average, which inverts from the settled rate on 36.46% of hours and is interval-bounded on the rest. Keep the Target C conclusion unchanged — it rests on the intra-hour path and is unaffected.
- [ ] **Step 3:** state plainly in `targets.md` that Phase 4 produces labels and no model, that every level score must be read against the no-change benchmark, and that `joint_baseline` rows are excluded from skill metrics by construction.
- [ ] **Verify:** `uv run pytest -q` passes; every number in `targets.md` is reproducible by a named script.

## After Task 6

Phase 5 can start. The one thing that should not wait for it: **Task 5's traded-price follow-up**. The mark-based basis number is the largest open question in the project, and the traded-price version needs the live recorder extended to capture synchronized top-of-book on both venues at the settlement timestamp. That is two weeks of accrual, so it starts now or it is two weeks late — and the recorder currently has no alerting, S3 uploads off and one disk, so it must be hardened in the same change or it will simply not produce the measurement.
