"""Forward hour windows that cannot bridge a hole, and the unit assert that cannot be bluffed.

Two silent corruptions live in Phase 4's target construction and neither would ever raise an
error on its own. This module exists to make both impossible rather than documented.

**1. The grid is not uniform, so nothing here lags with `shift`.** Hyperliquid's settled funding
carries 1,789 eight-hour intervals (funding was 8-hourly until 2023-06-08) and 213 single-hour
holes across 141 of its 234 markets, out of 4,676,131 intervals. A one-row `shift` pairs rates
two or eight hours apart on **2,002** occasions and the result reads as noise. `forward_window`
joins on an explicit hour difference instead: a forward hour that does not exist produces a null
and is counted, never substituted by whatever row happens to come next. Lighter has zero
irregular intervals in 1,585,452.

**2. Lighter's raw `rate` is an unsigned percent.** Used as-is it is 100x too large and sign
free, and a spread built on it is dominated by the units bug. The common basis is the **per-hour
signed fraction, positive = longs pay shorts**: Hyperliquid's `funding_rate` already is one,
Lighter's is `signed_rate / 100`. `assert_common_basis` tests the values, not the column name.

**`n_hours_used` is the contract, not a diagnostic.** A window that runs off the end of a series,
or over a hole, is legitimate data with fewer hours in it. Nothing here imputes a missing forward
hour and nothing here drops a row for having a short window -- the count travels with the row and
Phase 6 decides what to do with it.

**The same rule governs the two flags.** `add_venue_flags` tags the hours where the spread is
venue arithmetic rather than a market (`joint_baseline`) and the hours where Lighter's outer
clamp bound (`venue_clamped`). Neither ever deletes a row: dropping the clamp hours would fit a
model on a sample conditioned on the event never happening. What the flags are *for* is
`skill_metric_rows` and `tuning_rows`, which are the only two places a flagged row is allowed to
disappear.
"""
import polars as pl

# The venue's own four-decimals-of-percent truncation, imported rather than re-implemented so
# that Lighter's rounding rule has exactly one definition in this repo.
from fundr.funding.lighter_formula import _trunc4

#: Largest magnitude a per-hour signed funding fraction can plausibly take. Hyperliquid's outer
#: clamp is 0.04/hour and Lighter's 0.005/hour; the largest magnitude observed over 978,572
#: concurrent pair-hours is 2.26e-2. Lighter's raw percent reaches 1.454.
MAX_HOURLY_FRACTION = 0.05

#: Lighter truncates its published rate to four decimal places of **percent**, so every non-zero
#: raw value is an exact multiple of this. Measured: 100.0% of the 1,514,506 non-zero raw rates
#: sit on this lattice, against 0.12% of the same rows' signed fractions and 0.007% of
#: Hyperliquid's `funding_rate`. That gap is what makes the units bug detectable.
PERCENT_TICK = 1e-4

#: Non-zero values needed before the lattice test is evidence rather than coincidence.
MIN_LATTICE_ROWS = 8

#: Rows and distinct symbols needed before "no negative rate" is evidence. Both matter: 13
#: Lighter markets (TTWO, GME, ARM, QCOM, ...) have **no** negative hour in their entire history,
#: so a single-symbol frame may legitimately be all non-negative. A 20-symbol cross-section
#: cannot be -- only 13 of 235 markets qualify.
MIN_SIGN_ROWS = 1000
MIN_SIGN_SYMBOLS = 20

#: Columns that carry a funding rate and must therefore be on the common basis wherever they
#: appear. `rate` and `signed_rate` are Lighter's native percent columns; they are checked rather
#: than banned, because Phase 4's own target frames also call a column `rate`.
BASIS_COLUMNS = ("rate", "signed_rate", "signed_rate_fraction", "funding_rate", "spread")

#: Hyperliquid's settled rate wherever its inner clamp cancels the premium exactly -- the bare
#: interest-rate component, 0.01% per 8 hours. Venue-wide: Hyperliquid publishes no per-market
#: interest rate. The rate sits here on 59.86% of concurrent pair-hours, strictly below it on
#: 34.00% and negative on 23.98%, so it is a plateau, not a floor.
HL_BASELINE = 1.25e-5

#: Absolute tolerance for "this rate *is* that parameter". Both sides are float arithmetic over
#: the same decimals and they do not land on the same bits: `trunc4(0.01 / 8) / 100` is
#: 1.2000000000000002e-05 where the venue's own `0.0012 / 100` is 1.1999999999999999e-05, so `==`
#: finds none of the 445,795 rows it should. Six orders of magnitude below the 1e-6 fraction
#: lattice Lighter reports on, so it can never merge two distinct published values.
RATE_TOLERANCE = 1e-12

#: The flags `add_venue_flags` emits. Both are reported, neither deletes a row.
FLAG_COLUMNS = ("joint_baseline", "venue_clamped")


def non_adjacent_pairs(df: pl.DataFrame, *, on: str = "hour",
                       by: str = "symbol") -> pl.DataFrame:
    """The consecutive rows a one-row `shift` would pair and `forward_window` will not.

    One row per pair of rows adjacent **in the series** but not adjacent **in time**, carrying
    `by`, the later row's `on`, and `gap_hours`. Over the full Hyperliquid funding history this
    returns 2,002 rows: 1,789 at `gap_hours = 8` and 213 at `gap_hours = 2` (a single missing
    hour leaves a two-hour step). If it ever returns zero on that history, the grid is being
    read wrong.

    The `diff` here measures the grid itself, which is exactly what it is for; the rule this
    module enforces is that no *value* is ever carried across a row boundary."""
    return (df.select(by, on).sort([by, on])
            .with_columns(pl.col(on).diff().over(by).dt.total_hours().alias("gap_hours"))
            .drop_nulls("gap_hours")
            .filter(pl.col("gap_hours") != 1))


def forward_window(df: pl.DataFrame, *, h: int, on: str = "hour",
                   by: str = "symbol") -> pl.DataFrame:
    """Attach the next `h` hours of every value column to each row, by hour difference.

    For each `k` in `1..h` every column other than `on` and `by` gains a companion `<col>_h<k>`
    holding that column's value at `on + k` hours **for the same `by`**, found by an equality
    join on an explicitly offset hour. A forward hour that does not exist yields null: no row is
    borrowed from across a hole, and no value is interpolated.

    Adds `n_hours_used` -- how many of the `h` forward hours actually existed, 0 to `h`. It is a
    count of found *hours*, not of non-null values, so a legitimately null rate still counts as
    an hour that existed.

    Every input row comes back, in the input's order. A tail row whose window runs past the end
    of the series is data with `n_hours_used < h`, not a row to drop.

    A caller summing the window wants `pl.sum_horizontal`, which skips nulls -- so a short window
    sums the hours it has, and `n_hours_used` says how many that was.

    Select down to the key columns and the values you need before calling: the frame widens by
    one column per value per `k`."""
    if h < 1:
        raise ValueError(f"h must be at least 1, got {h}")
    values = [c for c in df.columns if c not in (on, by)]
    if not values:
        raise ValueError(f"nothing to attach: {df.columns} is only the join key")
    if df.select(by, on).is_duplicated().any():
        raise ValueError(f"duplicate ({by}, {on}) rows would multiply the join; de-duplicate "
                         "before taking a forward window")

    out, markers = df, []
    for k in range(1, h + 1):
        marker = f"_found_h{k}"
        markers.append(marker)
        out = out.join(
            df.select(pl.col(by),
                      (pl.col(on) - pl.duration(hours=k)).alias(on),
                      *(pl.col(c).alias(f"{c}_h{k}") for c in values),
                      pl.lit(True).alias(marker)),
            on=[by, on], how="left")
    return out.with_columns(
        pl.sum_horizontal(pl.col(m).fill_null(False).cast(pl.Int64) for m in markers)
        .alias("n_hours_used")).drop(markers)


def _lattice_violation(rates: pl.Series) -> bool:
    """Is every non-zero value an exact multiple of Lighter's percent tick?"""
    nz = rates.drop_nulls()
    nz = nz.filter(nz != 0)
    if nz.len() < MIN_LATTICE_ROWS:
        return False
    scaled = nz / PERCENT_TICK
    return bool(((scaled - scaled.round()).abs() < 1e-6).all())


def assert_common_basis(df: pl.DataFrame) -> None:
    """Raise unless every funding-rate column in `df` is a per-hour signed fraction.

    The common basis is the per-hour signed fraction, positive = longs pay shorts. Lighter's
    native rate is an unsigned percent; entering it without `signed_rate / 100` makes every
    number 100x too large and throws the sign away, which Phase 1 named the single most likely
    silent bug in this phase.

    Checked by value, on every column named in `BASIS_COLUMNS` that is present:

    1. **Scale** -- any magnitude above `MAX_HOURLY_FRACTION`. A percent crosses it as soon as
       the fraction passes 5e-4, which 0.80% of Lighter's hours do.
    2. **Lattice** -- every non-zero value an exact multiple of `PERCENT_TICK`. This is the sharp
       one: Lighter truncates to four decimals of percent, so 100.0% of its raw rates sit on that
       lattice against 0.12% of the same rows' fractions. It fires on quiet near-baseline data
       that the scale test cannot see -- the 13 all-positive equity markets have a whole history
       of `0.0004` percent, which is 4e-6 as a fraction.
    3. **Sign** -- a wide cross-section with no negative rate at all, which means the sign was
       dropped somewhere.

    A frame carrying no recognised rate column raises too, so the assert cannot silently pass
    something it never looked at.

    **What it cannot catch.** A basis error that preserves scale, lattice and sign: a
    convention flipped end to end (every sign inverted), a division by 100 applied twice, a
    correct column assembled from the wrong venue or the wrong hour, or a rate column under a
    name it does not know. The sign test needs a `symbol` column, `MIN_SIGN_ROWS` rows and
    `MIN_SIGN_SYMBOLS` symbols, so it is silent on a small, single-market or symbol-less frame;
    the lattice test needs `MIN_LATTICE_ROWS` non-zero values, so an all-zero column passes
    unexamined."""
    present = [c for c in BASIS_COLUMNS if c in df.columns]
    if not present:
        raise ValueError(f"no column on the common basis to check: expected one of "
                         f"{list(BASIS_COLUMNS)}, got {df.columns}")
    wide = (df.height >= MIN_SIGN_ROWS and "symbol" in df.columns
            and df["symbol"].n_unique() >= MIN_SIGN_SYMBOLS)
    for column in present:
        rates = df[column].drop_nulls()
        if rates.is_empty():
            continue
        biggest = float(rates.abs().max())
        if biggest > MAX_HOURLY_FRACTION:
            raise ValueError(
                f"{column!r} reaches {biggest:.6g}, too large for a per-hour funding fraction "
                f"(limit {MAX_HOURLY_FRACTION}) -- this looks like Lighter's raw percent; "
                "divide by 100 and apply the sign")
        if _lattice_violation(rates):
            raise ValueError(
                f"every non-zero value of {column!r} is a multiple of {PERCENT_TICK}, which is "
                "Lighter's four-decimal percent lattice, not the 1e-6 lattice a signed fraction "
                "lands on -- divide by 100 and apply the sign")
        if wide and rates.min() >= 0 and bool((rates != 0).any()):
            raise ValueError(
                f"{column!r} has no negative value across {df.height} rows of a cross-section: "
                "funding is signed, so the sign was dropped -- use `signed_rate`, not `rate`")


# --- the two flags: emitted, reported separately, never used to delete a row -----------------

def _sits_at(rate: pl.Expr, parameter: pl.Expr | float) -> pl.Expr:
    """Is this rate the value that parameter produces, up to `RATE_TOLERANCE`?"""
    return (rate - parameter).abs() <= RATE_TOLERANCE


def lighter_baseline(base_interest_rate_pct: pl.Expr) -> pl.Expr:
    """Lighter's settled rate at baseline, per market, as a per-hour signed fraction.

    The venue divides the market's annual-style base rate by 8 and truncates to four decimal
    places **of percent**, which is where the whole `joint_baseline` artifact comes from: 0.01%
    becomes `0.00125%`, truncates *down* to `0.0012%`, and lands 5.0e-7 below Hyperliquid's
    untruncated `1.25e-5`.

    **Takes the market's own parameter, never BTC's.** `base_interest_rate_pct` is 0.0100 on 119
    Lighter markets, 0.0032 on 89 and **0.0000 on 27** -- and a base rate of 0 puts the baseline
    at exactly 0, which is the only way the second joint-baseline spread value, `1.25e-5`, can
    arise. Within the 100 matched pairs only AI16Z, MKR, LAUNCHCOIN and YZY carry it."""
    return _trunc4(base_interest_rate_pct / 8) / 100


def lighter_clamp(funding_clamp_big_pct: pl.Expr) -> pl.Expr:
    """The largest magnitude a market's settled rate can take, as a per-hour signed fraction.

    Lighter applies its outer clamp *before* the division by 8, so the ceiling on the settled
    rate is `funding_clamp_big_pct / 8`, truncated as any rate is. It is 4.0% on 227 markets --
    every one of the 100 matched pairs, ceiling **5.0e-3** -- but 16.0 on RIVER, 20.0 on ARC,
    0.02 on five Korean equities and 0.0 on MKR.

    **Six markets breach their published ceiling and none of them is a matched pair**: the five
    Korean equities (SAMSUNG, HYUNDAI, KRCOMP, SKHYNIX, HANMI) observe rates up to 0.5%/h
    against a published 0.0025%/h, and MKR
    reaches 0.0636%/h against a published clamp of 0. Those parameters are wrong or differently
    scaled -- consistent with the multiplier-50 equity/RWA finding in `docs/phase4/decisions.md`
    -- so a later phase applying this to Target A's Lighter per-venue view cannot trust the
    parameter there. On Target B it is exact: the observed maximum on the matched panel is
    5.0000e-03 to five figures."""
    return _trunc4(funding_clamp_big_pct / 8) / 100


def add_venue_flags(df: pl.DataFrame, *, hl_rate: str = "hl_rate",
                    lighter_rate: str = "lighter_rate",
                    lighter_base_rate_pct: str = "lighter_base_rate_pct",
                    lighter_clamp_big_pct: str = "lighter_clamp_big_pct") -> pl.DataFrame:
    """Add `joint_baseline` and `venue_clamped`. Returns every input row, unchanged and in order.

    **This function cannot drop a row and nothing downstream may use these flags to drop one from
    the dataset.** The only sanctioned exclusions are `skill_metric_rows` and `tuning_rows`; call
    those at the point a metric is computed, not at the point the data is written.

    `joint_baseline` -- both venues sitting at their own baseline, **45.57% of concurrent
    pair-hours (445,904)**. The spread there takes exactly **two** values: `+5.0e-7` where
    Lighter's base rate is 0.01% (445,795 rows, the truncation of `0.00125%` to `0.0012%`) and
    `1.25e-5` where it is 0 (109 rows). That is a rounding rule, not a market, and a model scored
    on those hours is being graded on arithmetic. Excluding them un-masks the sign of everything
    else: mean spread is **-6.23e-8** overall and **-5.36e-7** without them. Reported separately
    they are a zero-turnover known-sign carry of **0.44%/year** -- the floor any model must beat.

    `venue_clamped` -- Lighter's outer clamp binding, **142 hours across 23 symbols and 42
    distinct days**. Mean |spread| there is 2.847e-3 = **28.5 bp/h, 149x the overall mean**, but
    the 142 hours are only **2.2% of all gross spread**: a risk and capacity question, not a
    revenue one. A market whose published clamp is not positive flags nothing -- MKR reports 0.0
    for every funding parameter while its own history reaches 6.36e-4, so believing that zero
    would flag all 4,463 of its hours as clamped.

    The two Lighter parameters are taken as **columns**, per market, because a scalar would be
    BTC's: the base rate alone takes three values across the venue and a flag calibrated on the
    matched set is wrong outside it. A null in either raises rather than silently flagging False,
    which is what an unjoined market would otherwise produce."""
    missing = [c for c in (hl_rate, lighter_rate, lighter_base_rate_pct, lighter_clamp_big_pct)
               if c not in df.columns]
    if missing:
        raise ValueError(f"cannot flag without {missing}: the flags need both venues' rates and "
                         f"each market's own Lighter parameters; {df.columns} carries neither a "
                         "spread's ingredients nor a substitute for them")
    for column in (lighter_base_rate_pct, lighter_clamp_big_pct):
        nulls = df[column].null_count()
        if nulls:
            raise ValueError(f"{column!r} is null on {nulls} of {df.height} rows -- a market "
                             "whose parameters did not join would flag False rather than raise; "
                             "join the Lighter market snapshot before flagging")
    ceiling = lighter_clamp(pl.col(lighter_clamp_big_pct))
    return df.with_columns(
        (_sits_at(pl.col(hl_rate), HL_BASELINE)
         & _sits_at(pl.col(lighter_rate),
                    lighter_baseline(pl.col(lighter_base_rate_pct)))).alias("joint_baseline"),
        ((ceiling > 0) & _sits_at(pl.col(lighter_rate).abs(), ceiling)).alias("venue_clamped"))


def _without(df: pl.DataFrame, flags: tuple[str, ...], purpose: str) -> pl.DataFrame:
    absent = [f for f in FLAG_COLUMNS if f not in df.columns]
    if absent:
        raise ValueError(f"{purpose} may not run on a frame missing {absent}: call "
                         "`add_venue_flags` first, rather than scoring the artifact hours")
    return df.filter(~pl.any_horizontal(pl.col(f) for f in flags))


def skill_metric_rows(df: pl.DataFrame) -> pl.DataFrame:
    """The rows a Phase 6-8 **skill metric** may be computed on: `joint_baseline` excluded.

    Decision D3. The clamp hours stay in -- they are real market, just a rare one -- and are
    reported separately. The excluded hours are reported separately too, as the 0.44%/year carry
    they are; they are not deleted from the dataset by this or anything else."""
    return _without(df, ("joint_baseline",), "a skill metric")


def tuning_rows(df: pl.DataFrame) -> pl.DataFrame:
    """The rows **hyperparameter selection** may see: both flags excluded.

    Decisions D3 and D4. A squared-error fit let near the 142 clamp hours would bend the whole
    model to chase a mean |spread| 149x its own, for 2.2% of the gross. They are still fitted on,
    still scored on, and still reported -- just not tuned on."""
    return _without(df, FLAG_COLUMNS, "hyperparameter selection")
