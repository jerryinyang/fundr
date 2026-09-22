# Council transcript — Phase 4 target construction

Run 2026-09-22 with `the-quant-council`, as a second, independent session alongside the Phase 3
universe council. The two sessions shared no advisor and no input; they were run separately
because a council session answers one question and these two do not share a frame.

One orchestrator, seven advisors answering independently, seven blind peer reviewers, one
chairman. This file is the full record: the framed question, all seven responses, all seven
reviews, the orchestrator's independently recomputed numbers, the convergence tally, and the
verdict.

Decisions taken from this session are recorded in [`../phase4/decisions.md`](../phase4/decisions.md).
The implementation plan is
[`../superpowers/plans/2026-09-22-phase4-targets.md`](../superpowers/plans/2026-09-22-phase4-targets.md).

**Read section 4 before section 1.** This session overturned one of the orchestrator's own
pinned assumptions and corrected the council's largest arithmetic error, and both corrections
are in the verified numbers table rather than in the framing.

---

## 1. The framed question (identical for all seven advisors)

# The question before the council

## 1. The decision

A funding-rate research project (`fundr`) is about to build its **Phase 4 forecasting
targets**. The parent design defines them as:

- **Target A** (per venue): `Y_t = F_{t+1} − F_t` — "the next funding-rate change."
- **Target B** (cross-venue, the primary target): `S_t = F_t^{HL} − F_t^{Lighter}`, and
  `Y_t = S_{t+1} − S_t` — "the next change in the funding spread."

Measurement (below) shows that **40–60% of those target values are exactly zero**, and that
the zeros are not noise: they are produced by a deterministic feature of both venues' funding
formulas.

The council is asked **one question**:

> **What quantity should Phase 4 actually forecast?**

Four concrete options are on the table. **Interrogate all four** — do not audit one and let
the others pass. Each seat should say which it would pick and why the others lose.

- **O1 — Ship the design as written.** Forecast the level difference of the settled rate,
  `F_{t+1} − F_t`, on every hour, atom and all.
- **O2 — Forecast the premium instead, and map it through the known formula.** Both venues'
  settled rates are an exact, published function of the hour's average premium, and both
  formulas have been reproduced on real data. Hyperliquid's premium is **on disk for every
  historical hour**. Lighter's premium **does not exist historically anywhere** and only
  starts when the project's live recorder started.
- **O3 — Forecast the rate *level*, `F_{t+1}`, rather than its change.**
- **O4 — A two-part (hurdle) formulation**: model "will the rate move at all?" and "by how
  much, given it moves" as two pieces.

Also within scope, because each would change the target's meaning:
- Should hours where a venue's rate is pinned at a clamp be dropped, modelled, or kept as-is?
- Target B's forecast horizon: the design says one hour. Is that the right horizon?

## 2. The system and its mechanics

Two perpetual-futures venues, **Hyperliquid** and **Lighter**, both settling funding hourly on
the hour, hours pairing one-to-one with no offset (re-tested on data, verdict ALIGNED). The
eventual aim is a delta-neutral cross-venue trade: long the venue with the more favourable
funding, short the other, sized on the forecast. **Phase 9 sizes real positions off whatever
this target turns out to be.**

**Both settlement formulas are confirmed against real settled data**, and the structure they
impose is the heart of this question:

```
Hyperliquid (fraction per hour):
    F_8h    = P + clamp(0.0001 − P, −0.0005, +0.0005)
    F_hourly = clamp(F_8h / 8, −0.04, +0.04)
  -> reproduces 245/245 off-baseline hours to 1e-10.

Lighter (percent per hour):
    small = P + clamp(0.01 − P, −0.05, +0.05)
    rate  = truncate_to_4dp( clamp(small, −4, +4) / 8 )
    signed by a separate `direction` field, then /100 for the common basis
  -> reproduces the exact 4-decimal string on 16/16 off-baseline hours.
```

Read the Hyperliquid formula carefully. **Whenever the hour's average premium `P` sits inside
a band of width 0.001, the inner clamp exactly cancels `P` and the settled rate is identically
`0.0001/8 = 0.0000125`** — not approximately, exactly. The same structure exists on Lighter
with a band of width 0.10 in percent. This is a **dead zone / soft-threshold**, not a floor:
the rate passes through it and goes negative on the other side.

Lighter's rate is additionally **truncated to 4 decimal places of percent**, so it lives on a
**1e-6 lattice** in the common basis. Hyperliquid's is reported to 1e-10.

## 3. Real numbers (measured on the project's own data by the orchestrator today)

Everything below is over the **978,572 concurrent pair-hours** where both venues settled the
same asset in the same hour — 100 matched symbols, 2025-01-17 08:00Z → 2026-09-21 21:00Z.
Rates are per-hour signed fractions, positive = longs pay shorts.

**The dead zone, Hyperliquid**
- rate exactly `0.0000125`: **585,752 hours (59.86%)**
- rate strictly **below** `0.0000125`: **332,726 (34.00%)**
- rate strictly **negative**: **234,692 (23.98%)**
- observed range: −2.263e-2 to +9.966e-3
- **the ±0.04 outer clamp never binds: 0 of 978,572 hours.**
- The hour's average premium, which produced those rates, **is on disk for 100% of rows** and
  **varies continuously inside the dead zone**: among the 585,752 dead-zone hours the premium
  has standard deviation 2.21e-4, 1st/99th percentiles −3.94e-4 / +5.61e-4, and **548,808
  distinct values**.

**The dead zone, Lighter**
- rate at its own market's reported (4-dp-truncated) baseline: **621,772 hours (63.54%)**
- rate reported as the string `0.0000`: 10,320 hours (1.05%)
- **the outer clamp does bind here**: |rate| is exactly `0.005` (= 4%/8) on **142 hours
  (0.0145%)**, across 23 of the 100 symbols. 8 at the positive cap, 134 at the negative cap.
- observed range: −5.000e-3 to +5.000e-3
- Lighter's premium has **no historical record anywhere** — not on its API, not from the
  third-party vendor, nowhere. It exists only from the date the project's own recorder started.

**The targets themselves** (978,472 valid consecutive-hour observations)

| Target | Exactly zero | Std dev | 1st pct | 99th pct | lag-1 autocorrelation |
|---|---|---|---|---|---|
| A, Hyperliquid (`ΔF_HL`) | **528,493 (54.01%)** | 6.97e-5 | −6.74e-5 | +6.96e-5 | **−0.040** |
| A, Lighter (`ΔF_Li`) | **591,128 (60.41%)** | 6.79e-5 | −8.30e-5 | +8.60e-5 | **−0.133** |
| B, spread (`ΔS`) | **394,055 (40.27%)** | 6.24e-5 | −7.73e-5 | +7.78e-5 | **−0.177** |

**Levels are strongly persistent; changes are not** (pooled lag-1, within symbol):

| Series | lag-1 autocorrelation |
|---|---|
| `F_HL` level | **0.886** |
| `F_Lighter` level | **0.867** |
| `S` (spread) level | **0.699** |
| Hyperliquid premium level | **0.896** |
| `Δ` Hyperliquid premium | **−0.042** |

- Spread level: mean −6.09e-8, standard deviation 8.04e-5, never exactly zero.
- **Conditional on Hyperliquid sitting exactly at `0.0000125` this hour, next hour's rate is
  identical 90.14% of the time.**
- **Change in the Hyperliquid premium is exactly zero only 0.38% of the time**, standard
  deviation 5.71e-4 — i.e. the premium target has essentially no atom at all.
- 3.26% of non-zero Target B changes are **smaller than Lighter's own 1e-6 reporting
  resolution** — they are visible only because Hyperliquid reports 10 decimal places.

**A log ratio cannot be formed.** At least one venue's rate is **≤ 0 on 33.61%** of concurrent
pair-hours (Hyperliquid ≤ 0 on 24.04%, Lighter ≤ 0 on 22.35%).

**One data-shape fact about Target A's longer Hyperliquid-only history**: Hyperliquid's settled
funding was **8-hourly until 2023-06-08** (1,789 eight-hour intervals across the roster) and
carries **213 single-hour holes across 141 markets** over its full 4,676,131 intervals. All of
those are **outside** the two-venue window; they matter only if Target A is built on
Hyperliquid's full history back to 2023. Lighter's history has **zero** irregular intervals in
1,585,452.

## 4. What is at stake

- **No capital is at risk at this step**, but this choice sets what every later phase is
  measuring and what Phase 9 sizes positions from. A target choice that makes a model look
  good and a strategy lose money is the failure mode.
- **Irreversible:** nothing. All inputs are on disk and free to re-read; targets are cheap to
  rebuild. Re-running Phases 5–10 on a second target definition is the real cost.
- **One asymmetry that cannot be undone:** Lighter's premium history begins the day the
  recorder began and no earlier. If a target needs Lighter's premium, the sample for it is
  days long, not twenty months.

## 5. NOT KNOWN — material inputs nobody has

1. **Whether the premium itself is predictable from observable market state.** Nothing in this
   project has tested it. O2's whole case rests on it.
2. **The economics.** No cost model exists yet. Nobody has computed what spread change, in
   basis points per hour, would clear fees plus slippage on two legs in a thin market. A 6.2e-5
   per-hour standard deviation is the size of the thing being forecast; whether that is a lot
   or a rounding error against costs is unmeasured.
3. **Whether the zeros are predictable separately from the magnitudes.** No baseline exists
   yet for either piece (that is Phase 6's job, which comes after this decision).

## 6. Pinned shared assumptions

Use these so the seats' figures are comparable. You may argue a pin is wrong — that is more
useful than quietly substituting your own.

- **Pinned: the common basis is a per-hour signed fraction, positive = longs pay shorts.**
  Hyperliquid's native value is already that; Lighter's is `rate/100`, signed by `direction`.
- **Pinned: the meaningful resolution of any cross-venue spread is 1e-6, not 1e-10** — Lighter's
  4-decimal truncation binds.
- **Pinned: the Target B sample is 978,472 usable consecutive-hour observations** across 100
  symbols over 20 months, before any universe exclusion. A universe rule removes roughly 15%.
- **Pinned: Hyperliquid's hourly average premium is available historically for every hour;
  Lighter's is not available for any historical hour.**
- **Pinned: the eventual trade is delta-neutral and cross-venue** — two perpetual legs, one per
  venue, and the P&L is the funding differential net of both legs' costs.

---

## 2. The seven advisor responses

Anonymized A-G for the review round. The mapping is in section 4.

# Anonymized advisor responses — target-definition question

**Response A:**

Lead: the question is unanswerable as posed, because nobody has measured whether any of these targets clears costs. §5.2 admits it. One calc, using pinned numbers plus one labeled assumption:

*Assumption (correct me): 2 bp all-in per leg per side — taker fee plus slippage in a thin market — so 8 bp round-trip for the two-leg pair.*

The trade earns the spread **level** S per hour, σ = 8.04e-5 = 0.804 bp/h, lag-1 0.699 (half-life 1.9 h). Enter at a 1σ spread, hold to exhaustion: 0.804 × (1−0.699¹⁰)/(1−0.699) = 2.6 bp earned against 8 bp paid. Even at 1 bp/leg it loses. You need roughly a 2.5σ spread to break even. Until that number is real, target selection is decorating.

I pick O3, on the spread level, at the hold horizon — not one hour. Phase 9 sizes from expected funding differential, which *is* S. ΔS is a quantity nobody trades. S level is never exactly zero, is persistent (0.699), and the entire 40% atom evaporates — it was manufactured by differencing. The one-hour horizon is also wrong on its face: you cannot round-trip two legs in an hour for 0.8 bp.

Why the others lose on the ops floor. O1 sizes real positions off a quantity with −0.177 autocorrelation, which is truncation and microstructure noise, not signal. O4 builds two models, two calibrations, two failure modes in production to service an artifact — and its "will it move" leg is 90% correct by answering "no" forever; it will score beautifully and trade nothing.

O2 is the one that will wake you at 3am, and I read the files to check. Its Lighter premium comes from a recorder provisioned 2026-09-21 — roughly one day of history against 978,472 pair-hours. Verified in `docs/phase2/recorder_runbook.md`: no alerting (line 146, `scripts/daily_check.py` is a manual daily check), S3 uploads disabled, one 8 GB volume, dynamic IP, and the recording exists in exactly one place. `docs/phase1/handoff.md` §6 says the same of the Phase 1 file — gitignored, one machine, off-machine backup outstanding. O2 bets the primary target on an unmonitored, unbacked single-disk feed whose predictability nobody has tested (§5.1). Also verified: HL's own archive silently returned 156/1440 and 505/1440 rows on recent days for unexplained reasons — feeds lie quietly here.

Clamp hours: keep them, flag them, never drop. The 142 Lighter cap-hours are where the spread is widest — your best trades. A model trained on rows with those removed has never seen its own payday.

Monday morning, concretely: (1) turn on S3 uploads and get an off-box copy — one `rm` or one terminated instance and O2 is dead forever; (2) wire one alert that pages when the recorder writes nothing for 15 minutes; (3) put $500 through the actual two-leg trade on 3 symbols and measure realised round-trip cost. That number decides the horizon, the universe, and whether Phases 5–10 are worth running at all.

*Files read that others may not have:* `docs/phase2/recorder_runbook.md`, `docs/phase1/handoff.md`, `docs/phase2/datasets.md`.

**Response B:**

*Read beyond the framing:* `docs/phase1/handoff.md` §3. Chairman: that is non-shared input.

Lead correction. Two claims in the framing are wrong.

1. *"Lighter's premium does not exist historically anywhere."* It does — as **interval-censored** data. Lighter's clamp saturates at P < −0.04% and P > +0.06% (per 8h), identical band to Hyperliquid's. On the 36.46% of off-baseline hours, P is **point-identified** by inverting the formula (P = ±8·rate ∓ 0.05, signed by `direction`, resolution ~8e-4% from truncation). On the remaining 63.54%, P is bounded to a known interval. That is censored regression, not missing data. O2 is far more buildable than stated.

2. Both venues run the *same* funding spec — 0.01%/8h interest, ±0.05% clamp, /8. So whenever both sit at baseline, the spread is exactly +5e-7/hr, a pure truncation artifact (HL 1.25e-5, Lighter 1.20e-5). A large share of Target B's "level" is Lighter rounding down, not economics — and ΔS's 40.27% atom is that same artifact.

The counterparty, one sentence. Leveraged directional traders on whichever venue's book is more crowded pay the differential because they want exposure now and will not open an offsetting leg on a second venue; on Lighter, points/airdrop farmers additionally accept negative carry because the token is the real payment. *(The first clause is a durable risk premium. The second is a subsidy with a termination date: TGE. Belief, not verified.)*

That distinction decides everything downstream. Imbalance-absorption carry is a risk premium in costume: you get run over precisely when the crowd is right and one venue gaps. It is not an arbitrage, and it must be sized as a premium, not a convergence trade.

One calculation. Perfect-sign capture of the spread: E|S| ≈ 0.798 × 8.04e-5 = 6.4e-5/hr → ×8760 ≈ 56%/yr gross on notional, the absolute ceiling. The deterministic truncation artifact is 5e-7 × 8760 = 0.44%/yr. So ~99% of the money sits in the ~36% of hours where the two venues' premia genuinely diverge. The atom is not the signal; it is the absence of one.

Verdicts.

- **O1 — reject, hardest.** Target A is *per venue*, and per-venue funding is not the trade — it is leverage-demand carry, a directional bet on crowd positioning. Worse, ΔF forecasts a *venue's administrative censoring rule*: inside the dead zone the premium has 548,808 distinct values and σ = 2.21e-4 while the settled rate is frozen. 54% of the target is bureaucracy. Lag-1 of −0.04 says there is nothing there.
- **O3 — right instinct, wrong object as written.** Forecasting `F_{t+1}` per venue is a trap: 0.886 autocorrelation is almost entirely "it is at baseline and stays at baseline 90.14% of the time." You would ship a spectacular R² that knows nothing. But applied to the **spread**, O3 is the correct framing: funding is a coupon, not a price. You do not trade S as an asset; you hold a position and receive `S_{t+1}` as a cash flow. `S_t` is a known constant at decision time, so ΔS carries identical information but a corrupted loss function.
- **O2 — correct latent variable, and my pick's input.** Premium is the counterparty's positioning imbalance made numeric: autocorrelation 0.896, essentially no atom. It is the only one of the four that models the thing a human is actually doing.
- **O4 — reject.** "Will it move?" has a *deterministic* answer given the premium. A hurdle model is an admission that you deleted the premium and are now reconstructing it from its own censored output. O4 is O2 with the information thrown away first.

My pick: forecast the **H-hour cumulative spread level** you will actually receive, built from both venues' premia — Hyperliquid's observed, Lighter's censored-inverted — pushed through the two known formulas. Not ΔS, not one venue.

Clamps: keep, and flag them. Lighter binds at ±0.005 on 142 hours across 23 symbols; HL never binds. Those are the only hours in 978k with a *named forced counterparty* — a premium through ±0.5%/8h is a liquidation cascade — and, since HL does not clamp, they are mechanically the max-spread hours. They are also 0.0145% of the sample and capacity-trivial, so model them, do not tune on them.

Horizon: one hour is wrong. Round-trip cost is paid once; the coupon accrues hourly. Levels persist (0.699) where changes do not (−0.177).

What I would need: (a) the joint-baseline table — how often are *both* venues at baseline, and is S exactly +5e-7 there? (b) Lighter's book depth at the top 5 levels — with no depth number the capacity question is unanswered. (c) Whether Lighter's points program is live and its schedule. Nothing above establishes capacity — I would not let Phase 9 near this until (b) exists.

**Response C:**

Nothing here has been shown to be forecastable yet, and one option is engineered to hide that.

First, the numbers hold together. 0.5986 × 0.9014 = 0.5396 ≈ the 54.01% zero share of ΔF_HL. So essentially *every* Target A zero is "sat in the dead zone, stayed there." And 0.5401 × 0.6041 = 0.3263 vs 40.27% observed for ΔS — the venues sit in their dead zones together. No arithmetic to dispute.

O3 is the trap and I reject it hardest. Level autocorr 0.886 ⇒ R² ≈ 0.785 from predicting *no change*. Any Phase 6 model on F_{t+1} reports ~0.8 R² and has learned nothing. That is precisely §4's stated failure mode, pre-built.

O2's only on-disk diagnostic is evidence against O2. Δpremium autocorr −0.042, zero atom. Removing the atom removed the structure and left white noise. And Lighter's premium is days long — O2 cannot build the primary target at all.

Selection/leak check, applies to all four: F_{t+1} is fixed by the premium averaged *during* hour t+1. Every feature must be hard-lagged to the close of hour t. A premium stamped at settlement sits inside the interval it explains, and that leak survives any train/test split. Run the one-period-lag test before anything else.

Effective sample. 978,472 pair-hours is 100 correlated crypto perps over ~14,600 hours. Honest SE is 1/√14,600 = 0.0083, or 0.041 if blocks are daily — 8× to 40× the quoted 0.0010.

Variant arithmetic (exact expected max of N standard normals, computed numerically, *not* √(2 ln N) — at N=24 exact is 1.948 vs 2.521): four targets × three clamp treatments × two horizons = 24 runs. Under zero true edge the best scores 1.95 SE, i.e. ~0.016–0.080 in correlation units on daily blocking. Pre-register one target now.

Pick: O1's form — a differential, not a level — scored against the no-change benchmark, at horizon h set by costs. 0.62 bp/hour is the whole prize; a one-hour horizon is an artifact of settlement frequency, not of economics. (Two-leg round-trip cost: LABELED ASSUMPTION, unmeasured — it decides h.) O4 doubles the variant count for an atom that is already fully explained by a published formula. Files read: only the framed question.

**Response D:**

The number answering a different question. "Premium change is exactly zero only 0.38% of the time — essentially no atom at all" is presented as a point in O2's favour. It isn't a fact about profit, it's a fact about convenience. The atom is not a defect in the data; it is the venue literally paying the same thing two hours running. Removing it from the target does not remove it from the P&L. Any option justified by "this series is nicer to model" is selecting for the exact failure mode §4 names.

Second: 142 hours is on the agenda, and the money might all be there. The clamp question covers 0.0145% of rows and is framed as a cleanup chore, sitting next to a question about 40%. But the clamp binds when premium is extreme — plausibly the only hours where the differential is large enough to pay for two legs. Nobody has checked whether P&L concentrates in that tail. If it does, the whole distribution being agonised over is noise around a payout that happens 142 times.

Third, and this sinks the framing: you get paid the level, not the change. The trade earns the funding differential over the hours you hold. `S_{t+1} − S_t` is not that. Three of four options forecast a change nobody is paid. So O3, forced — but only scored against "assume unchanged," because at 0.699–0.886 autocorrelation a level model will look excellent while knowing nothing. O2 is disqualified as written: it needs a history that §6 pins as nonexistent, for the untested reason in §5.1.

Missing, and a stranger expects it. Nobody states the holding period — a one-hour horizon is untradeable if two legs take longer than an hour to open and close. And nobody says who else is doing this. Cross-venue perp funding arb is the most obvious trade there is. If the spread is real and persists at 0.699, someone is declining it. Why?

Verified: I read the file. Everything above is reaction, not computation.

**Response E:**

Read nothing outside the framing doc.

The atom is not real, so O1 and O4 are solving an arithmetic problem, not an economic one. The brief supplies its own refutation: the premium moves continuously inside the dead zone (548,808 distinct values; Δpremium exactly zero 0.38% of the time). The 40–60% zeros are the venue's clamp-and-truncate applied to a continuous state. That map is *known exactly* and reproduces 245/245 and 16/16. Spending a hurdle model (O4) on predicting the zero is paying a modeller to re-derive a published formula. O1 ships the same defect into every downstream phase.

But O2 and O3 are also mis-specified, because none of the four is the P&L. You are not paid ΔS. You are paid the *level* S over however long you hold, once, net of a round trip. ΔS versus F_{t+1} (O1 vs O3) is the same model with a different denominator — it only changes which baseline you must beat. The real free parameter nobody has chosen is the horizon.

One calculation. Spread level σ = 8.04e-5/hr = 0.80 bp/hr. *Labelled assumption, nobody measured it:* 2 bp round-trip across both legs. Breakeven hold H = 2 / (0.80 × f), f = fraction of 1σ actually captured. f = 1 → 2.5 h; f = 0.3 → 8.3 h; f = 0.1 → 25 h. The one-hour horizon is defensible only if costs are ~0.2 bp. NOT-KNOWN #2 is not a gap in the analysis; it is the input that determines the answer.

My pick: O3, reframed — target E[Σ_{h=1..H} S_{t+h} | info at t], with H set by the cost model, and Hyperliquid's premium entering as a *feature* through the known link function (O2's real value; as a target it dies on the Lighter asymmetry — a days-long sample for the leg that pays half the spread).

Restated problem: *over what holding period does the HL–Lighter funding differential clear two-leg cost, and what is its conditional expectation over that period?* Answer that and Phase 4's target is implied. Measure costs before building any target.

One thing to check, unverified: at joint baseline, HL pays 1.25e-5 and Lighter truncates 0.00125% down to 0.0012% = 1.2e-5 — a deterministic +5e-7/hr spread, present on ~60% of hours, requiring no model. Truncation toward zero also systematically shrinks Lighter's magnitude. If that holds, part of "the signal" is a rounding rule, and Phase 9 would be sizing off it.

**Response F:**

The premise I'd challenge first: the framing treats the 142 clamp hours as a 0.0145% curiosity. They are the only part of this dataset that can kill the account, and every option on the table is chosen without reference to them.

The tail mechanic, made concrete (arithmetic on the document's own figures): Hyperliquid's observed range reaches −2.263e-2/hr. Lighter's is hard-capped at ±5.000e-3. So the spread `S` is structurally capable of ≈ −2.76e-2, against a spread standard deviation of 8.04e-5. That is ~340 sigma. Even one-sided, 2.263e-2 / 8.04e-5 ≈ 281 sigma.

This is not a fat tail. It is a different distribution welded onto the same series. And the clamp is what welds it: when Lighter is pinned, the spread cannot revert *through* the cap. The mean-reversion that makes the trade work (spread-level lag-1 0.699, half-life ~1.9 hours) is switched off precisely in the state where the position is largest and the loss is unbounded on one side. Short gamma, and nobody has priced the gamma.

Worse, it clusters: 134 of 142 clamp hours are at the negative cap, across 23 of 100 symbols. That is not 142 independent draws — it is a handful of stress episodes in which many legs clamp together. Correlation goes to 1 exactly where the hedge is supposed to hold.

What squared-error loss does to this. With 54% of `ΔF_HL` exactly zero and total std 6.97e-5, the conditional std given a move is 6.97e-5/√0.46 ≈ 1.03e-4. An MSE fit on the pooled sample returns the blend: ŷ ≈ P(move)·E[y|move]. Phase 9, sizing linearly in ŷ, therefore takes a ~half-size position every hour instead of a full-size position 46% of the time. You pay two legs of cost on 100% of hours to harvest an edge present on 46%. And the loss function assigns the 281-sigma clamp events a weight proportional to their squared error — meaning the fit will bend the whole model to chase a few hundred unhedgeable outliers while being systematically wrong about the 978,000 ordinary hours.

The four options, on ruin alone:

- O1 (ship as written). Worst sizing pathology. Atom + MSE + linear sizing = guaranteed cost bleed on a signal whose unconditional mean is −6.09e-8.
- O2 (forecast the premium). Highest ruin. Lighter's premium history is *days*, across 100 correlated crypto symbols. A model fitted there will look excellent and has never seen a stress episode. Reject on sample-length grounds alone.
- O3 (forecast the level). Most dangerous *because it looks best*. Level lag-1 is 0.886; a level model scores R² ≈ 0.78 by predicting "same as last hour." That is the document's own named failure mode, offered as an option.
- O4 (hurdle). The only one that lets sizing be conditioned on *which regime you are in*. Pick O4.

My pick: O4, with clamp hours modelled as a third state, not dropped. Dropping them removes the only observations of the event that ends the account.

Single change that cuts ruin most: not the target at all — require the cost model before the target is finalized. Target B's per-hour std is 6.24e-5 ≈ 0.62 bps. *Labeled assumption:* two-leg taker cost plus slippage of 2–5 bps round trip. If that is even roughly right, the entire target distribution — including its 99th percentile at 7.78e-5 — sits inside the cost band, and the spread's ~1.9-hour half-life means the edge decays before you can pay for entry. In that world every option ruins at any leverage, and the choice between O1–O4 is decoration.

Second change: state now whether the Phase 9 drawdown rule is static or trailing. Under a trailing rule, the 134 co-clustered negative-cap hours are a single correlated event that can breach the limit in one sitting.

Verified vs believed: the sigma ratios, the conditional std, and the half-life are arithmetic on figures in the framing document. The cost magnitude is my assumption. The clustering claim (134 negatives being few episodes rather than 134 independent hours) is inferred and should be checked — the timestamps are on disk. I read only the framing document.

**Response G:**

The framing's §5.2 admits no cost model exists. That omission is not a gap — it is the answer, and it disqualifies all four options as posed.

Verified on disk: `data/phase2/markets/venue=lighter/date=2026-09-21/part.parquet` — `maker_fee` and `taker_fee` are 0.0 on all 235 Lighter markets. Lighter is free. The HL market file carries no fee column at all, so HL fees are *not* in this project. **Assumption (correct me): HL base tier 4.5 bp taker / 1.5 bp maker, per side.** Everything below is one-legged cost — Lighter contributes zero.

The one calculation. The tradable quantity is the spread *level* S, earned per hour held. σ(S) = 8.04e-5 = 0.80 bp/hour. Lag-1 0.699 → half-life 1.94 h; total lifetime carry from a shock S₀ = S₀·ρ/(1−ρ) = 2.32 × S₀.

- Taker round trip, HL leg only: 9 bp + assume 2 bp crossed spread on two HL fills = ~13 bp. Break-even S₀ = 13/2.32 = 5.6 bp = 7σ. Does not exist in this sample.
- Maker-only HL: 3 bp → break-even S₀ = 1.29 bp = 1.6σ. Only this survives, and only if the maker fill arrives — which it won't when the spread is widest.

Consequence for the four options. O1, O3 and O4 all forecast the *settled rate*, whose entire lifetime edge is 2.32σ ≈ 1.9 bp against a 13 bp taker cost. O2 forecasts the premium — which is a higher-frequency, lower-persistence object (Δpremium autocorr −0.042, σ 5.71e-4), so mapping it through the formula produces *more* turnover, not less. O2 is the most expensive option on the table and nobody said so.

What I'd pick: O3, the level — not because levels forecast better, but because a level target is the only one whose units are the same as the carry you actually collect, so break-even is computable at all. Δ-targets at a 1-hour horizon (O1, O4) encode a 1-hour hold, and a 1-hour hold pays 13 bp to collect 0.8 bp.

The horizon question is the real question. One hour is wrong. Horizon must be set at break-even hold time — ~13 h taker, ~3 h maker — and that exceeds S's 1.94 h half-life, meaning the position decays faster than it amortizes entry.

Clamp hours: keep them. Lighter's 142 cap-binding hours are the only hours where S plausibly reaches multiple bp. Dropping them removes the only observations that could ever clear cost.

Unmodeled and material: delta-neutral cross-venue means margin posted on *both* venues with no netting — capital is ~2× notional, halving every return quoted above. Plus inter-venue USDC transfer cost and idle-margin drag.

What I'd need: HL fee tier at the user's real volume, HL top-of-book depth at the sizes intended, and realized maker fill rate.

---

## 3. The seven peer reviews

Each reviewer saw the framed question and all seven responses, not each other's reviews
and not the mapping.

# The seven peer reviews — target council

**Review 1:**
1. Strongest: B. It is the only seat that attacks a *pinned* assumption and wins. Lighter's premium is not missing — it is interval-censored: the formula inverts exactly on the 36.46% off-baseline hours (P = 8·rate ± 0.05) and bounds P elsewhere. Every other seat rejects O2 on "days-long sample," which B shows is false. It also derives the joint-baseline artifact (HL 1.25e-5 vs Lighter truncated 1.2e-5 = +5e-7/hr, free of any model) and names the counterparty. Its inversion needs verification against the `direction` field before it is relied on.
2. Biggest blind spot: D (F shares it). Both claim the 142 clamp hours may hold most of the P&L. Bound it from framing figures: total gross |S| ≈ 978,572 × 6.4e-5 ≈ 62.6 rate-hours; 142 hours at even the structural extreme 2.76e-2 give 3.9, ≈6% — realistically ~1%. The clamp hours are a risk question, not a revenue question. D also offers no computation at all.
3. Wrong number: E's break-even table. E writes H = 2/(0.80·f), giving f=1 → 2.5h. That assumes S is constant over the hold while E elsewhere cites a 1.9h half-life. From the original inputs (σ=8.04e-5, ρ=0.699): cumulative expected carry from a 1σ entry is σ·ρ/(1−ρ) = 0.804 × 2.322 = 1.87 bp, the limit as H→∞. Against 2 bp round-trip, a 1σ entry never breaks even, at any horizon. E's table is unreachable everywhere. A's "2.6 bp" is the same error (it includes h=0; correct 1.82 bp), and A's "≈2.5σ to break even" is inconsistent even with A's own figure — correct is 8/1.867 ≈ 4.3σ. G alone uses ρ/(1−ρ) correctly.
4. All seven missed cross-sectional selection. Every cost calculation (A, B, E, G) uses the *pooled unconditional* σ(S) = 0.80 bp/hr. But you do not trade a random symbol — you trade the best of ~100 each hour. Should be computed: per hour, the distribution of |S| across the 100 symbols; the mean of the top-1, top-5 and top-decile; and the lag-1 persistence of *those selected* rows. If the selected |S| runs several times the pooled σ, A/E/G's "it never clears cost" verdict reverses. Relatedly, nobody priced that cost is paid on *flips*, not hours.

**Review 2:**
1. Strongest: B. It is the only seat that corrects the framing instead of reasoning inside it. Lighter's premium is point-identified by inverting its own published formula on the 36.46% off-baseline hours (P = 8·rate − 0.05 below baseline, 8·rate + 0.05 above; resolution 8e-4%), and interval-bounded on the rest. That turns O2 from "impossible" into censored regression, and it is checkable today. B is also the only seat to name the counterparty and separate durable risk premium from a points subsidy with a termination date.
2. Biggest blind spot: C. It rejects O2 on Δpremium's −0.042 autocorrelation. O2 forecasts the premium *level* (autocorr 0.896) and maps it through an exact function; Δpremium is irrelevant. C commits the same level/change error it accuses O3 of. Its pick is then empty: "O1's form scored against no-change" is the identical model to O3 scored against no-change.
3. Wrong claim: G's "Lighter is free… everything below is one-legged cost." Zero fee columns do not mean zero execution cost, and G is internally inconsistent — it charges HL 2 bp of crossed spread while charging Lighter's thin book nothing. Rebuilt from the originals (σ(S)=0.804 bp/h, ρ=0.699 ⇒ ρ/(1−ρ)=2.32): maker-only HL at 3 bp gives break-even S₀=1.29 bp=1.6σ; add even 2 bp round-trip of Lighter half-spread and it becomes 5/2.32=2.16 bp=2.7σ, which the sample does not supply. Secondary: A's "roughly 2.5σ" conflates bp with σ; from the same inputs, 8/(0.804 × 3.229) = 3.08σ.
4. Missed by all seven. Every cost argument treats S's 0.699 lag-1 as shock decay toward zero. Nobody asked how much of that is a *permanent per-symbol basis* — a venue-pair spread that sits at a nonzero level for days. If between-symbol variance dominates, you do not round-trip at all: you hold and collect a near-constant differential. Needs computing: decompose Var(S) into between-symbol and within-symbol components, and re-estimate lag-1 on symbol-demeaned S. Relatedly, no seat proposed a cross-sectional target.

**Review 3:**
1. Strongest: B. Only seat that corrects a framing premise with a mechanism rather than a caveat: Lighter's premium is interval-censored, not missing — point-identified on the 36.46% off-baseline hours by inverting `P = ±8·rate ∓ 0.05`, bounded elsewhere. That reopens O2. It also sizes the joint-baseline truncation artifact (5e-7/hr ≈ 0.44%/yr) against the 56%/yr perfect-sign ceiling.
2. Biggest blind spot: F. F is the ruin seat and audits the wrong balance sheet. It calls the clamp state "unbounded on one side" while quoting the two clamps that bound it: |S| ≤ 0.045/hr, hard. Meanwhile the actual ruin channel in a two-venue delta-neutral perp — one leg liquidating on mark-price divergence, margin unnetted across venues — never appears. Only G touches margin at all.
3. Wrong claim: G's "O2 is a higher-frequency, lower-persistence object... produces *more* turnover." G compares Δpremium autocorr (−0.042) against the rate's *level* autocorr. From the framing's own table, premium level lag-1 = 0.896, above F_HL's 0.886. And since F_HL is an exact function of P, mapping P̂ through the formula returns F̂ — turnover is identical by construction. (Separately, A's "2.6 bp" double-counts the entry hour; lifetime carry is 2.32 × 0.804 = 1.87 bp — A's conclusion survives, its margin doesn't.)
4. All seven missed: every seat treats the atom, σ(S) and the clamp tail as pooled properties of 978k rows. They are per-symbol. Majors likely sit at baseline near-permanently; thin alts carry the variance — and 23 of 100 symbols produce all 142 clamp hours. A pooled MSE target is then dominated by a minority of names. Should be computed: per-symbol zero-share of ΔS, σ(S), Σ|S|, and clamp-hour count, plus the Gini across symbols.

**Review 4:**
1. Strongest: B. Only seat to overturn a pinned assumption with mechanism, not opinion. Lighter's premium is *interval-censored*, not missing: off-baseline hours (36.46%) point-identify P by inverting the formula, and Lighter's band ([−0.04%, +0.06%]) is identical to HL's. That turns O2 from unbuildable into censored regression. Its joint-baseline artifact is right, and it asked for the joint-baseline table rather than asserting it. E reached the same artifact; only B drew the consequence.
2. Biggest blind spot: F. It treats the 142 clamp hours as account-ending ("unbounded," "short gamma") without ever checking the *sign* — funding is a per-hour cash flow you can be on the receiving side of, not a mark-to-market gap. A, B and G all read the same hours as the payday. F's MSE argument is also wrong: ŷ = P(move)·E[y|move] is the conditional mean, the correct sizing input, not a "half-size" pathology.
3. Wrong claim: G's "Lighter is free." `maker_fee`/`taker_fee` = 0.0 on all 235 markets is far more likely an unpopulated column than a zero-fee venue; G makes it load-bearing. If Lighter's taker is even ~2bp/side, G's 13bp taker round-trip becomes ~17bp and break-even hold goes from ~13h to ~17h. Compute, don't estimate: pull Lighter's published fee schedule and confirm against a real fill. Secondary, A: break-even entry is 8/(0.804×3.23)=3.1σ, not "roughly 2.5σ".
4. All seven missed: the cross-venue *basis*. Two separate perps are not a hedge; P&L = funding differential **plus** the change in (HL mark − Lighter mark) between entry and exit, plus any liquidation on the losing leg. At 0.8bp/hr carry, a few bp of mark divergence dominates every cost model here. Should be computed: per-symbol hourly distribution of (HL mark − Lighter mark)/mid, and its change over a 3–13h hold. If its σ exceeds ~1bp, the target choice is secondary to whether this trade is hedged at all.

**Review 5:**
1. Strongest: B. The only seat that adds facts rather than reframing. Its censored-regression point is decisive, so the "days-long sample" objection A, D, E and F all lean on to kill O2 is simply wrong. B also identifies the joint-baseline truncation artifact that only E half-sees, and is the only seat to name a counterparty. Nit: its inversion should read P = 8·rate ± 0.05.
2. Biggest blind spot: F. Its O4 case rests on "unbounded loss" at 281–340σ, but per-hour funding is bounded (Lighter ±5e-3) and *signed* — a huge |S| is a payday if you are on the right side. F never conditions the tail on position sign. The real ruin channel is mark divergence and one-leg liquidation, which no target measures and F never mentions. Its MSE-blend sizing argument is the set's best original point and survives intact.
3. Wrong number: A's "2.6 bp earned / 2.5σ break-even". A's multiplier (1−ρ¹⁰)/(1−ρ)=3.23 sums h=0..9, counting the entry hour's spread, which you never receive. Derived from the pinned inputs only: lifetime expected carry from a 1σ entry = 0.804 × 2.322 = 1.87 bp, not 2.6. Break-even entry is ~4.3σ, not 2.5σ. G independently derives the correct 2.32 multiplier. Related: E's break-even-hold table assumes carry accrues linearly, contradicting the 0.699 decay E itself cites.
4. All seven missed: the panel is unbalanced and listing-selected. 978,572/100 ≈ 9,786 hours per symbol against ~14,640 in the window — roughly a third of the grid is absent because symbols list at different dates. The sample is weighted toward the longest-listed majors, while newly-listed symbols — where funding is most extreme and capacity thinnest — are underweighted. Needs computing: (a) per-symbol hour counts and σ(S), mean(S), |S| by symbol age since listing; (b) the joint-baseline table B asked for — note the all-hours mean S is −6.09e-8, i.e. negative, so the artifact is being offset somewhere, and that tension should be resolved before anyone sizes off it; (c) whether the 134 negative-cap hours cluster into a few episodes.

**Review 6:**
1. Strongest: B. Only seat to overturn a framing pin correctly: Lighter's premium is interval-censored, not missing. Off-baseline hours invert exactly; baseline hours give a known interval. That makes O2 buildable over 20 months, not days — and A, D, E and F all rejected O2 on a sample-length claim that is false. B also derives the joint-baseline +5e-7 truncation artifact and sees that S and ΔS carry identical information at decision time.
2. Biggest blind spot: C. It rejects O3 because a level model scores R²≈0.785 by predicting no change, then picks "O1's form, scored against the no-change benchmark" — the same estimator with that baseline subtracted. C is choosing a loss function while believing it is choosing a target. It also calls 0.62 bp/h (σ of ΔS) "the whole prize"; you are paid the level, σ 0.80 bp/h.
3. Wrong: G. "O2 forecasts the premium — lower-persistence — so more turnover; the most expensive option." The comparable input is the premium *level* autocorr, 0.896, against the rate level's 0.886 — the premium is slightly *more* persistent. And F is a deterministic function of P, so turnover is identical either way. Separately, G's "Lighter is free, one-legged cost" reads zero fee fields as zero cost, dropping the thinner venue's spread and impact. 13 bp is a floor, not an estimate.
4. All seven missed: this is cross-sectional selection, not a time series. 100 symbols settle each hour; you trade the few widest spreads, never the average one. Every cost verdict here benchmarks against pooled σ = 0.80 bp/h — a symbol you will not hold. Needs computing: per hour, take the top-5 and top-10 symbols by |S_t| and report mean |S_{t+1}| and lag-1 persistence for that selected set, against pooled 0.80 bp/h. If selected carry is several bp/h, every "costs swamp the edge" conclusion above flips.

**Review 7:**
1. Strongest: B. Only seat to overturn a pinned assumption with checkable mechanics: Lighter's inner clamp binds at P<−0.04%/P>+0.06% — identical band to HL — so on the 36.46% off-baseline hours P is point-identified by inversion, interval-bounded elsewhere. O2 is censored regression, not missing data. It also derives the joint-baseline artifact. Caveat: the inversion assumes the formula runs on signed P, but the framing says sign comes from a separate `direction` field. Verify before building on it.
2. Biggest blind spot: C. Rejects O2 on Δpremium autocorr −0.042. Wrong statistic — mapping through the formula needs P_{t+1}, whose *level* autocorr is 0.896. C then picks a change target after itself showing changes have no autocorrelation, and never sees B's inversion route. Its leak warning and variant arithmetic (exact E[max]=1.948 at N=24) are the best work here.
3. Wrong inputs. (a) G's "Lighter is free," from `maker_fee`/`taker_fee`=0.0 on 235 markets. A zero-filled column beside an HL file with no fee column is a placeholder, not a fee schedule; G's one-legged 13 bp model and "only maker survives" all rest on it. (b) A's breakeven "2.5σ" is understated. From originals — σ=0.804 bp/h, ρ=0.699, 8 bp cost — 1/(1−ρ)=3.32 gives 3.0σ; A's own 10-hour multiplier 3.23 gives 3.1σ.
4. All seven missed: every seat frames this as per-row time-series forecasting. Phase 9 chooses *which of 100 symbols* to hold each hour — a cross-sectional selection problem. A rank-across-symbols target makes the atom harmless (ties) and the 1e-6 lattice a tie-break, not a floor. Should be computed: per-hour cross-sectional dispersion of S and rank persistence.

---

## 4. The orchestrator's verified numbers, tally and ledgers

# Orchestrator's verified material — target council

## WHICH LETTER WAS WHICH ADVISOR

- A = The Operator
- B = The Mechanism Hunter
- C = The Null Skeptic
- D = The Outsider
- E = The First Principles Thinker
- F = The Ruin Theorist
- G = The Frictionist

Bench seats drafted: Mechanism Hunter and First Principles Thinker — the skill's default pair
for a question with no obvious hinge ("why does this work" and "are you measuring the right
thing"). That draft is vindicated: the Mechanism Hunter was rated strongest by all seven
reviewers.

## VERIFIED NUMBERS TABLE (recomputed by the orchestrator; these supersede any figure in the responses or reviews)

| # | Claim | Who | Recomputed | Verdict |
|---|---|---|---|---|
| 1 | **"Lighter's premium is interval-censored, not missing — point-identified on the 36.46% off-baseline hours by inverting the published formula"** | B (Mechanism Hunter); 7 of 7 reviewers rated this the session's best finding | Off-baseline Lighter hours: **356,800 (36.46%)**. Inverting on the 337,905 rows whose market carries the 0.01% base rate gives an implied hourly premium with **zero violations** of the band the formula requires (every implied value falls outside [−0.04%, +0.06%], as it must). Implied Lighter premium correlates **0.845** with Hyperliquid's observed premium | **holds — and it overturns a PINNED ASSUMPTION in the orchestrator's own framing.** The pin said "Lighter's premium is not available for any historical hour." That is wrong for 36.46% of hours. Two caveats that survive: this recovers the hour's *final average* premium, not the intra-hour path (so Target C is unaffected and still needs the recorder), and on the other 63.54% the premium is only bounded to a known interval. Reviewers also corrected B's notation: the inversion is **P = 8·rate + 0.05** above baseline and **P = 8·rate − 0.05** below, not B's "±8·rate ∓ 0.05". |
| 2 | "Whenever both venues sit at baseline the spread is exactly +5e-7/hr, a pure truncation artifact — present on ~60% of hours" | B and E independently | **445,904 hours (45.57%)**, not ~60%. The spread there takes exactly **two** values: 5.0e-7 (the truncation artifact) and 1.25e-5 (the 27 Lighter markets whose base interest rate is 0). Mean **+5.029e-7** | **holds on mechanism, wrong on size — corrected to 45.57%.** |
| 3 | The mean spread is negative (−6.09e-8) yet the artifact is positive — one reviewer flagged this as an unexplained tension | reviewer 5 | Overall mean S = **−6.23e-8**. Mean S **excluding** joint-baseline hours = **−5.36e-7**, 8.6× larger and negative. The positive artifact on 45.57% of hours is masking a genuinely negative spread elsewhere | **resolved — the tension was real and is now explained.** Any Phase 6 skill score must exclude the artifact hours or it will be scoring arithmetic. |
| 4 | "Lighter is free — maker and taker fees are 0.0 on all 235 markets, so this is a one-legged cost problem" | G (Frictionist) | The field values are confirmed: **0.0 on all 235 markets**. The Hyperliquid market snapshot carries **no fee column at all** | **`unauditable — depends on whether that zero is a real fee schedule or an unpopulated column`.** Four reviewers independently called it a placeholder. G's entire "only maker-only survives" conclusion rests on it. Do not build a cost model on this number. |
| 5 | Break-even entry spread: A said "2.6 bp earned, ~2.5σ break-even"; reviewers corrected it to 4.3σ, 3.08σ, 3.1σ and 3.0σ — **four different answers** | A, then 6 of 7 reviewers | **The disagreement is entirely a convention, and both are defensible.** From σ(S)=0.804 bp/h and ρ=0.699: if you collect from t+1 onward (correct for funding, which settles at the end of each hour held), lifetime carry = σ·ρ/(1−ρ) = **1.87 bp**, break-even at 8 bp cost = **4.29σ**. If you also collect the hour you enter, = σ/(1−ρ) = **2.67 bp**, break-even = **3.00σ**. The 4.3 camp used the first, the 3.0–3.1 camp the second | **A's 2.5σ is wrong under both conventions.** Record that reviewers split on convention rather than crowning one. **Both are superseded by number 6, which is measured rather than modelled.** |
| 6 | **The cross-section, which four reviewers said the whole council mispriced** — everyone costed the trade off the pooled spread, but Phase 9 picks the widest spreads across ~100 symbols each hour | requested independently by 4 of 7 reviewers | On non-top-10 pairs (843,692 hours): pooled sd(S) = **0.8476 bp/h**, mean \|S\| = **0.1966 bp/h**. Selecting each hour's **widest 1** symbol by \|S\|: mean \|S\| = **2.562 bp/h (13.0× pooled)**; widest 5: **1.136 bp/h (5.8×)**; widest 10: **0.775 bp/h (3.9×)**. Cumulative signed carry after entering the widest-1: **1.83 bp (1h), 7.56 bp (6h), 18.91 bp (24h), 35.99 bp (72h), 61.64 bp (168h)**. Widest-5 at 24h: **10.02 bp** | **new, verified — and it reverses the council's "costs swamp everything" majority.** Label it precisely: in-sample, gross of all costs, and from a rule with no forecasting in it (trade the sign of a spread you can already see). |
| 7 | "Much of the 0.699 persistence may be a permanent per-symbol basis you could simply hold" | reviewer 2 | Between-symbol variance is **1.90%** of Var(S); within-symbol is **98.10%**. Lag-1 on symbol-demeaned S = **0.6985**, essentially unchanged from 0.7042 raw. Per-symbol mean S ranges −3.79 to +0.47 bp/h with sd 0.39 bp/h across symbols | **wrong — the hypothesis fails.** There is no durable per-symbol spread to sit in; you must round-trip. Recorded because it would have changed the horizon answer entirely had it held. |
| 8 | "Perfect-sign capture ≈ 0.798 × σ = 6.4e-5/hr → ~56%/yr gross ceiling" | B | B used the half-normal constant √(2/π)=0.798, but the spread is nothing like normal — it has a 45.57% spike at ~5e-7. Measured **E\|S\|/sd(S) = 0.2374**, so E\|S\| = **1.909e-5/hr** and the perfect-sign annual ceiling is **16.7%/yr gross on notional**, not 56% | **wrong — corrected to 16.7%/yr.** This is the council's single largest arithmetic error and no reviewer caught it: all seven inherited the normal-distribution reflex. B's qualitative conclusion (the money is in the off-baseline hours, not the atom) survives. |
| 9 | "134 of 142 clamp hours are at the negative cap — a handful of stress episodes where legs clamp together" | F (Ruin Theorist), flagged by F as unverified; one reviewer asked for it | 142 clamp hours across **23 symbols, 139 distinct hours, 42 distinct days**, worst day 12. So: clustered, but 42 episodes, not a handful. Mean \|S\| on those hours = **2.847e-3 = 28.5 bp/h, 149× the overall mean \|S\|** | **half right.** Clustering is real but weaker than F implied. |
| 10 | "The 142 clamp hours might hold most of the P&L" | D (Outsider) and F; one reviewer bounded it at ~1–6% | 142 × 28.5 bp = 4,047 bp against total Σ\|S\| of 978,572 × 0.191 bp = 186,908 bp → **2.2% of all gross spread** | **wrong as stated, and the reviewer's bound was right.** The clamp hours are a risk question, not a revenue question. |
| 11 | "Conditional std given a move = 6.97e-5/√0.46 ≈ 1.03e-4" | F | Measured **1.0281e-4** | **holds, exactly.** |
| 12 | **"Two perps on two venues are not a hedge: P&L = funding differential PLUS the change in the mark basis"** — raised by 2 reviewers, measured by nobody | reviewers 3 and 4 | Hyperliquid-minus-Lighter mark basis, hour-close against hour-close, 564,391 hours across 96 symbols from 2025-08-25: mean **+3.36 bp**, lag-1 autocorrelation **0.9342** (a genuine persistent basis, not noise). Per-symbol sd: median **13.4 bp**; BTC alone **4.9 bp**. **Change in the basis over a hold: sd 19.5 bp (1h), 22.3 bp (24h), 27.4 bp (72h), 39.8 bp (168h). It exceeds ±18.91 bp — the entire 24-hour carry from the widest spread — on 17.7% of 24-hour holds.** | **new, verified, and it is the largest single finding of this council.** One label matters: this is measured on *mark* prices, and the two venues mark off different references (Hyperliquid oracle, Lighter index), so part of the basis is a convention difference rather than realizable P&L. The realizable figure needs traded prices, which are not on disk. A first pass produced 95 bp by comparing an hour-median to an hour-close; that was a timing artifact and was discarded. |
| 13 | "0.5986 × 0.9014 = 0.5396 ≈ the 54.01% zero share; 0.5401 × 0.6041 = 0.3263 against 40.27% observed" | C (Null Skeptic) | Both reproduce | **holds.** The second gap is real dependence: the two venues sit in their dead zones together more often than independence predicts. |
| 14 | Hyperliquid's settled funding grid is not uniform over its full history | orchestrator | **1,789 eight-hour intervals** (the pre-2023-06-08 era) and **213 single-hour holes across 141 of 234 markets**, out of 4,676,131 intervals. All outside the two-venue window. Lighter has **zero** irregular intervals in 1,585,452 | **new, verified.** Only bites if Target A is built on Hyperliquid's full history back to 2023, which two Council-1 reviewers argued it should be. A naive one-row shift would silently pair rates 2 or 8 hours apart. |
| 15 | Both venues' outer clamps | framing | Hyperliquid's ±0.04 clamp binds on **0 of 978,572** hours (max \|rate\| 2.26e-2). Lighter's binds on **142** | **holds.** |

## VERIFIED CONVERGENCE TALLY (counted from the actual text, not from memory)

**Reviewer-level (7 reviewers):**
- Strongest response: **B — 7 of 7. Unanimous.** No other response received a vote.
- Biggest blind spot: **F — 3 of 7** (a fourth named D "with F sharing it"); **C — 3 of 7**; D — 1 of 7.
- Wrong number, Q3: **A's break-even multiplier — 6 of 7** (and they produced four different corrections; see verified number 5). **G's "Lighter is free" — 4 of 7.** G's "O2 means more turnover" — 2 of 7. E's linear-carry table — 2 of 7.
- Q4 blind spots raised by **two or more** reviewers:
  - **This is cross-sectional selection, not time-series forecasting — you trade the widest few of 100 symbols each hour, never the pooled average — 4 of 7.**
  - **Two perps on two venues are not a hedge; the real ruin channel is mark divergence and one-leg liquidation, and no target measures it — 2 of 7.**
  - **Everything is treated as a pooled property of 978k rows when it is per-symbol — 2 of 7.**

**Advisor-level (7 advisors, counted from their own text):**
- **"You are paid the level, not the change" — 5 of 7 independently (A, B, D, E, G).**
- A level-based target rather than a difference — **5 of 7** (A, B, D, E, G). C picks a differential; F picks the hurdle.
- **"The one-hour horizon is wrong; the horizon must be set by the cost model" — 6 of 7 (all but F).**
- **"Keep the clamp hours, do not drop them" — 5 of 7 (A, B, D, F, G).** None of the seven argued for dropping them.
- **"No cost model exists and that fact dominates the target choice" — 5 of 7 (A, C, E, F, G).**
- O2 (forecast the premium) rejected on "Lighter's premium history is days long" — **4 of 7 (A, C, E, F)**, and verified number 1 shows that premise is false.

## BLIND SPOTS THAT MUST BE ADDRESSED (raised independently by two or more reviewers)

1. Cross-sectional selection — now measured, verified number 6. It reverses the cost verdict.
2. The trade may not be hedged at all — now measured, verified number 12. The unhedged price term is the same size as the entire funding edge.
3. Pooled statistics are hiding per-symbol heterogeneity — partially measured, verified number 7 (between-symbol variance is only 1.9%) and number 9 (23 of 100 symbols produce every clamp hour).

## SINGLE-SOURCE FINDINGS THAT WOULD CHANGE THE RECOMMENDATION'S DIRECTION (carry these, flagged as single-source)

- One reviewer: **the panel is unbalanced and listing-selected** — 978,572/100 ≈ 9,786 hours per symbol against ~14,640 in the window, so roughly a third of the grid is absent and the sample tilts toward long-listed majors, while newly-listed symbols (where funding is most extreme) are underweighted. Not yet measured.
- One reviewer: **a rank-across-symbols target** would make the 40% atom harmless (it becomes ties) and the 1e-6 lattice a tie-break rather than a floor. This dovetails with verified number 6 and nobody else proposed it.
- One reviewer: **cost is paid on flips, not on hours** — turnover, not horizon, is the free parameter. This reframes the entire break-even argument.

---

## 5. The verdict

> Written by the chairman agent, which received the framed question, all seven de-anonymized
> responses, all seven reviews, the letter→advisor mapping, the verified numbers table and the
> verified convergence tally. The orchestrator checked it afterwards against the table, the
> tally and the ledgers before recording it; no discrepancy was found.

## Council Verdict: What Phase 4 Should Forecast

### Where the Council Agrees
- **You are paid the level, not the change — 5 of 7 advisors independently** (A, B, D, E, G), and the same 5 pick a level target. C picks a difference, F picks the hurdle. This is the session's strongest signal.
- **One hour is the wrong horizon — 6 of 7** (all but F).
- **Keep the clamp hours — 5 of 7, and none of the seven argued for dropping them.**
- **"No cost model exists and that dominates the choice" — 5 of 7.** Caveat: five of nine seats lean cautious by construction, and that draft produced a "costs swamp the edge" majority which the measured cross-section (below) reverses. Read this consensus as partly a seating artifact.
- All 7 reviewers rated B strongest; no other response got a vote. Unanimous — and earned, because B overturned a pinned assumption rather than reasoning inside it.

### Where the Council Clashes
- **O2's viability.** 4 of 7 (A, C, E, F) killed it on "Lighter's premium history is days long." **That premise is false.** Lighter's premium is interval-censored, not missing: point-identified on **356,800 off-baseline hours (36.46%)** by inverting `P = 8·rate + 0.05` above baseline and `8·rate − 0.05` below — zero band violations, correlation **0.845** with Hyperliquid's premium — and interval-bounded elsewhere. O2's input is a 20-month sample. (The intra-hour path still needs the live recorder.)
- **Break-even entry spread.** 6 of 7 reviewers corrected A's "2.5σ" and produced four different answers. Two defensible conventions, both reported: collect from t+1 (correct for funding, which settles at hour end) → 1.87 bp lifetime carry, **4.29σ**; also collect the entry hour → 2.67 bp, **3.00σ**. A's 2.5σ is wrong under both. Both are superseded by measurement.
- **The error no reviewer caught.** B's "56%/yr gross ceiling" applied a normal-distribution constant to a distribution with a 45.57% spike. Measured E|S|/sd = 0.2374 → **16.7%/yr gross**. All seven inherited the reflex. B's qualitative point (the money is off-baseline, not in the atom) survives.
- **Two errors that pass every arithmetic check.** F called the clamped state "unbounded on one side" when the clamp is precisely what bounds it. A said O4's "will it move" leg is "90% right by saying no forever" — 90.14% is *conditional* on sitting in the dead zone; unconditionally the do-nothing answer is 54.01%.
- **G's "Lighter is free"** (maker/taker = 0.0 on all 235 markets) — 4 of 7 reviewers called it a placeholder. **Unauditable**: the field values are real, their meaning is not, and Hyperliquid's snapshot carries no fee column at all. Do not build a cost model on it.

### Blind Spots the Council Caught
1. **This is cross-sectional selection, not time-series forecasting** (4 of 7 reviewers). Widest-1 symbol per hour: mean |S| **2.562 bp/h, 13.0× pooled** (0.1966); widest-5 1.136; widest-10 0.775. Cumulative carry entering widest-1: **1.83 bp (1h), 7.56 (6h), 18.91 (24h), 61.64 (168h)**; widest-5 at 24h = 10.02 bp. In-sample, gross of all costs, from a rule with no forecasting in it. This is what reverses the cost verdict.
2. **Two perps on two venues may not be a hedge** (2 of 7). Mark basis: mean +3.36 bp, lag-1 0.934, per-symbol sd median 13.4 bp. Change over a hold: **sd 19.5 bp (1h), 22.3 (24h), 39.8 (168h)** — it exceeds the entire 24-hour carry on **17.7% of 24-hour holds**. Label that matters: measured on *mark* prices, and the venues mark off different references, so part is convention, not realizable P&L.
3. **Pooled numbers hide per-symbol structure** (2 of 7) — half-confirmed. There is **no durable per-symbol spread to sit in**: between-symbol variance is 1.9% of Var(S), lag-1 on demeaned S 0.6985 vs 0.7042 raw. You must round-trip. But 23 of 100 symbols produce all 142 clamp hours.

**Single-source, carried:** (a) the panel is listing-selected — ~9,786 hours per symbol against ~14,640, a third of the grid absent, majors overweighted; unmeasured. (b) A **rank-across-symbols target** makes the 40% atom harmless (ties) and the 1e-6 lattice a tie-break. (c) **Cost is paid on flips, not hours** — turnover, not horizon, is the free parameter.

### The Number That Decides This
**Basis drift over the hold: 22.3 bp standard deviation at 24 hours, against 18.91 bp of 24-hour carry from the widest spread.** Measured — but on mark prices with a known convention contaminant; the realizable figure needs traded prices not on disk. **Flip point:** if the traded-price version lands under ~6 bp at 24h (under a third of carry), build the funding target below. If it stays near 22 bp, the primary target must be funding *minus* basis, and this is a basis-risk project wearing funding-research clothes.

### The Recommendation
**O3, on the spread, reframed.**
- **Target:** expected cumulative spread level over the hold, `E[Σ_{h=1..H} S_{t+h} | info at t]`, **ranked across symbols each hour** — not a per-row regression.
- **O2 becomes the feature pathway, not a rival target:** Hyperliquid's observed premium plus Lighter's inverted/censored premium, pushed through both published formulas.
- **O1 loses:** ΔS has −0.177 autocorrelation and a 40.27% atom manufactured by differencing. Nobody is paid a change.
- **O4 loses as a target:** "will it move" is deterministic given the premium — it is O2 with the information deleted first. Keep F's sizing insight (an MSE fit returns P(move)·E[y|move]) at the *sizing* stage.
- **Score against no-change, and exclude joint-baseline hours** (45.57%, where S takes exactly two values, +5.0e-7 or 1.25e-5). Mean S excluding them is −5.36e-7 — 8.6× larger and negative. Score them and Phase 6 grades arithmetic.
- **Clamp hours: keep, tag, exclude from tuning.** 142 hours, 23 symbols, 42 distinct days — clustered, but 42 episodes, not a handful. Mean |S| 28.5 bp/h (149×) yet only **2.2% of gross spread**: a risk and capacity question, not a revenue one.
- **Horizon: not one hour.** Make H a parameter; run 6h and 24h as primary; fix it from the cost model and the basis number. Carry accrues hourly, cost is paid per flip.
- If Target A is ever built on Hyperliquid's full history back to 2023: 1,789 eight-hour intervals and 213 single-hour holes mean a naive one-row shift silently pairs rates 2 or 8 hours apart.

### The One Thing to Do First
**Extend the existing recorder, today, to capture synchronized top-of-book on both venues at the settlement timestamp for the 20 widest-|S| symbols** — and harden it in the same change (it currently has no alerting, S3 uploads off, one disk, so an unmonitored recorder will simply not produce the measurement). Two weeks gives the traded-price basis drift.
- Under ~6 bp sd at 24h → build the target as recommended, H = 24.
- Near 22 bp → the primary target becomes funding net of basis, and Phase 9 sizes nothing until it exists.
