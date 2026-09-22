"""Does the design's "outside the top 10 by open interest" rule do anything? -- Task 5.

This is the measurement Phase 3 exists for. It decides deferred item F1 in
`docs/phase3/decisions.md`, and it is the evidence that either earns or withdraws Phase 3's
decision to ship the size rule as a column instead of a gate.

Three outputs into `data/phase2/qa/universe_diagnostics.md`:

1. **Forecastability by size decile**, with standard errors **clustered on the hour**. 100
   symbols in one hour are one observation under a market-wide funding shock, and treating
   1,039,523 pair-hours as independent manufactures significance out of nothing -- the textbook
   error is reported beside the clustered one so the size of that lie is visible.
2. **The knob-grid dispersion report** -- pool x rebalance x start x Lighter leg, 24 cells --
   so four arguments become one table.
3. **The size rule against basis risk and trading cost**, by size decile and by pair age.

**The question is not "is funding more forecastable outside the top 10".** Two gradients
measured after the plan was written both run the other way: 24-hour basis drift is ~124 bp in a
pair's first week against ~17 bp after three months, and Hyperliquid's round-turn cost falls
from ~25 bp in the smallest open-interest decile to ~1 bp in the largest. The five names the
rule always excludes -- BTC, ETH, SOL, XRP, HYPE -- are both the best hedged and the cheapest to
trade. So the question this script answers is **"is it more forecastable by enough to pay for
the basis risk and the trading cost the exclusion takes on"**.

**Decile convention: D10 is the LARGEST by open interest, D1 the smallest.** This matches the
cost table in `decisions.md` ("D10 1.07 bp" is the cheapest, biggest names). The top-10 cohort
the design deletes sits in D10.

**`rank_unavailable` rows are dropped, never tested against 10.** Their rank is null on purpose;
`size_rank <= 10` on a null admits the market the rule exists to exclude.

**No `shift` anywhere.** Hyperliquid's funding history carries 1,789 eight-hour intervals and
213 single-hour holes, so a one-row shift pairs rows two or eight hours apart. Every lag here is
an inner join on an explicit hour difference, which drops a hole instead of bridging it.

Run as `uv run python -m scripts.universe_diagnostics`.
"""
import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NamedTuple

import polars as pl

from fundr import dataset
from scripts import build_universe
from scripts.qa_backfill import _md_table

H = timedelta(hours=1)
TOP_N = build_universe.TOP_N
CARRY_HORIZONS = (6, 24, 72)
HOLD_HOURS = 24

#: Pair-age cut points, matching the boxed note above Task 5 in the plan.
AGE_BUCKETS = ((168, "week 1"), (720, "weeks 2-4"), (2160, "months 1-3"), (None, "3 months+"))

#: Round-turn cost components that are NOT measured per decile here, in bp, from the
#: "Round-turn cost -- MEASURED" section of `decisions.md`. The Hyperliquid taker fee is the
#: published base tier across both legs of the round turn; Lighter's Standard-account taker fee
#: is zero; the Lighter spread is ONE live top-of-book snapshot, so its variation across hours
#: and regimes is unmeasured and this term is a point estimate, not a distribution.
HL_TAKER_BP = 9.0
LIGHTER_TAKER_BP = 0.0
LIGHTER_SPREAD_BP = 14.4

#: Carry figures the council recorded, repeated here only so the report can say where this
#: script's own unconditional measurement disagrees with them.
RECORDED_CARRY_BP = {6: 6.21, 72: 31.33}

DEFAULT_START = build_universe.DEFAULT_START
LATE_START = datetime(2025, 7, 29)


# --- the statistics -------------------------------------------------------------------------

class Fit(NamedTuple):
    rho: float | None
    se: float | None
    se_iid: float | None
    n: int
    clusters: int


class Gap(NamedTuple):
    value: float | None
    se: float | None
    n: int


def _standardised(df: pl.DataFrame, x: str, y: str, cluster: str) -> pl.DataFrame | None:
    """`cluster`, `x`, `y`, each variable centred and scaled to unit variance.

    Scaling both sides makes the OLS slope of y on x the correlation itself, so one estimator
    covers both, and the cluster-robust variance of the slope is the variance of the
    correlation to first order (the two scale factors are treated as fixed, which is the
    standard approximation and is stated in the report)."""
    d = df.select(pl.col(cluster).alias("_g"), pl.col(x).alias("x"),
                  pl.col(y).alias("y")).drop_nulls()
    if d.height < 3:
        return None
    d = d.select("_g", *[((pl.col(c) - pl.col(c).mean()) / pl.col(c).std(ddof=0)).alias(c)
                         for c in ("x", "y")]).drop_nulls()
    if d.height < 3 or d["x"].std(ddof=0) == 0 or d["y"].std(ddof=0) == 0:
        return None
    return d


def _influence(d: pl.DataFrame) -> tuple[float, pl.DataFrame]:
    """The correlation, and each cluster's contribution to its sampling error.

    The contribution is `sum_{i in g} x_i u_i / sum_i x_i^2`. Summing its square over clusters
    is the cluster-robust "meat"; the same per-cluster numbers subtract cleanly between two
    disjoint cohorts measured over the same hours, which is how the cohort gap gets an error."""
    n = d.height
    rho = float((d["x"] * d["y"]).sum()) / n          # sum(x^2) == n after standardising
    scores = (d.with_columns((pl.col("y") - rho * pl.col("x")).alias("u"))
              .group_by("_g").agg((pl.col("x") * pl.col("u")).sum().alias("s")))
    return rho, scores


def _correction(n: int, clusters: int) -> float:
    if clusters < 2 or n < 3:
        return float("nan")
    return (clusters / (clusters - 1)) * ((n - 1) / (n - 2))


def clustered_corr(df: pl.DataFrame, x: str, y: str, cluster: str = "hour") -> Fit:
    """Correlation of `x` and `y` with a standard error clustered on `cluster`.

    `se_iid` is the textbook error that assumes every pair-hour is its own observation. It is
    reported beside `se` because the ratio between them is the whole reason this function
    exists: under a market-wide funding shock the panel has ~14,700 observations, not ~1,040,000.
    """
    d = _standardised(df, x, y, cluster)
    if d is None:
        return Fit(None, None, None, df.height, 0)
    n = d.height
    rho, scores = _influence(d)
    clusters = scores.height
    var = _correction(n, clusters) * float((scores["s"] ** 2).sum()) / n ** 2
    se_iid = ((1 - rho ** 2) / (n - 2)) ** 0.5
    return Fit(rho, var ** 0.5, se_iid, n, clusters)


def clustered_gap(a: pl.DataFrame, b: pl.DataFrame, x: str, y: str,
                  cluster: str = "hour") -> Gap:
    """`corr(a) - corr(b)`, with one clustered error covering both cohorts.

    The two cohorts share hours, so their errors are not independent and cannot be added in
    quadrature. Subtracting their per-cluster contributions before squaring handles that."""
    da, db = _standardised(a, x, y, cluster), _standardised(b, x, y, cluster)
    if da is None or db is None:
        return Gap(None, None, a.height + b.height)
    rho_a, sa = _influence(da)
    rho_b, sb = _influence(db)
    joined = (sa.rename({"s": "sa"}).join(sb.rename({"s": "sb"}), on="_g", how="full",
                                          coalesce=True)
              .with_columns(pl.col("sa").fill_null(0.0) / da.height
                            - pl.col("sb").fill_null(0.0) / db.height))
    n = da.height + db.height
    var = _correction(n, joined.height) * float((joined["sa"] ** 2).sum())
    return Gap(rho_a - rho_b, var ** 0.5, n)


# --- panel assembly -------------------------------------------------------------------------

def spread_frame(panel: pl.DataFrame) -> pl.DataFrame:
    """Attach the Hyperliquid-minus-Lighter funding spread, in bp per hour, to every panel row.

    Each leg is read under its own venue's symbol -- `kPEPE` on Hyperliquid is `1000PEPE` on
    Lighter -- and funding *rates* need no denomination scaling, so the spread is safe on the
    five alias pairs where a price would not be."""
    def leg(name: str, symbol_col: str, out_symbol: str, out: str) -> pl.DataFrame:
        files = sorted((dataset.root() / name).glob("*/part.parquet"))
        if not files:
            return pl.DataFrame(schema={out_symbol: pl.String, "hour": pl.Datetime("ms"),
                                        out: pl.Float64})
        return (pl.scan_parquet(files)
                .select(pl.col(symbol_col).cast(pl.String).alias(out_symbol),
                        pl.col("settle_time").alias("hour"),
                        pl.col("signed_rate_fraction").alias(out))
                .collect())
    return (panel
            .join(leg("hl_funding", "coin", "symbol", "hl_rate"), on=["symbol", "hour"],
                  how="left")
            .join(leg("lighter_funding", "symbol", "lighter_symbol", "lighter_rate"),
                  on=["lighter_symbol", "hour"], how="left")
            .with_columns(((pl.col("hl_rate") - pl.col("lighter_rate")) * 1e4).alias("spread_bp"))
            .drop_nulls("spread_bp"))


def with_lags(frame: pl.DataFrame) -> pl.DataFrame:
    """Keep only hours that have a real neighbour on each side, joined on an hour difference.

    An inner join drops the hour beside a hole instead of reaching across it. `shift` would have
    paired the two ends of a 213-strong list of single-hour holes."""
    nxt = frame.select("symbol", (pl.col("hour") - H).alias("hour"),
                       pl.col("spread_bp").alias("s_next"))
    prv = frame.select("symbol", (pl.col("hour") + H).alias("hour"),
                       pl.col("spread_bp").alias("s_prev"))
    return (frame.join(nxt, on=["symbol", "hour"], how="inner")
            .join(prv, on=["symbol", "hour"], how="inner")
            .with_columns((pl.col("s_next") - pl.col("spread_bp")).alias("d_next"),
                          (pl.col("spread_bp") - pl.col("s_prev")).alias("d_now")))


def usable(frame: pl.DataFrame) -> pl.DataFrame:
    """Drop the rows whose governing rank does not exist. Never test their rank against 10."""
    return frame.filter(~pl.col("rank_unavailable"))


def with_deciles(frame: pl.DataFrame) -> pl.DataFrame:
    """Cut each hour's live cross-section into ten by point-in-time size. **D10 is the largest.**

    The cut is inside the hour because the venue grows from ~13 to ~96 live pairs over the
    window: a fixed rank band would mean "top 8%" early and "top 10%" late."""
    return (frame.with_columns(
        pl.col("hl_rank").rank("ordinal", descending=True).over("hour").alias("_r"),
        pl.len().over("hour").alias("_n"))
        .with_columns((((pl.col("_r") - 1) * 10 // pl.col("_n")) + 1)
                      .clip(1, 10).cast(pl.Int32).alias("decile"))
        .drop("_r", "_n"))


def with_age_bucket(frame: pl.DataFrame) -> pl.DataFrame:
    expr = pl.when(pl.col("pair_age_hours") < AGE_BUCKETS[0][0]).then(pl.lit(AGE_BUCKETS[0][1]))
    for cut, label in AGE_BUCKETS[1:-1]:
        expr = expr.when(pl.col("pair_age_hours") < cut).then(pl.lit(label))
    return frame.with_columns(expr.otherwise(pl.lit(AGE_BUCKETS[-1][1])).alias("age_bucket"))


def hl_prices(start: datetime, end: datetime) -> pl.DataFrame:
    """Hyperliquid end-of-hour mark price and median round-trip impact spread, per symbol-hour.

    **The mark is the hour's LAST print, not its median.** Lighter's candle stamped `T` closes at
    the end of hour `T`, so a median-of-the-hour Hyperliquid mark sits half an hour behind it,
    and the resulting price-timing noise swamps the basis it is meant to measure -- measured
    2026-09-22, the same 804,761 holds give a 24-hour drift sd of 88.7 bp on the median against
    17.7 bp on the close, and 53.6 bp against 9.9 bp on BTC/ETH/SOL.

    The impact spread is `(impact_ask - impact_bid) / mid`, the depth-aware cost of crossing once
    -- one full spread is one round turn, a half on the way in and a half on the way out. Rows
    without a two-sided impact quote produce no spread rather than a zero."""
    base = dataset.root() / "hl_asset_ctxs"
    lo, hi = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    files = [p for p in sorted(base.glob("date=*/part.parquet"))
             if lo <= p.parent.name.split("=", 1)[1] <= hi]
    if not files:
        return pl.DataFrame(schema={"hour": pl.Datetime("ms"), "symbol": pl.String,
                                    "hl_mark": pl.Float64, "hl_spread_bp": pl.Float64})
    mid = (pl.col("impact_ask_px") + pl.col("impact_bid_px")) / 2
    return (pl.scan_parquet(files)
            .filter((pl.col("time") >= start) & (pl.col("time") < end + H))
            .select(pl.col("time").dt.truncate("1h").alias("hour"), "time",
                    pl.col("coin").alias("symbol"), "mark_px",
                    pl.when((pl.col("impact_bid_px") > 0) & (pl.col("impact_ask_px") > 0))
                    .then((pl.col("impact_ask_px") - pl.col("impact_bid_px")) / mid * 1e4)
                    .alias("_spread"))
            .group_by("hour", "symbol")
            .agg(pl.col("mark_px").sort_by("time").last().alias("hl_mark"),
                 pl.col("_spread").median().alias("hl_spread_bp"))
            .collect(engine="streaming"))


def with_basis_and_cost(frame: pl.DataFrame, prices: pl.DataFrame) -> pl.DataFrame:
    """Attach the cross-venue basis in bp, its `HOLD_HOURS` drift, and the round-turn cost.

    Both legs are divided by their own `size_multiplier` before differencing: `NOT` measures one
    token on Hyperliquid against 1000 on Lighter's `1000NOT`, so an unscaled difference would
    read as a 100,000 bp basis.

    Lighter's mark-candle history starts 2025-08-25, so every basis figure covers only the part
    of the panel after that date. The report states the coverage."""
    files = sorted((dataset.root() / "lighter_mark_candles").glob("*/part.parquet"))
    marks = (pl.scan_parquet(files).select(
        pl.col("symbol").alias("lighter_symbol"), pl.col("time").alias("hour"),
        pl.col("close").alias("lighter_mark")).collect() if files else
        pl.DataFrame(schema={"lighter_symbol": pl.String, "hour": pl.Datetime("ms"),
                             "lighter_mark": pl.Float64}))
    out = (frame.join(prices, on=["symbol", "hour"], how="left")
           .join(marks, on=["lighter_symbol", "hour"], how="left")
           .with_columns((pl.col("hl_mark") / pl.col("size_multiplier")).alias("_hl_px"),
                         (pl.col("lighter_mark") / pl.col("lighter_size_multiplier"))
                         .alias("_li_px")))
    out = out.with_columns(
        ((pl.col("_hl_px") - pl.col("_li_px")) / ((pl.col("_hl_px") + pl.col("_li_px")) / 2)
         * 1e4).alias("basis_bp"),
        (pl.col("hl_spread_bp") + HL_TAKER_BP + LIGHTER_TAKER_BP + LIGHTER_SPREAD_BP)
        .alias("round_turn_bp")).drop("_hl_px", "_li_px")
    later = out.select("symbol", (pl.col("hour") - HOLD_HOURS * H).alias("hour"),
                       pl.col("basis_bp").alias("_basis_later"))
    return (out.join(later, on=["symbol", "hour"], how="left")
            .with_columns((pl.col("_basis_later") - pl.col("basis_bp")).alias("basis_drift_bp"))
            .drop("_basis_later"))


def with_carry(frame: pl.DataFrame, hours: int, name: str) -> pl.DataFrame:
    """Signed funding spread accumulated over the next `hours` hours, by explicit hour offsets.

    A hold that is missing any of its hours produces no value rather than a short sum, so the
    213 single-hour holes cannot masquerade as a cheap hold."""
    if name in frame.columns:
        return frame
    acc = frame.select("symbol", "hour").with_columns(pl.lit(0.0).alias(name),
                                                      pl.lit(0).alias("_n"))
    for k in range(1, hours + 1):
        step = frame.select("symbol", (pl.col("hour") - k * H).alias("hour"),
                            pl.col("spread_bp").alias("_v"))
        acc = (acc.join(step, on=["symbol", "hour"], how="left")
               .with_columns((pl.col(name) + pl.col("_v").fill_null(0.0)).alias(name),
                             (pl.col("_n") + pl.col("_v").is_not_null().cast(pl.Int32))
                             .alias("_n")).drop("_v"))
    acc = acc.with_columns(pl.when(pl.col("_n") == hours).then(pl.col(name)).alias(name))
    return frame.join(acc.drop("_n"), on=["symbol", "hour"], how="left")


# --- the knob grid --------------------------------------------------------------------------

class Cell(NamedTuple):
    pool: str
    rebalance: str
    start: datetime
    lighter_leg: str


def grid_cells(start: datetime, late_start: datetime) -> list[Cell]:
    """The 24 settings Phase 10's robustness pass would have swept, pulled forward to here."""
    return [Cell(pool, rebalance, when, leg)
            for pool in ("venue", "matched")
            for rebalance in ("hourly", "daily", "monthly")
            for when in (start, late_start)
            for leg in ("column", "gate")]


def apply_lighter_leg(frame: pl.DataFrame, leg: str) -> pl.DataFrame:
    """`column` keeps every row; `gate` applies the superseded intersection rule from D3.

    Gating drops the rows whose Lighter rank is *unavailable* as well as the Lighter top 10: a
    null `in_lighter_top10` is "no rank", and reading it as "not in the top 10" is exactly the
    fail-open this phase exists to prevent."""
    if leg == "column":
        return frame
    return frame.filter(pl.col("in_lighter_top10").is_not_null()
                        & ~pl.col("in_lighter_top10"))


def headline(frame: pl.DataFrame) -> Gap:
    """The number F1 turns on: how much more forecastable the kept cohort is than the excluded.

    Positive means funding spreads outside the venue's top 10 are more persistent than inside
    it, which is what the design's rule assumes without ever having measured it."""
    ranked = usable(frame)
    return clustered_gap(ranked.filter(pl.col("hl_rank") > TOP_N),
                         ranked.filter(pl.col("hl_rank") <= TOP_N), "spread_bp", "s_next")


# --- tables ---------------------------------------------------------------------------------

def _fmt(value, places: int = 3) -> str:
    if value is None or value != value:
        return "--"
    return f"{value:,.{places}f}"


def _pm(fit: Fit | Gap) -> str:
    return f"{_fmt(fit.rho if isinstance(fit, Fit) else fit.value)} ± {_fmt(fit.se)}"


def forecastability_table(frame: pl.DataFrame, by: str) -> pl.DataFrame:
    """Per group: how persistent the spread is, how persistent its change is, and how volatile.

    `no-change skill` is one minus the mean squared error of predicting next hour's spread with
    this hour's, over the error of predicting it with the group's own mean. Above zero means
    persistence beats the unconditional level; it is the simplest honest benchmark there is."""
    rows = []
    for key in frame[by].unique().sort():
        part = frame.filter(pl.col(by) == key)
        level = clustered_corr(part, "spread_bp", "s_next")
        change = clustered_corr(part, "d_now", "d_next")
        mse_persist = float(((part["s_next"] - part["spread_bp"]) ** 2).mean())
        mse_mean = float(((part["s_next"] - part["s_next"].mean()) ** 2).mean())
        rows.append({
            by: key, "pair-hours": part.height,
            "median hl_rank": part["hl_rank"].median(),
            "share in venue top 10": round(float((part["hl_rank"] <= TOP_N).mean()), 4),
            "sd of spread (bp/hr)": round(float(part["spread_bp"].std()), 3),
            "ac1 level ± clustered se": _pm(level),
            "(textbook se)": _fmt(level.se_iid, 4),
            "ac1 change ± clustered se": _pm(change),
            "no-change skill": round(1 - mse_persist / mse_mean, 4) if mse_mean else None,
        })
    return pl.DataFrame(rows)


def risk_and_cost_table(frame: pl.DataFrame, by: str, carry: str,
                        keys: list | None = None) -> pl.DataFrame:
    """Forecastability beside the two things the size rule buys with it: basis risk and cost."""
    rows = []
    present = set(frame[by].unique())
    for key in ([k for k in keys if k in present] if keys
                else frame[by].unique().sort().to_list()):
        part = frame.filter(pl.col(by) == key)
        level = clustered_corr(part, "spread_bp", "s_next")
        drift = part["basis_drift_bp"].drop_nulls()
        carry_bp = part[carry].drop_nulls()
        gross = float(carry_bp.abs().mean()) if carry_bp.len() else None
        cost = float(part["round_turn_bp"].median()) if part["round_turn_bp"].count() else None
        held = part.filter(pl.col("basis_drift_bp").is_not_null()
                           & pl.col(carry).is_not_null())
        rows.append({
            by: key, "pair-hours": part.height,
            "ac1 level ± clustered se": _pm(level),
            "24h basis drift sd (bp)": round(float(drift.std()), 1) if drift.len() > 1 else None,
            "share |drift| > 24h carry": (
                round(float((held["basis_drift_bp"].abs() > held[carry].abs()).mean()), 4)
                if held.height else None),
            "round turn (bp)": round(cost, 2) if cost is not None else None,
            "24h carry (bp)": round(gross, 3) if gross is not None else None,
            "net 24h (bp)": (round(gross - cost, 2)
                             if gross is not None and cost is not None else None),
        })
    return pl.DataFrame(rows)


def break_table(frame: pl.DataFrame, thresholds=(3, 5, 10, 15, 20, 25, 30, 40, 50)
                ) -> pl.DataFrame:
    """The design picked ten. This asks every threshold the same question the design asked."""
    rows = []
    for k in thresholds:
        gap = clustered_gap(frame.filter(pl.col("hl_rank") > k),
                            frame.filter(pl.col("hl_rank") <= k), "spread_bp", "s_next")
        t = (gap.value / gap.se) if gap.value is not None and gap.se else None
        rows.append({"threshold (rank <= k excluded)": k,
                     "rows excluded": int((frame["hl_rank"] <= k).sum()),
                     "ac1 gap (kept - excluded) ± clustered se": _pm(gap),
                     "t": round(t, 2) if t is not None else None})
    return pl.DataFrame(rows)


# --- report ------------------------------------------------------------------------------

def _extremes(table: pl.DataFrame, by: str) -> tuple[str, str]:
    values = [(row[by], float(row["ac1 level ± clustered se"].split(" ± ")[0]))
              for row in table.to_dicts()
              if not row["ac1 level ± clustered se"].startswith("--")]
    if not values:
        return "--", "--"
    return (f"D{max(values, key=lambda v: v[1])[0]}", f"D{min(values, key=lambda v: v[1])[0]}")


def _carry_lines(frame: pl.DataFrame) -> list[str]:
    """What the spread actually pays, unconditionally and on a large-spread entry.

    The council recorded 6.21 bp at 6h and 31.33 bp at 72h. Measured here over every hold in the
    panel the unconditional figure is far smaller; the recorded numbers are reproduced only when
    entry is restricted to hours whose spread is already in the widest decile. Both are
    reported, because which one applies decides whether any of this pays for a round turn."""
    lines = []
    for hours in CARRY_HORIZONS:
        name = f"carry_{hours}h"
        held = with_carry(frame, hours, name).drop_nulls(name)
        if held.is_empty():
            lines.append(f"- **{hours}h**: no complete hold in the panel")
            continue
        cut = held["spread_bp"].abs().quantile(0.9)
        wide = held.filter(pl.col("spread_bp").abs() >= cut)
        recorded = RECORDED_CARRY_BP.get(hours)
        lines.append(
            f"- **{hours}h**: every hold **{_fmt(float(held[name].abs().mean()))} bp**; "
            f"entering only on a widest-decile spread {_fmt(float(wide[name].abs().mean()))} bp"
            f" ({held.height:,} holds)"
            + (f" — `decisions.md` records {recorded} bp" if recorded else ""))
    return lines


def build_report(*, start: datetime, end: datetime, late_start: datetime) -> list[str]:
    panel = pl.read_parquet(dataset.root() / "panel" / "view=cross_venue" / "part.parquet")
    rows = spread_frame(panel)
    prices = hl_prices(start, end)
    frame = with_age_bucket(with_basis_and_cost(with_lags(rows), prices))
    frame = with_carry(frame, HOLD_HOURS, "carry_24h")
    ranked = with_deciles(usable(frame))

    by_decile = forecastability_table(ranked, "decile")
    strongest, weakest = _extremes(by_decile, "decile")
    breaks = break_table(usable(frame))
    best = (breaks.filter(pl.col("t").is_not_null()).sort(pl.col("t").abs(), descending=True)
            .head(1).to_dicts())
    head = headline(frame)

    grid, failures = [], 0
    for cell in grid_cells(start, late_start):
        built = _grid_headline(cell, end)
        if built.value is None:
            failures += 1
        grid.append({"pool": cell.pool, "rebalance": cell.rebalance,
                     "start": cell.start.date(), "lighter leg": cell.lighter_leg,
                     "rows": built.n, "ac1 gap ± clustered se": _pm(built)})
    values = [float(row["ac1 gap ± clustered se"].split(" ± ")[0])
              for row in grid if not row["ac1 gap ± clustered se"].startswith("--")]
    decile_values = [float(row["ac1 level ± clustered se"].split(" ± ")[0])
                     for row in by_decile.to_dicts()
                     if not row["ac1 level ± clustered se"].startswith("--")]
    grid_spread = (max(values) - min(values)) if values else None
    decile_spread = (max(decile_values) - min(decile_values)) if decile_values else None

    basis_rows = int(frame["basis_drift_bp"].is_not_null().sum())
    first_month = float((frame["pair_age_hours"] < 720).mean())

    lines = [
        "# Universe diagnostics — does the size rule do anything?", "",
        f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M}Z by `scripts/universe_diagnostics.py`. "
        f"Panel window {start:%Y-%m-%d} → {end:%Y-%m-%d}, {frame.height:,} usable pair-hours "
        f"over {frame['hour'].n_unique():,} distinct hours.", "",
        "The research design's universe is \"perpetual markets outside the top 10 by open "
        "interest\". Nothing had ever measured whether that rule does anything. This is that "
        "measurement, and it decides F1.", "",
        "## How to read this", "",
        "- **D10 is the LARGEST decile by open interest, D1 the smallest.** This is the "
        "convention `decisions.md`'s cost table uses. The cohort the design deletes sits in "
        "D10.",
        "- Deciles are cut **within each hour**, on the point-in-time Hyperliquid "
        "open-interest rank, because the live cross-section grows from ~13 pairs to ~96 across "
        "the window.",
        "- **Standard errors are clustered on the hour.** The textbook error is printed beside "
        "them; the gap between the two is how much significance independence would have "
        "invented.",
        "- Rows whose governing rank is unavailable are **dropped**, never tested against 10.",
        "- Every lag is an inner join on an explicit hour difference, so a hole in the grid "
        "drops the hour beside it instead of being bridged.",
        "- Figures are measured values. Nothing here is scored as a judgement.", "",
        "## 1. Forecastability by size decile", "",
        "`ac1 level` is the hour-to-hour autocorrelation of the funding spread — how much of "
        "next hour's spread this hour's already tells you. `ac1 change` is the same for the "
        "change, which is Target B itself. `no-change skill` is how much better this hour's "
        "spread predicts the next than the decile's own average does.", "",
        _md_table(by_decile), "",
        f"- strongest decile: **{strongest}**; weakest: **{weakest}**",
        f"- spread across deciles: **{_fmt(decile_spread)}** in `ac1 level`",
        f"- clustering costs a factor of roughly "
        f"{_fmt(_se_inflation(by_decile), 0)}x in precision against the textbook error", "",
        "## 2. Is there a break, and where?", "",
        "The design chose ten and said why nowhere. Each row asks the design's own question at "
        "a different threshold: are spreads outside the top *k* more persistent than inside "
        "it? `t` is the gap over its clustered error.", "",
        _md_table(breaks), "",
        f"- at the design's threshold of 10: gap **{_pm(head)}** "
        f"(t = {_fmt(head.value / head.se, 2) if head.se else '--'})",
        f"- largest |t| over every threshold tried: "
        f"{('rank <= ' + str(best[0]['threshold (rank <= k excluded)']) + ', t = '
            + str(best[0]['t'])) if best else '--'}",
        "- a break exists only if one of these gaps is large against its clustered error; the "
        "numbers above are the evidence, in full.", "",
        "## 3. The knob grid — 24 cells", "",
        "pool ∈ {venue, matched} × rebalance ∈ {hourly, daily, monthly} × start ∈ "
        f"{{{start:%Y-%m-%d}, {late_start:%Y-%m-%d}}} × Lighter leg ∈ {{column, gate}}. The "
        "number in each cell is the same headline as §2 at threshold 10: how much more "
        "persistent the kept cohort's spread is than the excluded cohort's.", "",
        _md_table(pl.DataFrame(grid)), "",
        f"- **dispersion across the 24 cells: {_fmt(grid_spread)}** in `ac1 gap`"
        + (f", against a spread of {_fmt(decile_spread)} across the deciles"
           if decile_spread is not None else ""),
        f"- cells that could not be measured: {failures} of 24", "",
        "## 4. Size rule vs basis risk and cost — by size decile", "",
        "`24h basis drift sd` is how far the Hyperliquid-minus-Lighter mark basis moves over a "
        "24-hour hold — the hedge failing, in the units the carry is paid in. `round turn` is "
        f"the measured Hyperliquid impact spread plus {HL_TAKER_BP} bp Hyperliquid taker fee, "
        f"{LIGHTER_TAKER_BP} bp Lighter taker fee and {LIGHTER_SPREAD_BP} bp Lighter spread. "
        "`24h carry` is what the spread actually paid over the next 24 hours. `net 24h` is "
        "carry minus round turn.", "",
        _md_table(risk_and_cost_table(ranked, "decile", "carry_24h")), "",
        f"- basis figures cover the {basis_rows:,} pair-hours with a Lighter mark candle "
        f"({basis_rows / max(frame.height, 1) * 100:.1f}% of the panel); Lighter's mark-candle "
        "history starts 2025-08-25 and nothing before that can be measured.", "",
        "## 5. The same split by pair age", "",
        "Basis risk is concentrated in young markets, and the size rule steers the universe "
        "toward exactly that cohort. Buckets are hours since the pair's first concurrent hour.",
        "",
        _md_table(risk_and_cost_table(ranked, "age_bucket", "carry_24h",
                                      [label for _, label in AGE_BUCKETS])), "",
        f"- excluding each market's first month costs **{first_month * 100:.1f}%** of panel "
        "rows.", "",
        "## 6. What the spread actually pays", "",
        *_carry_lines(frame), "",
        "## Caveats that travel with every number here", "",
        "- The basis is **mark** against **mark**, off two different venue references, so part "
        "of its level is convention rather than realizable P&L. F2 stays open.",
        "- The Hyperliquid mark is the hour's last print, aligned with Lighter's candle close. "
        "A median-of-the-hour mark measures price-timing noise, not basis.",
        f"- The Lighter spread term ({LIGHTER_SPREAD_BP} bp) is **one live snapshot**, carried "
        "from `decisions.md`. Its variation across hours and regimes is unmeasured.",
        "- Funding-settlement slippage is not evidenced anywhere and is excluded, not "
        "estimated, so every round-turn figure is a floor.",
        "- The Hyperliquid impact spread is the depth-aware cost of a fixed clip, so it "
        "overstates cost for a smaller clip and understates it for a larger one.",
        "- Phase 3 ships no `is_stale` column, so the 5 Hyperliquid-delisted matched symbols "
        "(1.99% of pair-hours, `decisions.md` hygiene §1) are still in every figure above. "
        "Their frozen series bias persistence upward, by at most a rounding of these numbers.",
        "- Correlations are standardised slopes; the two scaling factors are treated as fixed "
        "when the clustered error is formed, which is the usual first-order approximation.",
    ]
    return lines


def _se_inflation(table: pl.DataFrame) -> float | None:
    ratios = [float(row["ac1 level ± clustered se"].split(" ± ")[1]) / float(row["(textbook se)"])
              for row in table.to_dicts()
              if not row["(textbook se)"].startswith("--")
              and not row["ac1 level ± clustered se"].endswith("--")]
    return sum(ratios) / len(ratios) if ratios else None


def _grid_headline(cell: Cell, end: datetime) -> Gap:
    """One grid cell: rebuild the panel under its knobs, then take the same headline as §2."""
    built = build_universe.build(start=cell.start, end=end, rebalance=cell.rebalance,
                                 pool=cell.pool)["cross_venue"]
    frame = with_lags(spread_frame(built))
    return headline(apply_lighter_leg(frame, cell.lighter_leg))


def parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", type=datetime.fromisoformat, default=DEFAULT_START,
                    help="first panel hour, and the grid's early start (D5)")
    ap.add_argument("--late-start", type=datetime.fromisoformat, default=LATE_START,
                    help="the grid's second start date")
    ap.add_argument("--end", type=datetime.fromisoformat, default=None,
                    help="last panel hour; default is the panel's own last hour")
    ap.add_argument("--out", default=None)
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    end = args.end
    if end is None:
        end = pl.read_parquet(dataset.root() / "panel" / "view=cross_venue" / "part.parquet",
                              columns=["hour"])["hour"].max()
    lines = build_report(start=args.start, end=end, late_start=args.late_start)
    out = Path(args.out or (dataset.root() / "qa" / "universe_diagnostics.md"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nreport written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
