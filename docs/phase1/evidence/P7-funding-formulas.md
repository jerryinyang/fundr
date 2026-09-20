# P7 — Funding formulas (both venues)

Status: Hyperliquid **confirmed**; Lighter **confirmed**

This note is written in two parts. Part A covered Hyperliquid fully and the Lighter source reading only. Part B (added below, under "Lighter — confirmed") is the Lighter data check: `probes/p07_lighter_formula_check.py`, `tests/fixtures/lighter_formula_cases.json`, and the P9 live cross-check (Step 4).

## What ran

### Part A (Hyperliquid)
- Command: `FUNDR_DATA=/Users/jerryinyang/Trading/fundr/data/phase1 uv run python probes/p07_hl_formula_check.py` (run from the worktree root; writes `tests/fixtures/hl_formula_cases.json`)
- Run date (UTC): 2026-09-19, ~14:00
- Markets: the 7 P0 sample coins (BTC, ENA, ETHFI, JUP, KAITO, ONDO, PONS) — every row in P4's `settled_*.parquet`
- Dates / windows sampled: P4's three days — 2025-02-21, 2025-12-05, 2026-09-18 (settlements fall on those days and the first hour of the next day)
- Sources read (Step 1), fetched 2026-09-19:
  - HL: https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding (Markdown version: same URL + `.md`)
  - Lighter: https://docs.lighter.xyz/trading/funding (Markdown and rendered HTML; the formula was read from the HTML's TeX annotation because the Markdown export mangles `+`/`−` into list bullets and rules); https://docs.lighter.xyz/trading/contract-specifications; `elliottech/lighter-python` at commit `a38b6405` (2026-09-15) — `openapi.json`, `lighter/models/perps_order_book_detail.py`, `docs/PerpsOrderBookDetail.md`; https://nautilustrader.io/docs/nightly/integrations/lighter/; live parameters from P6 `data/phase1/p06/orderbookdetails.json` (ENA, market 29)

### Part B (Lighter)
- Command: `FUNDR_DATA=/Users/jerryinyang/Trading/fundr/data/phase1 uv run python probes/p07_lighter_formula_check.py` (from the worktree root; writes `tests/fixtures/lighter_formula_cases.json` and caches `data/phase1/p07/fundings_window.parquet`)
- Run date (UTC): 2026-09-20
- Premium input: the P9 live recording `data/phase1/p09/live.jsonl`, `source == "ws_market_stats"` rows (1,631 rows, 7 markets). **Not** 0xArchive: the vendor exposes no `premium` field for Lighter (P8) and Lighter publishes no historical premium series (P6), so this live recording is the only premium history that exists for these markets.
- Settled truth: Lighter's public `GET /api/v1/fundings` (`resolution=1h`), signed via `direction` (P5/P9). P5's parquet snapshot stops at `2026-09-19 11:00` — before the recording window — so the probe tops it up with a fresh public call for the window and unions the two. No 0xArchive calls, no AWS, no auth.
- Markets: the 7 P0 sample markets (BTC 1, JUP 26, ENA 29, KAITO 33, ONDO 38, ETHFI 64, PONS 231).

## What came back (part A, Hyperliquid)
- Row counts: 494 settled coin-hours in total; 245 off-baseline (settled ≠ `0.0000125`), 249 at baseline. Off-baseline by day: 70 (2025-02-21/22), 117 (2025-12-05/06), 58 (2026-09-18/19). Well above the 15 needed for the fixture; P4's range did not need widening.
- Schema (input): `coin, time, settle_time, funding_rate, premium, funding_rate_str` (P4).
- Probe output (verbatim):
  ```
  all hours: {'n': 494, 'n_match': 494, 'rate': 1.0, 'mean_signed_error': -9.868421043627772e-13}
  off-baseline hours: {'n': 245, 'n_match': 245, 'rate': 1.0, 'mean_signed_error': -1.989795915611506e-12}
  shape: (0, 7)   <- no mismatching rows
  ```
  Tolerance = one unit in the last reported decimal place of `fundingRate` = `1e-10`.
- Fixture: `tests/fixtures/hl_formula_cases.json` — 20 rows `{premium, settled, settled_str}`: the first 15 off-baseline rows + the first 5 baseline rows. First row: `{"premium": -0.0005591732, "settled": -7.3966e-06, "settled_str": "-0.0000073966"}`.

## Hyperliquid — confirmed

### Formula as documented (quotes, https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding)
- "The funding rate formula applies to 8 hour funding rate. However, funding is paid every hour at one eighth of the computed rate for each hour."
- "The specific formula is `Funding Rate (F) = Average Premium Index (P) + clamp (interest rate - Premium Index (P), -0.0005, 0.0005)`. The premium is sampled every 5 seconds and averaged over the hour."
- "interest rate component is predetermined at 0.01% every 8 hours, which is 0.00125% every hour"
- "`premium = impact_price_difference / oracle_price` where `impact_price_difference = max(impact_bid_px - oracle_px, 0) - max(oracle_px - impact_ask_px, 0)`" (HIP-3 perps use a different premium: `(0.5 * (impact_bid_px + impact_ask_px) / oracle_px) - 1`; not relevant to our sample, all standard perps.)
- "Funding on Hyperliquid is capped at 4%/hour. ... The funding cap and funding interval do not depend on the asset."
- "the funding payment at the end of the interval is `position_size * oracle_price * funding_rate`" (oracle price, not mark price).
- Note: the page's own "Numerical Example" computes `Funding Rate = 1% + (-0.05%) = 0.95%` and never divides by 8, although it says "The funding interval is 1 hour". The example is inconsistent with the "one eighth" sentence; the data (below) follows the one-eighth sentence.

### Formula as confirmed
```
F_8h     = P + clamp(0.0001 − P, −0.0005, +0.0005)
F_hourly = clamp(F_8h / 8, −0.04, +0.04)
```
with `P` = the `premium` field of `fundingHistory` for that settlement, and `F_hourly` = that row's `fundingRate`.
- Match: **245/245 off-baseline hours (100%)** and 494/494 all hours, within one reported unit (`1e-10`). Mean signed error ≈ −2e-12 (no bias). No changes to `candidate` were needed.
- Largest absolute error across all 494 rows: `5.0e-11` (half a reported unit) — i.e. only output rounding. (Diagnostic, scratch script not committed.)
- Which regime was exercised: every off-baseline hour had premium outside the ±0.0005 band around 0.0001 (61 above, 184 below); every baseline hour had premium inside it (249/249). So both the band (funding pinned to `0.0000125`) and the clamp-active branch (`F_8h = P ∓ 0.0005`) are exercised. The 4%/hour cap was **not** exercised (largest |fundingRate| seen: `0.0017571595`); it stays docs-only.

### Parameters, units, sampling
- Interest rate `0.0001` per 8h (a fraction, = 0.01%), so the hourly baseline is `0.0000125`. Clamp `±0.0005` per 8h. Cap `±0.04` per hour. All same for every asset (docs quote above).
- Units: `premium` and `fundingRate` are plain fractions; `fundingRate` is per hour (P4 found this too). Both are reported to 10 decimal places.
- Premium sampling: every 5 s, averaged over the hour (docs). The `premium` returned by `fundingHistory` behaves exactly as the hour's average `P` would in the formula (inference from the 100% match; we cannot see the 5-second samples themselves).
- Sign: positive premium → positive funding → longs pay shorts (docs; P4 checked 245/245 on data).
- Rounding (inference): rebuilding from the 10-dp `premium` and rounding the result to 10 dp reproduces the exact string on ~96% of rows (473/494 half-up, 475/494 half-even), not 100%. This suggests HL computes from an unrounded premium and rounds both fields separately. For our purposes, matching within one reported unit is the right test; exact string equality is not achievable from the published premium.

### What is public before settlement
- Per-minute snapshots of `premium`, `funding`, `oracle_px`, `mark_px`, `impact_bid_px`, `impact_ask_px` in the asset-ctx archive (P1). `premium` changes within every coin-hour (P1).
- `predictedFundings` gives HL's own predicted rate for the next settlement plus the next settlement time (P4).
- The 5-second premium samples themselves are **not** published anywhere we have found; only per-minute snapshots. So an in-hour estimate of `P` from public data is an average of ~60 minute snapshots, not of the ~720 samples the venue uses. (Whether that approximation is good enough is P10's question, not this probe's.)

## Lighter — confirmed

### Formula as documented (https://docs.lighter.xyz/trading/funding)
- Premium: "For each minute, at a random time, Lighter calculates the premium of each market":
  `premium_t = ( max(0, ImpactBidPrice_t − index_t) − max(0, index_t − ImpactAskPrice_t) ) / index_t`
- Funding rate: "At the end of each hour, a 1-hour premium is calculated as the time-weighted average of the 60 premiums calculated over the course of the last hour." Then (read from the page's TeX source):
  ```
  premium             = Average of premium_t since the latest funding × FundingPremiumMultiplier
  smallClamp          = SmallClamp × FundingPremiumMultiplier
  smallClampedPremium = premium + clamp(InterestRate − premium, −smallClamp, +smallClamp)
  fundingRate         = clamp(smallClampedPremium, −BigClamp, +BigClamp) / 8
  where clamp(x, a, b) = max(a, min(b, x))
  ```
- "Dividing the 1-hour premium by 8 ensures that funding payments for the premium are distributed over 8 hours".
- Parameters: "The values used for the majority of the markets are ... SmallClamp = 0.05%, BigClamp = 4%, InterestRate = 0.01%".
- "BigClamp limits funding payments to 4% per 8 hours, or 0.5% per hour." (the big clamp is applied **before** the /8, unlike HL's cap which is applied after.)
- "FundingPremiumMultiplier is configurable per market and ranges over (0, 1]. It is set to 1 for crypto markets, 1/2 for RWA markets, and 1/100 for Pre-IPO and Pre-Markets."
- "if the premium is within 5 basis points (scaled by the market's FundingPremiumMultiplier) of the interest rate, funding defaults to the normal value of 1 basis point per 8 hours."
- Worked example (docs, crypto, multiplier 1, premium 0.1%): `0.1% + clamp(0.01% − 0.1%, −0.05%, +0.05%) = 0.05%`, `fundingRate = clamp(0.05%, ±4%) / 8 = 0.00625%`.
- Payment: `funding_i,j = −position_i,j × index_j × fundingRate_j` (index price).
- Interval (contract-specifications page): "Currently each deployed market has a funding period of 1 hour. Funding period is a configuration of each market".

### Where the multiplier and each clamp sit (as documented)
- The multiplier scales **both** the averaged premium and the small clamp. It does not scale the interest rate or the big clamp.
- Small clamp: inside, on `(InterestRate − premium)`. Big clamp: outside, on the 8h-equivalent value, before `/8`.
- Note for part B: the brief's three Lighter candidates do not scale the small clamp by the multiplier. For a crypto market with documented multiplier 1 this makes no difference; if the API's `funding_premium_multiplier: 100` does not mean 1.0 (see units below), it would. **Part B result:** the effective multiplier *is* 1.0 for all 7 sample markets, so the two readings are numerically identical there and the question stays open for non-crypto markets.

### Live parameters (P6, ENA market 29) and units
- `funding_premium_multiplier: 100`, `funding_clamp_small: "0.0500"`, `funding_clamp_big: "4.0000"`, `base_interest_rate: "0.0100"`.
- Clamps and interest rate match the documented percent values exactly (0.05%, 4%, 0.01%), so these API fields are **in percent**. (Inference, supported by P5: `0.0100 / 8 = 0.00125`, reported truncated as settled `rate = 0.0012`, i.e. settled `rate` is percent per hour.)
- Multiplier: docs say it is 1 for crypto markets and lies in (0, 1]; the API gives the integer `100`. Part A's **inference** was that the API value is in hundredths (100 → 1.0). **Part B confirms this on data** — see "Resolution of part A's three open points", item 2.
- The SDK's `openapi.json` examples use different magnitudes (`funding_clamp_small` "0.005", `funding_clamp_big` "0.4", `base_interest_rate` "0.0001", `funding_premium_multiplier` "100"), inconsistent with the live values by 10×–100×. Treated as placeholder examples; the live values and the docs agree with each other.
- The SDK (`lighter-python`) contains no funding computation — only these field definitions (no descriptions). NautilusTrader's page gives no formula either.

### Sampling rate
- One premium sample per minute at a random time within the minute; 60 per hour; time-weighted average (docs).
- Part B (data): the venue exposes only the *running* average of those samples, refreshed about once a minute, on the `market_stats` websocket. The individual `premium_t` samples are never published, so the venue's own weighting (time-weighted vs simple) cannot be checked from outside — but the running average it publishes is exactly the input its settlement uses (16/16 hours, next section).

### The `premium` field on the `market_stats` websocket is a RUNNING average, not a spot sample (part B, new finding)
This was not known when the brief was written; it changes what "the hour's premium" means and is the single most important part-B result.
- The field resets at the settlement boundary: of the 28 samples recorded within 70 s of an hour boundary (4 boundaries × 7 markets), **22 read exactly `0.0000`**; the other 6 are already one minute-sample in (the poll lands 28–36 s past the boundary and Lighter samples at a random time within each minute).
- The mean absolute minute-to-minute change of the field decays monotonically through the hour, roughly as `1/k` for the `k`-th minute — the signature of a cumulative mean, not of a fresh sample each minute: minutes 1–9 `0.01035`, 10–19 `0.00199`, 20–29 `0.00118`, 30–39 `0.00078`, 40–49 `0.00056`, 50–59 `0.00046` (percent).
- Decisive: the **last** in-hour value of the field, fed to the documented formula, reproduces the settled rate on 16/16 off-baseline hours, while the **mean of the per-minute snapshots** of the same field reproduces 0/12 (table below). A mean of running means is not the running mean.
- So `premium` from `ws market_stats` **is** the docs' "average of `premium_t` since the latest funding". The individual `premium_t` minute samples are not published anywhere; only their running mean is.

### Part B — what came back
- Coverage filter. Hours are keyed by market and UTC hour, paired with the settlement that closes them (`settle_time = hour + 1h`, the P5/P9 convention). 35 market-hours were paired. Two filters, one per input, because the two inputs need different completeness:
  - `p_last` (last snapshot in the hour): requires the last snapshot to fall **within 120 s of the hour boundary** (`lag_s <= 120`). Because the field is cumulative, a late start does not matter — only a late-enough last reading does. **28 market-hours pass** (hours 11:00–14:00 UTC × 7 markets), of which **16 are off-baseline** (settled ≠ `0.0012`).
  - `p_mean` (mean of the hour's snapshots): requires **near-complete minute coverage, `n_rows >= 55`** of the ~60 one-minute samples. **21 market-hours pass** (hours 12:00–14:00 × 7), of which 12 are off-baseline. The 11:00 hour is excluded here (recording started 11:27, 33 samples).
- Tolerance `1e-4` = one unit in the last reported decimal place of `/fundings.rate` (all `rate_str` are 4 dp), from `reported_tolerance`. "exact-off" below counts off-baseline hours reproduced to the full reported 4-dp value, not merely within a unit.
- Candidate table (probe output; the `p_mean` block is abridged where marked, everything else verbatim; `all` = all hours passing the filter, `off-baseline` = settled ≠ `0.0012`):

```
markets=[1, 26, 29, 33, 38, 64, 231] hours paired=35 tol=0.0001
coverage filters: p_mean needs n_rows >= 55; p_last needs lag_s <= 120

=== input p_last: 28 hours, 16 off-baseline
  no_multiplier        raw     all  28/28  (1.000)  off-baseline  16/16  (1.000)  exact-off   1/16   mean_signed_err +5.40e-05
  no_multiplier        round4  all  28/28  (1.000)  off-baseline  16/16  (1.000)  exact-off   6/16   mean_signed_err +5.36e-05
  no_multiplier        trunc4  all  28/28  (1.000)  off-baseline  16/16  (1.000)  exact-off  16/16   mean_signed_err +2.40e-19
  premium_div_mult     raw     all  12/28  (0.429)  off-baseline   0/16  (0.000)  exact-off   0/16   mean_signed_err -2.08e-03
  premium_div_mult     round4  all  12/28  (0.429)  off-baseline   0/16  (0.000)  exact-off   0/16   mean_signed_err -2.12e-03
  premium_div_mult     trunc4  all  12/28  (0.429)  off-baseline   0/16  (0.000)  exact-off   0/16   mean_signed_err -2.12e-03
  premium_x_mult       raw     all   0/28  (0.000)  off-baseline   0/16  (0.000)  exact-off   0/16   mean_signed_err +7.22e-01
  premium_x_mult       round4  all   0/28  (0.000)  off-baseline   0/16  (0.000)  exact-off   0/16   mean_signed_err +7.22e-01
  premium_x_mult       trunc4  all   0/28  (0.000)  off-baseline   0/16  (0.000)  exact-off   0/16   mean_signed_err +7.22e-01
  docs_both_mult_m100  raw     all  11/28  (0.393)  off-baseline   0/16  (0.000)  exact-off   0/16   mean_signed_err +2.38e-01
  docs_both_mult_m100  round4  all  11/28  (0.393)  off-baseline   0/16  (0.000)  exact-off   0/16   mean_signed_err +2.38e-01
  docs_both_mult_m100  trunc4  all  11/28  (0.393)  off-baseline   0/16  (0.000)  exact-off   0/16   mean_signed_err +2.38e-01
  docs_both_mult_m1    raw     all  28/28  (1.000)  off-baseline  16/16  (1.000)  exact-off   1/16   mean_signed_err +5.40e-05
  docs_both_mult_m1    round4  all  28/28  (1.000)  off-baseline  16/16  (1.000)  exact-off   6/16   mean_signed_err +5.36e-05
  docs_both_mult_m1    trunc4  all  28/28  (1.000)  off-baseline  16/16  (1.000)  exact-off  16/16   mean_signed_err +2.40e-19

=== input p_mean: 21 hours, 12 off-baseline
  no_multiplier        raw     all   9/21  (0.429)  off-baseline   0/12  (0.000)  exact-off   0/12   mean_signed_err -1.92e-04
  no_multiplier        round4  all   9/21  (0.429)  off-baseline   0/12  (0.000)  exact-off   0/12   mean_signed_err -1.95e-04
  no_multiplier        trunc4  all   9/21  (0.429)  off-baseline   0/12  (0.000)  exact-off   0/12   mean_signed_err -2.43e-04
  premium_div_mult     raw     all   9/21  (0.429)  off-baseline   0/12  (0.000)  exact-off   0/12   mean_signed_err -1.99e-03
  premium_x_mult       raw     all   0/21  (0.000)  off-baseline   0/12  (0.000)  exact-off   0/12   mean_signed_err +6.94e-01
  docs_both_mult_m100  raw     all   8/21  (0.381)  off-baseline   0/12  (0.000)  exact-off   0/12   mean_signed_err +2.27e-01
  docs_both_mult_m1    raw     all   9/21  (0.429)  off-baseline   0/12  (0.000)  exact-off   0/12   mean_signed_err -1.92e-04
  (rounding variants of the p_mean block omitted here for length; all are 0/12 off-baseline — full output in the probe)

best = no_multiplier / trunc4 / p_last; mismatching hours:
shape: (0, 7)   <- none
```
- The `all`-hours rate of `0.429` etc. for the failing candidates is the 12/28 baseline hours that "match" trivially (a wrong premium still lands inside the small-clamp band and produces the baseline `0.0012`). This is exactly why the off-baseline column is the one that decides.
- `no_multiplier` and `docs_both_mult_m1` are numerically identical here — with multiplier 1 the docs' extra scaling of the small clamp is the identity, and the big clamp's position (before vs after `/8`) makes no difference at these magnitudes. See "what is not exercised" below.
- **Truncation, not rounding.** `raw` and `round4` already match within one reported unit on 16/16 off-baseline hours, but only `trunc4` reproduces all 16 exactly: `/fundings.rate` is the computed value **truncated toward zero at 4 dp**, confirming P5's reading (`0.01/8 = 0.00125` reported as `0.0012`). The probe's truncation adds `1e-9` before the floor: without it, one hour (market 64, 12:00 UTC, premium `0.0764` → exactly `0.0033`) truncates to `0.0032` purely from binary-float representation error. That guard is a numeric detail of our implementation, not a venue behaviour.

### Formula as confirmed
With every quantity in **percent** and `P` = the hour's final running-average premium (the `premium` field of `market_stats` read just before the hour boundary):
```
smallClamped = P + clamp(0.01 − P, −0.05, +0.05)
rate_pct     = trunc4( clamp(smallClamped, −4, +4) / 8 )      # per hour, 4 dp, truncated
signed       = +rate_pct if direction == "long" else −rate_pct
fraction     = signed / 100                                    # common cross-venue basis (P5)
```
- Match: **16/16 off-baseline hours (100%)** and 28/28 all hours, exact to the reported 4-dp string; mean signed error `+2.4e-19`. Zero mismatching hours.
- Effective `FundingPremiumMultiplier` = **1.0** for these crypto markets.

### Step 4 — live cross-check against `current_funding_rate`
Same formula and rounding applied to the *running* premium at each recorded minute, compared with the `current_funding_rate` in the very same websocket message (1,631 minute samples, 7 markets):
```
     0-300 s into hour: n= 140 exact=  58 within-1-unit=  66
   300-900 s into hour: n= 280 exact= 222 within-1-unit= 237
   900-1800s into hour: n= 371 exact= 371 within-1-unit= 371
  1800-3000s into hour: n= 560 exact= 560 within-1-unit= 560
  3000-3600s into hour: n= 280 exact= 280 within-1-unit= 280
  all minutes: n=1631 exact=1491 within-1-unit=1514
  minutes >= 900s into hour: n=1211 exact=1211 within-1-unit=1211
```
- **Yes**: from 15 minutes into the hour onward the match is **1,211/1,211 = 100% exact** (to the full reported 4 dp). Overall 1,491/1,631 = 91.4% exact, 1,514/1,631 = 92.8% within one reported unit.
- Every miss is in the first 15 minutes (280/420 exact there). *Inference, not confirmed*: early in the hour the running mean moves by ~1/k per new sample, so even a sub-minute difference between when the venue stamps `premium` and when it recomputes `current_funding_rate` shows up as a visible gap; later in the hour the mean barely moves and any such skew is invisible. Testing a one-message lag (`premium` of the previous message vs this message's `current_funding_rate`) makes it *worse* (1,049/1,631 exact), so it is not a clean one-sample offset.
- Conclusion: `current_funding_rate` is the **same formula applied to the same running premium** — i.e. Lighter's displayed live rate is built exactly as the settlement is, which is why P9 found it bit-identical to the closing settlement at the hour boundary.

### What is public before settlement (checked on data in part B)
- `ws market_stats` publishes, per market per message: `premium` (the running hourly average, percent), `current_funding_rate` (that premium already run through the formula), `funding_rate` + `funding_timestamp` (the last completed settlement), plus `funding_clamp_small`, `funding_clamp_big`, `base_interest_rate` — i.e. the formula's inputs *and* its parameters, live, unauthenticated. Confirmed present on all 1,631 recorded messages.
- The individual per-minute `premium_t` samples, the impact bid/ask prices, and the `FundingPremiumMultiplier` are **not** on this stream (the multiplier is on `orderBookDetails`, P6).
- `orderBookDetails` gives live `mark_price` and `index_price`, but no premium and no impact prices (P6). No historical premium series from Lighter (P6).
- 0xArchive's REST Lighter funding route has no `premium` field; its WebSocket replay does include one (P8) — untested here.
- NautilusTrader's reading ("`current_funding_rate` is the upcoming estimate; `funding_rate`/`funding_timestamp` the last completed payment") is consistent with P9's data and with the Step 4 result above.

### Resolution of part A's three open points
1. **Does the multiplier scale the small clamp as well as the premium?** *Not resolved, and not resolvable from these markets.* All 7 sample markets are crypto markets with an effective multiplier of 1.0, where scaling the clamp by 1 is the identity — `no_multiplier` and `docs_both_mult_m1` produce bit-identical output on all 28 hours. The docs say it does scale both; nothing in this data contradicts that, and nothing in this data tests it. It matters only for RWA (1/2) and Pre-IPO/Pre-Market (1/100) markets, which are outside the P0 sample.
2. **What does `funding_premium_multiplier: 100` mean?** **Resolved: it is hundredths — `100` denotes a multiplier of 1.0.** Taking it literally fails outright: `premium_x_mult` (premium × 100) matches 0/16 off-baseline hours with a mean error of `+0.72` percentage points, and `docs_both_mult_m100` (premium and small clamp both × 100) matches 0/16 with `+0.24`. Treating it as 1.0 matches 16/16 exactly. `premium_div_mult` (premium ÷ 100) also fails, 0/16. This agrees with the docs' "set to 1 for crypto markets" and with the field's documented range of (0, 1].
3. **Is the big clamp applied before the `/8`?** *Not resolved — untested.* The clamp never binds in this sample: the largest `smallClamped` seen is 0.1451% against a 4% clamp, ~28× below it. `no_multiplier` (clamp after `/8`) and `docs_both_mult_m1` (clamp before `/8`) are therefore indistinguishable here; both score 16/16. The docs' ordering (before `/8`) is what the confirmed formula above records, on the strength of the docs alone.

### What part B does not exercise
- Negative settled rates: all 28 hours settled `direction == "long"` (positive). The sign rule itself was verified separately in P9/P5 against market 212 (`CAP`) at a genuinely negative rate. Five of the 20 fixture rows do have a **negative premium** (BTC, ‑0.0034 to ‑0.0079 %) that the small clamp pulls back to the baseline, so the negative-premium branch is exercised even though no negative *settled* rate is.
- The 4% big clamp (see point 3 above).
- Any market with `FundingPremiumMultiplier != 1` (see point 1 above).
- Sample size: 28 market-hours over 4 consecutive UTC hours on one day, 7 markets. This is small and highly time-clustered compared with HL's 494 hours across 3 days — it is bounded by the fact that the P9 live recording is the *only* Lighter premium history in existence. The 16/16 exact-to-the-last-digit agreement (not merely within tolerance) is what makes it convincing despite the size; a wrong formula would not land on the exact 4-dp value 16 times.

### Fixture
- `tests/fixtures/lighter_formula_cases.json` — 20 rows `{premium, settled, settled_str}`: 15 off-baseline (the maximum available is 16) + 5 baseline. `premium` is the winning input, `p_last` (percent, the hour's final running average); `settled` is the signed rate in **percent per hour** (not a fraction); `settled_str` is Lighter's own 4-dp string. First row: `{"premium": 0.1186, "settled": 0.0085, "settled_str": "0.0085"}`. Task 16's Lighter test reads this file.

## Findings
- HL: the documented formula, with the hourly `/8` and the ±0.0005 clamp around 0.0001, reproduces every settled `fundingRate` from its `premium` in P4's data: 245/245 off-baseline hours and 494/494 overall, within `1e-10`. **Confirmed.** Cap (4%/h) not exercised.
- HL docs' numerical example omits the `/8`; the data follows the `/8` rule.
- Lighter: the documented formula with an effective multiplier of 1.0 and the result truncated to 4 dp reproduces every settled `rate` from the hour's final running-average premium: **16/16 off-baseline hours and 28/28 overall, exact to the reported digit. Confirmed.** The three rival placements of the multiplier all score 0/16 off-baseline.
- Lighter's `market_stats.premium` is a **running average since the last settlement**, not a per-minute spot premium — it resets to `0.0000` at each hour boundary. Averaging it over the hour (the brief's assumption) destroys the match (0/12 off-baseline); the last value in the hour is the venue's own hourly average.
- Lighter's `current_funding_rate` is that same formula applied to that same running premium: 100% exact from 15 minutes into the hour onwards, 91.4% exact over all minutes. This explains P9's finding that it becomes bit-identical to the closing settlement.
- Both venues' settled funding is now reproducible from a published premium. HL's premium is published as a settled-hour value only (its 5-second samples are not public); Lighter's is published live, per minute, as a running mean, and not at all historically.

## Open issues
- Lighter's 4% big clamp, and whether it sits before or after the `/8`, is docs-only — never exercised in the sample (largest value seen is ~28× below the clamp).
- Whether the multiplier scales the small clamp as well as the premium is untestable on crypto markets (multiplier 1.0); it would matter for RWA and Pre-IPO/Pre-Market markets.
- The Lighter check rests on 28 market-hours from a single 4-hour window on 2026-09-19, because the P9 recording is the only Lighter premium history that exists. A second recording on another day would strengthen it.
- The P9 recording's Lighter websocket data ends at **15:19:28Z**, not at the 16:54:35Z run end quoted in P9's note (the later timestamps are a `ws_reconnect` and a `poll_error` record, not data). Usable full hours are 11:00–14:00 UTC, which is what caps the sample at 28 market-hours / 16 off-baseline.
- All 28 Lighter hours settled positive (`direction == "long"`); the negative-rate path is covered only by P9/P5's separate market-212 check, not by this formula check.
- The first ~15 minutes of each hour show a ~1e-4-scale disagreement between `current_funding_rate` and the formula applied to the same message's `premium`; the timing-skew explanation above is an inference, not confirmed.
- Meaning of `funding_premium_multiplier: 100` is now **resolved** (hundredths → 1.0) by the candidate comparison, superseding part A's inference.
- HL's 4%/hour cap is docs-only; no capped hours in the sample.
- HL's exact 5-second premium samples are not public; only per-minute snapshots are. The 100% match here uses HL's own reported hourly `premium`, not a premium we computed.
