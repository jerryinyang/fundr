"""Backfill Lighter 1h price and volume per market, plus the native mark-price candles.

Free, no auth. Lighter's `v`/`V` are true per-bucket base and quote volume -- unlike
Hyperliquid's running daily total, which must be differenced. Phase 1 confirmed >= 1 year of 1h
history. `markPriceCandles` is the venue's own mark series (measured 2026-09-21: t,o,h,l,c,sc,
no volume); Lighter's INDEX price still has no historical source at all and stays recorder-only.

Discovery is imported from `fundr.markets`, not from a sibling script: `python scripts/x.py`
puts `scripts/` on sys.path, not the repo root, so a `from scripts.y import ...` here would
raise ModuleNotFoundError at run time even though pytest imports it fine."""
import argparse
import time

import polars as pl

from fundr import dataset, markets
from fundr.analysis import epoch_ms
from fundr.retry import with_retries
from fundr.sources.lighter_api import LighterAPI

DATASET = "lighter_candles"
MARK_DATASET = "lighter_mark_candles"
SOURCE = "lighter:/api/v1/candles"
MARK_SOURCE = "lighter:/api/v1/markPriceCandles"
WINDOW_S = 500 * 3600          # the endpoint's per-call row cap, measured 2026-09-22
HOUR_MS = 3_600_000
MAX_EMPTY_WINDOWS = 24         # ~2 years of silence before giving up on a market
PAGE_THROTTLE_S = 0.05


def candles_frame(rows: list[dict], *, market_id: int, symbol: str | None) -> pl.DataFrame:
    if not rows:
        return pl.DataFrame()
    df = pl.DataFrame(rows, schema={"t": pl.Int64, "o": pl.Float64, "h": pl.Float64,
                                    "l": pl.Float64, "c": pl.Float64, "v": pl.Float64,
                                    "V": pl.Float64})
    return df.select(
        pl.lit(market_id).alias("market_id"),
        pl.lit(symbol).alias("symbol"),
        epoch_ms("t").alias("time"),
        pl.col("o").alias("open"), pl.col("h").alias("high"),
        pl.col("l").alias("low"), pl.col("c").alias("close"),
        pl.col("v").alias("base_volume"), pl.col("V").alias("quote_volume"))


def mark_candles_frame(rows: list[dict], *, market_id: int, symbol: str | None) -> pl.DataFrame:
    if not rows:
        return pl.DataFrame()
    df = pl.DataFrame(rows, schema={"t": pl.Int64, "o": pl.Float64, "h": pl.Float64,
                                    "l": pl.Float64, "c": pl.Float64, "sc": pl.Int64})
    return df.select(
        pl.lit(market_id).alias("market_id"),
        pl.lit(symbol).alias("symbol"),
        epoch_ms("t").alias("time"),
        pl.col("o").alias("open"), pl.col("h").alias("high"),
        pl.col("l").alias("low"), pl.col("c").alias("close"),
        "sc")  # undocumented counter, kept raw as received


def _page(api, market_id: int, start_s: int, end_s: int, *, mark: bool, sleep) -> list[dict]:
    if mark:
        return with_retries(
            lambda: api.get("/api/v1/markPriceCandles", market_id=market_id, resolution="1h",
                            start_timestamp=start_s, end_timestamp=end_s,
                            count_back=700)["c"], sleep=sleep)
    return with_retries(lambda: api.candles(market_id, "1h", start_s, end_s, 700), sleep=sleep)


def backfill_candles(api, market: dict, *, end_s: int, start_s: int | None = None,
                     mark: bool = False, max_empty_windows: int = MAX_EMPTY_WINDOWS,
                     sleep=time.sleep) -> pl.DataFrame:
    """Page forward to `end_s`, tolerating silent windows.

    Candles exist only where trades happened, so a quiet first window says nothing about the
    rest of a market's life; stopping on the first empty page would drop the market. Only
    `max_empty_windows` CONSECUTIVE empties end the scan.

    Resume from the LAST BAR ACTUALLY RETURNED, never from the window edge. The endpoint caps
    every response at 500 rows however wide the window (measured 2026-09-22: 700h, 1000h and
    500h requests all return exactly 500), so advancing by the window silently abandons whatever
    the cap truncated. That bug cost 297,185 trade bars and 337,412 mark bars, in blocks of
    exactly 200 hours -- 700 asked for, 500 returned, 200 skipped, every page, permanently."""
    begin = start_s if start_s is not None else int(market["created_at"]) // 1000
    frames, empties = [], 0
    # Every cursor stays on an hour boundary. The endpoint rejects a window shorter than one
    # resolution bucket with a 400, so resuming at `last_bar + 1s` eventually asks for a
    # sub-hour span and kills the run (market 19, a 3,372s window, measured 2026-09-22).
    t = begin - begin % 3600
    while end_s - t >= 3600 and empties < max_empty_windows:
        upper = min(t + WINDOW_S, end_s)
        rows = _page(api, market["market_id"], t, upper, mark=mark, sleep=sleep)
        if rows:
            builder = mark_candles_frame if mark else candles_frame
            frame = builder(rows, market_id=market["market_id"], symbol=market.get("symbol"))
            frames.append(frame)
            empties = 0
            # Next expected bar after the last one actually returned. `max` guarantees progress
            # even if a response's last bar is the one we started from.
            last_s = int(frame["time"].dt.epoch("ms").max()) // 1000
            t = max(t + 3600, last_s + 3600)
        else:
            empties += 1
            t = upper
        sleep(PAGE_THROTTLE_S)
    if not frames:
        return pl.DataFrame()
    # The bucket containing `end_s` is the hour still in progress: a partial bar the merge would
    # then keep forever, because existing rows win on a key collision.
    open_bucket_ms = (end_s * 1000 // HOUR_MS) * HOUR_MS
    return (pl.concat(frames)
            .filter(pl.col("time").dt.epoch("ms") < open_bucket_ms)
            .unique(subset=["time"], keep="last").sort("time"))


def _run(api, market_list, *, name: str, source: str, mark: bool, end_s: int,
         partial: bool, refetch: bool = False) -> int:
    """`refetch` re-pages each market's whole history instead of resuming from its last bar.

    The resume cursor is the max timestamp on disk, so it can only ever extend the tail --
    a hole in the MIDDLE of a series is invisible to it and survives every future run. Any
    market whose history was collected before the paging fix has such holes, so filling them
    needs a full re-page. This is safe to repeat: `merge_partition` keeps existing rows on a
    key collision, so re-fetched bars never overwrite the provenance already on disk."""
    total, empty = 0, []
    for i, m in enumerate(market_list, 1):
        mid = m["market_id"]
        existing = dataset.read_partition(name, market_id=mid)
        cursor = None if refetch else dataset.resume_cursor(existing, "time")
        fresh = backfill_candles(api, m, end_s=end_s, mark=mark,
                                 start_s=None if cursor is None else cursor // 1000 + 1)
        if fresh.is_empty() and existing is None:
            empty.append((mid, m.get("symbol")))
            print(f"[{i}/{len(market_list)}] {m['symbol']} ({mid}): no {name}")
            continue
        merged = dataset.merge_partition(existing, fresh, key="time", source=source)
        dataset.write_partition(name, merged, source=source, market_id=mid)
        total += merged.height
        print(f"[{i}/{len(market_list)}] {m['symbol']} ({mid}): {merged.height} bars "
              f"(+{fresh.height} fetched) ({merged['time'].min()} -> {merged['time'].max()})")
    dataset.record_run(name, {"source": source, "markets": len(market_list),
                              "rows": total, "markets_without_history": empty,
                              "fetched_at_ms": int(time.time() * 1000)},
                       partial=partial)
    print(f"\n{name}: {total} rows across {len(market_list)} markets; "
          f"{len(empty)} with no history")
    return total


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market-ids", nargs="*", type=int, default=None)
    ap.add_argument("--skip-mark", action="store_true")
    ap.add_argument("--refetch", action="store_true",
                    help="re-page full history instead of resuming from the last bar; needed "
                         "once to fill the holes left by the pre-2026-09-22 paging bug")
    args = ap.parse_args()

    api = LighterAPI()
    end_s = int(time.time())
    market_list = markets.discover_lighter_markets(api)
    if args.market_ids:
        market_list = [m for m in market_list if m["market_id"] in set(args.market_ids)]
    partial = bool(args.market_ids)
    _run(api, market_list, name=DATASET, source=SOURCE, mark=False, end_s=end_s,
         partial=partial, refetch=args.refetch)
    if not args.skip_mark:
        _run(api, market_list, name=MARK_DATASET, source=MARK_SOURCE, mark=True, end_s=end_s,
            partial=partial, refetch=args.refetch)


if __name__ == "__main__":
    main()
