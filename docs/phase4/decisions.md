# Phase 4 decisions — target construction

Every decision Phase 4 makes, the reasoning behind it, and the ones deliberately deferred with
the trigger that reopens them. Written 2026-09-22.

**How these were reached.** The target-definition questions went to a quant-council session
(seven advisors with clashing priors, seven blind peer reviewers, a chairman), run as a separate
session from the Phase 3 universe council because the two do not share a frame. The full record
is in
[`../phase3/council-transcript-2026-09-22-targets.md`](../phase3/council-transcript-2026-09-22-targets.md).
Every figure below was measured on this repo's own data.

The implementation plan is
[`../superpowers/plans/2026-09-22-phase4-targets.md`](../superpowers/plans/2026-09-22-phase4-targets.md).

---

## Read this first: three facts that changed the question

The Phase 4 brief handed to the council said "funding clamps censor the target". Measurement
showed the premise was substantially wrong, and correcting it reframed everything.

**1. `0.0000125` is a plateau, not a floor, and nothing is censored.** Hyperliquid's settled
rate sits exactly on it for **59.86%** of concurrent pair-hours — but sits **strictly below it
for 34.00%** and is **negative for 23.98%**. Rates pass straight through. The plateau is made by
the *inner* clamp, which cancels the premium exactly whenever the premium lies inside a band of
width 0.001. That is a **dead zone**, a deterministic non-invertible transform, not censoring:
the settled rate is fully observed. What is not identified inside the band is the premium — and
Hyperliquid publishes the premium for **100%** of rows.

**2. The outer clamps barely bind, and one never does.** Hyperliquid's ±0.04 hourly clamp binds
on **0 of 978,572** hours (largest observed magnitude 2.26e-2). Lighter's binds on **142 hours
(0.0145%)**, across 23 symbols, 139 distinct hours, 42 distinct days.

**3. Lighter's historical premium is recoverable for 36.46% of hours.** This overturns a pinned
assumption in the council's own framing and a statement in `docs/phase1/handoff.md` §2. The
published formula inverts: above baseline `P = 8·rate + 0.05`, below baseline `P = 8·rate −
0.05`, in percent. Applied to the **337,905** off-baseline rows on markets carrying the 0.01%
base rate, **zero** implied values violate the band the formula requires, and the implied
Lighter premium correlates **0.845** with Hyperliquid's observed premium. On the other 63.54% the
premium is **bounded to a known interval** rather than unknown — that is censored regression,
not missing data. Four of seven advisors rejected the premium-based option on the false premise
that Lighter's premium history is days long; all seven reviewers named the advisor who caught
this the strongest of the session.

**Two caveats that survive intact:** this recovers the hour's *final average* premium, not the
intra-hour path, so **Target C is unaffected and still needs the live recorder**; and the
inversion assumes the formula runs on signed premium, which must be confirmed against the
`direction` field on a sample before anything is built on it.

> ### ⚠️ DISPUTED — do not implement D1's premium pathway on this premise
>
> **Flagged 2026-09-22, after the council session, by the orchestrating session.** The
> *measurement* above reproduces exactly: 337,905 off-baseline rows, correlation **0.8450**
> against Hyperliquid's observed premium, zero band violations. The *interpretation* does not
> survive.
>
> `P = 8·rate ± 0.05` is a **strictly monotone transform of `signed_rate_fraction`**, a column
> already on disk. Re-measured on the identical 337,905 rows:
>
> | Series vs HL observed premium | Pearson | Spearman |
> |---|---|---|
> | Inverted "premium" `P` | 0.8450 | 0.6233 |
> | Raw Lighter `signed_rate_fraction` | **0.8575** | **0.6233** |
>
> The raw rate correlates **better**, and the rank correlations are **identical to four
> decimals** — which is the proof rather than the evidence: a monotone map cannot change
> rank-order, so the inversion carries **zero additional information**. It is the same
> observation in different units.
>
> Therefore this does **not** overturn `docs/phase1/handoff.md` §2. That section claims no
> *independent* historical premium observation exists on Lighter. Re-deriving the premium from
> the rate through Lighter's own published formula is not an independent observation, and
> Phase 1's claim stands as written.
>
> **What this changes.** D1's "feature pathway" (line ~85) and deferred item F5 both rest on the
> inverted premium being new information. It is not. Any model consuming `P` is consuming
> `signed_rate_fraction` through a link function — which may still be a reasonable modelling
> choice on *scale* or *interpretability* grounds, but must be justified as that, not as
> recovered data. F6's `direction` check is now moot for this purpose: confirming the inversion
> is self-consistent cannot make it informative.
>
> **What survives.** The interval-bounding argument on the other 63.54% is likewise a
> restatement of the rate plus published parameters, not a new observation. The clamp and
> plateau findings in items 1, 2 and 4 of this section were independently re-measured and
> **hold exactly** (59.86% / 34.00% / 23.98%; outer clamp binds 0 of 978,572).
>
> Workings: `/private/tmp/.../scratchpad/` this session; reproduce with the rank-correlation
> comparison above on `hl_funding.premium` against `lighter_funding.signed_rate_fraction`.
>
> ---
>
> **Independent review, 2026-09-22 — dispute upheld, plus two further defects.**
>
> A third session re-measured this from the raw parquet and confirmed the dispute: `P` is
> strictly increasing in the rate across all **3,049** distinct observed rate values, so the
> identical Spearman is definitional, not evidence. It added:
>
> 1. **The "zero band violations on 337,905 rows" validation is a tautology and must be
>    deleted.** Branch selection is `rate > baseline` vs `rate < baseline`; the band condition
>    `P > I + 0.05` / `P < I − 0.05` reduces to *exactly that same inequality*. It cannot fail,
>    on any data, ever. It is zero evidence the inversion is correct.
> 2. ~~**The inversion is mis-calibrated by ~2×**~~ — **RETRACTED 2026-09-22, see the box below.**
>    The original text is kept for the record:
>
>    **The inversion is mis-calibrated by ~2×, and this IS testable** — the one falsifiable
>    restriction the monotone-transform argument does not cover, because it concerns *scale*,
>    not rank. Regressing HL's premium (percent) on Lighter's 8-hour funding gives slope
>    **0.9900**, so the ×8 conversion and units are right. But a regression-discontinuity
>    estimate at the baseline crossing (±0.004 window, n = 6,880 / 22,803) puts the jump in HL's
>    premium at **+0.0500**, converging as the window narrows. The published inversion asserts a
>    jump of **0.10** (−0.05 → +0.05), which at slope 0.99 should read ≈0.099. It reads 0.050.
>    So `funding_clamp_small_pct = 0.05` is the **full** dead-zone width, not the half-band, and
>    the correct inversion is **`P = 8·rate ± 0.025`**. This also mechanically explains the
>    Pearson gap — the stated inversion injects a discontinuity twice the real size, which is
>    why 0.8575 falls to 0.8450.
>
> ### ✅ F6 RUN AND SETTLED, 2026-09-22 — the ±0.025 correction above is WRONG and is retracted
>
> F6 turned out to be runnable today, free, with no recorder: Lighter's public websocket
> (`wss://mainnet.zklighter.elliot.ai/stream`, channel `market_stats`) serves `premium`,
> `current_funding_rate`, `base_interest_rate`, `funding_clamp_small` and `funding_clamp_big`
> **in one message, per market**. One ~25-second snapshot of all 234 markets settles it.
>
> Scored forward — feed each market's live premium and interest through
> `src/fundr/funding/lighter_formula.py` and compare the predicted rate against the venue's own
> published rate — under the two rival readings. Reproduced independently by the orchestrating
> session:
>
> | reading | crypto (multiplier 100) | equity/RWA (multiplier 50) | disputed band, crypto |
> |---|---|---|---|
> | **`clamp_small = 0.05` as the half-band (what the code ships)** | **100/136 exact**, MAE 0.00061 | 62/96, MAE 0.00065 | **10/13 correct** |
> | `0.025` half-band (the retracted correction) | 89/136, MAE 0.00140 | 63/96, MAE **0.00022** | **0/13 correct** |
>
> In the band where the two readings actually disagree the result is 10–0. **The shipped formula
> is right for every one of the 100 matched pairs**, all of which are multiplier-100 crypto
> markets, and no change to the inversion is warranted.
>
> **Why the regression-discontinuity estimate misled.** It measured the jump in *Hyperliquid's*
> premium at *Lighter's* baseline crossing — a cross-venue proxy standing in for a venue-specific
> parameter. There is no reason those two quantities should coincide, and they do not. The
> forward test uses Lighter's own premium against Lighter's own rate, from the same message, and
> is decisive where the RD was indirect.
>
> **One real finding survives, and it is new.** The multiplier-50 equity/RWA markets fit the
> *halved* bound better (MAE 0.00022 against 0.00065), which is consistent with the effective
> clamp scaling with `funding_premium_multiplier` — 0.05 × 1.0 for crypto, 0.05 × 0.5 for
> multiplier-50. That is exactly the "whether the multiplier matters for RWA/Pre-IPO markets is
> untested" open issue flagged in `lighter_formula.py`'s docstring. It does not touch Target B
> (no matched pair is multiplier-50) but it **does** touch Target A's Lighter per-venue view,
> which spans 96 multiplier-50 markets. Do not apply the crypto-calibrated formula there.
>
> Caveat: this scores the venue's *running* rate against a same-instant premium, not settled
> funding. The 36 crypto misses are small and look like timing skew between the two fields, not a
> different clamp. A ~3-minute capture across a settlement boundary would close that last gap,
> also free.

> **So F6 is NOT moot** (as the block above states) — it should be reinstated as a **calibration**
> check rather than a consistency check. And the interval-bounding argument on the 63.5% fails
> for an additional reason: the implied interval is a *constant* for every market at base 0.01%,
> so a Tobit on `P` is algebraically the same model as a Tobit on the rate plus an at-baseline
> dummy.

---

## D1 — Target B is the **expected cumulative spread level over the hold**, ranked across symbols

*Council decision. 5 of 7 advisors independently reached "you are paid the level, not the
change"; the same 5 picked a level target.*

$$ Y_t = \mathbb{E}\!\left[\textstyle\sum_{h=1}^{H} S_{t+h} \;\middle|\; \mathcal{F}_t \right],
\qquad S_t = F_t^{HL} - F_t^{Lighter} $$

scored as a **cross-sectional ranking across the symbols live in hour `t`**, not as a per-row
regression, **and scored against the rank-by-current-`S_t` benchmark defined below**.

> ### Two corrections, added 2026-09-22 after independent review
>
> **1. This is a redefinition of the design's Target B, and it must be reported as one.**
> `docs/funding_research_design.md` defines Target B as "next change in the Hyperliquid–Lighter
> funding spread" and asks whether the current state predicts the subsequent *movement* of the
> spread. `Y_t` above is the *cumulative level over a hold*, ranked cross-sectionally — the
> trading question, and arguably the better one, but not the design's question. At H = 1 the two
> are informationally identical (`S_t` is in `F_t`, so the choice is purely a loss-function
> choice and the argument below is correct). **At H = 6 and H = 24 they are different
> quantities**, dominated by the persistent component of `S` (lag-1 0.699) which is largely
> readable off `S_t` itself. Phase 4's results must say plainly that Target B was redefined, so
> that a reader comparing against the design is not misled.
>
> **2. Target B had no benchmark. It now has one, and it is mandatory.** D2 requires Target A to
> beat no-change. D1 as originally written specified *no* benchmark for the cross-sectional
> ranking — so the target could score well having learned nothing but persistence. Worse, the
> council's own motivating table ("trade each hour's widest spread → mean |S| 2.562 bp/h, 13.0×
> pooled") **is that null**, measured in-sample with no forecasting in it, and it was presented
> as motivation rather than as the bar to clear.
>
> **The benchmark is: rank symbols by current `S_t`.** Report the model's cross-sectional score
> against it on every run, exactly as Target A reports against no-change. A model that does not
> beat rank-by-`S_t` has not demonstrated forecasting ability, whatever its absolute score.

**Why the level, not the change.** The trade holds a position and receives the funding
differential as an hourly cash flow. `S_t` is known at decision time, so `S_{t+1} − S_t` carries
identical information with a corrupted loss function: it forces the model to spend capacity on a
**40.27% point mass at exactly zero** that differencing manufactured. The change has lag-1
autocorrelation **−0.177**; the level has **0.699**.

**Why ranked across symbols.** Four of seven reviewers independently said the council had
mispriced the whole trade by working from the pooled spread, when Phase 9 must choose *which* of
~100 symbols to hold each hour. Measured on non-top-10 pairs (pooled mean |S| = 0.1966 bp/h):

| Selection | Mean \|S\| | vs pooled |
|---|---|---|
| widest 1 symbol per hour | **2.562 bp/h** | **13.0×** |
| widest 5 | 1.136 bp/h | 5.8× |
| widest 10 | 0.775 bp/h | 3.9× |

A ranking target also makes the atom harmless — tied zeros are ties — and turns Lighter's 1e-6
reporting lattice into a tie-break rather than a floor.

**Why not the alternatives.**

- *Forecast the premium and map it through the formula.* Kept, but as the **feature pathway**,
  not as a rival target: Hyperliquid's observed premium plus Lighter's inverted premium, pushed
  through both published formulas. As a target it adds nothing, because the settled rate is an
  exact function of the premium.
- *A hurdle model ("will it move?" then "by how much?").* Rejected. Given the premium, "will it
  move" is **deterministic** — a hurdle model is the premium pathway with the information
  deleted first. One advisor's supporting claim that the hurdle's first leg is "90% right by
  saying no forever" is also wrong: 90.14% is *conditional* on already sitting in the dead zone;
  unconditionally the do-nothing answer is right **54.01%** of the time. The genuinely useful
  part of the hurdle argument — that a squared-error fit returns `P(move) × E[y | move]`, which
  is a blend — belongs at the **sizing** stage, not in the target.
- *A log ratio.* Impossible. At least one venue's rate is ≤ 0 on **33.61%** of concurrent
  pair-hours.

---

## D2 — Target A: the same shape, per venue

$$ Y_t^{venue} = \mathbb{E}\!\left[\textstyle\sum_{h=1}^{H} F_{t+h}^{venue} \;\middle|\;
\mathcal{F}_t\right] $$

scored against the no-change benchmark. Target A is per-venue and uses Phase 3's `per_venue`
panel view, not the cross-venue intersection.

**The trap in a level target, and how it is defused.** Hyperliquid's rate level has lag-1
autocorrelation **0.886**, so a model that predicts "same as last hour" scores roughly 0.79 R²
and has learned nothing. Two advisors flagged this as the most dangerous property of a level
target, and they were right. Every level score is therefore reported **against the no-change
benchmark**, never as a raw R².

**The data hazard, measured here and contradicting `datasets.md`.** Hyperliquid's settled
funding is not a uniform hourly grid over its full history: **1,789 eight-hour intervals**
(funding was 8-hourly until 2023-06-08) and **213 single-hour holes across 141 of 234 markets**,
out of 4,676,131 intervals. All of it is outside the Lighter window so Target B is untouched —
but a naive one-row shift on Hyperliquid's full history silently pairs rates 2 or 8 hours apart
on 2,002 occasions. Every target is therefore built on an explicit hour-difference join, never
on `shift`. Lighter has **zero** irregular intervals in 1,585,452.

---

## D3 — Exclude joint-baseline hours from every skill score

*Council decision. Raised independently by two advisors, then measured.*

**45.57% of concurrent pair-hours (445,904) have both venues sitting at their own baseline**, and
on those hours the spread takes exactly **two** values: `+5.0e-7` (Hyperliquid's `1.25e-5`
against Lighter's `1.20e-5`, the latter being `0.00125%` truncated to four decimal places) and
`1.25e-5` (the 27 Lighter markets whose base interest rate is 0). Mean **+5.029e-7**.

That is arithmetic, not economics. A model scored on those hours is being graded on a rounding
rule.

**And the artifact is masking the real sign.** Overall mean `S` = **−6.23e-8**. Mean `S`
**excluding** joint-baseline hours = **−5.36e-7** — 8.6× larger, and negative. One reviewer
flagged the tension between a positive artifact and a negative overall mean; this resolves it.

Rule: the hours stay in the dataset with a `joint_baseline` flag, and **every Phase 6–8 skill
metric is computed with them excluded**. They may be reported separately as a
zero-turnover known-sign carry of about **0.44%/year**, which is the floor any model must beat.

---

## D4 — Clamp hours: **keep, tag, exclude from tuning**

*Council decision. 5 of 7 advisors said keep; none of the seven argued for dropping them.*

The 142 hours where Lighter's outer clamp binds carry a mean |spread| of **2.847e-3 = 28.5
bp/hour, 149× the overall mean**. They are where the spread is widest and where the mechanism is
most visible.

But they are **not** where the money is. One reviewer bounded this and was right: 142 × 28.5 bp
against a total gross spread of 186,908 bp is **2.2% of all gross spread**. They are a risk and
capacity question, not a revenue one — so they must not be dropped (that would fit on a sample
conditioned on the event never happening) and must not be tuned on (a squared-error fit would
bend the whole model to chase them).

Rule: keep, flag `venue_clamped`, exclude from hyperparameter selection, report performance on
them separately.

**One characterisation to retire.** These hours were described in council as "unbounded on one
side". They are the opposite: the clamp is precisely what bounds them, at ±0.005 per hour on
Lighter. Funding is also a *signed cash flow*, so a large spread is a payday when you are on the
receiving side, not a mark-to-market gap. Clustering is real but weaker than argued — 42
distinct days, worst day 12, not a single episode.

---

## D5 — Horizon: **a parameter, not one hour. Primary runs at H = 6 and H = 24.**

*Council decision. 6 of 7 advisors said the one-hour horizon is wrong.*

The design's one-hour horizon is an artifact of the settlement frequency, not of economics.
Carry accrues hourly; cost is paid once per position flip. Measured, on non-top-10 pairs:

- Spread half-life **1.98 h** pooled, **2.23 h** per-pair median; sign-run median **2 h**, 95th
  percentile 29 h.
- Cumulative gross carry after entering the widest-|S| symbol: **1.83 bp (1h), 7.56 bp (6h),
  18.91 bp (24h), 35.99 bp (72h), 61.64 bp (168h)**. Widest-5 at 24h: 10.02 bp. *In-sample,
  gross of all costs, from a rule with no forecasting in it.*

H is therefore a parameter fixed by the cost model and by D6, not chosen now. 6 and 24 are the
primary runs.

**A measurement the council could not agree on, recorded with both conventions.** Six of seven
reviewers corrected one advisor's break-even entry spread and produced four different answers.
The disagreement is entirely a convention and both are defensible. From σ(S)=0.804 bp/h and
ρ=0.699: collecting from `t+1` onward (correct for funding, which settles at each hour's end)
gives lifetime carry **1.87 bp** and break-even **4.29σ** at an assumed 8 bp cost; also
collecting the entry hour gives **2.67 bp** and **3.00σ**. Both are superseded by the measured
carry figures above, which need no AR(1) approximation.

**One arithmetic error no reviewer caught, corrected here.** The perfect-sign annual ceiling was
put at 56%/year using the half-normal constant √(2/π) = 0.798. The spread is nothing like
normal — it has a 45.57% spike. Measured E|S|/sd = **0.2374**, so the ceiling is **16.7%/year
gross on notional**. All seven advisors and all seven reviewers shared the normality reflex;
this is exactly the failure mode where convergence on a *method* is almost no evidence at all.

---

## D6 — The open question that outranks the target: **is this trade hedged at all?**

*Raised by two reviewers, measured by nobody until now. It is the largest finding of the
session.*

A delta-neutral cross-venue position's P&L is the funding differential **plus** the change in
the price basis between the two venues over the hold. Nobody had measured the second term.

Measured, Hyperliquid-minus-Lighter mark basis, hour-close against hour-close, 564,391 hours
across 96 symbols from 2025-08-25:

- Level: mean **+3.36 bp**, lag-1 autocorrelation **0.9342** (a genuine persistent basis),
  per-symbol sd median **13.4 bp** (BTC alone 4.9 bp).
- **Change over a hold: sd 19.5 bp (1h), 22.3 bp (24h), 27.4 bp (72h), 39.8 bp (168h).**
- It exceeds ±18.91 bp — **the entire 24-hour carry from the widest spread** — on **17.7% of
  24-hour holds**.

**The label that matters:** this is measured on *mark* prices, and the two venues mark off
different references (Hyperliquid oracle, Lighter index), so part of it is a convention
difference rather than realizable P&L. The realizable figure needs traded prices, which are not
on disk. (A first pass produced 95 bp by comparing an hour-median to an hour-close; that was a
timing artifact and was discarded.)

**This does not block Phase 4** — the targets above are built either way. It decides whether the
*primary* target stays "funding spread" or becomes "funding spread net of basis", and Phase 9
sizes nothing until it is resolved.

> ### Re-measured on the repaired candles, 2026-09-22 — supersedes every number above
>
> The earlier measurements ran on a `lighter_mark_candles` series missing 337,412 bars in
> ~200-hour blocks. That series is fixed (1,389,772 rows, no internal gaps) and the basis has
> been re-measured on it: **778,597 concurrent pair-hours across the same 96 symbols**,
> 2025-08-25 to 2026-09-19, Hyperliquid's last per-minute `mark_px` in the hour against
> Lighter's hourly mark close. The concern in D6 is still legitimate and F2 still deserves to
> exist; the size of the concern is now smaller and much better understood.
>
> **Alignment, checked first because it has burned two sessions.** Hour-close against hour-close
> at zero offset gives level lag-1 **0.9894**; shifting either side by an hour drops it to 0.65.
> The known artifact reproduces exactly as described — Hyperliquid's hour-*median* against
> Lighter's hour-*close* gives a 24h drift sd of **98.0 bp** — and is discarded.
>
> **The headline: 24-hour drift is ~20 bp of standard deviation, not 30.** On the cleaned panel
> (frozen marks out, each new listing's first 14 days out — 764,264 hours, 94 symbols) drift sd
> runs **18.4 bp (1h) → 19.4 (6h) → 20.8 (24h) → 23.8 (72h) → 30.2 (168h)**, with median
> |24h drift| **7.53 bp**. Dropping the two chronic dislocators (XPL, MON) gives 19.2 bp at 24h.
> **The repair did not produce that improvement — the cleaning did.** Treated the old way, with
> nothing excluded, the repaired panel gives **38.0 bp** at 24h, *higher* than the 29.9 bp on
> record, because the extra hours are disproportionately new-listing hours.
>
> - **29.9 bp at 24h → superseded.** 20.8 bp cleaned, 38.0 bp unfiltered. Median |24h drift|
>   **7.58 → 7.53 bp: confirmed**, and it is the stable number here — it barely moves under any
>   filter, repair or convention.
> - **The council's 22.3 bp is no longer unreproducible.** The cleaned conventions now span
>   19.2–21.4 bp pooled and 17.3 bp as the per-symbol-sd median, so 22.3 sits at the edge of the
>   defensible range rather than outside it. It was still not derived; it is right by coincidence
>   of convention.
> - **17.7% of 24h holds exceeding ±18.91 bp: confirmed, and it is remarkably stable** — 17.0% to
>   18.0% under every filter tried, split almost evenly between the two directions (8.7% / 8.7%).
> - **The "persistent basis" remains an artifact, but of a different thing.** Unfiltered lag-1
>   **0.9894** (confirmed). Of the five frozen Hyperliquid markets previously named, only **AI**
>   is in the concurrent panel at all — MKR, LAUNCHCOIN, AI16Z and YZY never overlap Lighter, so
>   they never entered this statistic. Excluding frozen marks alone takes level sd from 158 bp to
>   50.5 bp, not to 14.6. What carries the rest is **XPL and MON**; without them level sd is
>   **14.9 bp** and lag-1 **0.26** — reproducing the prior figures (14.6 bp, 0.28) through a
>   different mechanism. Within-symbol persistence is low either way: the per-symbol median lag-1
>   on the cleaned panel is **0.196**.
> - **Mean reversion: confirmed and slightly stronger.** Drift sd grows as **t^0.088** on the
>   cleaned panel (t^0.159 unfiltered), against t^0.5 for a random walk. A random walk from the
>   1-hour figure would put 24h drift at 90 bp; it is 20.8. Basis risk **does not compound over a
>   hold**, so lengthening H buys carry without buying proportionate basis risk.
>
> **What nobody had checked: the risk is concentrated in newly listed markets, and Phase 3's
> universe rule steers toward exactly those.** By age of the pair, 24h drift sd and the share of
> holds exceeding the carry go: first week **90.0 bp / 29.7%**, weeks 2–4 **39.7 bp / 21.8%**,
> months 1–3 **26.6 bp / 23.1%**, three months and older **17.3 bp / 16.0%**. For markets that
> actually list inside the window the first week is **146 bp / 56.3%**. XPL's basis averaged
> +1,747 bp in its first month and MON's +120 to +190 bp for two months, both settling to single
> digits afterwards. Against that, **BTC/ETH/SOL run a level sd of 7.1 bp, a 24h drift sd of
> 9.8 bp, and exceed the carry on 5.0% of holds.** This is a universe rule, not a noise term:
> excluding a market's first month costs 8.6% of the panel and removes most of the tail.
>
> **The limitation the repair does not touch.** These are still **mark** prices, and the two
> venues mark off different references (Hyperliquid oracle, Lighter index), so part of every
> number above is a convention difference rather than realizable P&L. The realizable figure needs
> **traded** prices, which are not on disk. Clean candles do not make a mark-based basis a P&L
> basis, and no number here should be quoted as one.
>
> **The honest statement:** 24-hour mark-basis drift has a standard deviation near **20 bp** with
> a median absolute move of **7.5 bp**; it is strongly mean-reverting and does not compound; it
> exceeds the widest spread's 24-hour carry on about **17%** of holds, but on **5%** for the
> majors and on more than half for a market in its first week. It is a real cost that belongs in
> sizing and in the universe rule, and it is not, on this evidence, large enough to make the
> funding target the wrong target. Settling that needs traded prices, which is what F2 says.

---

## Deferred, with the trigger that reopens each

| # | Deferred | Trigger to reopen |
|---|---|---|
| F1 | **The value of H.** Parameterised, not chosen. | The cost model and D6's traded-price measurement. |
| F2 | **Whether the primary target is funding or funding-net-of-basis.** | D6, measured on traded prices. Under ~6 bp sd at 24h, keep funding. Near 22 bp, switch. |
| F3 | **Target C.** Prospective-only, exactly as Phase 1 decided. The premium inversion in D0 does **not** change this — it recovers the hour's final average premium, not the intra-hour path. | The recorder accumulating enough intra-hour history, which is Phase 11's business, not Phase 4's. |
| F4 | **Target C's single point of failure.** The Frankfurt recorder has no IAM role, so the recording exists in exactly one place, on one disk, with no alerting and S3 uploads off. This is not a decision — it is an operational risk with a written remedy (`recorder_runbook.md` § *To finish the S3 setup*) that needs an account administrator. | It blocks Target C and nothing else. Chase it in parallel; it is somebody else's approval, not Phase 4's work. |
| F5 | **Whether the premium is predictable at all.** The feature pathway in D1 rests on it and nothing has tested it. | Phase 5's exploratory work. **Corrected 2026-09-22:** there is no "20-month Lighter premium series it did not know it had" — the inverted premium is a monotone relabelling of `signed_rate_fraction`, which Phase 5 already has. Only Hyperliquid has an independently observed premium. |
| F6 | **CALIBRATING the Lighter premium inversion — reinstated 2026-09-22, and it is no longer optional.** The "zero band violations on 337,905 rows" evidence has been **withdrawn: it is a tautology** (the band condition reduces to the same inequality as the branch selection, so it cannot fail on any data). Independent measurement puts the true dead-zone jump at **0.0500**, not the 0.10 the formula asserts, implying `funding_clamp_small_pct = 0.05` is the **full** band width and the correct inversion is **`P = 8·rate ± 0.025`**. | Before any model consumes the inverted premium *on its own scale*. Settle it against the live recorder's `premium` field over an overlapping hour — a calibration check, not a consistency check. |
| F7 | **The unbalanced panel.** ~9,786 hours per symbol against ~14,640 in the window; a third of the grid absent, tilting toward long-listed majors. Single-reviewer finding, unmeasured. | Before any pooled cross-sectional result is reported. |
| F8 | **Whether cost is paid per flip rather than per hour** — which makes turnover, not horizon, the free parameter. Single-reviewer finding. | When the sizing rule is designed in Phase 9. |

---

## What was measured and found *not* to be a problem

Recorded so nobody re-litigates them.

- **There is no durable per-symbol spread to sit in.** One reviewer hypothesised that much of the
  0.699 persistence is a permanent per-symbol basis you could simply hold, which would have
  changed the horizon answer completely. Measured: between-symbol variance is **1.90%** of
  Var(S); lag-1 on symbol-demeaned S is **0.6985** against 0.7042 raw. You must round-trip.
- **Hyperliquid's outer clamp never binds.** 0 of 978,572 hours.
- **Settlement alignment holds.** Re-run independently: median per-pair correlation **0.5380** at
  lag 0 against 0.3861 at −1 and 0.4112 at +1, over 97 pairs.
- **The dead-zone atoms are dependent across venues, not independent.** 0.5986 × 0.9014 = 0.5396
  reproduces the 54.01% zero share of ΔF on Hyperliquid; but 0.5401 × 0.6041 = 0.3263 against
  **40.27%** observed for the spread change — the two venues sit in their dead zones together
  more often than independence predicts.
- **Lighter's fee fields read 0.0 on all 235 markets and Hyperliquid's snapshot has no fee
  column at all.** Four of seven reviewers called the zero a placeholder rather than a zero-fee
  venue. Treat both as **unknown**; no cost model may rest on them.

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
