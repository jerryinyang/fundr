"""Measure CONCURRENT cross-venue overlap -- the only overlap Target B can be built on.

`qa_backfill.py` reports that 100 symbols match across the two venues, but that is a LIFETIME
count: a symbol listed on Hyperliquid in 2023 and on Lighter in 2026 "matches" while never once
having been live on both venues at the same hour. Target B is the HL-minus-Lighter funding
spread, which exists only where both venues settled the same hour for the same asset. This
script measures that intersection hour by hour, and then asks how much of it survives Phase 3's
"outside the top 10 by open interest" universe rule.

Everything here is point-in-time: the top-10 rank is recomputed for every hour from the open
interest recorded in that hour, never from a rank measured today."""
import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from fundr import alias, dataset
from qa_backfill import _load, _md_table

MONTH_HOURS = 720
QUARTER_HOURS = 2160
TOP_N = 10


def pair_hours(hl: dict[str, pl.DataFrame], li: dict[str, pl.DataFrame],
               matched: list[tuple[str, str]]) -> pl.DataFrame:
    """One row per (canonical symbol, hour) that BOTH venues settled. The spine for everything
    below.

    A pair is TWO symbols, not one: `kPEPE` on Hyperliquid is `1000PEPE` on Lighter. Each leg is
    read under its own venue's name and relabelled with the canonical (Hyperliquid) symbol, which
    is also the name `hl_asset_ctxs` ranks under."""
    def side(frames, legs):
        parts = [frames[leg].select(pl.lit(symbol).alias("symbol"), "settle_time")
                 for symbol, leg in legs]
        return (pl.concat(parts).unique().rename({"settle_time": "hour"})
                if parts else pl.DataFrame(schema={"symbol": pl.String, "hour": pl.Datetime("ms")}))
    return side(hl, [(h, h) for h, _ in matched]).join(
        side(li, [(h, li_symbol) for h, li_symbol in matched]),
        on=["symbol", "hour"], how="inner")


def per_pair(both: pl.DataFrame) -> pl.DataFrame:
    return (both.group_by("symbol")
            .agg(pl.len().alias("shared_hours"),
                 pl.col("hour").min().alias("first_shared"),
                 pl.col("hour").max().alias("last_shared"))
            .with_columns(((pl.col("last_shared") - pl.col("first_shared"))
                           .dt.total_hours() + 1).alias("span_hours"))
            .with_columns((pl.col("shared_hours") / pl.col("span_hours")).alias("density"))
            .sort("shared_hours", descending=True))


def per_hour(both: pl.DataFrame) -> pl.DataFrame:
    return both.group_by("hour").agg(pl.len().alias("live_pairs")).sort("hour")


def first_hour_at_least(hours: pl.DataFrame, n: int) -> datetime | None:
    hit = hours.filter(pl.col("live_pairs") >= n)
    return hit["hour"].min() if hit.height else None


def hourly_oi(start: datetime, end: datetime) -> pl.DataFrame:
    """Hyperliquid open-interest NOTIONAL per coin per hour, over the overlap window only.

    `hl_asset_ctxs` is 275M per-minute rows across 1,218 daily files; scanning only the days the
    overlap actually spans, and reducing each to one value per coin per hour on the way in,
    keeps this to seconds. Notional (`open_interest x mark_px`), not base units -- ranking 1
    BTC against 1,000,000 PEPE by unit count ranks nothing. The median of the hour's minutes is
    the hour's level; a single minute's print can be a stale or a spike."""
    base = dataset.root() / "hl_asset_ctxs"
    files = [p for p in sorted(base.glob("date=*/part.parquet"))
             if start.strftime("%Y-%m-%d") <= p.parent.name.split("=", 1)[1]
             <= end.strftime("%Y-%m-%d")]
    if not files:
        return pl.DataFrame(schema={"hour": pl.Datetime("ms"), "coin": pl.String,
                                    "oi_notional": pl.Float64})
    return (pl.scan_parquet(files)
            .select(pl.col("time").dt.truncate("1h").alias("hour"), "coin",
                    (pl.col("open_interest") * pl.col("mark_px")).alias("oi_notional"))
            .group_by("hour", "coin").agg(pl.col("oi_notional").median())
            .collect(engine="streaming"))


def exclusion(both: pl.DataFrame, oi: pl.DataFrame, *, pool: str) -> pl.DataFrame:
    """Per-pair share of shared hours spent inside HL's top 10 by open interest.

    `pool="venue"` ranks a coin against every Hyperliquid market live that hour -- the design's
    words read literally. `pool="intersection"` ranks it only against the other matched pairs
    live that hour, which is what Target B's universe actually contains. The two give very
    different answers and the design does not say which it means, so both are measured."""
    ranked = oi if pool == "venue" else oi.join(
        both.select(pl.col("symbol").alias("coin"), "hour").unique(), on=["coin", "hour"],
        how="inner")
    ranked = ranked.with_columns(
        pl.col("oi_notional").rank("ordinal", descending=True).over("hour").alias("oi_rank"))
    j = both.join(ranked.select(pl.col("coin").alias("symbol"), "hour", "oi_rank"),
                  on=["symbol", "hour"], how="left")
    # The share divides by RANKED hours, not shared hours. 2026-07-08 has no archive file and
    # 131 days are short, so a coin that is top-10 in every hour it can be ranked in would
    # otherwise read as 0.9876 and never trip an "always excluded" test.
    return (j.group_by("symbol")
            .agg(pl.len().alias("shared_hours"),
                 pl.col("oi_rank").is_null().sum().alias("hours_unranked"),
                 (pl.col("oi_rank") <= TOP_N).sum().alias("hours_in_top10"))
            .with_columns((pl.col("shared_hours")
                           - pl.col("hours_unranked")).alias("hours_ranked"))
            .with_columns(pl.when(pl.col("hours_ranked") > 0)
                          .then(pl.col("hours_in_top10") / pl.col("hours_ranked"))
                          .alias("share_in_top10"))
            .sort("share_in_top10", descending=True, nulls_last=True))


def _summarise_exclusion(exc: pl.DataFrame, label: str) -> list[str]:
    always = int((exc["share_in_top10"] >= 0.99).sum())
    mostly = int(((exc["share_in_top10"] >= 0.5) & (exc["share_in_top10"] < 0.99)).sum())
    sometimes = int(((exc["share_in_top10"] > 0) & (exc["share_in_top10"] < 0.5)).sum())
    never = int((exc["share_in_top10"] == 0).sum())
    kept_hours = int(exc["shared_hours"].sum() - exc["hours_in_top10"].sum())
    return [f"### Ranked against {label}", "",
            f"- pairs never in the top 10 — fully in the universe: **{never}** of {exc.height}",
            f"- pairs in the top 10 for some hours but under half: {sometimes}",
            f"- pairs in the top 10 for most hours: {mostly}",
            f"- pairs in the top 10 essentially always — fully excluded: {always}",
            f"- shared hours surviving the rule: **{kept_hours:,}** of "
            f"{int(exc['shared_hours'].sum()):,}",
            f"- shared hours with no HL open-interest row to rank, counted as surviving: "
            f"{int(exc['hours_unranked'].sum()):,}", "",
            "Pairs spending any time in the top 10:", "",
            _md_table(exc.filter(pl.col("hours_in_top10") > 0)
                      .select("symbol", "shared_hours", "hours_ranked", "hours_in_top10",
                              pl.col("share_in_top10").round(4))
                      .head(20)), ""]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    hl = _load("hl_funding", "coin")
    li = _load("lighter_funding", "market_id")
    matched = alias.matched_pairs(list(hl), list(li))
    aliased = [pair for pair in matched if pair[0] != pair[1]]
    both = pair_hours(hl, li, matched)
    if both.is_empty():
        print("no concurrent overlap at all")
        return 1

    pairs, hours = per_pair(both), per_hour(both)
    concurrent = int((pairs["shared_hours"] > 0).sum())
    start, end = both["hour"].min(), both["hour"].max()
    oi = hourly_oi(start, end)

    monthly = (hours.group_by(pl.col("hour").dt.truncate("1mo").alias("month"))
               .agg(pl.col("live_pairs").median().alias("median_live_pairs"),
                    pl.col("live_pairs").max().alias("max_live_pairs")).sort("month"))

    lines = [
        "# Concurrent cross-venue overlap", "",
        f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M}Z by `scripts/cross_venue_overlap.py`.", "",
        "`qa_backfill.py`'s matched-symbol count is a **lifetime** count. Target B — the "
        "HL-minus-Lighter funding spread — only exists where an asset settled on *both* venues "
        "in the *same* hour. This is that intersection.", "",
        "Symbols are matched through `fundr.alias.matched_pairs`: exact symbol matches plus the "
        "five hand-checked denomination aliases, and nothing else.", "",
        "## Headline", "",
        f"- symbols matched across the two venues (lifetime): **{len(matched)}** "
        f"— {len(matched) - len(aliased)} exact plus {len(aliased)} denomination aliases",
        f"- of those, pairs with at least one concurrent hour: **{concurrent}**",
        f"- pairs with ≥ {MONTH_HOURS} concurrent hours (30 days): "
        f"**{int((pairs['shared_hours'] >= MONTH_HOURS).sum())}**",
        f"- pairs with ≥ {QUARTER_HOURS} concurrent hours (90 days): "
        f"**{int((pairs['shared_hours'] >= QUARTER_HOURS).sum())}**",
        f"- total concurrent pair-hours: **{both.height:,}**",
        f"- median concurrent hours per pair: {pairs['shared_hours'].median()}", "",
        "## The five denomination-scaled pairs", "",
        "These five are matched by name, not by unit. **The scaling is per venue leg, not per "
        "pair.** Funding *rates* are unaffected — the counts above and the spread itself are "
        "safe — but every price, size and notional built from these markets is not.", "",
        _md_table(pl.DataFrame(
            [{"hyperliquid": hl_symbol, "lighter": li_symbol,
              "hl_multiplier": alias.size_multiplier(hl_symbol, "hl"),
              "lighter_multiplier": alias.size_multiplier(li_symbol, "lighter"),
              "shared_hours": int(pairs.filter(pl.col("symbol") == hl_symbol)["shared_hours"]
                                  .sum())}
             for hl_symbol, li_symbol in aliased])), "",
        "`kBONK`, `kFLOKI`, `kPEPE` and `kSHIB` carry 1000 on **both** legs — both venues already "
        "quote 1000 tokens. `NOT` does not: Hyperliquid lists `NOT`, one token, not `kNOT`, "
        "against Lighter's `1000NOT`, so the legs differ by exactly 1000x (measured price ratio "
        "0.000999). See `src/fundr/alias.py` for the measurement.", "",
        "## When the overlap window truly starts", "",
        f"- first hour any pair is live on both venues: **{start}**",
        f"- last such hour: {end}",
        f"- first hour with ≥ 10 pairs live: {first_hour_at_least(hours, 10)}",
        f"- first hour with ≥ 25 pairs live: {first_hour_at_least(hours, 25)}",
        f"- first hour with ≥ 50 pairs live: {first_hour_at_least(hours, 50)}",
        f"- first hour with ≥ 75 pairs live: {first_hour_at_least(hours, 75)}",
        f"- most pairs ever live in one hour: {int(hours['live_pairs'].max())}",
        f"- hours in the window: {int((end - start).total_seconds() // 3600) + 1}; "
        f"hours with at least one live pair: {hours.height}", "",
        "Live pairs per month (median and peak of the hourly count):", "",
        _md_table(monthly), "",
        "## Top 20 pairs by shared hours", "",
        "`density` is shared hours divided by the span between the pair's first and last shared "
        "hour — below 1.0 means the pair's concurrent history has holes inside it.", "",
        _md_table(pairs.select("symbol", "shared_hours", "first_shared", "last_shared",
                               "span_hours", pl.col("density").round(4)).head(20)), "",
        "## Surviving Phase 3's top-10-by-open-interest exclusion", "",
        "Phase 3's universe is \"perpetual markets outside the top 10 by open interest\". The "
        "rank below is recomputed **for every hour** from Hyperliquid's own per-minute "
        "`open_interest x mark_px`, so it is point-in-time and carries no survivorship. "
        "Lighter has no open-interest history at all (see the universe decision in "
        "`handoff.md`), so the HL side does the ranking for both.", "",
        "**The design does not say what pool the top 10 is drawn from**, and the two readings "
        "differ a lot, so both are here.", ""]
    for pool, label in (("venue", "every Hyperliquid market live that hour"),
                        ("intersection", "the matched pairs only")):
        lines += _summarise_exclusion(exclusion(both, oi, pool=pool), label)

    out = Path(args.out or (dataset.root() / "qa" / "cross_venue_overlap.md"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines))
    print("\n".join(lines))
    print(f"\nreport written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
