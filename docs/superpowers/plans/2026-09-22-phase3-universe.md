# Phase 3 Universe Construction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce the point-in-time research panel that Phases 4–11 are computed on — one universe table, two views (per-venue for Target A, cross-venue intersection for Target B), with every market's size rank attached as a **column rather than a filter**, plus the symbol alias table that Target B's cross-section depends on, and one measurement that tells the project whether the design's "outside the top 10 by open interest" rule is worth applying at all.

**Architecture:** A `fundr.universe` module owns the point-in-time size ranking and the panel spine; a `fundr.alias` module owns the five-row symbol map and its denomination scaling; one script builds the universe datasets and one measures the size rule's validity. Everything reads the Phase 2b datasets under `data/phase2/` and writes new partitioned parquet under the same root. No existing dataset is modified.

**Tech Stack:** Python 3.13, uv, **polars only — never pandas**, pytest.

**Spec:** none. This plan is written from `docs/funding_research_design.md` (Phase 3, "Universe Construction"), `docs/phase2/handoff.md` §2/§3/§6, `docs/phase2/datasets.md`, and — for every judgement call — [`../../phase3/decisions.md`](../../phase3/decisions.md), which records each decision, its reasoning, and the council session behind it.

**Every number in this plan was measured on this repo's own data on 2026-09-22**, before the plan was written. Where a figure here disagrees with `docs/phase2/handoff.md` or `docs/phase2/datasets.md`, the measurement is what appears, and the disagreement is called out.

## Global Constraints

- Python 3.13 via `uv`. Run everything as `uv run ...`.
- **polars only, never pandas.**
- **Never write to `data/phase1/`.** Phase 3 writes only under `data/phase2/` (gitignored).
- **Never commit data, `.env` or `auth/`.** Code, tests and documents only.
- Every dataset carries the Phase 2b provenance columns via `fundr.dataset.stamp` — `_source`, `_fetched_at_ms`, `_git_sha`, per row, never overwritten on merge.
- **Point-in-time or nothing.** Every rank is computed from size recorded at or before the hour it labels, never from a rank measured today. `scripts/cross_venue_overlap.py` shows the shape.
- **Size means notional.** Open interest is `open_interest × mark_px`; volume is quote (USD) volume. Ranking base units would rank 1 BTC against 1,000,000 PEPE.
- **Never `shift` across a funding series to build a lag.** Hyperliquid's grid is not uniform (see Measured facts). Join on an explicit hour difference.
- Free. Phase 3 spends nothing: no S3, no vendor, no paid tier.

## Measured facts (2026-09-22, this plan's own measurements)

- **The concurrent cross-venue intersection is 978,572 pair-hours** over 14,702 distinct hours and 613 days, 100 matched symbols, 2025-01-17 08:00Z → 2026-09-21 21:00Z. Confirms `handoff.md` §3 exactly.
- **The top-10 exclusion, ranked hourly on Hyperliquid open-interest notional:** venue-wide pool leaves 843,692 surviving pair-hours counting unrankable hours as surviving, or **826,536** dropping them; matched-set pool leaves 833,372. The two pools disagree on **10,320 pair-hours (1.055%)**, strictly one-directional — the matched pool excludes a superset. 63 pairs never in the top 10 venue-wide, 61 within the matched set, 5 always (BTC, ETH, SOL, XRP, HYPE).
- **17,156 pair-hours (1.8%) have no Hyperliquid open-interest row to rank against.** The naive rule admits them, and the archive's short days **cluster**, so the contamination is correlated rather than random.
- **Rebalance frequency barely matters.** Hourly excludes 134,880 pair-hours, daily (previous day's median size) 136,010, monthly (previous month's) 134,635. Hourly vs daily disagree on 9,271 pair-hours (0.95%); hourly vs monthly on 41,480 (4.24%). 1,457 in/out transitions under hourly; a spell inside the top 10 lasts a median of 6 hours, 52% of spells ≤6 hours, 761 spells total — so boundary flicker is at most ~4,600 pair-hours.
- **The Lighter leg of the recorded rule is the bigger leg, and had never been measured.** Ranking Lighter markets hourly on trailing 24-hour quote volume across the 217 markets with candle history: the Lighter rank excludes **110,539** pair-hours, **39,245 (4.01%) of them that the Hyperliquid rank keeps**, touching **80 of 100 symbols** against 37 for the Hyperliquid leg. The recorded "intersection of two per-venue universes" rule excludes **174,125**, leaving **804,447** — not the 843,692 `handoff.md` §2 implies.
- **The volume proxy, validated where truth exists.** On Hyperliquid, which has both measures, over 108,362 coin-days / 608 days / 221 coins: the top-10 sets share a median **8 of 10** members (p10 7, worst day 6); **1,052 boundary swaps (0.97%)** — one quantity, not two, since each set holds exactly ten names; daily Spearman rank correlation **0.873** overall and **0.807** restricted to the true top 30, i.e. worst at the boundary. The "98.06% verdict agreement" figure is **not honest** — with a median **177** live coins per day the do-nothing null is **89.34%**.
- **The alias table has exactly five members.** `kBONK↔1000BONK`, `kFLOKI↔1000FLOKI`, `kPEPE↔1000PEPE`, `kSHIB↔1000SHIB`, `NOT↔1000NOT`, worth **+60,951 concurrent pair-hours (+6.2%)**. **This contradicts `handoff.md` §6 and `datasets.md` item 1**, which both call the unmatched set "dominated by denomination prefixes": after these five, 129 Hyperliquid-only and 130 Lighter-only symbols remain and they are genuine venue exclusives. Fuzzy matching at a 0.8 similarity threshold returns six further candidates and **all six are false pairs** (`HMSTR`/`MSTR`, `ME`/`GME`, `RLB`/`RKLB`, `AR`/`ARM`, `AR`/`ARC`, `COMP`/`KRCOMP`).
- **Survivorship is clean.** The match list is built from funding-history partitions, not a live roster: 5 of the 100 matched symbols are Hyperliquid-delisted today (AI, AI16Z, LAUNCHCOIN, MKR, YZY) and 4 are Lighter-inactive. Dead markets are present.
- **Hyperliquid's funding grid is NOT uniform over its full history**, contradicting `datasets.md`'s "complete, gap-free hourly grid to each market's listing": **1,789 eight-hour intervals** (funding was 8-hourly until 2023-06-08) and **213 single-hour holes across 141 of 234 markets**, out of 4,676,131 intervals. All outside the Lighter window, so Target B is untouched — but Target A's per-venue view runs on this history. Lighter has **zero** irregular intervals in 1,585,452.
- **Panel start options.** Surviving pair-hours after the hourly rule: 826,536 from 2025-01-17; 727,314 (88.0%) from 2025-07-29; 636,386 (77.0%) from 2025-10-08; 495,954 (60.0%) from 2026-01-01. The early window is **12.0% of rows but 31.8% of distinct hours** (4,624 of 14,520): mean 21.5 surviving pairs/hour early against 73.5 late. Over the first six weeks the median surviving pairs per hour is **8**, minimum **7**.
- **`docs/phase2/datasets.md` line 199 is wrong**: it says the `markets` dataset carries fees for both venues. The Hyperliquid partition has **no fee column at all**. Lighter's `maker_fee`/`taker_fee` read **0.0 on all 235 markets** — likely unpopulated, not a zero-fee venue.

## Scope decisions recorded here, deliberately

1. **Phase 3 ships no size gate.** The design's "outside the top 10" becomes point-in-time rank columns on every row, not deleted rows. Reasoning in `decisions.md` (headline decision) — four of seven council advisors reached independently that the rule has never been validated, and gating destroys the evidence needed to validate it. Task 5 produces the measurement that decides where, or whether, a threshold belongs.
2. **The intersection-of-two-universes rule from `handoff.md` §2 is superseded.** The Lighter volume rank becomes a column; it no longer gates. It was excluding 39,245 pair-hours on its own across 80 of 100 symbols, on an unvalidatable proxy. See `decisions.md` D3.
3. **Lighter is not ranked by Hyperliquid's open interest.** Two advisors proposed it; rejected. Target B *is* the venue spread, so ranking both legs off one venue's crowding assumes away the quantity being forecast.
4. **No vendor purchase.** 0xArchive's Lighter open interest starts 2025-08-25 at 84–97% completeness and was never validated; before that the data does not exist to buy.
5. **No feature engineering.** Phase 3 builds the universe and the panel spine. Features are Phase 7.
6. **The archive re-run (`handoff.md` §5.1) is not done here.** It changes the 17,156-pair-hour caveat, not a target, and it is billed. Deferred with a trigger in `decisions.md` F5.

## File map

| File | Responsibility |
|---|---|
| `src/fundr/alias.py` | the five-row symbol alias table, its `size_multiplier`, and `canonical(symbol, venue) -> str` |
| `src/fundr/universe.py` | point-in-time size ranking (both venues, all three frequencies), the panel spine, the cross-section-width column, the market-wide giant regressors |
| `scripts/build_universe.py` | writes the `universe` and `panel` datasets; `--rebalance`, `--pool`, `--start` all default to the decided settings |
| `scripts/universe_diagnostics.py` | the size-rule validity measurement and the knob-grid dispersion report |
| `scripts/cross_venue_overlap.py` | **modify**: use `fundr.alias` so the overlap numbers include the five recovered pairs |
| `docs/phase3/universe.md` | what was built, what is in it, what will bite |
| `tests/test_alias.py`, `tests/test_universe.py`, `tests/test_build_universe.py`, `tests/test_universe_diagnostics.py` | offline tests, fixtures only, no network |

## Dataset layout (all under `data/phase2/`)

```
data/phase2/
  universe/venue=<hl|lighter>/part.parquet   # symbol x hour x size rank, point-in-time
  panel/view=<per_venue|cross_venue>/part.parquet
  qa/universe_diagnostics.md                 # written by scripts/universe_diagnostics.py
```

`universe` columns: `venue`, `symbol`, `hour`, `size_measure` (`oi_notional` | `trailing_quote_volume_24h`), `size_value`, `size_rank`, `rank_basis` (`hourly` | `daily` | `monthly`), `rank_is_carried_forward` (bool), **`rank_stale_hours` (int — how old the carried-forward rank is; 0 when fresh)**, `rank_unavailable` (bool), plus provenance.

`panel` columns: `symbol`, `hour`, `hl_rank`, `lighter_rank`, `in_hl_top10`, `in_lighter_top10`, `rank_unavailable`, `pairs_live_this_hour`, `size_multiplier`, `is_alias_pair`, plus provenance. Target values are **not** here — that is Phase 4.

## Execution order

Task 1 (alias) first: every later count depends on it and `handoff.md` §3's numbers are all conditional on exact-symbol matching. Task 2 (ranking) is the core and is where the fail-closed rule lives. Task 3 assembles the panel and its two views. Task 4 re-runs the overlap report so the documented numbers include the recovered pairs. Task 5 is the measurement that decides F1 and is the reason this phase is worth running. Task 6 documents.

---

### Task 1: The symbol alias table

**Files:** create `src/fundr/alias.py`, `tests/test_alias.py`

**Interfaces produced:**
- `ALIASES: dict[str, tuple[str, int]]` — Lighter symbol → (Hyperliquid symbol, `size_multiplier`). Exactly five entries; `size_multiplier` is 1000 for the four `k`/`1000` pairs and ~~1000~~ **1** for `NOT`/`1000NOT`.

  > **Corrected 2026-09-22 by measurement, during Task 1.** The multiplier is **per venue, not per pair**, and the `NOT` figure above was wrong. Hyperliquid `mark_px` against Lighter mark-candle `close`, 772 common hours over 33 days sampled 2026-05-12 → 2026-09-18: the four `k` pairs read **1.0007 / 1.0001 / 1.0006 / 1.0004** — both venues already quote 1000 tokens, so both legs carry 1000. `NOT`/`1000NOT` reads **0.000999** — Hyperliquid lists `NOT` (one token), not `kNOT`, so the legs differ by exactly 1000×. `size_multiplier("NOT", "hl") == 1` and `size_multiplier("1000NOT", "lighter") == 1000`. **Task 3's verify step ("the alias pairs appear with `size_multiplier = 1000`") is therefore wrong for the `NOT` row and must not be asserted as written.**
- `canonical(symbol: str, venue: str) -> str` — maps either venue's symbol to one canonical key.
- `size_multiplier(symbol: str, venue: str) -> int` — the factor by which that venue's price and size units differ from the canonical unit. **Funding rates are unaffected; prices, sizes and notionals are not.**
- `matched_pairs(hl_symbols, lighter_symbols) -> list[tuple[str, str]]` — exact matches plus the five aliases, and nothing else.

**Why the table is hard-coded and small, and why nothing fuzzy is allowed:** measurement found exactly five recoverable pairs and six fuzzy candidates at 0.8 similarity, all six false. `HMSTR`/`MSTR` pairs Hamster Kombat with MicroStrategy; `ME`/`GME` pairs Magic Eden with GameStop. A wrong alias silently corrupts a spread and nothing downstream would catch it.

- [x] **Step 1: write the failing tests.** Assert `matched_pairs` returns 105 canonical pairs from the two venues' real symbol lists; assert every one of the six fuzzy candidates is **absent**; assert `size_multiplier("1000PEPE", "lighter") == 1000` and `size_multiplier("kPEPE", "hl") == 1000` resolve to the same canonical unit; assert `size_multiplier` is 1 for an ordinary symbol.
- [x] **Step 2: run, expect failure** — `uv run pytest tests/test_alias.py` → module absent.
- [x] **Step 3: implement**, then re-run.
- [x] **Verify:** `uv run pytest tests/test_alias.py -q` passes, and `matched_pairs` over the real partition lists returns **105**. Done 2026-09-22: 9 tests pass, `matched_pairs` over `hl_funding`/`lighter_funding` returns **105** (100 exact + 5 alias).

### Task 2: Point-in-time size ranking, both venues, fail-closed

**Files:** create `src/fundr/universe.py`, `tests/test_universe.py`

**Interfaces produced:**
- `hl_size(start, end) -> pl.DataFrame` — `hour`, `symbol`, `oi_notional`. Median of the hour's minutes from `hl_asset_ctxs` (`open_interest × mark_px`); a single minute can be a stale print or a spike. Scan only the day partitions the window spans.
- `lighter_size(start, end) -> pl.DataFrame` — `hour`, `symbol`, `trailing_quote_volume_24h`. Rolling 24-hour sum of `lighter_candles.quote_volume`, **shifted one hour** so the value labelling hour `T` is completed before `T`.
- `rank(size: pl.DataFrame, *, basis: str, pool: str) -> pl.DataFrame` — adds `size_rank`, `rank_is_carried_forward`, `rank_unavailable`. `basis` ∈ `hourly|daily|monthly`; `daily` ranks on the **previous day's** median size, `monthly` on the previous month's.
- `giant_regressors(...) -> pl.DataFrame` — BTC/ETH/SOL/XRP/HYPE point-in-time funding and size, one row per hour, joinable as market-wide columns (`decisions.md` D7).

**The fail-closed rule, which is the point of this task.** When an hour has no size row, the obvious implementation leaves the rank null and every downstream `rank <= 10` test evaluates false — i.e. the market is **admitted**. 17,156 pair-hours are in this state and the archive's short days cluster, so on those weeks BTC, ETH and SOL sit inside the panel the rule exists to exclude. Instead: carry the last known rank forward and set `rank_is_carried_forward`; if there is no prior rank, set `rank_unavailable` and exclude the row from every rank-conditioned statistic. Emit the counts.

**This rule was written for the Hyperliquid leg only, and the Lighter leg is far worse.** Corrected 2026-09-22: `lighter_candles` was missing 297,185 bars in blocks of ~200 hours (a paging bug in `scripts/backfill_lighter_candles.py`, now fixed — see `datasets.md`), which left **242,460 pair-hours (24.78%) with no Lighter volume rank** against 17,156 (1.75%) on the HL side. Carrying a rank across a 200-hour hole is an **8-day-stale rank wearing a point-in-time label**, which is exactly the failure this phase exists to avoid.

**Done 2026-09-22 — the re-collection has already run.** `--refetch` re-paged all 235 markets (~2h45m, free) and both candle series now have zero internal gaps. **Lighter-unrankable pair-hours fell from 242,460 (24.78%) to 2,323 (0.24%)**, so the Lighter leg is now better covered than the Hyperliquid one (17,156, 1.75%). `rank_stale_hours` remains mandatory — a boolean cannot distinguish a one-hour carry from an eight-day one, and it is what would have caught this — and any hour with `rank_stale_hours > 24` is still `rank_unavailable`, not a rank.

- [ ] **Step 1: write the failing tests**, on fixtures. Assert `rank` is dense and point-in-time; assert a gap of three hours carries the rank forward and flags all three; assert a leading gap sets `rank_unavailable` and not a rank; assert `basis="daily"` uses the previous day, never the current one (feed a day whose size would change the rank and check it does not); assert notional, not base units, decides order.
- [ ] **Step 2: run, expect failure.**
- [ ] **Step 3: implement**, then re-run.
- [ ] **Verify:** against the real data, `rank(basis="hourly", pool="venue")` reproduces the measured exclusion counts — **134,880** excluded pair-hours, **17,156** unrankable before carry-forward, **5** always-excluded symbols. If it does not, the ranking is wrong; stop and diagnose before Task 3.
- [ ] **Verify (Lighter leg):** report the Lighter unrankable share and the distribution of `rank_stale_hours` explicitly. Measured after re-collection: **2,323 pair-hours (0.24%)**. If the implementation reproduces materially more than that, it is not reading the re-collected candles — stop and diagnose.

### Task 3: The panel spine and its two views

**Files:** create `scripts/build_universe.py`, `tests/test_build_universe.py`

Writes `universe/venue=*` and `panel/view=*`.

- `view="per_venue"` — every market on each venue, for Target A. Not restricted to matched symbols.
- `view="cross_venue"` — the concurrent matched intersection, for Target B.

Defaults, from `decisions.md`: `--pool venue` (D1), `--rebalance daily` (D2), `--start 2025-01-17` (D5). Every default is overridable so Task 5 can sweep them.

**Non-negotiables in this task:**

- **`pairs_live_this_hour` on every cross-venue row.** The cross-section grows sevenfold; no cross-sectional statistic may be reported without conditioning on it. Over the first six weeks the median surviving pairs per hour is 8.
- **Both rank columns are carried, neither gates.** `in_hl_top10` and `in_lighter_top10` are booleans on the row, not filters applied to it.
- **`size_multiplier` and `is_alias_pair` travel with every row**, so a later notional feature cannot silently mis-scale a `k`/`1000` market.
- **`pair_age_hours` on every row — added 2026-09-22.** Hours since the pair's first concurrent hour. Required because basis risk is concentrated almost entirely in young markets and **Phase 3's own size rule steers the universe toward them** (see the note below). Without this column the interaction cannot be measured and a later phase will silently trade the riskiest cohort.
- **No `shift`-based lags anywhere.** Hyperliquid's history has 1,789 eight-hour intervals and 213 single-hour holes; joins are on an explicit hour difference.

- [ ] **Step 1: write the failing tests.** Assert `per_venue` contains Hyperliquid-only symbols absent from `cross_venue`; assert `pairs_live_this_hour` is present and non-null on every cross-venue row; assert no row is dropped for being top-10; assert the alias pairs carry **each leg's own measured multiplier** — `kBONK`/`kFLOKI`/`kPEPE`/`kSHIB` are 1000 on *both* legs, but **`NOT` is 1 on the Hyperliquid leg against 1000 on Lighter's `1000NOT`** (measured ratio 0.000999; Hyperliquid lists `NOT`, not `kNOT`). ⚠️ **This step previously read "assert the alias pairs appear with `size_multiplier = 1000`", which is false for `NOT` — corrected 2026-09-22 after Task 1 measured it.** Asserting a flat 1000 here would either fail, or be "fixed" by forcing the wrong factor back in and silently scaling a price by 1000.
- [ ] **Step 2: run, expect failure.**
- [ ] **Step 3: implement**, then re-run.
- [ ] **Verify:** `cross_venue` has **1,039,523** rows (978,572 plus the alias pairs' 60,951 — confirm, and if the figure differs, the alias join is wrong); `per_venue` covers 234 Hyperliquid and 235 Lighter markets; `panel` has zero rows where `in_hl_top10` is null and `rank_unavailable` is false.

### Task 4: Re-run the overlap report through the alias table

**Files:** modify `scripts/cross_venue_overlap.py`

Every number in `handoff.md` §3 is conditional on exact-symbol matching and changes once the five aliases land. `handoff.md` §6 action 3 asks for exactly this.

- [x] **Step 1:** replace the `match_symbols` call with `fundr.alias.matched_pairs`.

  > **Not a drop-in swap.** `match_symbols` returns single symbols that key both venues; `matched_pairs` returns `(hl, lighter)` tuples that **differ** for the five aliases. `pair_hours` had to read each leg under its own venue's name and relabel both with the canonical (Hyperliquid) symbol. Nothing downstream changed: `per_pair`, `exclusion` and `hl_asset_ctxs` all key on the canonical symbol already, and open-interest notional is denomination-invariant (`open_interest x mark_px` scales reciprocally), so the `k` markets rank correctly without rescaling.
- [x] **Step 2:** run `uv run python scripts/cross_venue_overlap.py` (≈40 s) and regenerate `data/phase2/qa/cross_venue_overlap.md`.
- [x] **Verify:** the matched count reads **105**, not 100, and total concurrent pair-hours rise by **60,951**. Note in the report that the five recovered pairs are denomination-scaled. Done 2026-09-22: **105** matched, **1,039,523** pair-hours — exactly 978,572 + 60,951. `handoff.md` §3 updated with a dated note.

> ### ⚠️ The size rule and basis risk pull in opposite directions — measured 2026-09-22
>
> Basis drift is concentrated in **young** markets. **Figures corrected 2026-09-22 — use
> `data/phase2/qa/universe_diagnostics.md` §5, not the earlier numbers.**
>
> | Pair age | 24h basis drift sd | Share of holds exceeding the carry |
> |---|---|---|
> | Week 1 | **80–200 bp — a range, not a point** | 34–55% |
> | Weeks 2–4 | 56–131 bp | 21.9–34.4% |
> | Months 1–3 | **30.5 bp** | 23.1–24.4% |
> | 3 months+ | **17.7 bp** | 15.8–16.0% |
> | **BTC / ETH / SOL** | **9.9 bp** | **5.0%** |
>
> > **This number has been corrected three times. Stop quoting it as a point estimate.**
> >
> > Two separate defects, both now understood:
> >
> > 1. **Wrong age origin.** The first figures (124.4 / 60.5 / 27.8) measured a pair's age from
> >    its first hour in the *mark-joined* panel. Lighter's mark candles begin 2025-08-25, so
> >    every pair already trading that day was relabelled "age 0" — **49% of that week-1 bucket
> >    were pairs at least a month old**. Age must run from the pair's **first concurrent funding
> >    hour**, which is what `panel.pair_age_hours` carries. (The mark-convention explanation once
> >    offered for this gap is *not* the cause; both measurements already used the end-of-hour
> >    mark.)
> > 2. **Extreme sensitivity to the cleaning rule**, which is why the replacement figure (246 bp)
> >    was no better. Measured on the correct origin by the orchestrating session: week-1 sd is
> >    **198.9 bp unfiltered, 117.3 bp with frozen marks out, 79.9 bp with XPL and MON also out**
> >    — a 2.5× swing across defensible rules, on 5,000–6,400 pair-hours. Task 5's own run reports
> >    140.2 bp under its filter. All are correct for their rule; none is *the* number.
> >
> > **What is robust**: the mature bucket is **17.7 bp under every rule**, BTC/ETH/SOL are
> > **9.9 bp**, and the gradient — young markets carry several times the basis risk of mature
> > ones — holds regardless. Quote those. For week 1, quote the range and the rule, and never a
> > bare point estimate.
>
> The five names the top-10 rule always excludes are the **safest** in the panel on this measure,
> and the rule's effect is to tilt the universe toward small, new listings — precisely the cohort
> where the hedge is worst. Individual cases are extreme: XPL averaged **+1,747 bp** basis in its
> first month, MON +120 to +190 bp for two months, both settling to single digits afterwards.
>
> **Nobody has looked at this interaction, and it is not a noise term — it is a second universe
> rule competing with the first.** Excluding each market's first month costs ~8.6% of the panel
> and removes most of the tail. Task 5 must measure it (see its added step); Task 3 must carry
> `pair_age_hours` so that it can.
>
> Caveat that travels with every number here: these are **mark** prices off different venue
> references, so part of the level is convention rather than realizable P&L. F2 stays open.

### Task 5: Does the size rule do anything? — the measurement this phase exists for

**Files:** create `scripts/universe_diagnostics.py`, `tests/test_universe_diagnostics.py`

This is the council's "one thing to do first", and it decides deferred item F1. It uses only data already on disk.

Two outputs into `data/phase2/qa/universe_diagnostics.md`:

1. **Forecastability by size decile.** Split the panel into deciles of point-in-time `hl_rank`. Within each decile, report the simplest honest measures of how forecastable the next funding change and the next spread level are — the lag-1 autocorrelation of the level and of the change, the standard deviation, and the skill of a no-change benchmark — with standard errors **clustered on the hour**, not computed on 978,572 pair-hours as if they were independent. 100 symbols in one hour are one observation under a market-wide funding shock.
2. **The knob-grid dispersion report.** Run the same decile measurement across the grid: pool ∈ {venue, matched}, rebalance ∈ {hourly, daily, monthly}, start ∈ {2025-01-17, 2025-07-29}, Lighter leg ∈ {column, gate}. Report the dispersion of the headline number across the 24 cells. This is Phase 10's robustness pass pulled forward to where it costs hours instead of a re-run, and it converts four arguments into one table.

**Read the result honestly.** If there is no break by size, the exclusion was cosmetic and nothing downstream should filter on it. If there is a break, it will almost certainly not be at rank 10, and that measured threshold — not the design's round number — is what goes into the column's documentation. If the knob grid's dispersion is small relative to the deciles' spread, the four knobs were never the question.

3. **Size rule vs basis risk — added 2026-09-22.** Report, by point-in-time size decile, both the forecastability measure *and* the 24-hour basis drift sd with the share of holds exceeding the carry. The size rule and the age gradient (see the note above Task 5) point in opposite directions, so the honest question is not "does the size rule improve forecastability" but **"does it improve forecastability net of the basis risk it takes on"**. Also report the same split by `pair_age_hours` bucket, and the cost in panel rows of excluding each market's first month (~8.6%). If the excluded cohort is both more forecastable and far worse hedged, that is the finding, and it belongs in F1's answer rather than in a later phase's surprise.

- [ ] **Step 1: write the failing tests** — assert the clustering is on the hour; assert the grid enumerates 24 cells; assert the report names measured values rather than pass/fail verdicts; assert the basis-by-decile and age-bucket splits are present.
- [ ] **Step 2: run, expect failure.**
- [ ] **Step 3: implement**, then run on the real panel.
- [ ] **Verify:** the report exists, names the decile with the strongest and weakest forecastability, states whether a break exists and where, reports the grid dispersion, and reports forecastability **net of basis risk** by decile and by age bucket. Record the answer in `docs/phase3/decisions.md` under F1.

### Task 6: Document what was built

**Files:** create `docs/phase3/universe.md`; modify `docs/phase2/datasets.md`

- [ ] **Step 1:** write `docs/phase3/universe.md` in the style of `docs/phase2/datasets.md` — what each dataset is, its source, coverage, known gaps, how to load it, how to refresh it.
- [ ] **Step 2: fix the two defects this plan found in `datasets.md`.** Line 199 claims the `markets` dataset carries fees for both venues; the Hyperliquid partition has no fee column at all, and Lighter's fee fields read 0.0 on all 235 markets and are probably unpopulated. And `hl_funding` is described as a gap-free hourly grid to each market's listing; it is gap-free inside the Lighter window but carries 1,789 eight-hour intervals and 213 single-hour holes over its full history.
- [ ] **Step 3:** update `docs/phase2/handoff.md` §6 and `datasets.md` item 1, which both say the unmatched symbols are dominated by denomination prefixes. Five pairs are recoverable; the remaining 259 are venue exclusives.
- [ ] **Verify:** `uv run pytest -q` passes; every number in `universe.md` is reproducible by a named script.

## After Task 6

Phase 4 can start. It needs `panel/view=cross_venue` and `panel/view=per_venue` and nothing else from this phase. The size-rule answer from Task 5 belongs in `decisions.md` F1 before Phase 5 begins, because it decides whether any later phase filters on size at all.
