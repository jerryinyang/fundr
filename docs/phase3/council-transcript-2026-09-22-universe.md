# Council transcript — Phase 3 universe construction

Run 2026-09-22 with `the-quant-council` (9-advisor roster, 7 seated: the core five plus two
bench seats). One orchestrator, seven advisors answering independently, seven blind peer
reviewers, one chairman. This file is the full record: the framed question every advisor
received, all seven responses, all seven reviews, the orchestrator's independently recomputed
numbers, the convergence tally, and the verdict.

Decisions taken from this session are recorded in [`decisions.md`](decisions.md). The
implementation plan is
[`../superpowers/plans/2026-09-22-phase3-universe.md`](../superpowers/plans/2026-09-22-phase3-universe.md).

Every number in the framing was measured by the orchestrator on this repo's own data before the
council convened; the scripts are named in `decisions.md`.

---

## 1. The framed question (identical for all seven advisors)

# The question before the council

## 1. The decision

A funding-rate research project (`fundr`) is about to build its **Phase 3 research universe**.
The parent design says the universe is:

> "Perpetual markets outside the top 10 by open interest."

The design does not say how to apply that rule. Four construction choices are open, and the
council is asked **one question**:

> **Given that three of these four knobs move the usable sample by only ~1–4%, and the fourth
> moves it by 12–40%, where should Phase 3 spend its care — and, for each knob, which setting
> should it pick?**

The four knobs, each of which has **two or more concrete options that must all be
interrogated** — do not audit one option and let the other pass:

- **K1 — Pool.** Is "top 10" drawn from *every market live on the venue that hour*, or from
  *only the markets in the universe being built* (the matched cross-venue set)?
- **K2 — Rebalance frequency.** Re-rank and re-apply the exclusion *every hour*, *every day*
  (on the previous day's size), or *every month* (on the previous month's size)?
- **K3 — The Lighter size proxy.** Hyperliquid has real open-interest history. Lighter has
  **none, anywhere**. The project's recorded decision is to rank Lighter markets by **trailing
  quote volume** instead, and to build the cross-venue universe from the intersection of the
  two per-venue universes. Keep that, or do something else (rank Lighter by Hyperliquid's
  open interest for the matched asset; buy a vendor history; drop the Lighter-side ranking
  entirely and let the HL ranking define both)?
- **K4 — Panel start date.** The two-venue cross-section **grows sevenfold** through the
  window. Start the panel at the first concurrent hour and model the growth, start it later
  when the cross-section is wide, or weight observations to neutralise it?

## 2. The system

Two perpetual-futures venues, **Hyperliquid** and **Lighter**. Both settle funding **hourly,
on the hour**; a row stamped `T` reports the interval `[T−1h, T)` that just closed; the two
venues' hours pair one-to-one with **no offset** (re-tested on data, verdict ALIGNED).

The research forecasts (A) the next change in each venue's funding rate and (B) the next
change in the Hyperliquid-minus-Lighter funding spread, with the eventual aim of a
delta-neutral cross-venue trade. **The universe decides which markets enter the panel, so it
decides the sample every later result is computed on.** It does not, on its own, risk capital.

Nothing has been built for Phase 3. All the data below is already on disk and free to re-read.

## 3. Real numbers (all measured on the project's own data by the orchestrator today)

**The cross-venue intersection**
- 100 symbols match by exact symbol across the two venues; all 100 have at least one hour
  live on both venues at once.
- **978,572 concurrent pair-hours**, window **2025-01-17 08:00Z → 2026-09-21 21:00Z**
  (14,702 hours). Median 9,822 concurrent hours per pair.
- **The cross-section grows sevenfold.** Median pairs live per hour, by month: **13** (2025-01),
  35 (2025-05), 66 (2025-09), 85 (2026-01), **96** (2026-09). First hour with ≥25 pairs live:
  2025-03-05. With ≥50: 2025-07-29. With ≥75: 2025-10-08.
- Hyperliquid's funding history is gap-free inside this window; Lighter's is gap-free
  everywhere (0 non-hourly intervals in 1,585,452).

**K1 — Pool.** Ranking hour by hour on Hyperliquid's own `open_interest × mark_px`,
point-in-time:

| Pool | Pairs never in the top 10 | Always in it | Surviving pair-hours |
|---|---|---|---|
| Every HL market live that hour | 63 of 100 | 5 | **843,692** of 978,572 (86.2%) |
| The matched pairs only | 61 of 100 | 5 | **833,372** of 978,572 (85.2%) |

- The two readings disagree on **10,320 pair-hours (1.055%)**, and the disagreement is
  **strictly one-directional**: the matched-set pool excludes a superset of what the venue-wide
  pool excludes. There is no hour that the matched-set reading keeps and the venue-wide
  reading drops. Two symbols (PENGU, VIRTUAL) are excluded under the matched pool while never
  entering the venue's top 10 at all.
- The five always-excluded pairs are the same either way: BTC, ETH, SOL, XRP, HYPE.
- 17,156 pair-hours (1.8%) have no Hyperliquid open-interest row to rank against at all
  (archive short days); they are counted as surviving in both rows above.

**K2 — Rebalance frequency.** Same exclusion rule, three frequencies, each point-in-time:

| Frequency | Pair-hours excluded |
|---|---|
| Hourly | 134,880 |
| Daily, on the previous day's median size | 136,010 |
| Monthly, on the previous month's median size | 134,635 |

- Hourly and daily give a **different verdict on 9,271 pair-hours (0.95%)**.
- Hourly and monthly differ on **41,480 pair-hours (4.24%)**.
- Under hourly rebalancing there are **1,457 in/out transitions** across 978,572 pair-hours —
  about one flip per 672 pair-hours.
- **A "currently inside the top 10" spell lasts a median of 6 hours; 52% of spells last ≤6
  hours** (761 spells). Under hourly rebalancing, 27–28 of the 100 pairs cross the boundary in
  and out for under half their hours.

**K3 — The Lighter size proxy, tested where truth is available.** Lighter has no open-interest
history, so the proxy cannot be validated on Lighter. It *can* be validated on
**Hyperliquid**, which has both measures. Ranking Hyperliquid's markets by the **previous
day's completed notional volume** and comparing to the true open-interest ranking, over
**108,362 coin-days / 608 days / 221 coins**:

- The top-10 in/out verdict **agrees on 98.06% of coin-days**.
- The two top-10 **sets** overlap **82.7%** — median **8 of 10** members shared per day
  (10th percentile 7, worst day 6).
- The proxy **wrongly admits a true top-10 giant into the research universe on 1,052 coin-days
  (0.97%)**, and **wrongly excludes a genuine mid-cap on 1,052 coin-days (0.97%)**.
- Daily Spearman rank correlation between open interest and previous-day volume: **median
  0.873** across all coins; **0.807** restricted to the true top 30.
- Two known dents in the Lighter proxy itself: it is **blind on 17 markets** (all equities/FX,
  all Lighter-exclusive, **none of them a matched pair**), and the volume history **starts
  2025-01-25, eight days after** Lighter's funding history begins.

**K4 — Panel start date.** Surviving pair-hours after the hourly top-10 exclusion, by start
date:

| Start | Surviving pair-hours | Share of full | Median pairs live per hour |
|---|---|---|---|
| 2025-01-17 (first concurrent hour) | 826,536 | 100% | 70 |
| 2025-07-29 (first hour with ≥50 pairs) | 727,314 | 88.0% | 80 |
| 2025-10-08 (first hour with ≥75 pairs) | 636,386 | 77.0% | 80 |
| 2026-01-01 | 495,954 | 60.0% | 81 |

**Related, already settled by measurement — not part of the question, but context.** The
symbol-alias table everyone expected to be large is small: exactly **5** recoverable
denomination pairs exist (`kBONK/kFLOKI/kPEPE/kSHIB ↔ 1000*`, and `NOT ↔ 1000NOT`), worth
**+60,951 concurrent pair-hours (+6.2%)**. After those, 129 Hyperliquid-only and 130
Lighter-only symbols remain, and they are genuine venue exclusives (Lighter lists equities,
FX and commodities; Hyperliquid lists older alts). Fuzzy name matching at a 0.8 similarity
threshold returns 6 further candidates and **all 6 are false pairs** (`HMSTR`/`MSTR`,
`ME`/`GME`, `RLB`/`RKLB`, `AR`/`ARM`, `AR`/`ARC`, `COMP`/`KRCOMP`).

## 4. What is at stake

- **No capital is at risk at this step.** The cost of getting this wrong is a biased or
  under-powered research sample, discovered late, after Phases 5–8 have been run on it.
- **Irreversible:** nothing. Every dataset is on disk and re-runnable for free; the whole
  universe build is a few hours of code. A universe can be rebuilt and every downstream phase
  re-run.
- **What is expensive:** analyst time spent now, and the risk of re-running Phases 5–10 on a
  second universe definition later. Phase 10 of the design already reserves a robustness pass
  over "market universe" and "OI thresholds."

## 5. NOT KNOWN — material inputs nobody has

1. **Whether Lighter's own open-interest ranking would agree with its volume ranking.** It
   cannot be checked: Lighter has no open-interest history anywhere. The 98.06% figure above is
   Hyperliquid's answer used as a stand-in, and Hyperliquid's market mix (crypto only) differs
   from Lighter's (crypto + equities + FX + commodities).
2. **Whether funding predictability differs systematically between top-10 markets and the
   rest.** The design asserts it does ("funding can be relatively large and persistent"
   outside the top 10) but nothing in this project has measured it. If it does not, the whole
   exclusion rule is cosmetic.
3. **Whether the early, narrow part of the window is representative.** The first months have
   13–35 live pairs, all of them the largest and oldest assets on both venues.

## 6. Pinned shared assumptions

Use these values so the seats' figures are comparable. You may argue a pin is wrong — that is
more useful than quietly substituting your own.

- **Pinned: "size" means notional, not units.** Open interest is `open_interest × mark_px`;
  volume is quote (USD) volume. Ranking base units would rank 1 BTC against 1,000,000 PEPE.
- **Pinned: every ranking is point-in-time.** Ranks are computed from size recorded at or
  before that hour, never from a rank measured today.
- **Pinned: the Target B sample is the 978,572 concurrent pair-hours**, before any exclusion.
- **Pinned: re-running the universe build and every downstream phase is cheap** — hours of
  compute, no money, no data re-collection.

---

## 2. The seven advisor responses

Anonymized A-G for the review round. The mapping is in section 4.

# Anonymized advisor responses — universe question

**Response A:**

The sample size in the pin is not a sample size. 978,572 pair-hours sit on 14,702 hours. Funding across perps is one common factor — aggregate basis and leverage — and Target B (HL−Lighter spread) is worse, not better: a venue-level shock (a Lighter incentive change, an HL HLP rebalance, a listing wave) moves all 96 spreads in the same direction in the same hour. The legs that look like 96 independent observations are one observation in stress. Everything downstream inherits this, and nothing in Phases 5–8 will catch it, because a pooled t-stat on 978k rows passes every check ever run on it.

Calc: 2025-01-17→2025-07-29 is ~4,632 of 14,702 hours (31.5% of the independent time) but only 99,222 of 826,536 pair-hours (12%). The K4 table's "88.0% of sample survives" is off by a factor of ~2.6 in power terms — 99,222/4,632 ≈ 21 pairs/hour early vs ~72 after.

The second ruin path is endogenous membership. Under hourly rebalancing a market leaves the panel when its OI spikes; funding *is* OI-weighted pressure, so the panel is censored precisely at the hours the signal is largest. Median spell 6 hours, 27–28 pairs flipping, confirms membership is tracking the fast variable. The Lighter volume proxy is the same defect, undisguised: trailing *flow* deciding membership in a study forecasting flow-driven funding. The 98.06% agreement measures noise, not this direction.

Settings. K3: drop the Lighter-side ranking; every intersection member is matched, so HL open interest is available for all 100 — this removes the endogenous channel entirely and is the one knob worth the care. K2: monthly — stale but exogenous to the hour's shock. K1: matched pool, it's the strict superset. K4: start 2025-07-29; the 13-pair regime is a different system, held out, never trained on.

Single change that cuts ruin most: cluster on the hour, not the pair-hour. That alone shows K1–K3 are rounding errors and K4 costs a third of your power, not a tenth.

Verified: only the framing file. Not read: project data.

**Response B:**

Read only the framing file — no workspace files, so the shared input is unchanged.

Lead: the premise is broken. NOT-KNOWN #2 says nobody has measured whether funding is more predictable outside the top 10. So the council is being asked to tune four knobs on a filter whose *existence* is unvalidated. Three of the four knobs are arguments about 1–4% of the sample. The exclusion itself throws away 134,880 pair-hours. Keeping them: 978,572 / 843,692 = +16.0% sample, for the cost of one extra column.

The 10x version: don't exclude, flag. Build the panel on all 978,572 pair-hours and carry `in_top10` as a point-in-time feature. You then get, free and in Phase 3 rather than Phase 10: the answer to NOT-KNOWN #2, a universe rule that is a fitted coefficient instead of a design assertion, and — critically — the five mega-caps back. BTC/ETH/SOL/XRP/HYPE are the only markets where a delta-neutral cross-venue trade has real capacity. Excluding them is choosing, before any evidence, to research the part of the book you cannot size into. That is the self-limit.

Under flagging, three knobs stop being decisions. K1: venue-wide pool (economically real, strictly the more inclusive reading). K2: hourly (point-in-time truth; and the 1,457 transitions across 761 short spells are their own event study — a market crossing the size boundary is a candidate signal, not noise to be smoothed away by monthly rebalancing). K4: first concurrent hour, always. You can subset a long panel later; you cannot un-truncate a short one.

K3 is where the real asset is. Lighter has no open-interest history *anywhere* — no vendor can sell you what does not exist. That makes every hour you don't snapshot it permanently lost, and it makes a Lighter OI series something you would own outright. Keep the volume proxy for history; start a Lighter OI snapshotter this week. It is hours of work and the only irreversible clock in this whole decision.

What would have to be true: Lighter exposes OI or enough book state to reconstruct it.

**Response C:**

Lead with the broken premise: the council is asked to spend care on four knobs, and not one of them can be evaluated, because the cost side of this system does not exist on disk. I read `docs/phase2/datasets.md` (the others did not). There is no bid/ask, no book depth, no historical fee series for either venue. `hl_asset_ctxs` has `mark_px`/`mid_px` but no quote. Fees exist as a single snapshot dated 2026-09-21, and only the Lighter column list carries `maker_fee`/`taker_fee` — the HL column list has none, despite the prose at line 199 claiming the table includes fees for both. That inconsistency should be checked before anyone quotes it.

The one calculation. The eventual trade is delta-neutral cross-venue: 4 taker legs (open 2, close 2). *Assume ~3 bps/leg taker and ~5 bps half-spread on a non-top-10 alt* — both correctable, both unsupplied. That is ~8 bps/leg, ~32 bps round turn. Use the document's own holding proxy, the median 6-hour top-10 spell: break-even funding spread ≈ 32/6 ≈ 5.3 bps per hour. *Assuming the standard 0.01%/8h baseline*, nominal hourly funding is ~0.125 bps. You need roughly 40× baseline funding, sustained six hours, to clear costs. That is the number the universe decision is actually about.

Turn that on all four knobs.

K1. Both pools are the same decision. They agree on 98.9% of pair-hours and agree *completely* on excluding BTC, ETH, SOL, XRP, HYPE — the only five markets where a 32 bps round turn is plausibly beatable. You are choosing between two ways of deleting your cheap inventory. Take venue-wide (fewer exclusions, 843,692 vs 833,372, and pool membership doesn't shift when the matched set grows), but understand you picked between two versions of one problem.

K2. Monthly. Hourly differs from it on 4.24% of pair-hours and buys nothing: a rule whose membership flips 1,457 times with median 6-hour spells describes a filter no book could follow without paying entry and exit at exactly the moments size is borderline. Monthly is the only setting whose membership survives contact with a cost model. 4% of sample for a rule that is implementable is the cheapest trade in this whole document.

K3 — this is where care belongs, not K4. The 98.06% agreement is the wrong statistic: it is computed across 221 coins, almost all of them nowhere near the boundary, so it measures how easy it is to agree that PEPE is not top 10. The load-bearing numbers are the ones near the line — set overlap median 8 of 10, worst day 6, and Spearman 0.807 restricted to the true top 30. And the two error counts are equal (1,052 each) but not economically symmetric: wrongly admitting a true giant adds a low-funding, low-cost, tight-spread observation; wrongly excluding a genuine mid-cap removes a high-funding, wide-spread one. Both errors push the panel's measured edge-per-unit-cost in the flattering direction. K3 is the only knob that changes *which cost tier the sample lives in* rather than how many rows it has.

Keep trailing volume — a vendor history you cannot validate on Lighter either is not an upgrade, and ranking Lighter by Hyperliquid's OI silently asserts the two venues have the same size ordering, which is precisely the untested claim. But stop quoting 98.06% as the validation. Quote 8-of-10.

K4. I dispute the framing that this knob is the big one. Yes it moves row count 12–40%, but row count is not power when the extra rows are the wrong markets. The early window (13–35 live pairs, "all of them the largest and oldest assets on both venues") is not merely *narrow* — after the top-10 exclusion it is a handful of the next-largest names, i.e. the tightest-spread, lowest-cost, lowest-funding markets on both venues. Pooling those hours with 2026 hours mixes two different cost regimes into one panel and calls the average representative. Start 2025-07-29 (88% of sample, median 80 pairs live) and never pool across the regime without a liquidity-tier control.

What I'd need to do this properly: a quoted spread series at the hours you actually trade, HL and Lighter fee schedules as of the historical dates (not one 2026-09-21 snapshot), book depth at your intended clip, and Lighter's withdrawal/settlement mechanics for the short leg. Until then every phase downstream is measuring gross.

Verified vs believed. Verified from disk: no spread/depth/fee-history data; fee snapshot is single-dated; HL column list omits fees while the prose claims them. Believed, from my prior: the funding spread outside the top 10 is large *because* nobody can arb it at cost, and the exclusion rule as written selects for exactly that. NOT-KNOWN #2 in the framing is the right question with the wrong denominator — measure predictability net of a cost proxy, and I expect the ranking of markets to invert.

**Response D:**

The 98.06% is mostly base rate, and the page doesn't say so. Only 10 of 221 coins are ever "in," so a ranking that knows *nothing* still agrees on the in/out verdict most of the time. Null: 1 − 2·(10/221)·(211/221) = 91.36% agreement, 8.64% disagreement (labeled assumption: 221 live coins/day; fewer live → lower null). Observed disagreement is 1.94%. So the proxy cuts errors ~4.5× versus a coin flip — real, but "98%" reads as 98% and it is 91% of nothing plus 7 points of signal. The 8-of-10 set overlap is the honest number (null = 0.45 members); quote that, not the 98. Keep K3 as recorded, and stop citing the inflated figure.

K2 is contemporaneous-variable selection — hourly is peeking. Funding at `T` covers `[T−1h, T)`. Ranking on `open_interest × mark_px` *at* `T` conditions on a quantity that settles at the end of the very interval being predicted, and the same intra-hour price/OI spike that pushes a market across the top-10 line is mechanically tied to that hour's premium. The leak sits inside each observation, so it survives any train/test split. It is concentrated exactly where hourly rebalancing does its work: 1,457 transitions, median spell 6 hours, 52% of spells ≤6 hours, 27–28 pairs straddling the line. Daily, on the previous day's median size, is the lag test — and hourly vs daily differ on 0.95% of pair-hours. You remove the leak for free. Pick daily. Monthly buys nothing further and adds 4.24% staleness.

K1: venue-wide pool. Not for the 1.055%. The matched pool makes exclusion intensity a function of panel width — top 10 of 13 early (77% cut) versus top 10 of 96 late (10%) — confounding K1 with K4's sevenfold growth. Venue-wide is invariant to how you build the universe.

K4: both options are worse than advertised. Start-early buys pair-hours where, after removing the top 10 from a 13-wide cross-section, roughly 3 usable pairs per hour remain — all the largest, oldest assets, i.e. near-zero cross-sectional variation and no panel to speak of. Start-late (60% of sample) is one regime. Take 2025-07-29 (88%, median 80 pairs) and drop the pretense that the first six months contribute width.

And the rule itself is unmeasured (NOT KNOWN #2). Nobody has checked that funding is more predictable outside the top 10. 978,572 pair-hours is not 978,572 events — hourly funding is heavily autocorrelated, 100 symbols share one crypto beta, and 14,702 hours is one ~20-month regime. If the true difference between top-10 and the rest is zero, everything on this page looks exactly as it does. Spend the care on measuring that one thing first — it is hours of compute — because if it's null, all four knobs are decorating a cosmetic filter.

*Verified: the arithmetic above, and that the framing's 1,052+1,052 of 108,362 = 1.94% is internally consistent. Believed, not verified: the intra-hour leak mechanism — I read no workspace files.*

**Response E:**

The two tables disagree, and the gap is exactly the data hole. K1 says venue-wide hourly leaves 843,692 surviving pair-hours. K4's "100%" row, same rule, same start, says 826,536. 843,692 − 826,536 = 17,156 — precisely the pair-hours with no HL open-interest row. K1 counts them as surviving; K4 drops them. Two knobs are being priced on different samples. Fix that before anyone ranks anything.

That bucket is also a fail-open default, and that is the real defect. When the size feed is missing, the rule admits the market. HL archive short days cluster (`datasets.md`: a 2024 cluster, recent 2026 short days, 2026-07-08 missing entirely), so the contamination is correlated, not random: on those hours BTC/ETH/SOL sit inside the panel the rule exists to keep them out of. Fail closed — carry forward the last known rank, drop the hour if there isn't one, and emit a count. 1.8% that all lands in the same weeks is not 1.8%.

K3 does not need deciding — it needs a calendar entry. I read `src/fundr/recorder/feeds/lighter_state.py`: the recorder writes Lighter's entire `market_stats` payload verbatim (`_write` → `records.envelope(..., stats, ...)`), and `tests/recorder/test_lighter_state.py:17` shows `open_interest` inside that payload. The Frankfurt box has been live since 2026-09-21. If live messages populate that field, Lighter OI is accruing right now, hourly, at zero marginal cost, and NOT-KNOWN #1 becomes measurable in 30–60 days. *Verified:* the code path and the fixture. *Not verified:* that live frames carry a real value, or whether it is base units (needs `lighter_mark_candles` to reach notional per the pinned definition).

Verdicts. K1: venue-wide — the matched pool's membership moves as the cross-section grows sevenfold, so the same market's rank depends on what else you happened to match. K2: daily — 0.95% from hourly, one job at a fixed time, one cached ranking file you can diff and alert on; hourly is 14,702 rankings against a feed with a hole, and a median 6-hour spell is a churn rule no live book could follow. K4: don't decide; keep everything, flag the narrow era with a dummy, let Phase 10 cut it.

Monday: SSH the recorder, `zgrep` one hour of `lighter_state` records for a populated `open_interest`. First thing that will go wrong: the field is present and always `"0"` or absent outside a subscription you aren't holding — and you find out in November instead of tomorrow.

*Files read beyond the framing:* `docs/phase2/handoff.md`, `docs/phase2/datasets.md`, `docs/phase2/recorder_runbook.md`, `src/fundr/recorder/feeds/lighter_state.py`, `tests/recorder/test_lighter_state.py`.

**Response F:**

The 1,052 is one number printed twice. K3 says the proxy "wrongly admits a giant on 1,052 coin-days" *and* "wrongly excludes a mid-cap on 1,052 coin-days," as if those were two independent error rates. If the top-10 is always exactly ten names, every wrong admit forces a wrong exclude. That is one error, double-counted, and it reads as two confirmations.

And 98.06% is the answer to a different question. The same panel says the two top-10 *sets* share a median 8 of 10 members — two wrong names a day, every day. Both statements are about the same ranking, so the 98% must be diluted by the 221-coin field: it measures "can volume tell BTC from an obscure alt," which nobody doubts. The exclusion rule only ever acts at the boundary — rank 8 versus rank 12 — and that is exactly where the panel says the proxy is worst (Spearman drops from 0.873 to 0.807 inside the top 30). Do not let 98.06% into the write-up unbadged; it will get quoted as "the proxy is 98% right."

Nobody has shown the rule does anything. NOT KNOWN #2 concedes it: no one has measured whether funding is more predictable outside the top 10. Four knobs are being tuned on a filter of unverified value, and every knob is scored in *sample moved* (1–4%, 12–40%), which is not the thing that matters. A knob that moves 40% of rows and 0% of signal is worth less than the reverse. Also unanswered: why "ten"? Not five, not twenty. No one states where it came from.

Two things a stranger expects and doesn't get. First, capacity — you are deliberately deleting BTC, ETH, SOL, XRP and HYPE, the five places size actually fits, and nothing says the remainder can hold the eventual trade. Second, the other side: a delta-neutral funding spread between two venues is described, never explained. Who is paying it, and why hasn't it been taken?

One structural trap. The matched pool makes membership depend on your symbol table — PENGU and VIRTUAL get excluded while never being top-10 anywhere. Add the five alias pairs the "already settled" section adds, and the pool changes, the ranks change, the universe changes. K1 is not separable from the matching decision.

Picks. K1 venue-wide (a market's fate shouldn't hinge on who else you matched). K2 daily (hourly membership flipping on a 6-hour median spell is not something you could ever trade). K3 keep volume, but restate the error as "2 of 10 boundary names a day," and check the top-10 exclusion against a start month where only a median 13 pairs are live — deleting ten of thirteen leaves almost nothing, which makes K4's "100% of rows" column misleading. K4 start late.

Verified: nothing — I ran no numbers. All of the above is read off the page.

**Response G:**

I read one file the others may not have: `/Users/jerryinyang/Trading/fundr/docs/funding_research_design.md`. It contradicts the question.

The design (§Phase 3) already says: "Record market-level characteristics so results can later be segmented by: Open interest, Trading volume, Funding magnitude, Liquidity." H1 says the relationship "may differ systematically with market size and liquidity." And H2 lists open interest level and change in open interest as *predictors*.

So the project intends to (a) use size as a regressor, (b) measure size-dependence as a finding, and (c) hard-gate the sample on size at an unmeasured threshold of 10. (c) destroys the evidence for (b) and truncates the support of (a). The framing's own §5.2 admits nobody has measured whether the gate does anything.

Restated problem: *not* "how do we build the top-10 exclusion" but "at what size does funding-change forecastability break, and is there a break at all?" That is a Phase 3 *measurement*, not a construction choice. "Top 10" is a round number someone typed.

One calculation. Five pairs (BTC/ETH/SOL/XRP/HYPE) are excluded always, under every K1/K2 setting. Assume they are live near the full 14,702 hours (labeled assumption — they are the oldest listings; verify): 5 × 14,702 ≈ 73,500 hours. Total excluded hourly = 134,880. So ~61,000 excluded hours (~45%) are not giants — they are holes punched into the interiors of mid-cap series by 1,457 boundary flips with a median 6-hour spell. For a target that is a *first difference* of funding, deleting interior hours conditional on size corrupts the lag structure of exactly the markets you want. K1 and K2 are arguing over how to do the damage.

Recommendation: build no exclusion. Ship the full 978,572 pair-hours with a point-in-time `hl_oi_notional_rank` column attached to every row. Then:

- K1 — venue-wide pool. The matched pool is strictly more exclusion (superset, one-directional) with no justification; it drops PENGU and VIRTUAL, which were never top-10 on the venue at all. That is an artifact of the intersection, not a size fact.
- K2 — daily. Not because 0.95% matters, but because a daily rank is a contiguous-block label, and hourly is the only setting that fragments series.
- K3 — the recorded decision is the weakest of the four options. Drop the Lighter-side ranking; rank the matched asset by Hyperliquid's true open interest for both venues. You keep 100 matched symbols of the *same underlying asset*; crowdedness is an asset property more than a venue property, and this replaces an unvalidatable proxy (Spearman 0.807 at the top 30 — worst precisely at the boundary where it is used) with measured truth, at zero cost. Buying a vendor history is spending money to refine a gate I am arguing should not exist.
- K4 — start at the first concurrent hour, 2025-01-17. Truncating to 2026-01 burns 40% of sample to buy homogeneity you should be *modelling*, and the late-listing markets are the mid-caps the thesis is about. But do not let the early window drive any cross-sectional statistic: at 13 live pairs minus 5 giants, the early cross-section is ~8 markets, which is not a cross-section. Report every result conditioned on cross-section width.

Where care goes: none of the four knobs. It goes into one column being correct and point-in-time, and then a single Phase-3 deliverable: forecastability by size decile. If there is no break, the exclusion was cosmetic and you saved Phases 5–10 from a filtered sample. If there is a break, it will not be at 10.

Verified: the design-doc quotes and the framing's arithmetic. Believed, not verified: that the five permanent giants are live near the full window (drives the ~61,000 figure), and that funding-change persistence is continuous in size rather than cliff-edged at the top 10.

---

## 3. The seven peer reviews

Each reviewer saw the framed question and all seven responses, not each other's reviews
and not the mapping.

# The seven peer reviews — universe council

(Reviewers saw only the framed question and the anonymized responses A–G. They did not see
each other's reviews and did not know which advisor wrote which letter.)

**Review 1:**

1. Strongest: E. The only response that found a real defect in the framing's own numbers: K1's 843,692 minus K4's 826,536 is exactly 17,156 — the no-OI bucket — so two knobs are being scored on different samples. It then shows that bucket is a *fail-open* default and correlated (archive short days cluster), putting BTC/ETH/SOL inside the panel on precisely the hours the rule exists to exclude them. Plus a disk-verified path (`lighter_state.py` writes `market_stats` including `open_interest`) that could retire NOT-KNOWN #1 at zero cost.

2. Biggest blind spot: A. Its clustering point is right, then it contradicts itself. A argues hours are the independent unit and that 2025-01→07-29 is 31.5% of independent time — then recommends discarding that window. By A's own metric that is the most expensive cut on the table, not the cheapest.

3. Wrong number: C's break-even. "32bps ÷ 6h ≈ 5.3bps/hour ≈ 40× baseline funding." The 6 hours is the median top-10 *membership spell of the markets being removed* — not a holding period for the markets being traded. Nothing supplies a holding period; the 3/5bps costs are self-labeled assumptions. The headline has no valid input. Compute instead: half-life/persistence of the HL−Lighter funding spread on non-top-10 pairs. Also G: the "~61,000 interior holes from 6-hour flips" is unsupported — 761 spells ≤6h bounds flip-fragmentation at ≤4,566 pair-hours; that mass is long tenures of next-largest names.

4. All seven missed: the recorded K3 rule builds the universe as the *intersection of two per-venue exclusions* — a pair drops if top-10 on Lighter's volume rank even when far outside HL's. Every K1/K2/K4 figure in the framing is HL-only, so the Lighter leg's marginal exclusion is entirely unmeasured, and A's and G's "drop the Lighter ranking" changes the rule, not just the proxy. Compute: pair-hours excluded by the Lighter rank alone, and the intersection-vs-HL-only delta.

**Review 2:**

1. Strongest: E. The only response that found a checkable defect: K1's 843,692 and K4's 826,536 differ by exactly 17,156 — the no-OI rows — so two knobs are being priced on different samples. It also names the fail-open default (missing size ⇒ admit, on clustered archive-short days, putting BTC/ETH back inside the panel the rule exists to exclude) and found code evidence that Lighter OI may be accruing now, turning NOT-KNOWN #1 from unanswerable into a 30-day wait. Others reason from priors; E reasons from disk.

2. Biggest blind spot: A. Its one real contribution is that the unit of independence is the hour — and it computes that 2025-01-17→07-29 is 31.5% of hours but 12% of rows. It then recommends discarding that window. Under A's own model those are the cheapest independent observations in the panel; A never reconciles cutting a third of its effective sample to shed 12% of rows.

3. Wrong: C's ~5.3 bps/hour break-even. The 6-hour denominator is the median *top-10 membership spell* — a property of the size boundary, unrelated to trade holding period. C also benchmarks the textbook 0.01%/8h rate, but the trade earns the HL−Lighter *spread*. Needs computing: distribution and persistence (sign-run length, AR half-life) of the hourly HL−Lighter spread per pair, by size decile. Minor: D's null assumes 221 coins/day; 108,362/608 = 178. Null ≈ 89.4%, not 91.36%.

4. All seven missed: Target A is per-venue and needs no matched pair. Every answer was built on the 978,572-row intersection, discarding ~129 HL-only and ~130 Lighter-only markets — over half each venue's cross-section. Only Target B requires matching.

**Review 3:**

1. Strongest: E. The only one that found a defect in the shared inputs rather than in reasoning about them: 843,692 − 826,536 = 17,156 — exactly the no-OI-row bucket, so K1 and K4 are scored on different samples and cross-knob comparison is invalid. Adds the fail-open default (missing size feed *admits* the market; archive gaps cluster, so the giants sit inside the panel in correlated blocks) and a code-verified path that makes NOT-KNOWN #1 measurable. G is second and has the better strategic call.

2. Biggest blind spot: A. Its clustering argument and arithmetic hold (193d×24=4,632; 99,222/4,632≈21 pairs/hr). But it concludes clustering "shows K1–K3 are rounding errors." Clustering fixes standard errors; it says nothing about sample *composition*, which is what K3 moves — C is right there. A also kills "endogenous membership" by ranking everything on HL OI, yet HL OI at T is exactly as contemporaneous as Lighter volume. Its fix leaves its own diagnosis intact.

3. Wrong number: C's break-even. "median 6-hour spell → 32/6 ≈ 5.3 bps/hr → 40× baseline funding." The 6-hour spell is how long a market sits *inside* the top-10 boundary — a property of excluded markets, not a holding period for carry in the included ones. From C's own inputs (4 legs × 8 bps = 32 bps), a 7-day hold gives 32/168 = 0.19 bp/hr ≈ 1.5× the 0.125 bp/hr baseline. Same premise, opposite verdict — and C's whole ranking of the knobs rests on it. G misuses the same 6-hour figure to describe 61,000 excluded hours; with only ~32 pairs ever crossing, most of that mass is long blocks, not flickers.

4. All seven missed: is the 100-symbol match list itself point-in-time? Everyone audits rank timing; nobody asks whether *membership* was built from today's symbol lists. If so, anything delisted from either venue mid-window is silently absent and the panel is survivorship-selected — a bias larger than all four knobs, and precisely what the pinned point-in-time rule exists to prevent. Compute, don't estimate: first/last-seen date per symbol per venue, and the count of symbols concurrent at some hour but missing from the 100.

**Review 4:**

1. Strongest: E. Only response that reconciled the framing's own tables: 843,692 − 826,536 = 17,156 = exactly the no-OI-row bucket, so K1 and K4 are scored on different samples. Only one to see that missing size data fails *open* (giants admitted) and that HL archive gaps cluster, so 1.8% is not 1.8%. Only one to check code for a live Lighter OI path — the single action that could retire NOT-KNOWN #1. G is close behind on reasoning (flag, don't gate; ship forecastability-by-size-decile).

2. Biggest blind spot: A. A argues the real unit is the hour (14,702), not the pair-hour — then picks K4 = start 2025-07-29, discarding 4,632 of those hours. Under its own unit of account that is the most expensive option available, and A never reconciles it.

3. Wrong: C's break-even. C divides its ~32bps round turn by the *median 6-hour top-10 membership spell* to get 5.3bps/hr and "~40× baseline." Membership churn is not holding period; the two are unrelated quantities. The denominator should be the persistence of the HL−Lighter funding spread, which nobody has measured. Compute: per-pair autocorrelation/half-life of the HL−Lighter spread over the 978,572 pair-hours already on disk. Until then C's headline number has no denominator. Secondary: D's null uses 221 coins, but the framing's own 108,362/608 = 178 coins per live day. Null agreement is then 2·(10/178)(168/178) → 89.4%, not 91.36%; error reduction 5.5×, not 4.5×.

4. All seven missed: excluding the giants from the *panel* does not require dropping them from the *model*. BTC/ETH/SOL/XRP/HYPE are the best available proxy for the common funding factor A identifies; keep them as point-in-time regressors while excluding them as observations. That costs nothing and dissolves most of the capacity objection (B, F) and part of the dependence objection (A). Also unquantified by everyone: the framing's exclusion arithmetic is HL-side only, but the recorded K3 design intersects two per-venue universes — a pair dies if it is top-10 on *either* venue. Compute: incremental pair-hours dropped by the Lighter volume-ranked exclusion alone, and the overlap with the HL-side drops.

**Review 5:**

1. Strongest: E. Only one with a verified defect nobody else saw: K1's 843,692 and K4's 826,536 differ by exactly 17,156 — the missing-OI bucket. Two knobs are being priced on different samples. E also read the recorder code and found `open_interest` in Lighter's persisted payload, turning NOT-KNOWN #1 from unanswerable into a 30-day measurement. New, verified, actionable.

2. Biggest blind spot: G (A shares it). Both say rank Lighter by Hyperliquid's OI. Target B *is* the HL−Lighter spread; it exists because venue positioning differs. Ranking both legs off one venue's crowding assumes away the quantity being forecast and builds the universe out of the predictor. G calls this "measured truth at zero cost" — it is HL truth imported as a Lighter assumption.

3. Wrong number: C's "40× baseline funding." It divides a 32 bps round turn by the 6-hour median top-10 *spell*. Spell length measures how long a market sits inside the excluded set — filter churn. It is not a holding period. From C's own inputs, break-even = 32 bps ÷ H hours; H is a design choice nobody has set, so the 5.3 bps/hr and the 40× both vanish. Needs computing: an intended holding period, then re-derive. Also wrong: D's "hourly is peeking." Selecting on OI at T while the label is the change at T+1 is conditioning on the information set, not look-ahead. It is endogenous selection, and daily lagging does not remove it — "you remove the leak for free" is false.

4. All seven missed: the rule ranks on Hyperliquid size only, but the panel serves both targets. For Target A on Lighter, markets are dropped for being big on the *other* venue. Compute: apply the rule per leg, count pair-hours where the legs disagree.

**Review 6:**

1. Strongest: E. The only response that found a defect in the shared inputs: 843,692 − 826,536 = 17,156 exactly, so K1 and K4 are scored on different samples. It names the real bug — missing HL open-interest rows fail *open*, admitting BTC/ETH/SOL on clustered archive-short days — and turns NOT-KNOWN #1 from unknowable into a 30–60 day measurement by finding `open_interest` in the live Lighter payload, refuting B's "no vendor can sell what does not exist."

2. Biggest blind spot: A. Its endogenous-membership argument is the best idea in the set, then its K3 pick destroys it: ranking Lighter by Hyperliquid's open interest leaves the same fast, contemporaneous variable deciding membership. A also treats clustering — a standard-error fix — as if it answered a sample-construction question. It answers none of the four.

3. Wrong number: D's null. D assumes 221 live coins/day. The framing's own inputs give 108,362 coin-days / 608 days = 178.2. Null disagreement = 2·(10/178.2)·(168.2/178.2) = 10.6%, not 8.64%, so the proxy beats chance ~5.5×, not 4.5×. D flagged the direction and still used the wrong figure. Separately, C's break-even treats the median 6-hour *top-10 membership spell* as a holding period. It is not one, and it is measured on markets the panel excludes, so "40× baseline funding" is unsupported.

4. All seven missed: the pin says re-running is free, so these knobs are measurable, not arguable. Nobody proposed running the full knob grid through one downstream estimate and reporting the dispersion — Phase 10's robustness pass pulled forward. Needs computing: that grid, and the effective cluster count (hours, not pair-hours).

**Review 7:**

1. Strongest: E. Only seat to find a real inconsistency in the framing: 843,692 − 826,536 = 17,156, exactly the no-OI bucket — K1 and K4 score knobs on different samples. It then names the defect correctly (the rule *fails open*, admitting BTC/ETH/SOL on archive-short days that cluster, so 1.8% is not spread evenly), and it read code showing Lighter `market_stats` already carries `open_interest`, converting NOT-KNOWN #1 from unknowable into a dated measurement. Checkable, and it changes Monday.

2. Biggest blind spot: A. A's entire case is that the *hour*, not the pair-hour, is the independent unit — then it picks start 2025-07-29, which by A's own arithmetic burns 31.5% of independent time to save 12% of rows. Under A's own metric, late-start is the most expensive choice on the table. A never reconciles this.

3. Wrong number: D's null. D assumes 221 live coins/day. The framing's own inputs give 108,362/608 = 178.2 coins/day. Null disagreement = 2·(10/178.2)·(168.2/178.2) = 10.59%, null agreement 89.4%. So the proxy beats chance by 5.5×, not 4.5×. D's conclusion survives; its multiplier doesn't. Worse: C's headline "40× baseline funding" uses the median 6-hour *top-10 spell* as the holding period. Spell length measures filter churn, not signal persistence. That input is unrelated to the quantity; the whole break-even is unsupported.

4. All seven missed the target split. The pin says 978,572 is the Target B sample. Target A (each venue's own funding) needs no intersection at all — 129 HL-only + 130 Lighter-only symbols and HL history back to 2023 are being discarded for free, and K4's sevenfold-growth problem doesn't exist there. All seven decided one universe for two targets. Second, smaller: the matched 100 come from currently-active symbol tables, and Lighter has no delisting timestamp. The panel is survivorship-selected. Needs computing: (a) matched-pair count and pair-hours when Lighter's 21 `inactive` markets are included; (b) per-hour cluster-robust effective N versus the pooled 978,572.

---

## 4. The orchestrator's verified numbers, tally and ledgers

# Orchestrator's verified material — universe council

## WHICH LETTER WAS WHICH ADVISOR

- A = The Ruin Theorist
- B = The Opportunist
- C = The Frictionist
- D = The Null Skeptic
- E = The Operator
- F = The Outsider
- G = The First Principles Thinker

Bench seats drafted: First Principles Thinker and Opportunist. Rationale: the question is
"where should care be spent", the five core seats all lean cautious and would each answer
"everywhere", so the Opportunist was seated specifically as the one voice that would say "this
is a 1% question, stop", and First Principles because the exclusion rule's own validity was
the thing most likely to be the real question.

## VERIFIED NUMBERS TABLE (recomputed by the orchestrator; these supersede any figure in the responses or reviews)

| # | Claim | Who | Recomputed | Verdict |
|---|---|---|---|---|
| 1 | "843,692 and 826,536 differ by exactly 17,156, the no-open-interest bucket — K1 and K4 are scored on different samples" | E (Operator), then all 7 reviewers | 843,692 − 826,536 = 17,156, and the unrankable pair-hours are 17,156 | **holds — and this is an error in the ORCHESTRATOR'S OWN FRAMING.** K1's table counted unrankable hours as surviving; K4's table dropped them. Corrected: on a single consistent treatment (drop unrankable), venue-wide hourly leaves **826,536**; counting them as surviving leaves 843,692. Use 826,536 as the base for all K2/K4 comparisons. |
| 2 | "The missing-size bucket fails OPEN: no rank ⇒ market admitted, and archive short days cluster" | E | `hl_asset_ctxs` short days cluster (102 of 131 in 2024-07→2024-11; ten more in-window days plus 2026-07-08 absent entirely) | **holds** |
| 3 | "1,052 wrong-admits and 1,052 wrong-excludes are two independent error rates" (framing's wording) | orchestrator's framing | On all 608 days both top-10 sets contain exactly 10 members, so every wrong admit forces a wrong exclude | **wrong — one quantity printed twice.** F caught it. The honest statement is 1,052 boundary swaps on 108,362 coin-days (0.97%), i.e. **a median 2 of the 10 top-10 names differ every day**. |
| 4 | Null agreement rate for the top-10 verdict, "1 − 2·(10/221)·(211/221) = 91.36%" | D (Null Skeptic) | Live coins per day is **median 177** (min 161, max 191), not 221. Null agreement = **89.34%**; observed **98.06%** | **wrong — corrected to 89.34% null.** D's direction is right and its conclusion survives: error reduction is **5.5×**, not 4.5×. Four reviewers caught this independently. |
| 5 | "32 bp round turn ÷ median 6-hour top-10 spell = 5.3 bp/hour break-even = 40× baseline funding" | C (Frictionist) | The arithmetic is right; the **denominator is the wrong quantity** — 6 hours is how long a market sits inside the excluded set, not how long a position is held. All 7 reviewers rejected it | **wrong premise — see #6 for the correct denominator.** C's cost inputs (3 bp/leg taker, 5 bp half-spread) are self-labelled assumptions and remain unmeasured. |
| 6 | The correct persistence denominator — **computed by the orchestrator because reviewers asked for it and had no compute budget** | 5 reviewers requested it | On non-top-10 pairs: lag-1 autocorrelation of the spread **0.7042**, half-life **1.98 h** pooled, **2.23 h** per-pair median (88 pairs with >2,000 hours). Sign-run length of the spread: median **2 h**, 75th pct 6 h, 95th pct 29 h | **new, verified** |
| 7 | Expected gross carry from the naive rule "trade the sign of the current spread when \|S\| > 1 sd" (non-top-10 pairs, in-sample, gross of all costs, no forecasting) | orchestrator, prompted by the reviewers' break-even dispute | 1 h: **1.42 bp**; 6 h: **6.21 bp**; 24 h: **16.51 bp**; 72 h: **31.33 bp**; 168 h: **49.82 bp** | **new, verified — but label it: in-sample, gross, and from a rule with no model in it.** |
| 8 | "~61,000 excluded hours are interior holes punched by 6-hour boundary flips" | G (First Principles) | Of 134,880 hourly-rule exclusions, the five permanent giants are **71,654 (53.1%)** and everything else is **63,226 (46.9%)** — so G's ~61,000 magnitude is close. But 761 in-top-10 spells with median 6 h bound the *flicker* mass at roughly 4,600 pair-hours; the remaining ~58,000 is long tenures of next-largest names | **half right — magnitude holds, mechanism wrong.** Two reviewers caught the mechanism. |
| 9 | "Start 2025-07-29 costs 12% of rows but 31.5% of independent hours" | A (Ruin Theorist) | Early window = **99,222 of 826,536 pair-hours (12.0%)** and **4,624 of 14,520 distinct hours (31.8%)**. Mean surviving pairs/hour 21.5 early vs 73.5 late | **holds.** Six reviewers then noted A recommends discarding exactly the window its own metric makes most expensive. |
| 10 | "The first months leave ~3 usable pairs per hour after the exclusion" | D | To 2025-03-01: **median 8** surviving pairs per hour, minimum 7 | **wrong — corrected to 8.** D's qualitative point (the early cross-section is not a cross-section) survives. |
| 11 | **The Lighter leg of the exclusion, which the orchestrator's framing never measured** | requested independently by 3 reviewers | Ranking Lighter markets per hour on trailing 24-hour quote volume (217 markets with candle history): HL open-interest rank excludes 134,880 pair-hours; **the Lighter volume rank excludes 110,539**; both exclude 71,294; **the Lighter rank alone excludes a further 39,245 (4.01% of all pair-hours)**. The recorded "intersection of two per-venue universes" rule therefore excludes **174,125**, leaving **804,447** — not the 843,692 the framing implied. **80 of 100 symbols are excluded by the Lighter rank at some point, against 37 by the HL rank.** | **new, verified — and it materially changes K3.** The Lighter leg is the more aggressive and more churn-prone of the two, and it rests entirely on the unvalidated proxy. |
| 12 | "The 100-symbol match list may be survivorship-selected from today's active rosters" | 2 reviewers | The match list is built from funding-history partitions (every market ever collected), not from a live roster. **5 of the 100 matched symbols are HL-delisted today** (AI, AI16Z, LAUNCHCOIN, MKR, YZY) and **4 are Lighter-inactive**. Dead markets are present | **wrong — survivorship is clean here.** Recorded as a null result. |
| 13 | Effective sample size | A, D | 978,572 pair-hours rest on **14,702 distinct hours / 613 distinct days / 100 symbols** | **holds** |
| 14 | Lighter `open_interest` is in the recorder's persisted payload | E | `src/fundr/recorder/feeds/lighter_state.py` writes the whole `market_stats` payload; Phase 1's live probe (P9) lists `open_interest` among the fields that endpoint actually emits | **holds for the code path and for P9's live observation.** `unauditable` for whether the Frankfurt box's current frames carry a non-zero value — nobody has looked at a live record. |
| 15 | No spread, depth or historical fee data exists; the HL market snapshot claims fees but has none | C | Confirmed: `markets/venue=hl` columns are `coin, is_delisted, sz_decimals, max_leverage, margin_table_id, margin_mode, only_isolated` — **no fee column**, while `datasets.md` line 199 prose says the table includes fees for both venues | **holds — a real documentation defect.** |
| 16 | The alias table is small | orchestrator | Exactly **5** true denomination pairs (kBONK/kFLOKI/kPEPE/kSHIB ↔ 1000*, NOT ↔ 1000NOT), worth **+60,951** concurrent pair-hours (+6.2%). Fuzzy matching at 0.8 returns 6 further candidates, **all 6 false** (HMSTR/MSTR, ME/GME, RLB/RKLB, AR/ARM, AR/ARC, COMP/KRCOMP) | **holds** |

## VERIFIED CONVERGENCE TALLY (counted from the actual text, not from memory)

**Reviewer-level (7 reviewers):**
- Strongest response: **E — 7 of 7. Unanimous.** No other response received a single vote.
- Biggest blind spot: **A — 6 of 7**; G — 1 of 7 (and that reviewer said A shares the same flaw). So A is named by all 7 in some form.
- Wrong number, Q3: **C's 6-hour-spell break-even — 7 of 7** (primary in 5, secondary in 2). **D's 221-coin null — 4 of 7.** G's 61,000 mechanism — 2 of 7. D's "hourly rebalancing is look-ahead peeking" disputed — 1 of 7.
- Q4 blind spots raised by **two or more** reviewers:
  - **The Lighter leg of the exclusion was never measured; the recorded rule intersects two per-venue universes — 3 of 7.**
  - **Target A is per-venue and needs no intersection at all; one universe was decided for two different targets, discarding ~129 HL-only and ~130 Lighter-only markets and HL history back to 2023 — 2 of 7.**
  - **Survivorship of the 100-symbol match list — 2 of 7.**

**Advisor-level (7 advisors, counted from their own text):**
- K1 venue-wide pool: **6 of 7** (B, C, D, E, F, G). A alone picks the matched pool.
- K2: daily **4 of 7** (D, E, F, G); monthly 2 (A, C); hourly 1 (B).
- K3: keep the recorded volume proxy **4 of 7** (B, C, D, F); replace it with Hyperliquid open interest for both legs 2 (A, G); defer pending the recorder 1 (E).
- K4: start later **4 of 7** (A, C, D, F); keep the full window 3 (B, E, G).
- **"The exclusion rule itself has never been validated and that is the real question" — 4 of 7 independently (B, D, F, G).**
- "Don't gate on size, carry it as a point-in-time column and measure forecastability by size" — 2 of 7 explicitly (B, G), with E arriving at the same operational answer by a different route.
- "No cost model exists and that dominates everything" — 1 of 7 (C).

## BLIND SPOTS THAT MUST BE ADDRESSED (raised independently by two or more reviewers)

1. The Lighter leg of the exclusion was unmeasured — now measured, see verified number 11. It is larger and churnier than the HL leg.
2. One universe was decided for two different targets. Target A is per-venue and does not need the cross-venue intersection at all.
3. Survivorship of the match list — checked, clean, see verified number 12.

## SINGLE-SOURCE FINDINGS THAT WOULD CHANGE THE RECOMMENDATION'S DIRECTION (carry these, flagged as single-source)

- One reviewer: **excluding the giants from the panel does not require dropping them from the model.** Keep BTC/ETH/SOL/XRP/HYPE as point-in-time regressors (they are the best available proxy for the common funding factor) while excluding them as observations. This costs nothing and dissolves most of the capacity objection.
- One reviewer: since the pinned assumption says re-running is free, **these knobs are measurable, not arguable** — run the full knob grid through one downstream estimate and report the dispersion. Phase 10's robustness pass, pulled forward into Phase 3.

---

## 5. The verdict

> Written by the chairman agent, which received the framed question, all seven de-anonymized
> responses, all seven reviews, the letter→advisor mapping, the verified numbers table and the
> verified convergence tally. The orchestrator checked it afterwards against the table, the
> tally and the ledgers before recording it; no discrepancy was found.

## Council Verdict: The fundr Phase 3 Research Universe

### Where the Council Agrees

- **K1 venue-wide pool — 6 of 7 advisors** (A alone dissents). The matched pool is the *harsher* reading: it excludes a strict superset, killing PENGU and VIRTUAL, which never enter the venue's top 10 at all. It also makes a market's fate depend on who else you matched — and adding the 5 verified alias pairs (+60,951 pair-hours, +6.2%) would change the pool, the ranks and the universe again.
- **K2 daily — 4 of 7.** Hourly and daily disagree on 0.95% of pair-hours; nothing turns on it.
- **The gate itself has never been validated — 4 of 7 independently** (both bench seats plus two core seats). This is the council's strongest signal, and it is not a caution artifact: the Opportunist was seated specifically to say "stop", and it landed here alongside three others.
- **Reviewer unanimity, read carefully.** All 7 named E strongest for one arithmetic catch: K1's 843,692 minus K4's 826,536 is exactly 17,156 — the pair-hours with no open-interest row, counted as surviving in one table and dropped in the other. Verified, and it is an error in the framing. **Use 826,536.** That bucket also *fails open*: missing size admits the market, on archive-short days that cluster, putting BTC/ETH/SOL inside the panel in correlated blocks.
- All 7 also rejected C's break-even. They were right on the denominator and wrong to drop the argument — see below.

### Where the Council Clashes

- **K4 start date, 4 of 7 say start late. Overturn them.** A's own metric, verified: the early window is 12.0% of rows but **31.8% of distinct hours** (4,624 of 14,520). Six reviewers caught A recommending the most expensive cut on its own accounting. D's supporting claim that the early window leaves "~3 usable pairs per hour" is **wrong — verified median 8, minimum 7**.
- **D's "hourly rebalancing is look-ahead peeking" is wrong** (1 of 7 reviewers caught it). Ranking on size at T when the label is the change at T+1 is conditioning on the information set, not leakage. Daily wins on operations, not on bias.
- **G's "~61,000 interior holes from 6-hour flips": magnitude right, mechanism wrong.** The five permanent giants are 71,654 exclusions (53.1%); flicker bounds at roughly 4,600 pair-hours. Fragmentation is not an argument.
- **C's cost case was dismissed on a bad denominator and is the best argument in the pack.** Correct denominator, verified: spread half-life **1.98h** pooled, sign-run median **2h**.

### Blind Spots the Council Caught

1. **The Lighter leg was never measured — and it is the bigger leg.** Verified: the Lighter volume rank excludes 110,539 pair-hours, **39,245 of them (4.01%) that the HL rank keeps**, and it touches **80 of 100 symbols against 37**. The recorded intersection rule excludes 174,125, leaving **804,447** — not the 843,692 the framing implied. The more aggressive leg rests entirely on the proxy nobody can validate.
2. **One universe was built for two targets.** Target A is per-venue and needs no intersection: ~129 HL-only and ~130 Lighter-only symbols and HL history back to 2023 are being discarded for nothing.
3. **Survivorship: checked, clean.** 5 of 100 matched symbols are HL-delisted today, 4 Lighter-inactive. Dead markets are present. Null result — do not spend more on it.
4. *Single-source:* excluding the giants from the **panel** does not require dropping them from the **model** — keep BTC/ETH/SOL/XRP/HYPE as point-in-time regressors. Costs nothing, dissolves the capacity objection.
5. *Single-source:* re-running is free, so these knobs are **measurable, not arguable** — run the knob grid through one downstream estimate and report the dispersion.

Also verified: the HL market table has **no fee column** while `datasets.md` line 199 claims it does — a real documentation defect, and the cost side of this system does not exist on disk.

### The Number That Decides This

**Round-turn cost, assumed at ~32 bp** (3 bp/leg taker + 5 bp half-spread — both self-labelled assumptions, never measured). Against it, verified gross carry from the naive sign rule on non-top-10 pairs: **6.21 bp at 6 hours, 31.33 bp at 72 hours** — in-sample, gross, no model in it — while the spread's sign flips every **2 hours** at the median.

At 32 bp you must hold ~72 hours to break even on a signal that does not survive 3. **The flip: if measured round-turn cost comes in at or below ~6 bp, a 6-hour hold clears and this universe question deserves the care it is getting. Above that, the panel measures edge nobody can collect and all four knobs are decoration.**

### The Recommendation

- **K1 — venue-wide pool**, ranked on HL `open_interest × mark_px`, point-in-time. **Fail closed** on the 17,156 unrankable hours: carry forward the last rank, drop the hour if there is none, emit a count.
- **K2 — daily, on the previous day's median size.** One cached ranking file you can diff and alert on. (Note: monthly is the *most lenient* of the three by count, 134,635 vs daily's 136,010 — the three are within noise.)
- **K3 — keep trailing quote volume for Lighter, but delete the intersection.** The Lighter leg gates 4.01% of pair-hours and 80 of 100 symbols on an unvalidatable proxy; demote it to a column. Do **not** rank Lighter by HL open interest — that assumes away the venue difference Target B exists to forecast. Do not buy a vendor: the history does not exist to sell.
- **K4 — start 2025-01-17, the first concurrent hour**, with a cross-section-width column on every row. Never compute a cross-sectional statistic on the narrow era; you can subset a long panel, you cannot un-truncate a short one.
- **Above all: do not gate. Flag.** Ship all 978,572 pair-hours with point-in-time rank columns attached. The above settings then define a *column*, not a filter, and three of four knobs stop being decisions.

Caveat that stays: Lighter `open_interest` is in the recorder's persisted payload and in Phase 1's live probe, but **nobody has looked at a live record — unauditable** whether current frames carry a non-zero value.

### The One Thing to Do First

**Today, run the gate's own validity test on data already on disk:** forecastability of the next funding change, by size decile, across all 978,572 pair-hours, with round-turn cost swept as a parameter from 0 to 32 bp. Hours of compute, no new data.

- **No break by size** → the exclusion was cosmetic. Ship the full panel with rank columns and never revisit the four knobs.
- **A break exists** → it tells you where. It will not be at 10, and that threshold, not "top 10", goes into the column.
- **No net edge at any decile once cost exceeds ~6 bp** → stop building the universe and go measure the quoted spread instead, because that is the only clock you cannot rewind.
