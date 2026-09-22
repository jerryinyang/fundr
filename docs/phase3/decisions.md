# Phase 3 decisions — universe construction

Every decision Phase 3 makes, the reasoning behind it, and the ones deliberately deferred with
the trigger that reopens them. Written 2026-09-22.

**How these were reached.** Four of the open questions went to a quant-council session (seven
advisors with clashing priors, seven blind peer reviewers, a chairman); the full record is in
[`council-transcript-2026-09-22-universe.md`](council-transcript-2026-09-22-universe.md). The
rest were settled by measurement and are marked as such. **Every number below was measured on
this repo's own data before anything was decided** — the scripts are named against each figure
and live in the session scratchpad; the two that belong in the repo are
`scripts/cross_venue_overlap.py` (already committed) and a new
`scripts/universe_diagnostics.py` that Task 5 of the plan writes.

The implementation plan is
[`../superpowers/plans/2026-09-22-phase3-universe.md`](../superpowers/plans/2026-09-22-phase3-universe.md).

---

## The headline decision

**Phase 3 does not gate the panel on size. It flags it.**

The research design says the universe is "perpetual markets outside the top 10 by open
interest". Phase 3 will build **all 978,572 concurrent pair-hours** and attach point-in-time
size-rank columns to every row, rather than deleting rows.

> ### This is a change to the research design, and it is conditional
>
> Added 2026-09-22 after independent review. `docs/funding_research_design.md:451` specifies the
> exclusion as the *primary universe*, not as a suggestion. The argument below — that gating
> destroys the evidence needed to test the gate, and that a column is reversible where a deleted
> row is not — is sound, but "nobody measured whether the rule does anything" is a reason to
> **measure** it, not a licence to proceed as though it has been answered.
>
> **Binding rule until F1 is resolved: the GATED panel is the reporting default.** No headline
> result in Phases 5–8 may be reported on the un-gated panel until Task 5's
> forecastability-by-size-decile measurement has answered F1. Un-gated results may be computed
> and shown *alongside* gated ones as sensitivity, never in place of them.
>
> This matters because the five always-excluded names — BTC, ETH, SOL, XRP, HYPE — are precisely
> the most crowded and most arbitraged markets in the panel. Including them is not a neutral
> act, and a result that depends on their inclusion is a different claim from the one the design
> asked for.

Why:

- **Nobody has ever measured whether the rule does anything.** Four of seven advisors reached
  this independently, from four incompatible starting priors. Nothing in Phases 1 or 2 tested
  whether funding is more forecastable outside the top 10, and nothing says where "ten" came
  from.
- **Gating destroys the evidence needed to test the gate.** The design's own Phase 3 section
  asks for size to be *recorded so results can be segmented by it*, and H2 lists open interest
  as a *predictor*. A hard cut at rank 10 truncates the support of the predictor and deletes the
  variation the segmentation needs.
- **It costs nothing.** Every dataset is on disk, re-runnable for free. A column can be turned
  into a filter in one line; a deleted row cannot be recovered without a re-run.
- **It keeps 174,125 pair-hours** that the recorded design would have thrown away (see D3).

Everything below therefore sets the value of a **column**, not a filter.

---

## D1 — What pool the "top 10" is ranked against: **every market live on the venue that hour**

*Council decision. 6 of 7 advisors.*

Ranked per hour on Hyperliquid's `open_interest × mark_px`, against the whole Hyperliquid
universe live that hour — not against the matched cross-venue set.

**Measured.** The two readings disagree on **10,320 pair-hours (1.055%)**, and the disagreement
is strictly one-directional: the matched-set pool excludes a **superset** of what the venue-wide
pool excludes. There is no hour the matched reading keeps and the venue reading drops. Two
symbols (PENGU, VIRTUAL) are excluded under the matched pool while **never** entering the
venue's top 10 at all.

**Why venue-wide wins.** A market's size rank should not depend on which other markets you
happened to match. Under the matched pool, exclusion intensity is a function of panel width —
top 10 of 13 live pairs early is a 77% cut, top 10 of 96 late is a 10% cut — which confounds
this decision with the cross-section's sevenfold growth. It is also fragile: adding the five
alias pairs (D4) would re-rank the pool and silently move the universe again.

**Fail closed on missing size.** 17,156 pair-hours (1.8%) have no Hyperliquid open-interest row
to rank against, because the S3 archive has short days. The obvious implementation admits those
markets — and the short days **cluster**, so on exactly those weeks BTC, ETH and SOL sit inside
the panel the rule exists to keep them out of. Rule: carry the last known rank forward; if there
is none, mark the hour unranked and exclude it from any rank-conditioned statistic; emit the
count. This was caught by one advisor and endorsed by all seven reviewers.

---

## D2 — Rebalance frequency: **daily, on the previous day's median size**

*Council decision. 4 of 7 advisors.*

**Measured.** Same rule at three frequencies: hourly excludes 134,880 pair-hours, daily 136,010,
monthly 134,635. Hourly and daily give a different verdict on **9,271 pair-hours (0.95%)**;
hourly and monthly on 41,480 (4.24%). Under hourly ranking there are 1,457 in/out transitions,
and a spell inside the top 10 lasts a median of 6 hours with 52% of spells ≤6 hours.

**Why daily.** Nothing statistical turns on it — the three settings are within noise of each
other. Daily wins on operations: one cached ranking file per day that can be diffed, checked and
alerted on, against 14,702 separate hourly rankings computed against a feed with holes in it. A
day-level rank is also a contiguous block label rather than something that flickers inside a
series.

**Two arguments for daily that the council made and the measurements do not support**, recorded
so they are not repeated:

- *"Hourly ranking is look-ahead."* It is not. Ranking on size at `T` while the label is the
  change at `T+1` is conditioning on the information set, not leakage. One reviewer caught this
  and was right.
- *"Hourly fragments the series."* Boundary flicker accounts for at most ~4,600 pair-hours
  (761 spells, median 6 hours) — about 0.5% of the sample, not the ~6% argued.

---

## D3 — The Lighter size measure: **keep trailing quote volume, but delete the intersection**

*Council decision, and the one that overturned the advisor majority (4 of 7 had voted to keep
the recorded rule — they voted without the number in the second paragraph below).*

`docs/phase2/handoff.md` §2 recorded: two universes, Hyperliquid ranked by real open interest,
Lighter ranked by trailing quote volume as a size proxy, and Target B takes only the
**intersection**. Phase 3 keeps the Lighter volume rank **as a column** and **drops the
intersection as a gate**.

**Why — the measurement nobody had made.** Only the Hyperliquid leg of that rule had ever been
measured. Three reviewers independently asked for the Lighter leg. Measured:

| | Pair-hours excluded |
|---|---|
| Hyperliquid open-interest rank | 134,880 |
| **Lighter trailing-volume rank** | **110,539** |
| Both | 71,294 |
| **Lighter rank alone** | **39,245 (4.01%)** |
| The recorded intersection rule (union of the two) | **174,125** → 804,447 surviving |

The Lighter leg touches **80 of 100 symbols**, against 37 for the Hyperliquid leg. It is the
**more aggressive and more churn-prone half of the rule**, and it rests entirely on a proxy that
cannot be validated on the venue it is applied to. That is the opposite of what the handoff
implied when it called the proxy acceptable because "the rule excludes the top 10; it does not
need to rank precisely".

**How good is the proxy?** It can never be checked on Lighter, which has no open-interest
history anywhere. It *can* be checked on Hyperliquid, which has both measures. Over 108,362
coin-days / 608 days / 221 coins, ranking by the previous day's completed notional volume
against the true open-interest ranking:

- The two top-10 **sets** share a median **8 of 10** members (10th percentile 7, worst day 6).
- **1,052 boundary swaps** on 108,362 coin-days (**0.97%**) — *one* quantity, not two: with
  exactly ten names in each set, every wrong admission forces a wrong exclusion.
- Daily Spearman rank correlation **0.873** across all coins, **0.807** restricted to the true
  top 30 — i.e. **worst precisely at the boundary where the rule acts**.
- The headline "98.06% verdict agreement" is **not** the honest number and should not be quoted.
  With a median 177 live coins per day, a ranking that knows nothing scores **89.34%**. The
  proxy cuts errors 5.5× against chance — real, but "98%" reads as far better than it is.

**Two options rejected.**

- *Rank Lighter by Hyperliquid's open interest for the matched asset.* Two advisors proposed it;
  one reviewer killed it. Target B **is** the Hyperliquid-minus-Lighter spread; it exists
  because venue positioning differs. Ranking both legs off one venue's crowding assumes away the
  quantity being forecast.
- *Buy a vendor history.* There is nothing to buy. 0xArchive's Lighter open interest starts
  2025-08-25 at 84–97% completeness and was never validated; before that the data does not
  exist.

---

## D4 — The symbol alias table: **exactly five pairs, exact-prefix rule only**

*Decided by measurement, not by council. The question as posed — "how aggressive should the
alias table be?" — has no dial.*

`kBONK ↔ 1000BONK`, `kFLOKI ↔ 1000FLOKI`, `kPEPE ↔ 1000PEPE`, `kSHIB ↔ 1000SHIB`,
`NOT ↔ 1000NOT`. Worth **+60,951 concurrent pair-hours (+6.2%)**.

- After those five, **129 Hyperliquid-only and 130 Lighter-only** symbols remain, and they are
  genuine venue exclusives — Lighter lists equities, FX and commodities; Hyperliquid lists older
  alts. This **contradicts** `handoff.md` §6 and `datasets.md` item 1, which both say the
  unmatched set is "dominated by denomination prefixes". It is not.
- **Fuzzy matching is actively dangerous here.** At a 0.8 similarity threshold it returns six
  further candidates and **all six are false**: `HMSTR`/`MSTR` (Hamster Kombat vs
  MicroStrategy), `ME`/`GME` (Magic Eden vs GameStop), `RLB`/`RKLB` (Rollbit vs Rocket Lab),
  `AR`/`ARM`, `AR`/`ARC`, `COMP`/`KRCOMP`. Any one of them silently corrupts a spread.
- **Denomination scaling is mandatory and separate from the name map.** The `k`/`1000` markets
  differ by 1000× in price and size. Funding *rates* are unaffected, so Targets A and B are
  safe; any price-, size- or notional-derived feature is not. The alias table therefore carries
  a `size_multiplier` column, and open-interest notional for these markets must be computed
  after scaling.
- Re-run `scripts/cross_venue_overlap.py` after the alias table lands: every figure in
  `handoff.md` §3 is conditional on exact-symbol matching.

---

## D5 — Panel start date: **2025-01-17, the first concurrent hour**

*Council decision, and the second overturn — 4 of 7 advisors voted to start later.*

**Measured.** Surviving pair-hours after the hourly rule: 826,536 from 2025-01-17; 727,314
(88.0%) from 2025-07-29; 636,386 (77.0%) from 2025-10-08; 495,954 (60.0%) from 2026-01-01.

**Why keep everything.**

- The late-start case rests on the early window being cheap. It is not. On the metric its own
  proponent introduced — distinct hours, since 100 symbols in one hour are not 100 independent
  observations — the early window is **12.0% of rows but 31.8% of distinct hours** (4,624 of
  14,520). Starting later discards a third of the independent time to shed a ninth of the rows.
  Six of seven reviewers caught the advisor arguing against its own metric.
- The supporting claim that the early window leaves "roughly 3 usable pairs per hour" is
  **wrong**: measured **median 8, minimum 7** over the first six weeks.
- You can subset a long panel later in one line. You cannot un-truncate a short one.

**The obligation that comes with it.** Every row carries `pairs_live_this_hour`. **No
cross-sectional statistic may be reported without conditioning on it**, and the narrow era
(median 8–22 surviving pairs) must never drive one. If a cost model later shows the early window
is a different liquidity tier, down-weight it — a one-line change once the column exists.

---

## D6 — One table, two panel views

*Decided by measurement, raised by two reviewers. Not a judgement call once stated.*

Target A is **per-venue** and needs no cross-venue intersection. Building one matched-only
universe for both targets silently discards ~129 Hyperliquid-only and ~130 Lighter-only markets
and Hyperliquid's history back to 2023, for nothing.

Phase 3 therefore emits one universe table with two views:

- `view = "per_venue"` — every market on each venue, for Target A.
- `view = "cross_venue"` — the concurrent matched intersection, for Target B.

**One hazard that comes with the wider Target A view**, measured here and contradicting
`datasets.md`: Hyperliquid's settled funding is **not** a uniform hourly grid over its full
history. There are **1,789 eight-hour intervals** (funding was 8-hourly until 2023-06-08) and
**213 single-hour holes across 141 of 234 markets**. All of it is before the Lighter window, so
Target B is untouched — but a naive one-row shift on the full Hyperliquid history would silently
pair rates 2 or 8 hours apart on 2,002 occasions. Lighter's history has **zero** irregular
intervals in 1,585,452.

---

## D7 — Keep the giants as regressors even though they are flagged out as observations

*Single-source finding from peer review, adopted.*

BTC, ETH, SOL, XRP and HYPE are the five markets excluded under every setting of D1 and D2. They
are also the best available proxy for the common funding factor that makes 100 symbols much less
than 100 independent observations. Excluding them from the *panel* does not require dropping
them from the *model*: Phase 3 emits their point-in-time funding and size as market-wide
regressor columns joinable by hour. This costs nothing and answers most of the capacity
objection raised against the exclusion rule.

---

## Deferred, with the trigger that reopens each

| # | Deferred | Trigger to reopen |
|---|---|---|
| F1 | **Where the size threshold actually belongs — if anywhere.** Phase 3 ships rank columns, not a gate. The threshold is not set. | The forecastability-by-size-decile measurement (Task 5 of the plan). If there is a break, the threshold goes where the break is, and it will not be at 10. If it is flat, the gate was cosmetic and never gets built. |
| F2 | **Whether the Lighter volume proxy is any good on Lighter.** Unanswerable today. | Lighter's `open_interest` is in the live recorder's persisted payload (`src/fundr/recorder/feeds/lighter_state.py`) and Phase 1's live probe saw the field. **Nobody has looked at a live record** — treat as unverified until someone does. Once 30–60 days have accrued, rank Lighter both ways over the same window and measure. |
| F3 | **Whether the early narrow window is a different liquidity tier.** Kept in, flagged, not down-weighted. | A measured cost model. If quoted spreads in the narrow era differ materially from 2026, add the weight. |
| F4 | **The unbalanced panel.** ~9,786 hours per symbol against ~14,640 in the window — a third of the grid is absent because symbols list on different dates, tilting the sample toward long-listed majors. Single-reviewer finding, unmeasured. | Before any pooled cross-sectional result is reported. Measure per-symbol hour counts and funding statistics by days-since-listing. |
| F5 | **The 24-hour archive re-run** (`handoff.md` §5.1). Billed, ~$0.13, budget $1.20. Not blocking. | Whenever someone wants the ten in-window short days resolved. It changes a caveat (17,156 unrankable pair-hours, 1.8%), not a target. |
| F6 | **Buying 0xArchive Build.** | Only if F2 shows the volume proxy is badly wrong *and* the vendor's Lighter open interest from 2025-08-25 validates against the recorder over an overlapping window. Both conditions, in that order. |

---

## Two document defects found while deciding this

1. **`docs/phase2/datasets.md` line 199** says the `markets` dataset carries fees for both
   venues. The Hyperliquid partition has **no fee column at all**. Only Lighter carries
   `maker_fee`/`taker_fee`, and those read **0.0 on all 235 markets** — more likely an
   unpopulated field than a zero-fee venue. No cost model should rest on either until someone
   checks Lighter's published schedule against a real fill.
2. **`docs/phase2/datasets.md`** describes `hl_funding` as "a complete, gap-free hourly grid to
   each market's listing". True inside the Lighter window; false over the full history (see D6).

---

## Cross-cutting data hygiene — added 2026-09-22 after independent review

Two defects affect every measurement in this phase and neither council checked them.

**1. Stale and dead markets are still in the panel, and they poison price-derived statistics.**
Hyperliquid's `mark_px` and funding freeze on delisted markets rather than stopping. `AI`
contributes 460 concurrent pair-hours in which HL's rate *and* premium each take exactly one
value while Lighter's rate takes 317 — a dead market still emitting rows. The 5 HL-delisted
matched symbols are **19,498 pair-hours (1.99%)**. Their presence is correctly cited as evidence
the panel is survivorship-safe, and it should stay — but a frozen price must not enter a
statistic. **Rule: add an `is_stale` flag (mark or funding unchanged over a rolling window) and
exclude flagged rows from every price-derived statistic — basis, open-interest notional, and the
size ranking — while keeping them in the panel.** Deleting them would reintroduce the
survivorship bias the panel avoids.

**2. Lighter's per-market funding parameters were never read; both councils reasoned as if they
were venue-wide constants.** From `data/phase2/markets/venue=lighter`:
`base_interest_rate_pct` is 0.01 on 119 markets, 0.0032 on 89, 0.0 on 27;
`funding_premium_multiplier` is 100 on 137, 50 on 96, 1 on 2;
`funding_clamp_big_pct` is 4.0 on 227 but 16.0, 20.0, 0.02 (×5) and 0.0 elsewhere;
`funding_clamp_small_pct` is 0.05 on 234 and 0.0 on 1.

For **Target B this is nearly harmless** — all 100 matched markets carry multiplier 100, and 96
of 100 carry base rate 0.01 (the other 4, AI16Z/MKR/LAUNCHCOIN/YZY, carry 0.0). For **Target A it
is not**: the Lighter per-venue view spans 89 markets at base 0.0032 and 96 at multiplier 50.
**Rule: read each market's own parameters before making any Lighter-wide statement about
baselines, dead zones or clamps. A statement calibrated on the matched set is wrong outside it.**
