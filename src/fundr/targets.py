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
"""
import polars as pl

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
