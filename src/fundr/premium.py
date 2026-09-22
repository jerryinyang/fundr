"""Both venues' hourly premium, on one basis -- and a warning label on half of it.

Hyperliquid publishes its premium on 100% of rows. Lighter publishes none, but its settlement
formula inverts, so the premium can be written down for every hour that settled off the market's
baseline. That inversion is what this module is mostly about, and what it is **not** matters as
much as what it is.

**The Lighter premium is a link function, not recovered data.** `P = 8*rate +/- clamp_small` is a
*strictly monotone transform of `signed_rate_fraction`*, a column already on disk. It cannot
change a rank, so the Spearman correlation of the inverted premium against Hyperliquid's observed
premium is **identical to four decimals (0.6233)** to that of the raw rate, and the raw rate's
Pearson is the **higher** of the two (0.8575 against 0.8450), measured over the 337,905
off-baseline hours on markets carrying the 0.01% base rate. What it buys is scale and structure:
the rate expressed in the units of the quantity that drives it, with the dead zone made explicit.
It buys no information. `docs/phase1/handoff.md` §2 -- no *independent* historical Lighter premium
observation exists -- stands unchanged, and re-deriving the premium from the rate through the
venue's own formula is not an independent observation.

Two things follow, and both are load-bearing:

- Do not write a test asserting that the point estimates respect the dead-zone band. Branch
  selection is `rate > baseline` against `rate < baseline`, and the band condition reduces to
  exactly that inequality, so the check passes on any data whatsoever. It is not evidence. The
  test that *is* evidence is the round trip through `lighter_formula.hourly_rate`.
- A model consuming `lighter_premium_point` is consuming `signed_rate_fraction`. Feeding both to
  the same fit is feeding one variable twice.

**Each market's own clamp, and never BTC's.** The parameters are not uniform:
`funding_premium_multiplier` is 100 on 137 markets, 50 on 96 and 1 on 2; `base_interest_rate_pct`
is 0.0100 on 119, 0.0032 on 89 and 0.0000 on 27. F6 (settled 2026-09-22 against Lighter's public
`market_stats` websocket, recorded in `docs/phase4/decisions.md`) confirmed `clamp_small = 0.05`
as the **half**-band on all 100 matched crypto pairs -- 100/136 exact and 10/13 in the band where
the rival 0.025 reading scored 0/13 -- and separately found that the multiplier-50 equity/RWA
markets fit the **halved** bound better (MAE 0.00022 against 0.00065), consistent with the
effective clamp scaling as `clamp_small * multiplier / 100`. No matched pair is multiplier-50, so
Target B is untouched; Target A's Lighter per-venue view spans 96 such markets, so it is not.
`effective_clamp_small_pct` is therefore the only clamp this module will use, it is derived from
each market's own row rather than accepted from the caller, and the value actually applied is
emitted beside the premium as `funding_clamp_small_eff_pct`. There is no crypto-calibrated
constant in this file to reach for by accident.

**Units.** Lighter's formula, its clamps and its base rate are all in **percent**, and the
inversion runs in percent. The emitted columns are per-hour signed **fractions**, Phase 4's
common basis and the units Hyperliquid's premium already arrives in -- the two venues' premia sit
in the same frame, and a 100x mismatch between neighbouring columns is exactly the silent bug
`targets.assert_common_basis` exists to stop.

**What the inversion cannot see.** Truncation: the venue truncates the settled rate toward zero
at four decimals of percent, so eight hours of premium land in one bucket `TRUNCATION_WIDTH_PCT`
wide. Every point estimate is the edge of that bucket nearest zero, and the width is irreducible
-- the information was discarded at settlement. The outer clamp: `lighter_formula` clamps at
`clamp_big` *before* the `/8`, which would cap the settled rate at 0.5 percent per hour, yet the
history reaches **1.454** percent. That ordering is P7's documented inference, not a measured
fact, and until it is settled there is no dependable way to tell a clamped hour from an extreme
one, so nothing here tries; Phase 4 Task 4's `venue_clamped` flag is where those hours are
marked. And the aggregation: this is the hour's *final running-average* premium, never the
intra-hour path, so Target C still needs the live recorder.
"""
import polars as pl

from fundr import targets
from fundr.funding import lighter_formula
from fundr.markets import DEFAULT_MULTIPLIER

#: The width of the premium bucket one settled rate stands for, in percent. Lighter truncates the
#: rate to `targets.PERCENT_TICK` and the premium is eight times the rate.
TRUNCATION_WIDTH_PCT = 8 * targets.PERCENT_TICK

#: How close to its baseline a rate must sit to count as being on it, in percent. Not an equality
#: test: `0.01 / 8` truncated is `12 / 10000`, which is not the double the venue's own `"0.0012"`
#: parses to -- they differ by 2.2e-19, and exact equality calls all 621,772 baseline hours
#: off-baseline and invents a premium for every one of them. Any genuinely off-baseline rate is a
#: whole `PERCENT_TICK` (1e-4) away, so this sits nine orders of magnitude clear of both hazards.
BASELINE_TOLERANCE_PCT = 1e-9

#: What a market must tell us before its hours can be inverted. All four come straight off
#: `data/phase2/markets/venue=lighter`. `funding_clamp_big_pct` plays no part in the inversion; it
#: is required so the baseline rate can be computed by the shipped forward formula verbatim
#: rather than by a second, drifting copy of it.
REQUIRED_PARAMS = ("symbol", "base_interest_rate_pct", "funding_clamp_small_pct",
                   "funding_clamp_big_pct", "funding_premium_multiplier")

#: What `lighter_premium` adds to its input.
PREMIUM_COLUMNS = ("lighter_premium_point", "lighter_premium_lo", "lighter_premium_hi",
                   "premium_is_point_identified", "funding_clamp_small_eff_pct")


def effective_clamp_small_pct(clamp_small_pct, multiplier):
    """The market's own inner clamp, in percent -- the only bound this module ever applies.

    `funding_premium_multiplier` is reported in hundredths, so 100 is an effective 1.0 and the
    crypto clamp is its reported 0.05; 50 is an effective 0.5 and the clamp is 0.025. Takes floats
    or polars expressions. Callers of `lighter_formula.hourly_rate` on a non-crypto market want
    this value, not the raw `funding_clamp_small_pct` the API reports."""
    return clamp_small_pct * multiplier / DEFAULT_MULTIPLIER


def hl_premium(df: pl.DataFrame, *, column: str = "premium") -> pl.DataFrame:
    """Copy Hyperliquid's observed premium to `hl_premium`, refusing an incomplete one.

    A passthrough with one guard: the venue publishes the premium on 100% of rows, so a null is a
    broken frame rather than a quiet hour. Already a per-hour signed fraction, the same basis
    `lighter_premium` emits."""
    if column not in df.columns:
        raise ValueError(f"no {column!r} column to pass through: got {df.columns}")
    nulls = df[column].null_count()
    if nulls:
        raise ValueError(f"{column!r} is null on {nulls} of {df.height} rows; Hyperliquid "
                         "publishes the premium on every row, so the frame is wrong")
    return df.with_columns(pl.col(column).alias("hl_premium"))


def _check_params(rates: pl.DataFrame, params: pl.DataFrame) -> None:
    missing = [c for c in REQUIRED_PARAMS if c not in params.columns]
    if missing:
        raise ValueError(f"`params` is missing {missing}; every market's premium is read with "
                         "its own base rate, clamp and multiplier, never another market's")
    nulls = [c for c in REQUIRED_PARAMS if params[c].null_count()]
    if nulls:
        raise ValueError(f"`params` has null {nulls}; a market whose parameters are unknown "
                         "cannot be inverted with a guess")
    duplicated = params.filter(params["symbol"].is_duplicated())["symbol"].unique().to_list()
    if duplicated:
        raise ValueError(f"`params` carries more than one row for {duplicated}; the join would "
                         "multiply the rates")
    unknown = sorted(set(rates["symbol"].unique()) - set(params["symbol"]))
    if unknown:
        raise ValueError(f"no parameters for {unknown}; these markets' hours cannot be inverted "
                         "and must not borrow another market's clamp or base rate")


def lighter_premium(rates: pl.DataFrame, params: pl.DataFrame) -> pl.DataFrame:
    """Lighter's hourly premium, as a point estimate off baseline and an interval on it.

    `rates` carries `symbol` and `signed_rate_fraction` -- the common basis, checked by value with
    `targets.assert_common_basis`, not taken on trust. `params` carries one row per market with
    `REQUIRED_PARAMS`, as `data/phase2/markets/venue=lighter` writes them.

    Returns `rates` with `PREMIUM_COLUMNS` attached, the premia as per-hour signed fractions:

    - **off the market's baseline** -- `lighter_premium_point` holds `8*rate +/- clamp`, the sign
      of the offset taken from which side of the baseline the rate settled, and
      `premium_is_point_identified` is true. `lo` and `hi` are null.
    - **on it** -- the premium is anywhere in the dead zone, so `lo` and `hi` bound it and the
      point is null. The bounds are read off the settled rate rather than quoted as
      `interest +/- clamp`, which makes them hold at the edges: a premium up to
      `TRUNCATION_WIDTH_PCT` above `interest + clamp` still truncates to the baseline rate, and a
      bound that a real premium can sit outside is worse than no bound.

    One market (MKR, inactive, 4,463 hours) reports every parameter as zero. Its dead zone is a
    point, so all but its exactly-zero hours are "identified" at `8*rate` with no offset. Nothing
    special is done for it: those are the parameters the venue publishes.

    The estimate is a *relabelling* of the rate, not new information -- see the module docstring
    before building anything on it."""
    targets.assert_common_basis(rates)
    if "signed_rate_fraction" not in rates.columns or "symbol" not in rates.columns:
        raise ValueError(f"`rates` needs `symbol` and `signed_rate_fraction` (the common basis, "
                         f"not Lighter's raw percent); got {rates.columns}")
    clash = [c for c in REQUIRED_PARAMS if c != "symbol" and c in rates.columns]
    if clash:
        raise ValueError(f"`rates` already carries {clash}; the parameters used must come from "
                         "`params`, so that the clamp on every row is the one this module chose")
    _check_params(rates, params)

    interest = pl.col("base_interest_rate_pct")
    clamp = effective_clamp_small_pct(pl.col("funding_clamp_small_pct"),
                                      pl.col("funding_premium_multiplier"))
    rate_pct = pl.col("signed_rate_fraction") * 100
    # The rate the venue settles for *any* premium inside the dead zone, computed by the confirmed
    # forward formula itself -- including its truncation -- so the inverse cannot drift from it.
    baseline_pct = lighter_formula.hourly_rate(interest, interest, clamp,
                                               pl.col("funding_clamp_big_pct"),
                                               pl.col("funding_premium_multiplier"))
    above = rate_pct > baseline_pct + BASELINE_TOLERANCE_PCT
    below = rate_pct < baseline_pct - BASELINE_TOLERANCE_PCT
    identified = above | below
    return (rates.join(params.select(REQUIRED_PARAMS), on="symbol", how="left")
            .with_columns(
                (pl.when(above).then(8 * rate_pct + clamp)
                   .when(below).then(8 * rate_pct - clamp) / 100)
                .alias("lighter_premium_point"),
                (pl.when(identified).then(None).otherwise(8 * rate_pct - clamp) / 100)
                .alias("lighter_premium_lo"),
                (pl.when(identified).then(None)
                   .otherwise(8 * rate_pct + clamp + TRUNCATION_WIDTH_PCT) / 100)
                .alias("lighter_premium_hi"),
                identified.alias("premium_is_point_identified"),
                clamp.alias("funding_clamp_small_eff_pct"))
            .drop([c for c in REQUIRED_PARAMS if c != "symbol"]))
