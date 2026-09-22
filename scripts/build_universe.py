"""Build the Phase 3 universe table and the panel spine's two views.

Writes, under `data/phase2/`:

    universe/venue=<hl|lighter>/part.parquet   symbol x hour x point-in-time size rank
    panel/view=<per_venue|cross_venue>/part.parquet

`per_venue` is every market on each venue (Target A); `cross_venue` is the concurrent matched
intersection (Target B). Defaults come from `docs/phase3/decisions.md`: `--pool venue` (D1),
`--rebalance daily` (D2), `--start 2025-01-17` (D5). Every one is overridable, because Task 5
sweeps them.

**Nothing here gates.** `in_hl_top10` and `in_lighter_top10` are booleans on the row, never
filters applied to it: the design's "outside the top 10 by open interest" rule has never been
measured, and a deleted row cannot be recovered without a re-run (see the headline decision).

**`rank_unavailable` is the rank that GOVERNS THIS ROW**, so that `filter(~rank_unavailable)` is
safe to write on any row of either view:

    view=cross_venue              -> the Hyperliquid leg (D1 ranks the pair on HL open-interest
                                     notional, so the HL leg governs the pair)
    view=per_venue, venue=hl      -> the Hyperliquid leg
    view=per_venue, venue=lighter -> the Lighter leg

`hl_rank_unavailable` and `lighter_rank_unavailable` are carried beside it and never ambiguous:
**a statistic about a specific venue conditions on that venue's own column**, not on
`rank_unavailable`. The earlier draft of this script defined `rank_unavailable` as the
Hyperliquid leg on every row, which made the obvious filter delete all 1,585,687 rows of Target
A's Lighter panel in silence. A Lighter-only market has no Hyperliquid rank to fail closed on;
it is not an unusable row.

`in_hl_top10` is null exactly where `hl_rank_unavailable`, and `in_lighter_top10` exactly where
`lighter_rank_unavailable`, on every row of both views -- that pair of equivalences is the
fail-closed guarantee. **Drop unavailable rows from rank-conditioned statistics; never test
their rank against 10.**

**The multipliers are carried, never applied.** Both size measures are notional (USD) and
therefore denomination-invariant: scaling a `kPEPE` size by 1000 and its price by 1/1000 leaves
the notional unchanged. `size_multiplier` and `lighter_size_multiplier` travel on the row so
that a later *price* or *size* feature cannot silently mis-scale a `k`/`1000` market -- and they
are per **venue leg**, not per pair. `NOT` measures 1 on Hyperliquid against 1000 on Lighter's
`1000NOT`.

**No `shift` anywhere.** Hyperliquid's funding history carries 1,789 eight-hour intervals and
213 single-hour holes, so a one-row shift pairs rows two or eight hours apart. `pair_age_hours`
is an explicit hour difference against the pair's first concurrent hour, and it is measured over
the pair's whole history rather than from `--start`, so sweeping the start date does not
manufacture a fresh young cohort.
"""
import argparse
import sys
from datetime import datetime, timedelta

import polars as pl

from fundr import alias, dataset, universe

SOURCE = "phase3:build_universe"
TOP_N = 10
DEFAULT_START = datetime(2025, 1, 17)
HOUR = timedelta(hours=1)

#: How far before `--start` the size feed is read, so a daily or monthly basis has the previous
#: period to rank on instead of opening the window with `rank_unavailable`.
LOOKBACK = {"hourly": timedelta(0), "daily": timedelta(days=1), "monthly": timedelta(days=31)}

_FUNDING = {"hl": ("hl_funding", "coin", "coin"),
            "lighter": ("lighter_funding", "market_id", "symbol")}

_HL_MULTIPLIER = {hl: multiplier for hl, multiplier in alias.ALIASES.values()}
_LIGHTER_MULTIPLIER = {li: alias.LIGHTER_MULTIPLIER for li in alias.ALIASES}

PANEL_COLUMNS = ["view", "venue", "symbol", "lighter_symbol", "hour", "hl_rank", "lighter_rank",
                 "in_hl_top10", "in_lighter_top10", "rank_unavailable", "hl_rank_unavailable",
                 "lighter_rank_unavailable", "pairs_live_this_hour", "pair_age_hours",
                 "size_multiplier", "lighter_size_multiplier", "is_alias_pair"]


# --- spines ---------------------------------------------------------------------------------

def funding_hours(venue: str) -> pl.DataFrame:
    """Every (symbol, settled hour) a venue has, over its whole history.

    The spine is the settled funding grid, not a live roster: 5 of the 100 exact-matched symbols
    are Hyperliquid-delisted today and 4 are Lighter-inactive, and they belong in the panel. The
    whole history is read even when `--start` is late, because `pair_age_hours` is measured from
    a pair's true first concurrent hour."""
    name, partition, symbol_col = _FUNDING[venue]
    files = sorted((dataset.root() / name).glob(f"{partition}=*/part.parquet"))
    if not files:
        return pl.DataFrame(schema={"symbol": pl.String, "hour": pl.Datetime("ms")})
    return (pl.scan_parquet(files)
            .select(pl.col(symbol_col).cast(pl.String).alias("symbol"),
                    pl.col("settle_time").alias("hour"))
            .unique().collect().sort(["symbol", "hour"]))


def cross_spine(hl_hours: pl.DataFrame, lighter_hours: pl.DataFrame,
                matched: list[tuple[str, str]]) -> pl.DataFrame:
    """One row per (canonical symbol, hour) that BOTH venues settled.

    A pair is two symbols, not one -- `kPEPE` on Hyperliquid is `1000PEPE` on Lighter -- so each
    leg is looked up under its own venue's name and the row is keyed by the canonical
    (Hyperliquid) symbol. This is the same intersection `scripts/cross_venue_overlap.py`
    reports, and it must reproduce its count."""
    legs = pl.DataFrame({"symbol": [hl for hl, _ in matched],
                         "lighter_symbol": [li for _, li in matched]},
                        schema={"symbol": pl.String, "lighter_symbol": pl.String})
    return (hl_hours.join(legs, on="symbol", how="inner")
            .join(lighter_hours.rename({"symbol": "lighter_symbol"}),
                  on=["lighter_symbol", "hour"], how="inner")
            .select("symbol", "lighter_symbol", "hour"))


def _with_age(spine: pl.DataFrame, over: list[str]) -> pl.DataFrame:
    """Hours since this pair's (or market's) first hour, as an explicit difference.

    Computed before the window filter, so a later `--start` shifts which rows are kept without
    changing any row's age. Basis risk is concentrated in young markets -- week-1 24-hour drift
    sd ~124 bp against ~17 bp at three months -- and Phase 3's own size rule steers the universe
    toward exactly that cohort, so Task 5 cannot measure the interaction without this column."""
    return spine.with_columns(
        (pl.col("hour") - pl.col("hour").min().over(over)).dt.total_hours()
        .cast(pl.Int64).alias("pair_age_hours"))


def _window(spine: pl.DataFrame, start: datetime, end: datetime, over: list[str]) -> pl.DataFrame:
    """Clip to the panel window and count the cross-section's width in each surviving hour.

    `pairs_live_this_hour` is D5's obligation: the cross-section grows sevenfold over the window
    (median 8 live pairs per hour over the first six weeks against 73.5 late), and no
    cross-sectional statistic may be reported without conditioning on it."""
    return (spine.filter(pl.col("hour").is_between(start, end))
            .with_columns(pl.len().over(over).cast(pl.Int64).alias("pairs_live_this_hour")))


# --- ranks ----------------------------------------------------------------------------------

def _ranked(size: pl.DataFrame, *, basis: str, pool) -> pl.DataFrame:
    return universe.rank(size, basis=basis, pool=pool)


def _rank_columns(ranked: pl.DataFrame, prefix: str) -> pl.DataFrame:
    return ranked.select("symbol", "hour",
                         pl.col("size_rank").alias(f"{prefix}_rank"),
                         pl.col("rank_unavailable").alias(f"{prefix}_rank_unavailable"))


def universe_frame(ranked: pl.DataFrame, *, venue: str, measure: str, basis: str,
                   start: datetime, end: datetime) -> pl.DataFrame:
    return (ranked.filter(pl.col("hour").is_between(start, end))
            .select(pl.lit(venue).alias("venue"), "symbol", "hour",
                    pl.lit(measure).alias("size_measure"),
                    pl.col(measure).alias("size_value"),
                    "size_rank", pl.lit(basis).alias("rank_basis"),
                    "rank_is_carried_forward", "rank_stale_hours", "rank_unavailable")
            .sort(["symbol", "hour"]))


# --- panel ----------------------------------------------------------------------------------

def _top10(rank: str, unavailable: str) -> pl.Expr:
    """Null where the rank is unavailable, never False.

    The distinction is the whole fail-closed rule: a null rank makes `size_rank <= 10` evaluate
    False, which ADMITS the market the rule exists to exclude."""
    return (pl.when(pl.col(unavailable)).then(None)
            .otherwise(pl.col(rank) <= TOP_N).cast(pl.Boolean).alias(
                "in_hl_top10" if rank == "hl_rank" else "in_lighter_top10"))


def _finalise(panel: pl.DataFrame, *, view: str) -> pl.DataFrame:
    return (panel
            .with_columns(pl.col("hl_rank_unavailable").fill_null(True),
                          pl.col("lighter_rank_unavailable").fill_null(True))
            .with_columns(pl.when(pl.col("hl_rank_unavailable")).then(None)
                          .otherwise(pl.col("hl_rank")).alias("hl_rank"),
                          pl.when(pl.col("lighter_rank_unavailable")).then(None)
                          .otherwise(pl.col("lighter_rank")).alias("lighter_rank"))
            .with_columns(_top10("hl_rank", "hl_rank_unavailable"),
                          _top10("lighter_rank", "lighter_rank_unavailable"),
                          pl.lit(view).alias("view"),
                          # The governing leg: Lighter for a per-venue Lighter row, Hyperliquid
                          # for everything else. `~rank_unavailable` must never delete a row
                          # whose own venue ranked it perfectly well.
                          pl.when(pl.col("venue") == "lighter")
                          .then(pl.col("lighter_rank_unavailable"))
                          .otherwise(pl.col("hl_rank_unavailable")).alias("rank_unavailable"))
            .select(PANEL_COLUMNS).sort(["symbol", "hour"]))


def cross_panel(spine: pl.DataFrame, hl_ranks: pl.DataFrame,
                lighter_ranks: pl.DataFrame) -> pl.DataFrame:
    """Target B's view: the concurrent matched intersection, both rank columns, neither gating."""
    return _finalise(
        spine
        .join(_rank_columns(hl_ranks, "hl"), on=["symbol", "hour"], how="left")
        .join(_rank_columns(lighter_ranks, "lighter").rename({"symbol": "lighter_symbol"}),
              on=["lighter_symbol", "hour"], how="left")
        .with_columns(
            pl.lit("cross").alias("venue"),
            pl.col("symbol").replace_strict(_HL_MULTIPLIER, default=1, return_dtype=pl.Int64)
            .alias("size_multiplier"),
            pl.col("lighter_symbol").replace_strict(_LIGHTER_MULTIPLIER, default=1,
                                                    return_dtype=pl.Int64)
            .alias("lighter_size_multiplier"),
            (pl.col("symbol") != pl.col("lighter_symbol")).alias("is_alias_pair")),
        view="cross_venue")


def per_venue_panel(hl_spine: pl.DataFrame, lighter_spine: pl.DataFrame,
                    hl_ranks: pl.DataFrame, lighter_ranks: pl.DataFrame) -> pl.DataFrame:
    """Target A's view: every market on each venue, matched or not.

    A Lighter row carries no Hyperliquid rank -- 130 of the 235 Lighter markets are venue
    exclusives and the rest are not ranked off the other venue's crowding (D3 rejected that) --
    so `hl_rank_unavailable` is true on every Lighter row. That is a statement about the
    Hyperliquid leg, not about the row: the Lighter rank governs a Lighter row, so
    `rank_unavailable` follows `lighter_rank_unavailable` there."""
    hl = (hl_spine
          .join(_rank_columns(hl_ranks, "hl"), on=["symbol", "hour"], how="left")
          .with_columns(
              pl.lit("hl").alias("venue"),
              pl.lit(None, dtype=pl.String).alias("lighter_symbol"),
              pl.lit(None, dtype=pl.Int64).alias("lighter_rank"),
              pl.lit(None, dtype=pl.Boolean).alias("lighter_rank_unavailable"),
              pl.col("symbol").replace_strict(_HL_MULTIPLIER, default=1, return_dtype=pl.Int64)
              .alias("size_multiplier"),
              pl.lit(None, dtype=pl.Int64).alias("lighter_size_multiplier"),
              pl.col("symbol").is_in(list(_HL_MULTIPLIER)).alias("is_alias_pair")))
    lighter = (lighter_spine
               .join(_rank_columns(lighter_ranks, "lighter"), on=["symbol", "hour"], how="left")
               .with_columns(
                   pl.lit("lighter").alias("venue"),
                   pl.col("symbol").alias("lighter_symbol"),
                   pl.lit(None, dtype=pl.Int64).alias("hl_rank"),
                   pl.lit(None, dtype=pl.Boolean).alias("hl_rank_unavailable"),
                   pl.col("symbol").replace_strict(_LIGHTER_MULTIPLIER, default=1,
                                                   return_dtype=pl.Int64)
                   .alias("size_multiplier"),
                   pl.col("symbol").replace_strict(_LIGHTER_MULTIPLIER, default=1,
                                                   return_dtype=pl.Int64)
                   .alias("lighter_size_multiplier"),
                   pl.col("symbol").is_in(list(_LIGHTER_MULTIPLIER)).alias("is_alias_pair")))
    return _finalise(pl.concat([hl, lighter], how="diagonal_relaxed"), view="per_venue")


# --- orchestration --------------------------------------------------------------------------

def build(*, start: datetime, end: datetime, rebalance: str, pool: str) -> dict[str, pl.DataFrame]:
    """The four frames Phase 3 emits, in memory. `main` writes them and reports the gates."""
    if pool not in ("venue", "matched"):
        raise ValueError(f"unknown pool {pool!r}; expected 'venue' or 'matched'")
    if rebalance not in LOOKBACK:
        raise ValueError(f"unknown rebalance {rebalance!r}; expected one of {sorted(LOOKBACK)}")

    hl_hours, lighter_hours = funding_hours("hl"), funding_hours("lighter")
    matched = alias.matched_pairs(hl_hours["symbol"].unique(), lighter_hours["symbol"].unique())
    hl_pool = [hl for hl, _ in matched] if pool == "matched" else "venue"
    lighter_pool = [li for _, li in matched] if pool == "matched" else "venue"

    size_start = start - LOOKBACK[rebalance]
    hl_ranks = _ranked(universe.hl_size(size_start, end), basis=rebalance, pool=hl_pool)
    lighter_ranks = _ranked(universe.lighter_size(size_start, end), basis=rebalance,
                            pool=lighter_pool)

    cross = _window(_with_age(cross_spine(hl_hours, lighter_hours, matched), ["symbol"]),
                    start, end, ["hour"])
    hl_spine = _window(_with_age(hl_hours, ["symbol"]), start, end, ["hour"])
    lighter_spine = _window(_with_age(lighter_hours, ["symbol"]), start, end, ["hour"])

    return {
        "universe_hl": universe_frame(hl_ranks, venue="hl", measure=universe.HL_MEASURE,
                                      basis=rebalance, start=start, end=end),
        "universe_lighter": universe_frame(lighter_ranks, venue="lighter",
                                           measure=universe.LIGHTER_MEASURE,
                                           basis=rebalance, start=start, end=end),
        "per_venue": per_venue_panel(hl_spine, lighter_spine, hl_ranks, lighter_ranks),
        "cross_venue": cross_panel(cross, hl_ranks, lighter_ranks),
    }


def write(frames: dict[str, pl.DataFrame]) -> None:
    dataset.write_partition("universe", frames["universe_hl"], source=SOURCE, venue="hl")
    dataset.write_partition("universe", frames["universe_lighter"], source=SOURCE,
                            venue="lighter")
    dataset.write_partition("panel", frames["per_venue"], source=SOURCE, view="per_venue")
    dataset.write_partition("panel", frames["cross_venue"], source=SOURCE, view="cross_venue")


def report(frames: dict[str, pl.DataFrame]) -> list[str]:
    """The plan's verify step, measured rather than asserted, so a mismatch is visible."""
    cross, per_venue = frames["cross_venue"], frames["per_venue"]
    lines = [f"cross_venue rows: {cross.height:,}",
             f"per_venue rows: {per_venue.height:,}"]
    for venue in ("hl", "lighter"):
        rows = per_venue.filter(pl.col("venue") == venue)
        lines.append(f"per_venue {venue} markets: {rows['symbol'].n_unique()} "
                     f"({rows.height:,} rows)")
    # The fail-closed guarantee, one line per leg. Scoped to the leg rather than to the row,
    # because a Lighter-only market has no Hyperliquid rank to fail closed on.
    lines.append("cross_venue: rows with in_hl_top10 null and rank_unavailable false: "
                 f"{cross.filter(pl.col('in_hl_top10').is_null()
                                 & ~pl.col('rank_unavailable')).height:,}")
    for name, panel in (("cross_venue", cross), ("per_venue", per_venue)):
        for flag, leg in (("in_hl_top10", "hl"), ("in_lighter_top10", "lighter")):
            fail_open = panel.filter(pl.col(flag).is_null()
                                     & ~pl.col(f"{leg}_rank_unavailable")).height
            lines.append(f"{name}: rows with {flag} null and "
                         f"{leg}_rank_unavailable false: {fail_open:,}")
    lines.append(f"cross_venue alias pair-hours: {int(cross['is_alias_pair'].sum()):,}")
    # What `filter(~rank_unavailable)` -- the filter everyone will actually write -- keeps.
    for name, panel in (("cross_venue", cross), ("per_venue hl",
                                                 per_venue.filter(pl.col("venue") == "hl")),
                        ("per_venue lighter", per_venue.filter(pl.col("venue") == "lighter"))):
        kept = panel.filter(~pl.col("rank_unavailable")).height
        lines.append(f"{name}: rank_unavailable false on {kept:,} of {panel.height:,} rows "
                     f"({kept / panel.height * 100:.2f}% survive ~rank_unavailable); "
                     f"hl leg unavailable {int(panel['hl_rank_unavailable'].sum()):,}, "
                     f"lighter leg unavailable "
                     f"{int(panel['lighter_rank_unavailable'].sum()):,}")
    return lines


def parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pool", choices=("venue", "matched"), default="venue",
                    help="what the size rank is computed against (D1)")
    ap.add_argument("--rebalance", choices=tuple(LOOKBACK), default="daily",
                    help="how often the rank is recomputed (D2)")
    ap.add_argument("--start", type=datetime.fromisoformat, default=DEFAULT_START,
                    help="first panel hour (D5)")
    ap.add_argument("--end", type=datetime.fromisoformat, default=None,
                    help="last panel hour; default is the last settled hour on either venue")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    end = args.end
    if end is None:
        end = max(funding_hours("hl")["hour"].max(), funding_hours("lighter")["hour"].max())
    frames = build(start=args.start, end=end, rebalance=args.rebalance, pool=args.pool)
    write(frames)
    print(f"window {args.start} -> {end}; pool={args.pool} rebalance={args.rebalance}")
    print("\n".join(report(frames)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
