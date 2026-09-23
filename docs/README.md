# What this research asked, how it was done, and what it found

A complete record of the `fundr` funding-rate study: four phases, the process that produced
each conclusion, and the result — which is negative, and is the reason the project stopped.

**The short version.** The research asked whether hourly perpetual-futures funding is
forecastable well enough to trade, primarily as a Hyperliquid-minus-Lighter spread. Four phases
established that the data exists, collected it, built a point-in-time universe, and constructed
the targets. It then measured the two things nobody had measured — what a round turn costs, and
whether the two-venue position is actually hedged — and found that **net of both, the trade does
not pay at a 24-hour hold under taker execution.** No model was ever fitted. The negative result
came from measuring costs, not from failing to forecast.

---

## 1. The question

Perpetual futures pay funding hourly. The rate is *mechanically* computed from a premium, so
predicting it is not conventional forecasting — the question is whether information available at
time *t* (open interest, basis, returns, volume, funding history) says anything about subsequent
funding beyond what the formula already determines.

Three targets were specified:

| Target | Definition | Status |
|---|---|---|
| **A** | Next funding change, per venue | Built |
| **B** | Next change in the HL–Lighter funding spread | Built, **redefined** — see §5 |
| **C** | Realised minus published funding (intra-hour) | Never possible historically; recorder-only |

The primary universe was specified as "perpetual markets outside the top 10 by open interest".
That rule was tested and **discarded** — see §6.

---

## 2. The phases

| Phase | What it did | Outcome |
|---|---|---|
| **1** | Probed every candidate data source on both venues | Target C impossible retrospectively; A and B fully supported |
| **2a** | Built a live recorder (EC2, Frankfurt) | Ran; the only source for Target C |
| **2b** | Backfilled all history | 4.68M HL funding rows, 1.59M Lighter, 275M per-minute HL market-state rows |
| **3** | Universe construction | No size gate belongs anywhere — see §6 |
| **4** | Target construction | Targets built; the trade does not clear costs — see §7 |

Phases 5–10 (exploratory analysis, baselines, features, simulation, robustness) were **never
started**. The cost measurement in Phase 3/4 removed the reason to.

---

## 3. How the work was done

Three practices did most of the real work, and each one caught errors that would otherwise have
shipped.

**Every claim was measured, then re-measured by someone who did not produce it.** Phases 3 and 4
were executed by subagents against written plans, and every numeric gate was independently
reproduced before the work was accepted. This is how the errors in §8 were found — not one of
them surfaced as a test failure or a crash.

**Decisions were pressure-tested before implementation.** Phase 3 and 4's design questions went
through a structured adversarial process (`.agents/skills/the-quant-council/`): seven advisors
with deliberately clashing priors, seven blind peer reviewers, and a chairman, with the
orchestrator verifying every load-bearing number rather than trusting the advisors' arithmetic.
The full transcripts are in `phase3/council-transcript-*.md`. The process was not decorative — it
produced two conclusions that were later overturned by measurement, which is itself the argument
for measuring.

**Corrections were recorded in place, not silently patched.** Where a figure was wrong, the
document says what it used to say, what it says now, and why. Several numbers were corrected more
than once; one was corrected twice *wrongly* before being settled as a range (§8).

---

## 4. What was collected

All datasets were free except one. Everything below has since been deleted (§9), but is
reproducible from `scripts/`.

| Dataset | Size | Source | Cost |
|---|---|---|---|
| `hl_funding` | 4,676,365 rows, 234 markets, from 2023-05-12 | HL public API | free |
| `lighter_funding` | 1,585,687 rows, 235 markets, from 2025-01-17 | Lighter public API | free |
| `hl_asset_ctxs` | 275,320,095 rows, 1,218 days, per-minute | HL S3 archive | **~$0.92, requester-pays** |
| `lighter_candles` / `lighter_mark_candles` | 1.62M / 1.39M bars | Lighter public API | free |
| `markets` | 469 markets, both venues | both public APIs | free |

---

## 5. Observations that shaped the research

**The cross-venue overlap is smaller and younger than the headline count.** 100 symbols match by
name, but that is a *lifetime* count. Concurrent overlap — the only thing Target B can use — is
978,572 pair-hours (1,039,523 after aliasing), and **the cross-section grows sevenfold through
the window**: 13 live pairs in 2025-01, 96 by 2026-09. A panel trained across the whole window
trains on a cross-section that is mostly not there at the start.

**Symbol matching was not the problem it was assumed to be.** The record claimed the 269
unmatched symbols were "dominated by denomination prefixes". Measured: exactly **5** recoverable
pairs (`kPEPE`↔`1000PEPE` and four others). The rest are genuine venue exclusives — Lighter lists
equities and FX, Hyperliquid lists older alts. All six fuzzy-match candidates at 0.8 similarity
are false (`ME`/`GME` pairs Magic Eden with GameStop). The alias table is a 5-row lookup.

**The denomination factor belongs to a venue leg, not to a pair.** Four `k`-pairs agree because
*both* venues re-denominate. `NOT` does not — Hyperliquid lists one token against Lighter's
`1000NOT`, measured price ratio 0.000999. Getting this wrong scales a price by 1000 silently.

**Hyperliquid's `0.0000125` is a plateau, not a floor, and nothing is censored.** The rate sits
on it 59.86% of the time but is *strictly below* it 34.00% and negative 23.98%. The plateau is
the inner clamp cancelling the premium inside a band. An earlier "censored target" framing was
wrong.

**45.57% of hours are both venues sitting at baseline**, where the spread takes exactly two
values because Lighter truncates `0.00125%` to `0.0012%`. That is a rounding rule, not a market.
Scoring a model there grades it on arithmetic, so those hours are flagged and excluded from skill
metrics.

**Target B was redefined, and the documents say so.** The design asks for the *next change* in
the spread; Phase 4 forecasts the *cumulative level over a hold*, ranked cross-sectionally.
Identical at H=1, different quantities at H≥6. It is the better trading question — but it is not
the design's question, and a reader comparing the two should not be misled.

---

## 6. Phase 3's result: the size rule does nothing

The design's primary universe rule — "outside the top 10 by open interest" — was tested rather
than applied. Ranking was point-in-time, hourly, on open-interest notional, with standard errors
**clustered on the hour** (100 symbols in one hour are one observation under a market-wide
funding shock; clustering inflated the errors ~32×, and without it every difference looked
decisive).

- **No break at rank 10** (gap 0.108 ± 0.086, t = 1.25), and none anywhere that survives.
- **No monotone gradient** — the most forecastable decile is D7, mid-sized.
- **The knobs are bigger than the effect**: dispersion across 24 (pool × rebalance × start ×
  Lighter-leg) cells is 0.291, against a decile spread of 0.279.

So Phase 3 ships **rank columns, not a gate** — no row is deleted for being large. The five
always-excluded names (BTC, ETH, SOL, XRP, HYPE) turned out to be the *cheapest to trade and best
hedged* in the panel, so the rule deletes the only inventory close to payable and buys nothing
measurable.

---

## 7. The result that ended the research

Two quantities decided it, and neither had ever been measured.

**Round-turn cost: ~33 bp** at the median non-top-10 name (HL taker fee 9 bp + HL spread 10.6 +
Lighter spread 14.4; Lighter's fees are genuinely zero). Zero of 88 such names reach the ~6 bp at
which the picture would reverse; the cheapest is 11.2 bp. Cost is **monotone in size** — 24.96 bp
at decile 1 against 1.07 bp at decile 10.

**Basis drift: ~20 bp of standard deviation at 24 hours.** A delta-neutral cross-venue position
earns funding *plus* the change in price basis, and the second term is the same size as the
first. It is concentrated in young markets (mature markets 17.7 bp, BTC/ETH/SOL 9.9 bp) and it
does **not** compound — sd grows as roughly *H*^0.02–0.16 against 0.5 for a random walk.

**Carry, measured unconditionally: 1.03 / 3.70 / 9.78 bp** at 6 / 24 / 72 hours. Figures of
6.21 and 31.33 bp circulated in this project as "gross carry"; they are **selective-entry**
numbers, reachable only by entering on already-wide spreads, and are labelled as such everywhere.

**Net of measured cost and basis, nothing in the panel is tradeable at a 24-hour hold** — net
runs −22 to −32 bp across every decile and every age bucket, and the basis moves further than the
carry on 81% of holds.

**What that does and does not settle.** It kills the *taker-executed* version decisively. A
feasible no-forecast rule (rank by current spread, take the side it points to, top decile) earns
9.4 bp at 24h and 18.3 bp at 72h — against ~33 bp of taker cost, dead twice over; against ~3 bp
of maker cost, marginal. Passive execution was never tested, and it is the only thing that could
change the answer. It is a long shot: resting fills are adversely selected, leg risk on an
unhedged perp dwarfs an 18 bp edge, Lighter's alt books are thin, and capacity at these clip
sizes is small.

**Conclusion: stopped.** The research converted "we think there is a funding edge" into "there is
a 9–18 bp gross carry that only survives passive execution, which is unlikely to work" — a
better-posed question than it started with, and a sufficient reason not to build Phases 5–10.

---

## 8. Errors this project made and caught

Recorded because the pattern is the useful part: **not one of these produced a crash, an
exception, or a failing test.** Every one was found by measuring something against an expected
count.

| Error | How it would have shipped |
|---|---|
| **Candle paging bug** — 700-hour windows against a 500-row cap, advancing by the window regardless | 297,185 trade bars and 337,412 mark bars missing in exactly-200-hour blocks, and 24.78% of Lighter pair-hours unrankable. Two documents asserted the holes did not exist |
| **`rank_unavailable` defined as "no Hyperliquid rank"** | True on all 1,585,687 per-venue Lighter rows, so the natural filter `~rank_unavailable` would have silently deleted Target A's entire Lighter panel |
| **Float equality against baseline** | `0.01/8` truncated lands one ulp off the venue's own published value. One direction invents a premium for 621,772 hours; the other finds zero of 445,795 baseline rows |
| **"Zero band violations" as evidence** | A tautology — the band condition reduces to the same inequality as the branch selection, so it passes on any data |
| **Premium "recovered" from the rate** | A strictly monotone transform of a column already on disk. Identical Spearman to four decimals; it carries no new information |
| **Selective-entry carry quoted as unconditional** | Made the cost comparison look 6× better than it is |
| **Young-market basis quoted as a point estimate** | Corrected *twice wrongly* before being settled: the figure swings 80→199 bp on the cleaning rule and is not point-identifiable. Mature buckets are stable at 17.7 bp under every rule |

Two design-process observations worth keeping: **convergence on a judgment is evidence;
convergence on a method is almost none** — several independent reviewers reached for the same
wrong formula and then checked each other's arithmetic rather than the formula. And a plan is a
source of truth that **goes stale**: three task gates in these plans demanded reproducing numbers
that later measurement had superseded, and would have forced an implementer to reproduce an
artifact.

---

## 9. Final state

The research was stopped and the infrastructure destroyed. All AWS resources (EC2, EBS, S3,
security group, key pair) were deleted; all local datasets (9.6 GB) were deleted. The Frankfurt
recorder's Target C recording — the only thing here that could never be re-collected — was
destroyed with it, deliberately.

**Code, documentation and findings survive in full.** Every dataset except `hl_asset_ctxs`
(~$0.92, requester-pays) is free to re-collect from `scripts/`.

## Where to look

| Question | Document |
|---|---|
| Data semantics, venue quirks, why Target C is impossible | [`phase1/handoff.md`](phase1/handoff.md) |
| What was collected and what will bite | [`phase2/datasets.md`](phase2/datasets.md) |
| Phase 2 → 3 handoff, the universe decision | [`phase2/handoff.md`](phase2/handoff.md) |
| The universe, and why there is no size gate | [`phase3/universe.md`](phase3/universe.md), [`phase3/decisions.md`](phase3/decisions.md) |
| The targets, and the six things easy to get wrong | [`phase4/targets.md`](phase4/targets.md), [`phase4/decisions.md`](phase4/decisions.md) |
| How Phases 3 and 4 were decided | [`phase3/council-transcript-2026-09-22-universe.md`](phase3/council-transcript-2026-09-22-universe.md), [`phase3/council-transcript-2026-09-22-targets.md`](phase3/council-transcript-2026-09-22-targets.md) |
| The original research design | [`funding_research_design.md`](funding_research_design.md) |
