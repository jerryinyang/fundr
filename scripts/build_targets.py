"""Build Phase 4's forecasting targets: the cumulative funding level over a hold, ranked.

Writes, under `data/phase2/`:

    targets/view=<cross_venue|per_venue>/horizon=<1|6|24|72>/part.parquet

`cross_venue` is Target B -- the Hyperliquid-minus-Lighter spread `S_t` on the concurrent
matched panel. `per_venue` is Target A -- each venue's own settled rate `F_t`, on every market
that venue lists. Both carry the same target columns:

    y_cum_level          Sigma_{h=1..H} S_{t+h}  (or F_{t+h}) -- what the hold is actually paid
    n_hours_used         how many of the H forward hours existed. Nothing is imputed.
    y_rank               dense cross-sectional rank of y_cum_level inside the hour, 1 = largest
    n_ranked_this_hour   the width of that cross-section, so a rank can be read
    benchmark_cum_level  the no-change benchmark: n_hours_used x today's level
    benchmark_rank       the rank-by-current-level benchmark, over exactly the same rows

**This target is a REDEFINITION of the design's Target B, and the redefinition is the point.**
`docs/funding_research_design.md` defines Target B as the *next change* in the spread,
`S_{t+1} - S_t`, and asks whether today's state predicts the spread's subsequent movement.
`y_cum_level` is a different quantity: the *cumulative level over a hold*. At H = 1 the two are
informationally identical -- `S_t` is in the information set, so subtracting it changes the loss
function and nothing else -- but **at H = 6, 24 and 72 they are different quantities**, dominated
by the persistent component of the level (lag-1 0.699) rather than by its increments (lag-1
-0.177). The reasoning is `docs/phase4/decisions.md` D1: a position is paid the level hourly, and
differencing manufactures a 40.27% point mass at exactly zero that the model then has to spend
capacity on. Anyone comparing these datasets against the design document should read this
paragraph first; the design's question was replaced deliberately, not answered.

**The benchmark ships with the target because a level target scores beautifully for free.**
Hyperliquid's rate level has lag-1 autocorrelation 0.886, so "same as last hour" is worth roughly
0.79 R-squared having learned nothing. D2 makes the no-change benchmark mandatory for Target A;
D1's boxed correction makes **rank by current `S_t`** mandatory for Target B. Both are emitted as
columns rather than left to a later phase to remember. A model that does not beat
`benchmark_rank` on the ranking, or `benchmark_cum_level` on the level, has demonstrated
persistence and nothing else -- whatever its absolute score.

**No lag is built with `shift`.** Every forward hour comes from `fundr.targets.forward_window`,
an explicit hour-difference join: Hyperliquid's settled grid carries 1,789 eight-hour intervals
and 213 single-hour holes, and a one-row shift pairs rates two or eight hours apart on 2,002
occasions without raising anything. A window that runs over a hole or off the end of a series is
short, and `n_hours_used` says by how much. Measured on what this script actually reads: all
**2,002** of those non-adjacent pairs sit in Hyperliquid's full 4.68M-row history and **none**
of them falls inside Phase 3's panel window, so every short window here is a series tail rather
than a hole. That is a property of the current `--start`, not a property of the data -- move the
window back and the holes reappear, which is why the join is built this way regardless.

**Both legs enter on the common basis and `assert_common_basis` is called on each.** Lighter's
raw `rate` is an unsigned percent -- 100x too large, sign-free -- and Phase 1 named it the single
most likely silent bug in this phase. Every join here is on `signed_rate_fraction`.

**What this script does not claim.** Phase 4 produces labels, not a model and not an edge. Phase
3's Task 5 measured that, net of a measured round-turn cost near 33 bp and of cross-venue basis
drift, **neither cohort of this universe is tradeable at a 24-hour hold**: net carry runs -22 to
-32 bp across every size decile and every age bucket. Unconditional carry is 1.03 bp at 6h,
3.70 bp at 24h and 9.78 bp at 72h; the 6.21 / 31.33 figures in circulation are *selective-entry*
numbers, reachable only by restricting entry to hours already in the widest decile, in sample.
Whether these targets are forecastable is still an open research question and these datasets are
how it gets asked -- but nothing here demonstrates a tradeable edge, and no downstream document
should read a target dataset as evidence of one.

**What is deliberately not here.** `joint_baseline` and `venue_clamped` are Task 4's flags; the
premium columns are Task 2's. The panel's size ranks are not copied onto these rows either --
Phase 6 joins `panel` on `(symbol, hour)` for them, and when it does it must **drop**
`rank_unavailable` rows from any rank-conditioned statistic rather than testing `hl_rank <= 10`
on a null rank, which silently admits the giant.
"""
import argparse
import sys

import polars as pl

from fundr import alias, dataset, targets

SOURCE = "phase4:build_targets"

#: D5: the horizon is a parameter, not a choice. 6 and 24 are the primary runs; 1 is kept only
#: as the design's original, for comparison; 72 bounds the long end.
HORIZONS = (1, 6, 24, 72)

VIEWS = ("cross_venue", "per_venue")

#: Rows per forward-window batch. `forward_window` widens a frame by two columns per forward
#: hour, so H = 72 over a 3.1M-row per-venue leg would carry ~150 columns and several GB at once.
#: A window never crosses a symbol, so batching whole symbols is exactly equivalent to one call.
BATCH_ROWS = 250_000

#: The canonical names of the five denomination-alias pairs (`kPEPE`/`1000PEPE` and friends).
#: Used only by `report`, to measure the gates on the exact-symbol population they were measured
#: on -- the datasets themselves keep every pair.
ALIAS_SYMBOLS = [hl for hl, _ in alias.ALIASES.values()]


# --- the rates, on the common basis ----------------------------------------------------------

def _leg(name: str, symbol_col: str, out_symbol: str, out: str) -> pl.DataFrame:
    """One venue's settled per-hour signed fraction, keyed by that venue's own symbol.

    `assert_common_basis` is called on the leg before it is joined to anything, so Lighter's raw
    percent fails here rather than becoming a spread 100x too large."""
    files = sorted((dataset.root() / name).glob("*/part.parquet"))
    if not files:
        return pl.DataFrame(schema={out_symbol: pl.String, "hour": pl.Datetime("ms"),
                                    out: pl.Float64})
    frame = (pl.scan_parquet(files)
             .select(pl.col(symbol_col).cast(pl.String).alias(out_symbol),
                     pl.col("settle_time").alias("hour"),
                     pl.col("signed_rate_fraction").alias(out))
             .collect())
    targets.assert_common_basis(frame.select(pl.col(out_symbol).alias("symbol"),
                                             pl.col(out).alias("signed_rate_fraction")))
    return frame


def _panel(view: str) -> pl.DataFrame:
    panel = dataset.read_partition("panel", view=view)
    if panel is None:
        raise FileNotFoundError(f"no panel/view={view}; run scripts/build_universe.py first")
    return panel


def cross_venue_rates() -> pl.DataFrame:
    """Target B's input: `S_t = F_t^HL - F_t^Lighter`, one row per concurrent pair-hour.

    Each leg is looked up under its own venue's symbol -- `kPEPE` on Hyperliquid is `1000PEPE`
    on Lighter -- and funding *rates* need no denomination scaling, so the spread is safe on the
    five alias pairs where a price would not be."""
    frame = (_panel("cross_venue").select("symbol", "lighter_symbol", "hour")
             .join(_leg("hl_funding", "coin", "symbol", "hl_rate"),
                   on=["symbol", "hour"], how="left")
             .join(_leg("lighter_funding", "symbol", "lighter_symbol", "lighter_rate"),
                   on=["lighter_symbol", "hour"], how="left")
             .with_columns((pl.col("hl_rate") - pl.col("lighter_rate")).alias("spread"))
             .drop_nulls("spread")
             .select("symbol", "hour", "spread")
             .sort(["symbol", "hour"]))
    targets.assert_common_basis(frame)
    return frame


def per_venue_rates() -> pl.DataFrame:
    """Target A's input: each venue's own settled rate, on every market that venue lists.

    A per-venue Lighter row is keyed by Lighter's own symbol, which is what `panel`'s `symbol`
    already holds on those rows."""
    panel = _panel("per_venue").select("venue", "symbol", "hour")
    hl = (panel.filter(pl.col("venue") == "hl")
          .join(_leg("hl_funding", "coin", "symbol", "rate"), on=["symbol", "hour"], how="left"))
    lighter = (panel.filter(pl.col("venue") == "lighter")
               .join(_leg("lighter_funding", "symbol", "symbol", "rate"),
                     on=["symbol", "hour"], how="left"))
    frame = (pl.concat([hl, lighter]).drop_nulls("rate")
             .select("venue", "symbol", "hour", "rate")
             .sort(["venue", "symbol", "hour"]))
    targets.assert_common_basis(frame)
    return frame


# --- the target ------------------------------------------------------------------------------

def _batches(rates: pl.DataFrame) -> list[pl.DataFrame]:
    """Whole symbols, grouped so no single forward-window call has to hold the panel."""
    out, batch, size = [], [], 0
    for frame in rates.partition_by("symbol", maintain_order=True):
        if batch and size + frame.height > BATCH_ROWS:
            out.append(pl.concat(batch))
            batch, size = [], 0
        batch.append(frame)
        size += frame.height
    if batch:
        out.append(pl.concat(batch))
    return out


def _forward_sum(rates: pl.DataFrame, *, h: int, value: str) -> pl.DataFrame:
    """`Sigma_{k=1..h}` of `value` at hour `t+k`, by explicit hour join, never by `shift`.

    A row with no forward hour at all gets a **null** target, not a zero: `sum_horizontal` over
    an all-null window returns 0.0, which would read as "the hold paid nothing" rather than
    "there is no hold here". The 2,002 rows beside a Hyperliquid hole and every series tail land
    in exactly that case."""
    pieces = [
        targets.forward_window(batch, h=h).select(
            "symbol", "hour", value,
            pl.when(pl.col("n_hours_used") > 0)
            .then(pl.sum_horizontal(pl.col(f"{value}_h{k}") for k in range(1, h + 1)))
            .alias("y_cum_level"),
            "n_hours_used")
        for batch in _batches(rates.select("symbol", "hour", value))]
    return pl.concat(pieces) if pieces else rates.select(
        "symbol", "hour", value,
        pl.lit(None, dtype=pl.Float64).alias("y_cum_level"),
        pl.lit(0, dtype=pl.Int64).alias("n_hours_used"))


def add_targets(rates: pl.DataFrame, *, h: int, value: str) -> pl.DataFrame:
    """The cumulative level, its cross-sectional rank, and both mandatory benchmarks.

    `rates` is one cross-section's worth of rows -- `(symbol, hour, value)` with `symbol` unique
    within an hour. The per-venue view is two cross-sections and is passed through one venue at
    a time, because Target A ranks a venue's markets against each other and not against the other
    venue's.

    **Ties share a rank and do not consume the next one** (`rank("dense")`). That is the whole
    reason D1 ranks rather than regresses: on the real panel 45.57% of hours have both venues at
    their own baseline and the spread there takes exactly two values, so ties are the common
    case, not an edge case. Lighter's 1e-6 reporting lattice becomes the tie-break.

    **A row with no target is in neither ranking.** Ranking it last would be an imputation under
    a different name, so `y_rank` and `benchmark_rank` are both null there and `benchmark_rank`
    does not count it -- the two rankings always cover exactly the same rows.

    **The no-change benchmark is scaled by the hours actually used**, not by `h`. Scoring a
    two-hour sum against a three-hour prediction would hand the model a free win on every short
    window."""
    has_target = pl.col("y_cum_level").is_not_null()
    return _forward_sum(rates, h=h, value=value).with_columns(
        pl.col("y_cum_level").rank("dense", descending=True).over("hour").alias("y_rank"),
        pl.col("y_cum_level").count().over("hour").cast(pl.Int64).alias("n_ranked_this_hour"),
        pl.when(has_target).then(pl.col("n_hours_used") * pl.col(value))
        .alias("benchmark_cum_level"),
        pl.when(has_target).then(pl.col(value))
        .rank("dense", descending=True).over("hour").alias("benchmark_rank"),
    ).sort(["symbol", "hour"])


def build(*, horizons=HORIZONS) -> dict[tuple[str, int], pl.DataFrame]:
    """Every (view, horizon) target frame, in memory. `main` writes them and reports the gates."""
    cross, per_venue = cross_venue_rates(), per_venue_rates()
    frames = {}
    for h in horizons:
        frames["cross_venue", h] = add_targets(cross, h=h, value="spread")
        frames["per_venue", h] = pl.concat(
            [add_targets(venue.drop("venue"), h=h, value="rate")
             .with_columns(pl.lit(name).alias("venue"))
             .select("venue", pl.exclude("venue"))
             for name, venue in sorted(
                 (name, frame) for (name,), frame in
                 per_venue.partition_by("venue", as_dict=True).items())])
    return frames


def write(frames: dict[tuple[str, int], pl.DataFrame]) -> None:
    for (view, h), frame in frames.items():
        dataset.write_partition("targets", frame, source=SOURCE, view=view, horizon=h)


# --- the gates -------------------------------------------------------------------------------

def _gate_lines(cross_h1: pl.DataFrame) -> list[str]:
    """The two numbers that say the join and the basis are right.

    At H = 1 the target *is* next hour's spread, so `y_cum_level - spread` is the spread change
    and `corr(spread, y_cum_level)` is the level's own lag-1. Both were measured on the 978,572
    **exact-symbol** concurrent pair-hours, so both are reported on that population as well as on
    the full panel, which adds the 60,951 alias pair-hours. If either misses on the exact-symbol
    population, the join or the basis is wrong and nothing downstream is worth running."""
    lines = []
    for label, frame in (("all pairs", cross_h1),
                         ("exact-symbol pairs",
                          cross_h1.filter(~pl.col("symbol").is_in(ALIAS_SYMBOLS)))):
        paired = frame.filter(pl.col("n_hours_used") == 1)
        if paired.is_empty():
            lines.append(f"H=1 cross_venue {label}: no adjacent pair to measure")
            continue
        zero = float(((paired["y_cum_level"] - paired["spread"]) == 0).mean())
        lag1 = paired.select(pl.corr("spread", "y_cum_level")).item()
        lines.append(f"H=1 cross_venue {label}: {paired.height:,} adjacent pairs; "
                     f"exact-zero share of (y_cum_level - S_t) {zero * 100:.2f}% "
                     f"(measured 40.27%); spread level lag-1 {lag1:.4f} (measured 0.699)")
    return lines


def report(frames: dict[tuple[str, int], pl.DataFrame]) -> list[str]:
    """The plan's verify step, measured rather than asserted, so a mismatch is visible."""
    lines = []
    for (view, h), frame in sorted(frames.items()):
        used = frame["n_hours_used"]
        full, short, none = int((used == h).sum()), int(used.is_between(1, h - 1).sum()), \
            int((used == 0).sum())
        lines.append(
            f"{view} H={h}: {frame.height:,} rows, {frame['symbol'].n_unique()} symbols, "
            f"{frame['hour'].n_unique():,} hours; full window on {full:,} "
            f"({full / frame.height * 100:.2f}%), short (0 < n < {h}) on {short:,} "
            f"({short / frame.height * 100:.2f}%), no target (n = 0) on {none:,} "
            f"({none / frame.height * 100:.2f}%); ranked rows {frame['y_rank'].count():,}, "
            f"widest hour {frame['n_ranked_this_hour'].max()}")
        for venue in frame["venue"].unique().sort() if "venue" in frame.columns else []:
            rows = frame.filter(pl.col("venue") == venue)
            lines.append(f"  venue={venue}: {rows.height:,} rows, "
                         f"{rows['symbol'].n_unique()} symbols, widest hour "
                         f"{rows['n_ranked_this_hour'].max()}")
    if ("cross_venue", 1) in frames:
        lines += _gate_lines(frames["cross_venue", 1])
    return lines


def parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--horizons", type=int, nargs="+", default=list(HORIZONS),
                    help="hold lengths in hours (D5: 6 and 24 are the primary runs)")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    frames = build(horizons=tuple(args.horizons))
    write(frames)
    print("\n".join(report(frames)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
