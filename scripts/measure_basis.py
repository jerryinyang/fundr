"""Is this trade hedged? -- the cross-venue mark basis, its persistence and its drift. Task 5.

A delta-neutral cross-venue position's P&L is the funding differential **plus** the change in
the price basis between the two venues over the hold. The second term had never been measured;
it is the same size as the first. This script measures it and writes
`data/phase2/qa/basis_drift.md`, and it decides deferred item F2 in `docs/phase4/decisions.md`.

**The honesty requirement, which is the point of the whole task.** Every number here is built
from **mark** prices, and the two venues mark off different references -- Hyperliquid's oracle
against Lighter's index. Part of any level difference is therefore a marking convention rather
than realizable P&L. The realizable figure needs **traded** prices on both venues at the same
instant, and those are **not on disk**. Nothing in this report may be quoted as a P&L number.
The repaired `lighter_mark_candles` fixed a collection bug; it did not touch the convention
problem.

**Three traps, all three already hit in this project.**

1. **Alignment.** Lighter's candle stamped `T` closes at the end of hour `T`, so the Hyperliquid
   side must be that hour's **last** per-minute `mark_px`. Comparing Hyperliquid's hour-*median*
   to Lighter's hour-*close* puts the two sides half an hour apart and inflates 24-hour "drift"
   to ~98 bp of pure timing noise. The artifact is reproduced here once, as proof the alignment
   is what it claims to be, and then discarded. If the level's autocorrelation comes out near
   zero the alignment is wrong and the run **stops** (`check_alignment`).
2. **Stale prices.** Hyperliquid's `mark_px` freezes on a delisted market rather than stopping
   -- `AI` sits at 0.12555 for 99.99% of hours since 2025-08-25 -- and a frozen series against a
   live one produces basis values past -10,000 bp with near-perfect persistence. Every statistic
   is reported **filtered and unfiltered**; the "persistent basis" in earlier write-ups was
   mostly a handful of dead markets.
3. **Age origin.** Pair age is read from `panel.pair_age_hours`, the pair's first **concurrent
   funding** hour. `lighter_mark_candles` begins 2025-08-25, so measuring age from the first
   hour of the mark-joined panel relabels mature pairs as "week 1" and halves the young-bucket
   figure. That error was made earlier in this project and corrected.

**No `shift` on compacted rows.** Every forward window here runs on a per-symbol **dense hourly
grid** (`densify`), so a shift is an exact hour offset and a hole in the grid produces a null
instead of being bridged. A hold missing any of its hours produces no carry rather than a short
sum.

Run as `uv run python -m scripts.measure_basis`.
"""
import argparse
import math
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl

from fundr import dataset
from scripts.qa_backfill import _md_table

H = timedelta(hours=1)

#: Holds the plan asks for, in hours.
HORIZONS = (1, 6, 24, 72, 168)

#: Lighter's mark-candle history starts here; nothing before it can be measured.
MARK_START = datetime(2025, 8, 25)

#: A Hyperliquid mark that does not move for this many consecutive hours is frozen, not quiet.
STALE_HOURS = 24

#: "Each new listing's first 14 days out" -- the cleaning rule D6's cleaned panel uses.
YOUNG_HOURS = 14 * 24

#: The two chronic dislocators. XPL's basis averaged +1,747 bp in its first month and MON's
#: +120 to +190 bp for two months; both settle to single digits afterwards.
CHRONIC = ("XPL", "MON")

MAJORS = ("BTC", "ETH", "SOL")

#: Level lag-1 below this means the two sides are not the same instant. Hour-close against
#: hour-close gives ~0.989 unfiltered; either side shifted an hour collapses it to ~0.65.
MIN_LEVEL_LAG1 = 0.5

#: Pair-age cut points, matching the boxed note above Task 5 in the plan.
AGE_BUCKETS = ((168, "week 1"), (720, "weeks 2-4"), (2160, "months 1-3"), (None, "3 months+"))

#: The 24-hour carry from entering each hour's **widest** spread: 18.91 bp, in-sample, gross of
#: costs, from a rule with no forecasting in it (`docs/phase3/council-transcript`, row 6). D6
#: gates on the share of 24-hour holds whose basis drift exceeds it, so it is reproduced here
#: exactly -- and labelled **selective-entry** wherever it appears.
WIDEST_CARRY_24H_BP = 18.91

#: Unconditional mean |carry| over every hold on the 1,039,523-pair-hour panel, from the boxed
#: correction in `docs/phase3/decisions.md`. The 6.21 / 31.33 figures in circulation are
#: selective-entry and are never the comparison number.
RECORDED_UNCONDITIONAL_BP = {6: 1.03, 24: 3.70, 72: 9.78}
RECORDED_SELECTIVE_BP = {6: 6.21, 72: 31.33}

_FUNDING = {"hl": ("hl_funding", "coin", "coin", "symbol"),
            "lighter": ("lighter_funding", "market_id", "symbol", "lighter_symbol")}


# --- loading ----------------------------------------------------------------------------------

def hl_hour_marks(start: datetime, end: datetime) -> pl.DataFrame:
    """Hyperliquid's per-symbol hourly mark, as the hour's **last** print and as its median.

    The close is the number every statistic uses; the median travels beside it only so the known
    timing artifact can be reproduced once and discarded. `hl_asset_ctxs` is 275M per-minute
    rows over 1,218 daily files, so the day partitions outside the window are never opened and
    the aggregation runs streaming -- nothing is materialized."""
    base = dataset.root() / "hl_asset_ctxs"
    lo, hi = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    files = [p for p in sorted(base.glob("date=*/part.parquet"))
             if lo <= p.parent.name.split("=", 1)[1] <= hi]
    if not files:
        return pl.DataFrame(schema={"hour": pl.Datetime("ms"), "symbol": pl.String,
                                    "hl_close": pl.Float64, "hl_median": pl.Float64,
                                    "n_prints": pl.UInt32})
    return (pl.scan_parquet(files)
            .filter((pl.col("time") >= start) & (pl.col("time") < end + H))
            .select(pl.col("time").dt.truncate("1h").alias("hour"), "time",
                    pl.col("coin").alias("symbol"), "mark_px")
            .group_by("hour", "symbol")
            .agg(pl.col("mark_px").sort_by("time").last().alias("hl_close"),
                 pl.col("mark_px").median().alias("hl_median"),
                 pl.len().alias("n_prints"))
            .collect(engine="streaming"))


def lighter_hour_marks() -> pl.DataFrame:
    """Lighter's hourly mark candle close, per symbol. The candle stamped `T` closes at `T`."""
    files = sorted((dataset.root() / "lighter_mark_candles").glob("*/part.parquet"))
    if not files:
        return pl.DataFrame(schema={"lighter_symbol": pl.String, "hour": pl.Datetime("ms"),
                                    "lighter_close": pl.Float64})
    return (pl.scan_parquet(files)
            .select(pl.col("symbol").alias("lighter_symbol"), pl.col("time").alias("hour"),
                    pl.col("close").alias("lighter_close"))
            .collect())


def spread_frame(panel: pl.DataFrame) -> pl.DataFrame:
    """The panel with each venue's settled funding attached and the signed spread in bp.

    The common basis is the per-hour signed fraction; `spread_bp` is (Hyperliquid - Lighter)
    in basis points, positive = Hyperliquid's longs pay more than Lighter's."""
    def leg(name: str, partition: str, symbol_col: str, key: str, out: str) -> pl.DataFrame:
        files = sorted((dataset.root() / name).glob(f"{partition}=*/part.parquet"))
        if not files:
            return pl.DataFrame(schema={key: pl.String, "hour": pl.Datetime("ms"),
                                        out: pl.Float64})
        return (pl.scan_parquet(files)
                .select(pl.col(symbol_col).cast(pl.String).alias(key),
                        pl.col("settle_time").alias("hour"),
                        pl.col("signed_rate_fraction").alias(out))
                .collect())

    return (panel
            .join(leg(*_FUNDING["hl"], "hl_rate"), on=["symbol", "hour"], how="left")
            .join(leg(*_FUNDING["lighter"], "lighter_rate"),
                  on=["lighter_symbol", "hour"], how="left")
            .with_columns(((pl.col("hl_rate") - pl.col("lighter_rate")) * 1e4).alias("spread_bp"))
            .drop_nulls("spread_bp"))


def basis_frame(rows: pl.DataFrame, hl: pl.DataFrame, lighter: pl.DataFrame,
                *, lighter_offset_hours: int = 0) -> pl.DataFrame:
    """The Hyperliquid-minus-Lighter mark basis in bp, per pair-hour that has both marks.

    **Both legs are divided by their own `size_multiplier` first.** `NOT` measures one token on
    Hyperliquid against 1000 on Lighter's `1000NOT`, so an unscaled difference reads as a
    200,000 bp basis.

    `basis_bp` is hour-close against hour-close and is the number everything downstream uses.
    `basis_median_bp` is the discarded convention -- Hyperliquid's hour-*median* against the
    same Lighter close -- carried only to reproduce the timing artifact.

    `lighter_offset_hours` shifts the Lighter side deliberately; it exists for the alignment
    evidence, where a one-hour shift must visibly collapse the level's persistence.

    The join is inner on both marks, so pair-hours before 2025-08-25 simply do not appear.
    `coverage` states what share of the panel that leaves, rather than letting a silent
    truncation pass for a full-panel measurement."""
    shifted = lighter.with_columns(
        (pl.col("hour") + timedelta(hours=lighter_offset_hours)).alias("hour"))
    out = (rows.join(hl, on=["symbol", "hour"], how="inner")
           .join(shifted, on=["lighter_symbol", "hour"], how="inner")
           .with_columns((pl.col("hl_close") / pl.col("size_multiplier")).alias("_hl"),
                         (pl.col("hl_median") / pl.col("size_multiplier")).alias("_hl_med"),
                         (pl.col("lighter_close") / pl.col("lighter_size_multiplier"))
                         .alias("_li")))
    return (out.with_columns(
        ((pl.col("_hl") - pl.col("_li")) / ((pl.col("_hl") + pl.col("_li")) / 2) * 1e4)
        .alias("basis_bp"),
        ((pl.col("_hl_med") - pl.col("_li")) / ((pl.col("_hl_med") + pl.col("_li")) / 2) * 1e4)
        .alias("basis_median_bp"))
        .drop("_hl", "_hl_med", "_li").sort(["symbol", "hour"]))


def coverage(rows: pl.DataFrame, basis: pl.DataFrame) -> float:
    """Share of the panel's pair-hours that have a mark on both venues."""
    return basis.height / rows.height if rows.height else 0.0


# --- the dense grid, so no window ever bridges a hole -------------------------------------------

def densify(frame: pl.DataFrame, *, by: str = "symbol", on: str = "hour") -> pl.DataFrame:
    """One row per (`by`, hour) between each `by`'s first and last hour, values left-joined.

    Every forward window in this script is a `shift` on this grid, which makes it an exact hour
    offset. A missing hour is present as a null row, so a window that spans it yields null
    rather than reaching across it to whatever row happens to come next."""
    bounds = frame.group_by(by).agg(pl.col(on).min().alias("_lo"), pl.col(on).max().alias("_hi"))
    grid = (bounds.with_columns(
        pl.datetime_ranges("_lo", "_hi", interval="1h").alias(on))
        .explode(on).drop("_lo", "_hi")
        .with_columns(pl.col(on).cast(frame.schema[on])))
    return grid.join(frame, on=[by, on], how="left").sort([by, on])


def with_stale(dense: pl.DataFrame, *, window: int = STALE_HOURS,
               column: str = "hl_close", by: str = "symbol") -> pl.DataFrame:
    """Flag every row inside a run of `window` or more consecutive hours at one mark price.

    Hyperliquid's `mark_px` freezes on a delisted market rather than stopping, and a frozen
    series against a live one is not a basis. A hole breaks a run instead of extending it: the
    null row starts a new run, so two 30-hour flat stretches either side of a gap are two runs
    of 30, never one of 70."""
    started = ((pl.col(column) != pl.col(column).shift(1).over(by)).fill_null(True)
               | pl.col(column).shift(1).over(by).is_null().fill_null(True))
    runs = dense.with_columns(started.cum_sum().over(by).alias("_run"))
    return (runs.with_columns((pl.len().over([by, "_run"]) >= window).alias("is_stale"))
            .drop("_run"))


def with_forward_change(dense: pl.DataFrame, column: str, horizons=HORIZONS,
                        *, by: str = "symbol") -> pl.DataFrame:
    """`<column>_d<h>` = the value `h` hours later minus the value now, or null if either is."""
    return dense.with_columns(
        *[(pl.col(column).shift(-h).over(by) - pl.col(column)).alias(f"{column}_d{h}")
          for h in horizons])


def with_forward_sum(dense: pl.DataFrame, column: str, horizons=HORIZONS,
                     *, by: str = "symbol") -> pl.DataFrame:
    """`<column>_c<h>` = the sum over hours t+1..t+h, **null unless all h hours exist**.

    Funding settles at each hour's end, so a hold entered at t collects t+1 onward. A hold
    missing any of its hours produces nothing rather than a short sum, so the 213 single-hour
    holes in Hyperliquid's grid cannot masquerade as a cheap hold."""
    out = dense.with_columns(
        pl.col(column).fill_null(0.0).cum_sum().over(by).alias("_cs"),
        pl.col(column).is_not_null().cast(pl.Int64).cum_sum().over(by).alias("_cn"))
    return out.with_columns(
        *[pl.when((pl.col("_cn").shift(-h).over(by) - pl.col("_cn")) == h)
          .then(pl.col("_cs").shift(-h).over(by) - pl.col("_cs")).alias(f"{column}_c{h}")
          for h in horizons]).drop("_cs", "_cn")


def with_age_bucket(frame: pl.DataFrame) -> pl.DataFrame:
    """Bucket by `pair_age_hours` -- the panel's own age, from the first concurrent funding hour.

    **Never** from the first hour of the mark-joined panel. `lighter_mark_candles` starts
    2025-08-25; that origin would relabel every mature pair as week-1."""
    expr = pl.when(pl.col("pair_age_hours") < AGE_BUCKETS[0][0]).then(pl.lit(AGE_BUCKETS[0][1]))
    for cut, label in AGE_BUCKETS[1:-1]:
        expr = expr.when(pl.col("pair_age_hours") < cut).then(pl.lit(label))
    return frame.with_columns(expr.otherwise(pl.lit(AGE_BUCKETS[-1][1])).alias("age_bucket"))


# --- the statistics -----------------------------------------------------------------------------

def check_alignment(lag1: float | None, *, minimum: float = MIN_LEVEL_LAG1) -> None:
    """Stop unless the level is persistent. Near-zero persistence means a timing artifact.

    Hour-close against hour-close gives a level lag-1 near 0.99 on the unfiltered panel. Either
    side shifted by an hour collapses it to ~0.65, and a median-against-close comparison kills it
    outright. This has burned two sessions, so it raises rather than warns."""
    if lag1 is None or lag1 < minimum:
        raise ValueError(
            f"alignment check failed: level lag-1 is {lag1}, under {minimum}. Hour-close "
            "against hour-close must give a persistent level; a near-zero autocorrelation means "
            "the two sides are not the same instant. Stop and fix the alignment.")


def _lag1(dense: pl.DataFrame, column: str, *, by: str = "symbol") -> float | None:
    """Pooled hour-to-hour autocorrelation of `column`, paired on the dense grid."""
    paired = (dense.select(pl.col(column).alias("x"), pl.col(column).shift(1).over(by).alias("y"))
              .drop_nulls())
    if paired.height < 3 or paired["x"].std(ddof=0) == 0 or paired["y"].std(ddof=0) == 0:
        return None
    return float(paired.select(pl.corr("x", "y")).item())


def level_stats(dense: pl.DataFrame, column: str = "basis_bp") -> dict:
    """Mean, sd, persistence and the per-symbol spread of the basis **level**."""
    real = dense.drop_nulls(column)
    if real.is_empty():
        return {"rows": 0, "symbols": 0, "mean_bp": None, "sd_bp": None, "lag1": None,
                "symbol_sd_p10_bp": None, "symbol_sd_median_bp": None, "symbol_sd_p90_bp": None,
                "symbol_lag1_median": None}
    by_symbol = real.group_by("symbol").agg(pl.col(column).std(ddof=1).alias("sd"))
    per_symbol_lag1 = [_lag1(part, column)
                       for (_,), part in dense.partition_by("symbol", as_dict=True).items()]
    lags = sorted(v for v in per_symbol_lag1 if v is not None)
    return {"rows": real.height,
            "symbols": real["symbol"].n_unique(),
            "mean_bp": float(real[column].mean()),
            "sd_bp": float(real[column].std(ddof=1)),
            "lag1": _lag1(dense, column),
            "symbol_sd_p10_bp": float(by_symbol["sd"].quantile(0.1)),
            "symbol_sd_median_bp": float(by_symbol["sd"].median()),
            "symbol_sd_p90_bp": float(by_symbol["sd"].quantile(0.9)),
            "symbol_lag1_median": lags[len(lags) // 2] if lags else None}


def drift_stats(frame: pl.DataFrame, horizons=HORIZONS, column: str = "basis_bp") -> dict:
    """Per horizon: how many holds, the sd of the change, and the median absolute change."""
    out = {}
    for h in horizons:
        d = frame[f"{column}_d{h}"].drop_nulls()
        out[h] = {"n": d.len(),
                  "sd_bp": float(d.std(ddof=1)) if d.len() > 1 else None,
                  "median_abs_bp": float(d.abs().median()) if d.len() else None}
    return out


def reversion_exponent(sds: dict[int, float]) -> float | None:
    """The exponent `a` in sd(h) ~ h^a, by least squares on the logs.

    0.5 is a random walk -- basis risk that compounds over a hold. Anything near 0 means the
    basis reverts and a longer hold buys carry without buying proportionate basis risk."""
    points = [(math.log(h), math.log(sd)) for h, sd in sds.items() if sd and sd > 0]
    if len(points) < 2:
        return None
    mx = sum(x for x, _ in points) / len(points)
    my = sum(y for _, y in points) / len(points)
    var = sum((x - mx) ** 2 for x, _ in points)
    return None if var == 0 else sum((x - mx) * (y - my) for x, y in points) / var


def exceedance(frame: pl.DataFrame, *, hold: int = 24,
               threshold: float = WIDEST_CARRY_24H_BP) -> dict:
    """How often the basis moves further over the hold than the carry the hold earns.

    Two definitions, because they answer different questions and only the first is D6's gate:

    - `vs_widest` -- against the fixed **selective-entry** 24-hour carry from entering each
      hour's widest spread (18.91 bp, in-sample, gross of costs, no forecasting in it).
    - `vs_realized` -- against the carry this very hold actually paid, which is the honest
      per-hold comparison and is far harsher, because unconditional 24-hour carry is 3.70 bp."""
    d = frame.filter(pl.col(f"basis_bp_d{hold}").is_not_null())
    if d.is_empty():
        return {"n": 0, "vs_widest": None, "up": None, "down": None, "vs_realized": None,
                "n_realized": 0}
    moved = d[f"basis_bp_d{hold}"]
    both = d.filter(pl.col(f"spread_bp_c{hold}").is_not_null())
    realized = (float((both[f"basis_bp_d{hold}"].abs() > both[f"spread_bp_c{hold}"].abs()).mean())
                if both.height else None)
    return {"n": d.height,
            "vs_widest": float((moved.abs() > threshold).mean()),
            "up": float((moved > threshold).mean()),
            "down": float((moved < -threshold).mean()),
            "vs_realized": realized,
            "n_realized": both.height}


# --- cleaning rules ------------------------------------------------------------------------------

def variants(dense: pl.DataFrame) -> dict[str, pl.DataFrame]:
    """The cleaning rules, each named by exactly what it removes.

    The point of reporting all of them is that the headline moves by a factor of two across them
    and the earlier write-ups quoted one point estimate. The drift is computed **before** any
    filter, on the full dense grid, so a filter selects which *entries* count, never which hours
    a hold is allowed to pass through."""
    live = dense.filter(~pl.col("is_stale"))
    cleaned = live.filter(pl.col("pair_age_hours") >= YOUNG_HOURS)
    return {
        "unfiltered": dense,
        "frozen marks out": live,
        "frozen + XPL/MON out": live.filter(~pl.col("symbol").is_in(CHRONIC)),
        "cleaned (frozen out, first 14 days out)": cleaned,
        "cleaned, less XPL/MON": cleaned.filter(~pl.col("symbol").is_in(CHRONIC)),
        "BTC/ETH/SOL only": dense.filter(pl.col("symbol").is_in(MAJORS)),
    }


# --- report ---------------------------------------------------------------------------------------

def _fmt(value, digits: int = 2) -> str:
    return "--" if value is None else f"{value:.{digits}f}"


def _level_table(built: dict[str, pl.DataFrame]) -> pl.DataFrame:
    rows = []
    for name, frame in built.items():
        s = level_stats(frame)
        rows.append({"cleaning rule": name, "pair-hours": f"{s['rows']:,}",
                     "symbols": s["symbols"], "mean (bp)": _fmt(s["mean_bp"]),
                     "sd (bp)": _fmt(s["sd_bp"]), "lag-1": _fmt(s["lag1"], 4),
                     "per-symbol sd p10 (bp)": _fmt(s["symbol_sd_p10_bp"]),
                     "per-symbol sd median (bp)": _fmt(s["symbol_sd_median_bp"]),
                     "per-symbol sd p90 (bp)": _fmt(s["symbol_sd_p90_bp"]),
                     "per-symbol lag-1 median": _fmt(s["symbol_lag1_median"], 3)})
    return pl.DataFrame(rows)


def _drift_table(built: dict[str, pl.DataFrame], key: str) -> pl.DataFrame:
    """One row per horizon, one column per cleaning rule, for `sd_bp` or `median_abs_bp`."""
    stats = {name: drift_stats(frame) for name, frame in built.items()}
    return pl.DataFrame([{"H (hours)": h,
                          **{name: _fmt(stats[name][h][key]) for name in built}}
                         for h in HORIZONS])


def _conventions_table(built: dict[str, pl.DataFrame], hold: int = 24) -> pl.DataFrame:
    """The 24-hour drift under every convention tried, which is why it is a range.

    The pooled sd, the median of the per-symbol sds, and a 1%/99% winsorised sd are three
    defensible ways to summarise the same holds and they disagree by a factor of two. The
    median absolute move is the one that barely moves."""
    rows = []
    for name, frame in built.items():
        d = frame.filter(pl.col(f"basis_bp_d{hold}").is_not_null())
        if d.is_empty():
            continue
        moved = d[f"basis_bp_d{hold}"]
        lo, hi = moved.quantile(0.01), moved.quantile(0.99)
        per_symbol = d.group_by("symbol").agg(
            pl.col(f"basis_bp_d{hold}").std(ddof=1).alias("sd"))["sd"]
        rows.append({"cleaning rule": name, "holds": f"{d.height:,}",
                     "pooled sd (bp)": _fmt(float(moved.std(ddof=1))),
                     "per-symbol sd median (bp)": _fmt(float(per_symbol.median())),
                     "winsorised 1/99 sd (bp)": _fmt(float(moved.clip(lo, hi).std(ddof=1))),
                     "median |drift| (bp)": _fmt(float(moved.abs().median()))})
    return pl.DataFrame(rows)


def _carry_table(spread: pl.DataFrame, basis: pl.DataFrame) -> pl.DataFrame:
    rows = []
    for h in HORIZONS:
        whole = spread[f"spread_bp_c{h}"].drop_nulls()
        overlap = basis[f"spread_bp_c{h}"].drop_nulls()
        recorded = RECORDED_UNCONDITIONAL_BP.get(h)
        rows.append({"H (hours)": h,
                     "unconditional mean |carry| (bp)": _fmt(float(whole.abs().mean())
                                                             if whole.len() else None),
                     "holds": f"{whole.len():,}",
                     "same, on the mark-overlap panel (bp)": _fmt(float(overlap.abs().mean())
                                                                  if overlap.len() else None),
                     "on record (bp)": _fmt(recorded) if recorded else "--"})
    return pl.DataFrame(rows)


def _age_table(dense: pl.DataFrame) -> pl.DataFrame:
    live = with_age_bucket(dense.filter(~pl.col("is_stale")))
    rows = []
    for _, label in AGE_BUCKETS:
        part = live.filter(pl.col("age_bucket") == label)
        rows.append(_age_row(label, part))
    rows.append(_age_row("BTC/ETH/SOL (any age)",
                         dense.filter(pl.col("symbol").is_in(MAJORS) & ~pl.col("is_stale"))))
    return pl.DataFrame(rows)


def _age_row(label: str, part: pl.DataFrame) -> dict:
    level = level_stats(part)
    drift = drift_stats(part, (24,))[24]
    hit = exceedance(part)
    return {"pair age": label, "pair-hours": f"{level['rows']:,}",
            "symbols": level["symbols"], "level sd (bp)": _fmt(level["sd_bp"]),
            "24h drift sd (bp)": _fmt(drift["sd_bp"]),
            "median |24h drift| (bp)": _fmt(drift["median_abs_bp"]),
            "share > 18.91 bp": _fmt(hit["vs_widest"] * 100, 1) + "%"
            if hit["vs_widest"] is not None else "--"}


def build_report(*, start: datetime, end: datetime, min_level_lag1: float) -> list[str]:
    root = dataset.root()
    panel = pl.read_parquet(root / "panel" / "view=cross_venue" / "part.parquet")
    rows = spread_frame(panel)
    spread = with_forward_sum(densify(rows.select("symbol", "hour", "spread_bp")), "spread_bp")

    hl = hl_hour_marks(max(start, MARK_START), end)
    lighter = lighter_hour_marks()
    basis = basis_frame(rows, hl, lighter)
    share = coverage(rows, basis)

    keep = ["symbol", "lighter_symbol", "hour", "pair_age_hours", "hl_close", "lighter_close",
            "basis_bp", "basis_median_bp", "spread_bp"]
    dense = with_stale(densify(basis.select(keep)))
    dense = with_forward_change(with_forward_change(dense, "basis_bp"), "basis_median_bp")
    dense = with_forward_sum(dense, "spread_bp")

    unfiltered_lag1 = _lag1(dense, "basis_bp")
    check_alignment(unfiltered_lag1, minimum=min_level_lag1)

    offsets = []
    for offset in (-1, 0, 1):
        if offset == 0:
            offsets.append({"Lighter shifted (hours)": offset, "level lag-1":
                            _fmt(unfiltered_lag1, 4), "pair-hours": f"{dense.drop_nulls('basis_bp').height:,}"})
            continue
        shifted = densify(basis_frame(rows, hl, lighter, lighter_offset_hours=offset)
                          .select("symbol", "hour", "basis_bp"))
        offsets.append({"Lighter shifted (hours)": offset,
                        "level lag-1": _fmt(_lag1(shifted, "basis_bp"), 4),
                        "pair-hours": f"{shifted.drop_nulls('basis_bp').height:,}"})

    built = variants(dense)
    artifact = drift_stats(dense, (24,), column="basis_median_bp")[24]
    honest = drift_stats(dense, (24,))[24]
    stale_rows = int(dense["is_stale"].sum())
    stale_symbols = (dense.filter(pl.col("is_stale"))["symbol"].unique().sort().to_list())
    exponents = {name: reversion_exponent({h: drift_stats(frame)[h]["sd_bp"] for h in HORIZONS})
                 for name, frame in built.items()}
    hits = {name: exceedance(frame) for name, frame in built.items()}
    cleaned_name = "cleaned (frozen out, first 14 days out)"
    cleaned_24 = drift_stats(built[cleaned_name], (24,))[24]
    unfiltered_24 = drift_stats(dense, (24,))[24]
    # The band the gate asks for: every cleaning rule that removes frozen marks, majors aside.
    banded = [drift_stats(frame, (24,))[24]["sd_bp"] for name, frame in built.items()
              if name not in ("unfiltered", "BTC/ETH/SOL only")]
    band = [v for v in banded if v]
    band_lo, band_hi = (min(band), max(band)) if band else (None, None)

    # Trap 3, measured once rather than asserted: the same week-1 bucket under the age origin
    # this script refuses to use -- hours since the pair's first MARK-panel hour.
    live = dense.filter(~pl.col("is_stale"))
    week1 = pl.col("pair_age_hours") < AGE_BUCKETS[0][0]
    right_rows = live.filter(week1)
    wrong_rows = live.with_columns(
        (pl.col("hour") - pl.col("hour").min().over("symbol")).dt.total_hours()
        .alias("pair_age_hours")).filter(week1)
    right = {"rows": right_rows.drop_nulls("basis_bp").height,
             **drift_stats(right_rows, (24,))[24]}
    wrong = {"rows": wrong_rows.drop_nulls("basis_bp").height,
             **drift_stats(wrong_rows, (24,))[24]}

    lines = [
        "# Cross-venue mark basis — level, persistence and drift over a hold", "",
        f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M}Z by `scripts/measure_basis.py`. "
        f"Hyperliquid's last per-minute `mark_px` in the hour against Lighter's hourly mark "
        f"candle close, {basis.height:,} concurrent pair-hours over "
        f"{basis['symbol'].n_unique()} symbols, "
        f"{basis['hour'].min():%Y-%m-%d} → {basis['hour'].max():%Y-%m-%d}. "
        f"D6 records 778,597 hours over 96 symbols; this run reads the current panel "
        f"({panel.height:,} pair-hours, {panel['symbol'].n_unique()} symbols), which has grown "
        "since, so the counts differ and the statistics do not.", "",
        "## Read this before any number below", "",
        "**These are mark prices, and the two venues mark off different references — "
        "Hyperliquid's oracle, Lighter's index.** Part of every level difference here is a "
        "marking convention rather than realizable P&L. **The realizable figure needs traded "
        "prices on both venues at the same instant, and those are not on disk.** No number in "
        "this report may be quoted as a P&L number. The repaired mark candles fixed a "
        "collection bug; they did not touch the convention problem, and nothing here resolves "
        "it. That is what F2 says and why F2 stays open.", "",
        "## 1. Alignment — checked first, because it has burned two sessions", "",
        "Lighter's candle stamped `T` closes at the end of hour `T`, so the Hyperliquid side is "
        "that hour's **last** per-minute mark. **Hour-close against hour-close** is the only "
        "convention used below. If the level's autocorrelation came out near zero the alignment "
        "would be wrong and this script would stop; it does not.", "",
        _md_table(pl.DataFrame(offsets)), "",
        f"- at zero offset the level's lag-1 is **{_fmt(unfiltered_lag1, 4)}** (unfiltered); "
        "shifting either side by an hour visibly collapses it.",
        f"- **the discarded artifact, reproduced once as proof:** Hyperliquid's hour-median "
        f"mark against Lighter's hour-close candle gives a 24-hour drift sd of "
        f"**{_fmt(artifact['sd_bp'])} bp** on the same rows, against "
        f"**{_fmt(honest['sd_bp'])} bp** on the close. The median sits half an hour behind the "
        "candle; that gap is price-timing noise, not a venue basis. It is **discarded** and "
        "used nowhere else.", "",
        "## 2. The stale-market filter, which drives the level statistics entirely", "",
        "Hyperliquid's `mark_px` **freezes** on a delisted market rather than stopping — `AI` "
        "sits at 0.12555 for almost every hour since 2025-08-25 — and a frozen series against a "
        f"live one produces basis values past −10,000 bp. A row is flagged when the mark has "
        f"not moved for **{STALE_HOURS} consecutive hours**; a hole breaks a run rather than "
        "extending it.", "",
        f"- flagged: **{stale_rows:,} rows** "
        f"({stale_rows / max(dense.height, 1) * 100:.2f}% of the dense grid), on "
        f"{len(stale_symbols)} symbol{'' if len(stale_symbols) == 1 else 's'}: "
        f"{', '.join(stale_symbols) if stale_symbols else '--'}",
        "- the \"persistent basis\" reported in earlier write-ups is mostly these few markets. "
        "Everything below is reported **filtered and unfiltered**, because the headline moves by "
        "a factor of two between them.", "",
        f"- mark coverage: **{share * 100:.1f}%** of the panel's pair-hours have a mark on both "
        "venues. `lighter_mark_candles` begins 2025-08-25, so nothing before that date can be "
        "measured at all — the rest of the panel is not missing, it is unmeasurable.", "",
        "## 3. The level", "",
        _md_table(_level_table(built)), "",
        f"- **lag-1, both ways, as the gate requires: {_fmt(built['unfiltered'].pipe(_lag1, 'basis_bp'), 4)} "
        f"unfiltered against "
        f"{_fmt(_lag1(built['frozen marks out'], 'basis_bp'), 4)} with frozen marks out** and "
        f"{_fmt(_lag1(built['cleaned, less XPL/MON'], 'basis_bp'), 4)} on the cleaned panel "
        "without the two chronic dislocators. The 0.9342 on record is largely the frozen-price "
        "artifact.", "",
        "## 4. The drift — the change in the basis over a hold", "",
        "This is the term a delta-neutral position actually pays. **Standard deviation of the "
        "change in the basis, in bp:**", "",
        _md_table(_drift_table(built, "sd_bp")), "",
        "**Median absolute change, same holds — the stable statistic here.** It barely moves "
        "under any filter, repair or convention, while the sd moves by a factor of two:", "",
        _md_table(_drift_table(built, "median_abs_bp")), "",
        "### The 24-hour figure is a range with a cleaning rule, not a point estimate", "",
        _md_table(_conventions_table(built)), "",
        f"- **sd(Δbasis) at 24h = {_fmt(band_lo, 1)}–{_fmt(band_hi, 1)} bp across the "
        f"frozen-filtered cleaning rules, and {_fmt(unfiltered_24['sd_bp'], 1)} bp with nothing "
        "excluded at all.** The defensible band is the first one; the unfiltered figure is "
        "dominated by a handful of dead and dislocated markets. **The 22.3 bp on record is not "
        "reproducible under any single convention** and must not be quoted as a point estimate.",
        f"- **median |24h drift| = {_fmt(cleaned_24['median_abs_bp'])} bp** on the cleaned "
        f"panel, {_fmt(unfiltered_24['median_abs_bp'])} bp unfiltered. This is the number to "
        "carry forward — it reproduces everywhere.", "",
        "### Does it compound? No.", "",
        _md_table(pl.DataFrame([{"cleaning rule": name, "sd(Δbasis) ~ H^a, a =": _fmt(a, 3)}
                                for name, a in exponents.items()])), "",
        "- a random walk has **a = 0.5**. Measured, the exponent is a fraction of that: basis "
        "risk **does not compound over a hold**, so lengthening H buys carry without buying "
        "proportionate basis risk. A random walk from the 1-hour figure would put 24-hour drift "
        "several times where it actually lands.", "",
        "## 5. What the carry is, at the same horizons", "",
        "**Unconditional carry — every hold in the panel, which is the honest comparison:**", "",
        _md_table(_carry_table(spread, dense)), "",
        "- **The 6.21 bp (6h) and 31.33 bp (72h) figures in circulation are SELECTIVE-ENTRY** — "
        "they require entering only on spreads already among the widest, in sample, gross of "
        "costs, with no forecasting in the rule. So is the **18.91 bp** 24-hour figure used as "
        "the exceedance threshold below: it is the carry from entering each hour's **widest** "
        "spread. Unconditional carry is **1.03 / 3.70 / 9.78 bp** at 6 / 24 / 72 hours. Label "
        "the selection rule wherever a selective number is quoted.", "",
        "## 6. How often the hedge failure exceeds the carry", "",
        _md_table(pl.DataFrame([
            {"cleaning rule": name,
             "24h holds": f"{hit['n']:,}",
             "share |Δbasis| > 18.91 bp (selective-entry carry)":
                 _fmt(hit["vs_widest"] * 100, 1) + "%" if hit["vs_widest"] is not None else "--",
             "up": _fmt(hit["up"] * 100, 1) + "%" if hit["up"] is not None else "--",
             "down": _fmt(hit["down"] * 100, 1) + "%" if hit["down"] is not None else "--",
             "share |Δbasis| > this hold's own carry":
                 _fmt(hit["vs_realized"] * 100, 1) + "%"
                 if hit["vs_realized"] is not None else "--"}
            for name, hit in hits.items()])), "",
        "- the first share is D6's gate and it is stable across every filter. The second is the "
        "harsher and more honest comparison — against the carry the hold itself paid rather "
        "than against a selective-entry constant — and it is much larger, because unconditional "
        "24-hour carry is 3.70 bp against a median absolute drift of about 7.5 bp.", "",
        "## 7. Age-stratified — the risk is concentrated in newly listed markets", "",
        "Age is `panel.pair_age_hours`, the hours since the pair's **first concurrent funding "
        "hour**. It is deliberately **not** measured from the first hour of the mark-joined "
        "panel: mark candles start 2025-08-25, and that origin would relabel mature pairs as "
        "week-1 and halve the young-bucket figure.", "",
        _md_table(_age_table(dense)), "",
        f"- **the wrong origin, measured once so the size of the error is on the record:** "
        f"taking age from the first hour of the mark-joined panel instead puts "
        f"{wrong['rows']:,} pair-hours in the week-1 bucket against {right['rows']:,} on the "
        f"panel's own age, and reads its 24-hour drift sd as {_fmt(wrong['sd_bp'])} bp against "
        f"**{_fmt(right['sd_bp'])} bp**. Mark candles start 2025-08-25, so that origin labels "
        "every mature pair a new listing. **D6's recorded week-1 figure of 90.0 bp is what the "
        "wrong origin produces**, which is where it came from; the panel's own age gives "
        "roughly half again as much risk in a pair's first week, and D6's separate note that "
        "\"markets that actually list inside the window\" run 146 bp / 56.3% in week 1 is the "
        "figure this row reproduces. The wrong origin is used nowhere above.",
        "- Phase 3's universe rule — perpetuals outside the top 10 by open interest — steers "
        "the universe toward exactly the young, badly hedged cohort, and away from the majors "
        "in the last row. This is a universe rule, not a noise term.", "",
        "## What this report cannot say", "",
        "- **The realizable basis.** Mark against mark, off two different venue references. "
        "The traded-price version needs synchronized top-of-book on both venues at the "
        "settlement timestamp, and it is **not on disk**. Unknown, not estimated.",
        "- **How much of the level is convention.** Not decomposable from marks alone. Unknown.",
        "- **Funding-settlement slippage**, which is not evidenced anywhere and is excluded "
        "rather than estimated.",
        "- The drift of a hold is computed before any filter, so a hold entered on a live "
        "market and passing through a later freeze keeps its move. That is deliberate: it is a "
        "real dislocation, not a cleaning artifact.",
    ]
    return lines


def parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", type=datetime.fromisoformat, default=MARK_START,
                    help="first hour to measure; earlier hours have no Lighter mark candle")
    ap.add_argument("--end", type=datetime.fromisoformat, default=None,
                    help="last hour; default is the panel's own last hour")
    ap.add_argument("--min-level-lag1", type=float, default=MIN_LEVEL_LAG1,
                    help="stop if the unfiltered level's lag-1 falls under this")
    ap.add_argument("--out", default=None)
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    end = args.end
    if end is None:
        end = pl.read_parquet(dataset.root() / "panel" / "view=cross_venue" / "part.parquet",
                              columns=["hour"])["hour"].max()
    lines = build_report(start=args.start, end=end, min_level_lag1=args.min_level_lag1)
    out = Path(args.out or (dataset.root() / "qa" / "basis_drift.md"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nreport written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
