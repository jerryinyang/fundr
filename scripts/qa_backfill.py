"""Measure what the backfill actually got: coverage per market against the market's LISTING,
gaps, how far the two venues overlap, whether the hour grids really line up, whether the coin
roster is survivorship-safe, and whether a third party agrees with us.

A dataset nobody has measured is a dataset nobody should model on -- and a coverage number
computed from the data's own first and last row measures nothing at all."""
import argparse
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl

from fundr import dataset
from fundr.analysis import gap_scan

HOUR = timedelta(hours=1)
LAGS = (-1, 0, 1)
VENDOR_TOL = 1e-9


def _md_table(df: pl.DataFrame) -> str:
    """Minimal markdown table -- avoids pulling pandas/tabulate in for a report."""
    if df.is_empty():
        return "_(empty)_"
    cols = df.columns
    head = "| " + " | ".join(cols) + " |"
    rule = "|" + "|".join("---" for _ in cols) + "|"
    rows = ["| " + " | ".join("" if v is None else str(v) for v in row) + " |"
            for row in df.rows()]
    return "\n".join([head, rule, *rows])


def last_complete_hour(now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC).replace(tzinfo=None)
    return now.replace(minute=0, second=0, microsecond=0) - HOUR


def coverage_row(key: str, df: pl.DataFrame, *, time_col: str,
                 expected_start: datetime | None = None, expected_end: datetime | None = None,
                 start_source: str = "data") -> dict:
    if df.is_empty():
        return {"key": key, "rows": 0, "first": None, "last": None, "expected_hours": 0,
                "coverage": None, "gaps": 0, "missing_head_hours": None,
                "missing_tail_hours": None, "expected_start_source": start_source}
    first, last = df[time_col].min(), df[time_col].max()
    # A listing-derived start is a LOWER bound on history, never an upper one: HL's own settled
    # funding begins 2023-05-12 while the archive (which dates HL listings) begins 2023-05-20, so
    # taking the proxy literally would make BTC score above 1.0. Whichever is earlier wins.
    start = min(expected_start, first) if expected_start else first
    end = max(expected_end, last) if expected_end else last
    expected = int((end - start).total_seconds() // 3600) + 1
    gaps = gap_scan(df.with_columns(pl.lit(key).alias("_k")), time_col, "_k", HOUR)
    return {"key": key, "rows": df.height, "first": first, "last": last,
            "expected_hours": expected,
            "coverage": df.height / expected if expected > 0 else None,
            "gaps": gaps.height,
            "missing_head_hours": max(0, int((first - start).total_seconds() // 3600)),
            "missing_tail_hours": max(0, int((end - last).total_seconds() // 3600)),
            "expected_start_source": start_source}


def coverage(frames: dict[str, pl.DataFrame], *, time_col: str,
             expected_starts: dict[str, datetime] | None = None,
             expected_ends: dict[str, datetime | None] | None = None,
             expected_end: datetime | None = None) -> pl.DataFrame:
    """The per-dataset coverage table.

    `expected_starts` maps key -> listing time; a key absent from it falls back to its own first
    row and says so in `expected_start_source`. `expected_ends` overrides the tail anchor per
    key, with an explicit None meaning "measure this market to its own last row" -- which is the
    right answer for a market that has stopped settling (Lighter 173 ended 2026-06-18 and would
    otherwise score ~30% forever). `expected_end` is the default for keys it does not name."""
    expected_starts = expected_starts or {}
    expected_ends = expected_ends or {}
    rows = []
    for key, df in frames.items():
        start = expected_starts.get(key)
        end = expected_ends.get(key, expected_end) if key in expected_ends else expected_end
        rows.append(coverage_row(key, df, time_col=time_col, expected_start=start,
                                 expected_end=end,
                                 start_source="listing" if start else "data"))
    return pl.DataFrame(rows) if rows else pl.DataFrame()


def worst_by_coverage(cov: pl.DataFrame, n: int = 10) -> pl.DataFrame:
    """Worst first -- with nulls LAST. A null coverage means "not measurable", not "worst"."""
    return cov.sort("coverage", nulls_last=True).head(n)


def match_symbols(hl_symbols: list[str], lighter_symbols: list[str]) -> dict:
    hl, li = set(hl_symbols), set(lighter_symbols)
    return {"matched": sorted(hl & li), "hl_only": sorted(hl - li),
            "lighter_only": sorted(li - hl)}


def _paired(hl_by_symbol, li_by_symbol, symbol, lag_hours=0):
    h = hl_by_symbol[symbol].select("settle_time", pl.col("signed_rate_fraction").alias("hl"))
    li = li_by_symbol[symbol].select(
        (pl.col("settle_time") + pl.duration(hours=lag_hours)).alias("settle_time"),
        pl.col("signed_rate_fraction").alias("li"))
    return h.join(li, on="settle_time", how="inner")


def _corr(j: pl.DataFrame) -> float | None:
    if j.height > 2 and j["hl"].std() and j["li"].std():
        return float(j.select(pl.corr("hl", "li")).item())
    return None


def cross_venue(hl_by_symbol: dict[str, pl.DataFrame],
                li_by_symbol: dict[str, pl.DataFrame]) -> pl.DataFrame:
    rows = []
    for symbol in sorted(set(hl_by_symbol) & set(li_by_symbol)):
        j = _paired(hl_by_symbol, li_by_symbol, symbol)
        rows.append({"symbol": symbol, "overlap_hours": j.height,
                     "hl_hours": hl_by_symbol[symbol].height,
                     "lighter_hours": li_by_symbol[symbol].height,
                     "corr_signed_rate": _corr(j),
                     "overlap_start": j["settle_time"].min() if j.height else None,
                     "overlap_end": j["settle_time"].max() if j.height else None})
    return pl.DataFrame(rows)


def lag_scan(hl_by_symbol: dict[str, pl.DataFrame], li_by_symbol: dict[str, pl.DataFrame],
             lags=LAGS) -> pl.DataFrame:
    """Re-test Hyperliquid's settlement-stamp convention, which Phase 1 could only INFER.

    Target B is the HL-minus-Lighter spread at lag 0. If some other lag correlates better, the
    inference is wrong and every spread in the phase is an hour out of place."""
    rows = []
    for lag in lags:
        corrs = [c for symbol in sorted(set(hl_by_symbol) & set(li_by_symbol))
                 if (c := _corr(_paired(hl_by_symbol, li_by_symbol, symbol, lag))) is not None]
        rows.append({"lag_hours": lag, "pairs": len(corrs),
                     "mean_corr": sum(corrs) / len(corrs) if corrs else None,
                     "median_corr": float(pl.Series(corrs).median()) if corrs else None})
    return pl.DataFrame(rows)


def alignment_verdict(lag_table: pl.DataFrame) -> str:
    usable = lag_table.filter(pl.col("mean_corr").is_not_null())
    if usable.is_empty():
        return "NO DATA"
    best = usable.sort("mean_corr", descending=True).row(0, named=True)
    if best["lag_hours"] == 0:
        return "ALIGNED"
    return (f"MISALIGNED: lag {best['lag_hours']}h correlates best "
            f"(mean {best['mean_corr']:.4f}) -- HL's settlement-stamp inference is wrong")


def roster_diff(archive_coins: list[str], meta_coins: list[str]) -> dict:
    a, m = set(archive_coins), set(meta_coins)
    return {"archive_only": sorted(a - m), "meta_only": sorted(m - a),
            "both": len(a & m)}


def _vendor_ms(value) -> int:
    """The live API returns ISO strings ('2026-09-21T13:26:00.117Z'); fixtures used epoch ms."""
    if isinstance(value, str):
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
    return int(value)


def vendor_crosscheck(ox, venue: str, symbol: str, ours: pl.DataFrame, *, days: int = 29,
                      tol: float = VENDOR_TOL) -> dict:
    """Free-tier 0xArchive cross-check (handoff §8 action 6).

    The free tier holds ~30 days and answers a request for exactly 30 with a 403, so the
    default window sits strictly inside it (measured 2026-09-22).

    Units already agree: the vendor's HL `funding_rate` is HL's own fraction, and its Lighter
    `funding_rate` is Lighter's native percent / 100 (P8) -- both on our `signed_rate_fraction`
    basis. The vendor's Lighter rows carry the PREVIOUS settlement within an hour, so the match
    rate is reported at lag 0 and at -1h and the caller reads which one wins."""
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 86_400_000
    rows = ox.get_all(f"/v1/{venue}/funding/{symbol}", max_pages=30,
                      start=start_ms, end=end_ms, limit=1000)
    if not rows:
        return {"symbol": symbol, "venue": venue, "rows_vendor": 0, "rows_ours": ours.height,
                "matched_lag0": 0, "matched_lag_minus_1h": 0, "max_abs_diff": None}
    vendor = (pl.DataFrame({"timestamp": [_vendor_ms(r["timestamp"]) for r in rows],
                            "vendor": [float(r["funding_rate"]) for r in rows]})
              .with_columns(pl.from_epoch("timestamp", time_unit="ms")
                            .dt.cast_time_unit("ms").dt.truncate("1h").alias("settle_time"))
              .unique(subset=["settle_time"], keep="last", maintain_order=True))
    out = {"symbol": symbol, "venue": venue, "rows_vendor": vendor.height,
           "rows_ours": ours.height, "max_abs_diff": None}
    for lag, label in ((0, "matched_lag0"), (-1, "matched_lag_minus_1h")):
        shifted = vendor.select(
            (pl.col("settle_time") + pl.duration(hours=lag)).alias("settle_time"), "vendor")
        j = ours.select("settle_time", "signed_rate_fraction").join(
            shifted, on="settle_time", how="inner")
        diff = (j["signed_rate_fraction"] - j["vendor"]).abs() if j.height else None
        out[label] = int((diff <= tol).sum()) if j.height else 0
        if lag == 0 and j.height:
            out["max_abs_diff"] = float(diff.max())
    return out


def _load(name: str, key: str) -> dict[str, pl.DataFrame]:
    out = {}
    base = dataset.root() / name
    if not base.exists():
        return out
    for part in sorted(base.glob(f"{key}=*/part.parquet")):
        df = pl.read_parquet(part)
        symbol = df["symbol"][0] if "symbol" in df.columns and df.height else \
            part.parent.name.split("=", 1)[1]
        out[symbol] = df
    return out


def _lighter_metadata() -> pl.DataFrame | None:
    """The newest Task 2 snapshot, or None if Task 2 has not run."""
    base = dataset.root() / "markets" / "venue=lighter"
    parts = sorted(base.glob("date=*/part.parquet")) if base.exists() else []
    return pl.read_parquet(parts[-1]) if parts else None


def _lighter_listings(meta: pl.DataFrame | None) -> dict[str, datetime]:
    """Listing times, keyed by the symbol `_load` keys on."""
    if meta is None:
        return {}
    return {row["symbol"]: row["listed_at"] for row in meta.iter_rows(named=True)
            if row["symbol"] and row["listed_at"]}


def _lighter_tail_anchors(meta: pl.DataFrame | None, end: datetime) -> dict[str, datetime | None]:
    """`end` for markets still trading, None (= the market's own last row) for inactive ones.

    Lighter publishes no delisting timestamp, so an inactive market's last settled hour is the
    only exit date that exists. Measuring it against "now" would manufacture a coverage hole out
    of a market that simply ended -- 173 (SPACEX) settled 985 complete hours and stopped."""
    if meta is None:
        return {}
    return {row["symbol"]: (end if row["status"] == "active" else None)
            for row in meta.iter_rows(named=True) if row["symbol"]}


def _hl_listings(completeness: pl.DataFrame | None) -> dict[str, datetime]:
    """First appearance per HL coin, from the archive's own completeness record.

    HL publishes no listing date, but `asset_ctxs` names the coins present on every day, so the
    earliest date a coin appears is its listing to within a day. Free: the record is already a
    per-(date, coin) table. Without the archive this returns {} and the HL coverage denominators
    fall back to each coin's own first settled hour, marked `expected_start_source = data`."""
    if completeness is None or completeness.is_empty():
        return {}
    first = completeness.group_by("coin").agg(pl.col("date").min().alias("first_date"))
    return {row["coin"]: datetime.strptime(row["first_date"], "%Y-%m-%d")
            for row in first.iter_rows(named=True)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    ap.add_argument("--vendor-symbols", nargs="*", default=[],
                    help="symbols to cross-check against 0xArchive (needs OXARCHIVE_API_KEY)")
    args = ap.parse_args()

    end = last_complete_hour()
    arch = dataset.read_partition("hl_asset_ctxs_completeness", kind="daily")
    meta = _lighter_metadata()
    listings, tails = _lighter_listings(meta), _lighter_tail_anchors(meta, end)
    hl = _load("hl_funding", "coin")
    li = _load("lighter_funding", "market_id")
    candles = _load("lighter_candles", "market_id")
    mark = _load("lighter_mark_candles", "market_id")
    hl_cov = coverage(hl, time_col="settle_time", expected_starts=_hl_listings(arch),
                      expected_end=end)
    li_cov = coverage(li, time_col="settle_time", expected_starts=listings,
                      expected_ends=tails, expected_end=end)
    candle_cov = coverage(candles, time_col="time", expected_starts=listings,
                          expected_ends=tails, expected_end=end)
    mark_cov = coverage(mark, time_col="time", expected_starts=listings,
                        expected_ends=tails, expected_end=end)
    symbols = match_symbols(list(hl), list(li))
    xv = cross_venue(hl, li)
    lags = lag_scan(hl, li)
    verdict = alignment_verdict(lags)

    lines = ["# Phase 2b backfill coverage", "",
             f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M}Z; expected coverage runs to "
             f"{end} (the last complete hour).", ""]
    for name, cov, unit in (("Hyperliquid funding", hl_cov, "hours"),
                            ("Lighter funding", li_cov, "hours"),
                            ("Lighter candles", candle_cov, "bars"),
                            ("Lighter mark-price candles", mark_cov, "bars")):
        if cov.is_empty():
            lines += [f"## {name}", "", "**no data** — this dataset was not collected.", ""]
            continue
        lines += [f"## {name}", "",
                  f"- markets: {cov.height}",
                  f"- rows: {int(cov['rows'].sum())} {unit}",
                  f"- earliest: {cov['first'].min()}  latest: {cov['last'].max()}",
                  f"- markets with gaps: {int((cov['gaps'] > 0).sum())}",
                  f"- markets short at the head: {int((cov['missing_head_hours'] > 0).sum())}",
                  f"- markets short at the tail: {int((cov['missing_tail_hours'] > 0).sum())}",
                  f"- median coverage: {cov['coverage'].median():.4f}",
                  f"- coverage denominators from the venue's listing: "
                  f"{int((cov['expected_start_source'] == 'listing').sum())} of {cov.height}", "",
                  "Worst ten by coverage (nulls last):", "",
                  _md_table(worst_by_coverage(cov)), ""]

    if candle_cov.height and mark_cov.height:
        gaps = (candle_cov.select("key", pl.col("first").alias("candle_first"))
                .join(mark_cov.select("key", pl.col("first").alias("mark_first")),
                      on="key", how="inner")
                .with_columns((pl.col("mark_first") - pl.col("candle_first"))
                              .dt.total_hours().alias("mark_starts_later_hours"))
                .filter(pl.col("mark_starts_later_hours").abs() > 1)
                .sort("mark_starts_later_hours", descending=True))
        lines += ["## Mark-price candles vs trade candles: head gaps", "",
                  "The two price series do not necessarily start together. Measured on market "
                  "138 (AMD) while planning: trade candles from 2026-02-09 21:00Z, mark candles "
                  "from 2026-02-18 05:00Z — eight days apart. Any market below starts its two "
                  "series on different days, and a feature that joins them is silently short at "
                  "the head unless it says so.", "",
                  f"- markets whose series start more than an hour apart: {gaps.height}", "",
                  _md_table(gaps.head(20)), ""]

    if arch is None or arch.is_empty():
        lines += ["## Hyperliquid per-minute state (`hl_asset_ctxs`)", "",
                  "**not collected** — Task 7 was declined or has not run. Phase 3's "
                  "point-in-time universe and Phase 7's HL-side features have no source "
                  "without it.", ""]
    else:
        by_day = (arch.group_by("date")
                  .agg(pl.col("completeness").median().alias("median_completeness"),
                       pl.len().alias("coins")).sort("date"))
        lines += ["## Hyperliquid per-minute state (`hl_asset_ctxs`)", "",
                  f"- days measured: {by_day.height}",
                  f"- days below 0.99 median completeness: "
                  f"{int((by_day['median_completeness'] < 0.99).sum())}",
                  "- **2026-07-08 has no file in the archive at all** and never will; treat it "
                  "as missing, never as zero.", "",
                  "Worst ten days:", "",
                  _md_table(by_day.sort("median_completeness").head(10)), ""]

    lines += ["## Cross-venue symbol matching", "",
              f"- matched on symbol: {len(symbols['matched'])}",
              f"- Hyperliquid only: {len(symbols['hl_only'])}",
              f"- Lighter only: {len(symbols['lighter_only'])}", "",
              "Unmatched symbols are not necessarily absent from the other venue — Phase 1 found "
              "denomination prefixes differ (`kPEPE` vs `1000PEPE`). Phase 3 owns the alias "
              "table; this is the size of the problem.", "",
              f"- Hyperliquid only: {', '.join(symbols['hl_only'][:40])}",
              f"- Lighter only: {', '.join(symbols['lighter_only'][:40])}", ""]
    if not xv.is_empty():
        lines += ["## Cross-venue overlap (matched symbols)", "",
                  f"- pairs: {xv.height}",
                  f"- total overlapping hours: {int(xv['overlap_hours'].sum())}",
                  f"- median overlap per pair: {xv['overlap_hours'].median()}",
                  f"- median correlation: {xv['corr_signed_rate'].median()}", "",
                  _md_table(xv.sort("overlap_hours", descending=True).head(20)), ""]
    lines += ["## Settlement alignment (re-test of an inference)", "",
              "Phase 1 could only INFER that Hyperliquid's row stamped `T` closes the hour "
              "ending at `T`; Lighter's convention was verified directly. Target B is the "
              "lag-0 spread, so if another lag correlates better the whole target is an hour "
              "out of place.", "", _md_table(lags), "",
              f"**Verdict: {verdict}**", ""]

    if args.vendor_symbols:
        from fundr.sources.oxarchive import OXArchive
        ox = OXArchive()
        vendor_rows = []
        for symbol in args.vendor_symbols:
            if symbol in hl:
                vendor_rows.append(vendor_crosscheck(ox, "hyperliquid", symbol, hl[symbol]))
            if symbol in li:
                vendor_rows.append(vendor_crosscheck(ox, "lighter", symbol, li[symbol]))
        lines += ["## Independent cross-check (0xArchive free tier, last 30 days)", "",
                  "Handoff §8 action 6. Units already agree (P8): the vendor's HL rate is HL's "
                  "own fraction, its Lighter rate is Lighter's percent ÷ 100.", "",
                  "**Read the two venues differently (measured 2026-09-22).** The vendor's "
                  "LIGHTER series is settled hourly funding and matches ours to ~1e-20 -- a "
                  "genuine independent confirmation. Its HYPERLIQUID series is sampled every "
                  "60s and carries the *instantaneous* rate, not the settled one, so it is not "
                  "comparable to `hl_funding` row-for-row. The HL match counts below are a "
                  "FLOOR-CLAMP ARTIFACT, not partial agreement: measured on BTC, 366 of 506 "
                  "overlapping hours had the settled rate pinned at the floor 0.0000125, and "
                  "the instantaneous rate clamps to the same floor when the premium is near "
                  "zero, so those matches are a definitional coincidence. On the 140 hours "
                  "OFF the floor, agreement was 0 of 140, residual ~2.3e-5 -- the scale of "
                  "funding itself. So a non-match is not evidence about our data. HL's settled "
                  "rates were validated separately in Phase 1 (245/245 formula reproduction). "
                  "To use the vendor on HL, join it to `hl_asset_ctxs` per-minute rows instead.",
                  "",
                  _md_table(pl.DataFrame(vendor_rows)), "",
                  f"credits logged this run: {len(ox.calls)} calls", ""]

    out = Path(args.out or (dataset.root() / "qa" / "coverage_report.md"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines))
    print("\n".join(lines[:60]))
    print(f"\nreport written to {out}")
    if verdict.startswith("MISALIGNED"):
        print("\nSTOP: " + verdict)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
