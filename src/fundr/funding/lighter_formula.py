"""Lighter hourly funding, in PERCENT per hour (not a fraction — same units as
`/api/v1/fundings`' `rate` field). Confirmed in P7 (docs/phase1/evidence/P7-funding-formulas.md,
Part B, the `no_multiplier` candidate); parameters come from `orderBookDetails` (P6).

The `p` input must be the hour's *final* running-average premium — the `market_stats`
websocket's `premium` field read just before the hour boundary — not a mean of in-hour
samples. That field is a cumulative running average since the last settlement (it resets
to 0 at each hour boundary), so averaging per-minute snapshots of it over the hour is the
wrong aggregation and does not reproduce settled funding (P7 part B: 0/12 off-baseline
matches for the mean vs. 16/16 for the last value).

`multiplier` is accepted for interface parity with the API's `funding_premium_multiplier`
field, but the confirmed candidate does not scale the premium by it: P7 found the venue's
`funding_premium_multiplier: 100` denotes an *effective* multiplier of 1.0 for all sample
(crypto) markets, and the winning candidate (`no_multiplier`) uses the premium unscaled.
Whether the multiplier would matter for RWA/Pre-IPO markets (multiplier != 1) is untested
(P7, "Open issues").

Lighter truncates the computed rate toward zero at 4 decimal places (P7: `raw` and `round4`
only match settled funding within one reported unit; only `trunc4` reproduces the exact
4-dp string on 16/16 off-baseline hours).
"""
import polars as pl


def _trunc4(x: pl.Expr) -> pl.Expr:
    # Truncate toward zero at 4 dp. A tiny epsilon counters binary-float representation
    # error before flooring/ceiling (P7's guard: without it, a value like 0.0764/8 that is
    # exactly 0.0033 in decimal can truncate to 0.0032 due to float representation).
    eps = 1e-9
    scaled = x * 10000
    return pl.when(x >= 0).then((scaled + eps).floor()).otherwise((scaled - eps).ceil()) / 10000


def hourly_rate(p: pl.Expr, interest: float, clamp_small: float, clamp_big: float, multiplier: float) -> pl.Expr:
    small_clamped = p + (interest - p).clip(-clamp_small, clamp_small)
    # Big clamp applied BEFORE the /8, not after. This deliberately differs from the task-16
    # brief's Step 4 snippet (which clamps after /8); it follows P7's confirmed formula
    # (docs/phase1/evidence/P7-funding-formulas.md: "the big clamp is applied before the /8,
    # unlike HL's cap which is applied after"). Untested on real settled data (P7 "Open
    # issues": the clamp never binds in the P7 sample), so this ordering is a documented
    # inference from the docs, not a data-confirmed fact — see the clamp-ordering test below.
    f8 = small_clamped.clip(-clamp_big, clamp_big)
    return _trunc4(f8 / 8)
