"""Point-in-time size ranking for both venues, and the market-wide giant regressors.

Every rank here comes from size recorded **at or before** the hour it labels. Nothing is ranked
on a size measured today, and nothing is lagged with `shift`: Hyperliquid's funding grid carries
1,789 eight-hour intervals and 213 single-hour holes, so a one-row shift silently pairs rows two
or eight hours apart. Lags are explicit calendar offsets and joins.

**Size means notional.** Hyperliquid is `open_interest x mark_px`, Lighter is completed quote
(USD) volume. Ranking base units would rank 1 BTC against 1,000,000 PEPE. Both measures are
denomination-invariant, which is why nothing here calls `fundr.alias.size_multiplier`: scaling a
`kPEPE` size by 1000 and its price by 1/1000 leaves the notional unchanged.

**Fail closed on a missing size — the reason this module exists.** The obvious implementation
leaves an unrankable hour's rank null, and every downstream `size_rank <= 10` test then evaluates
false, which **admits** the market instead of excluding it. 17,156 Hyperliquid pair-hours are in
that state and the archive's short days *cluster*, so on exactly those weeks BTC, ETH and SOL
would sit inside the panel the rule exists to keep them out of. Instead `rank` carries the last
known rank forward, counts how old it is, and refuses to carry it past `MAX_STALE_HOURS`.

`rank_stale_hours` is not decoration. A boolean cannot tell a one-hour carry from an eight-day
one, and an earlier paging bug in `scripts/backfill_lighter_candles.py` left 297,185 missing
candles in blocks of ~200 hours -- carry-forward papered over every one of them. The bug is
fixed and the Lighter leg is now 0.24% unrankable, but the counter stays, because it is what
would have caught it.

**`rank_unavailable` rows must be dropped from any rank-conditioned statistic, not tested.**
Their `size_rank` is null on purpose: a stale rank wearing a point-in-time label is worse than
no rank at all.
"""
from collections.abc import Iterable
from datetime import datetime, timedelta

import polars as pl

from fundr import dataset

HL_MEASURE = "oi_notional"
LIGHTER_MEASURE = "trailing_quote_volume_24h"
LIGHTER_WINDOW_HOURS = 24
#: How stale a carried-forward rank may be before it stops being a rank at all.
MAX_STALE_HOURS = 24
#: Excluded under every setting of D1 and D2, and kept as regressors by D7.
GIANTS = ("BTC", "ETH", "SOL", "XRP", "HYPE")

_PERIOD = {"hourly": None, "daily": "1d", "monthly": "1mo"}
HOUR = timedelta(hours=1)


def _hour_window(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    return start.replace(minute=0, second=0, microsecond=0), end.replace(
        minute=0, second=0, microsecond=0)


def hl_size(start: datetime, end: datetime) -> pl.DataFrame:
    """Hyperliquid open-interest notional per symbol per hour, over `[start, end]` inclusive.

    `hl_asset_ctxs` is 275M per-minute rows across 1,218 daily files, so only the day partitions
    the window actually spans are scanned and each is reduced on the way in. The hour's level is
    the **median** of its minutes: a single minute's print can be stale or a spike."""
    start, end = _hour_window(start, end)
    base = dataset.root() / "hl_asset_ctxs"
    lo, hi = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    files = [p for p in sorted(base.glob("date=*/part.parquet"))
             if lo <= p.parent.name.split("=", 1)[1] <= hi]
    if not files:
        return pl.DataFrame(schema={"hour": pl.Datetime("ms"), "symbol": pl.String,
                                    HL_MEASURE: pl.Float64})
    return (pl.scan_parquet(files)
            .filter((pl.col("time") >= start) & (pl.col("time") < end + HOUR))
            .select(pl.col("time").dt.truncate("1h").alias("hour"),
                    pl.col("coin").alias("symbol"),
                    (pl.col("open_interest") * pl.col("mark_px")).alias(HL_MEASURE))
            .group_by("hour", "symbol").agg(pl.col(HL_MEASURE).median())
            .collect(engine="streaming")
            .select("hour", "symbol", HL_MEASURE).sort(["symbol", "hour"]))


def lighter_size(start: datetime, end: datetime) -> pl.DataFrame:
    """Lighter trailing 24-hour quote volume per symbol per hour, over `[start, end]` inclusive.

    Lighter publishes no open-interest history anywhere, so completed quote volume is the size
    proxy (`decisions.md` D3 measures how good it is: median 8 of 10 top-10 members against the
    truth on Hyperliquid, worst precisely at the boundary).

    The candle stamped `T` covers `[T, T+1)` and is still forming at `T`, so the window ending at
    bar `T-1` is the newest one completed before `T` -- hence the label is the bar time plus one
    hour, an explicit offset rather than a `shift` over a series that may have holes. A window
    short of its full 24 bars produces **no row**: a partial sum understates volume, which would
    rank a young market as tiny and quietly admit it. `rank` then fails that hour closed."""
    start, end = _hour_window(start, end)
    files = sorted((dataset.root() / "lighter_candles").glob("market_id=*/part.parquet"))
    empty = pl.DataFrame(schema={"hour": pl.Datetime("ms"), "symbol": pl.String,
                                 LIGHTER_MEASURE: pl.Float64})
    if not files:
        return empty
    bars = (pl.scan_parquet(files)
            .select("symbol", "time", "quote_volume")
            .filter((pl.col("time") >= start - LIGHTER_WINDOW_HOURS * HOUR)
                    & (pl.col("time") <= end - HOUR))
            .collect()
            .sort(["symbol", "time"]))
    if bars.is_empty():
        return empty
    return (bars.with_columns(
                pl.col("quote_volume")
                .rolling_sum_by("time", f"{LIGHTER_WINDOW_HOURS}h", closed="right",
                                min_samples=LIGHTER_WINDOW_HOURS)
                .over("symbol").alias(LIGHTER_MEASURE))
            .select((pl.col("time") + HOUR).alias("hour"), "symbol", LIGHTER_MEASURE)
            .drop_nulls(LIGHTER_MEASURE)
            .filter(pl.col("hour") >= start)
            .sort(["symbol", "hour"]))


def _measure_column(size: pl.DataFrame) -> str:
    rest = [c for c in size.columns if c not in ("symbol", "hour")]
    if len(rest) != 1:
        raise ValueError(f"expected one size column beside symbol/hour, got {rest}")
    return rest[0]


def _hourly_grid(size: pl.DataFrame) -> pl.DataFrame:
    """Every hour from a symbol's first size row to the frame's last, for every symbol.

    The grid is what makes the fail-closed rule possible: an hour with no size row has to EXIST
    as a row before it can be flagged. It runs to the frame's end rather than to each symbol's
    own last row so that a market whose size feed stops -- delisted, or an archive hole at the
    tail -- keeps producing rows, which `MAX_STALE_HOURS` then turns into `rank_unavailable`
    instead of silence that reads as admitted."""
    last = size["hour"].max()
    return (size.group_by("symbol").agg(pl.col("hour").min().alias("_first"))
            .with_columns(pl.datetime_ranges(pl.col("_first"), pl.lit(last, dtype=pl.Datetime("ms")),
                                             interval="1h", closed="both").alias("hour"))
            .explode("hour", empty_as_null=False).select("symbol", "hour"))


def _fresh_ranks(size: pl.DataFrame, measure: str, period: str | None) -> pl.DataFrame:
    """`symbol`, `_key`, `size_rank` -- the rank each hour is entitled to before any carry.

    For `hourly` the key is the hour itself. For `daily`/`monthly` the key is the period the rank
    LABELS, reached by offsetting the period it was MEASURED on forward by exactly one period:
    the rank in force today is computed from yesterday's median and never from today's."""
    if period is None:
        base = size.select("symbol", pl.col("hour").alias("_key"),
                           pl.col(measure).alias("_value"))
    else:
        base = (size.with_columns(pl.col("hour").dt.truncate(period).alias("_key"))
                .group_by("symbol", "_key").agg(pl.col(measure).median().alias("_value")))
    return (base.drop_nulls("_value")
            .with_columns(pl.col("_value").rank("ordinal", descending=True)
                          .over("_key").cast(pl.Int64).alias("size_rank"))
            .with_columns(pl.col("_key").dt.offset_by(period) if period else pl.col("_key"))
            .select("symbol", "_key", "size_rank"))


def rank(size: pl.DataFrame, *, basis: str, pool: str | Iterable[str]) -> pl.DataFrame:
    """Attach a point-in-time size rank to every hour of every symbol in `size`.

    `basis` is `hourly`, `daily` (the previous day's median size) or `monthly` (the previous
    month's). `pool` is `"venue"` -- rank against every symbol in the frame, which is D1's
    decision -- or an explicit collection of symbols to rank within and report on.

    Adds, beside the input columns:

    - `size_rank` -- 1 is the largest. Null wherever `rank_unavailable`.
    - `rank_is_carried_forward` -- this row's rank came from an earlier hour.
    - `rank_stale_hours` -- how many hours earlier. 0 when fresh; null when no rank has ever
      existed for this symbol. Under a daily or monthly basis the basis's own lag is not
      staleness: an hour ranked on yesterday's median as designed reads 0.
    - `rank_unavailable` -- there is no rank for this hour, either because none has ever existed
      or because the newest is more than `MAX_STALE_HOURS` old. **Drop these rows from every
      rank-conditioned statistic; do not test their rank.**

    A caller wanting a daily or monthly basis on the first day of `size` gets `rank_unavailable`
    there, correctly -- the previous period is outside the frame. Pass a window that starts one
    period early if those hours matter."""
    if basis not in _PERIOD:
        raise ValueError(f"unknown basis {basis!r}; expected one of {sorted(_PERIOD)}")
    measure = _measure_column(size)
    if pool != "venue":
        size = size.filter(pl.col("symbol").is_in(list(pool)))
    if size.is_empty():
        return size.with_columns(pl.lit(None, dtype=pl.Int64).alias("size_rank"),
                                 pl.lit(False).alias("rank_is_carried_forward"),
                                 pl.lit(None, dtype=pl.Int64).alias("rank_stale_hours"),
                                 pl.lit(True).alias("rank_unavailable"))

    period = _PERIOD[basis]
    grid = _hourly_grid(size).with_columns(
        (pl.col("hour").dt.truncate(period) if period else pl.col("hour")).alias("_key"))
    return (grid.join(_fresh_ranks(size, measure, period), on=["symbol", "_key"], how="left")
            .join(size, on=["symbol", "hour"], how="left")
            .sort(["symbol", "hour"])
            # The hour the live rank was measured in, carried alongside the rank itself, so
            # staleness is a measured hour difference and not a count of filled rows.
            .with_columns(pl.when(pl.col("size_rank").is_not_null()).then(pl.col("hour"))
                          .alias("_rank_hour"))
            .with_columns(pl.col("size_rank").forward_fill().over("symbol").alias("_carried"),
                          pl.col("_rank_hour").forward_fill().over("symbol"))
            .with_columns((pl.col("hour") - pl.col("_rank_hour")).dt.total_hours()
                          .alias("rank_stale_hours"))
            .with_columns(pl.col("rank_stale_hours").gt(MAX_STALE_HOURS)
                          .or_(pl.col("_carried").is_null())
                          .fill_null(True).alias("rank_unavailable"))
            .with_columns(pl.when(pl.col("rank_unavailable")).then(None)
                          .otherwise(pl.col("_carried")).alias("size_rank"))
            .with_columns(pl.col("size_rank").is_not_null()
                          .and_(pl.col("rank_stale_hours") > 0).alias("rank_is_carried_forward"))
            .select("symbol", "hour", measure, "size_rank", "rank_is_carried_forward",
                    "rank_stale_hours", "rank_unavailable"))


def giant_regressors(start: datetime, end: datetime,
                     size: pl.DataFrame | None = None) -> pl.DataFrame:
    """BTC/ETH/SOL/XRP/HYPE point-in-time funding and open-interest notional, one row per hour.

    These five are excluded from the panel under every setting of D1 and D2, and they are also
    the best available proxy for the common funding factor that makes 100 symbols much less than
    100 independent observations. Excluding them as observations does not require dropping them
    as regressors (`decisions.md` D7). Join by `hour`.

    `size` re-uses an `hl_size` frame the caller already holds; omitted, it is recomputed, which
    rescans the archive."""
    start, end = _hour_window(start, end)
    base = dataset.root() / "hl_funding"
    files = [p for p in (base / f"coin={g}" / "part.parquet" for g in GIANTS) if p.exists()]
    funding = (pl.scan_parquet(files)
               .filter(pl.col("settle_time").is_between(start, end))
               .select(pl.col("coin").alias("symbol"),
                       pl.col("settle_time").alias("hour"),
                       pl.col("signed_rate_fraction").alias("funding"))
               .collect() if files else
               pl.DataFrame(schema={"symbol": pl.String, "hour": pl.Datetime("ms"),
                                    "funding": pl.Float64}))
    notional = (hl_size(start, end) if size is None else size).filter(
        pl.col("symbol").is_in(GIANTS))
    return (funding.join(notional, on=["symbol", "hour"], how="full", coalesce=True)
            .pivot(on="symbol", index="hour", values=["funding", HL_MEASURE])
            .sort("hour"))
