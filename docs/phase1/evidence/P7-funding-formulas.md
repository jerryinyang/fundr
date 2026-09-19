# P7 — Funding formulas (both venues)

Status: Hyperliquid **confirmed**; Lighter **not confirmed — data check pending (part B)**

This note is written in two parts. Part A (this version) covers Hyperliquid fully and the Lighter source reading only. The Lighter data check (`probes/p07_lighter_formula_check.py`, the Lighter fixture, and the P9 cross-check) waits for the P9 live recording to finish (~15:40 UTC) and will be added in part B.

## What ran
- Command: `FUNDR_DATA=/Users/jerryinyang/Trading/fundr/data/phase1 uv run python probes/p07_hl_formula_check.py` (run from the worktree root; writes `tests/fixtures/hl_formula_cases.json`)
- Run date (UTC): 2026-09-19, ~14:00
- Markets: the 7 P0 sample coins (BTC, ENA, ETHFI, JUP, KAITO, ONDO, PONS) — every row in P4's `settled_*.parquet`
- Dates / windows sampled: P4's three days — 2025-02-21, 2025-12-05, 2026-09-18 (settlements fall on those days and the first hour of the next day)
- Sources read (Step 1), fetched 2026-09-19:
  - HL: https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding (Markdown version: same URL + `.md`)
  - Lighter: https://docs.lighter.xyz/trading/funding (Markdown and rendered HTML; the formula was read from the HTML's TeX annotation because the Markdown export mangles `+`/`−` into list bullets and rules); https://docs.lighter.xyz/trading/contract-specifications; `elliottech/lighter-python` at commit `a38b6405` (2026-09-15) — `openapi.json`, `lighter/models/perps_order_book_detail.py`, `docs/PerpsOrderBookDetail.md`; https://nautilustrader.io/docs/nightly/integrations/lighter/; live parameters from P6 `data/phase1/p06/orderbookdetails.json` (ENA, market 29)

## What came back
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

## Lighter — source reading only (data check pending, part B)

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
- Note for part B: the brief's three Lighter candidates do not scale the small clamp by the multiplier. For a crypto market with documented multiplier 1 this makes no difference; if the API's `funding_premium_multiplier: 100` does not mean 1.0 (see units below), it would.

### Live parameters (P6, ENA market 29) and units
- `funding_premium_multiplier: 100`, `funding_clamp_small: "0.0500"`, `funding_clamp_big: "4.0000"`, `base_interest_rate: "0.0100"`.
- Clamps and interest rate match the documented percent values exactly (0.05%, 4%, 0.01%), so these API fields are **in percent**. (Inference, supported by P5: `0.0100 / 8 = 0.00125`, reported truncated as settled `rate = 0.0012`, i.e. settled `rate` is percent per hour.)
- Multiplier: docs say it is 1 for crypto markets and lies in (0, 1]; the API gives the integer `100`. **Inference:** the API value is in hundredths (100 → 1.0). Not verified; part B's candidates will show it.
- The SDK's `openapi.json` examples use different magnitudes (`funding_clamp_small` "0.005", `funding_clamp_big` "0.4", `base_interest_rate` "0.0001", `funding_premium_multiplier` "100"), inconsistent with the live values by 10×–100×. Treated as placeholder examples; the live values and the docs agree with each other.
- The SDK (`lighter-python`) contains no funding computation — only these field definitions (no descriptions). NautilusTrader's page gives no formula either.

### Sampling rate
- One premium sample per minute at a random time within the minute; 60 per hour; time-weighted average (docs).

### What is public before settlement (from sources; not yet checked on data)
- NautilusTrader: "The live funding update uses `current_funding_rate` as the upcoming estimate; `funding_rate` and `funding_timestamp` describe the last completed payment." (from the `market_stats` WebSocket stream)
- `orderBookDetails` gives live `mark_price` and `index_price`, but no premium and no impact prices (P6). No historical premium series from Lighter (P6).
- 0xArchive's REST Lighter funding route has no `premium` field; its WebSocket replay does include one (P8).
- Whether `current_funding_rate` equals the formula applied to the running in-hour average premium is Step 4 of part B.

## Findings
- HL: the documented formula, with the hourly `/8` and the ±0.0005 clamp around 0.0001, reproduces every settled `fundingRate` from its `premium` in P4's data: 245/245 off-baseline hours and 494/494 overall, within `1e-10`. **Confirmed.** Cap (4%/h) not exercised.
- HL docs' numerical example omits the `/8`; the data follows the `/8` rule.
- Lighter: documented formula recorded above. Multiplier scales premium and small clamp; big clamp applied before `/8`; 60 one-minute samples per hour; parameters in percent. **Not confirmed** until part B.

## Open issues
- Lighter data check (Steps 3–4) pending part B.
- Meaning of Lighter's `funding_premium_multiplier: 100` (hundredths vs literal) is an inference.
- HL's 4%/hour cap is docs-only; no capped hours in the sample.
- HL's exact 5-second premium samples are not public; only per-minute snapshots are. The 100% match here uses HL's own reported hourly `premium`, not a premium we computed.
