"""Backfill Lighter settled hourly funding for every perp market, from its listing.

Free, no auth. Lighter reports percent per hour with an unsigned rate plus a direction field
(Phase 1, P5); `signed_rate_fraction` converts to Hyperliquid's per-hour-fraction basis.

Paged here rather than through `LighterAPI.fundings_all` so each 700-hour window is retried on
its own instead of restarting a 14,000-hour market on one 429."""
import argparse
import time

import polars as pl

from fundr import dataset, markets
from fundr.retry import with_retries
from fundr.sources.lighter_api import LighterAPI, fundings_frame

DATASET = "lighter_funding"
SOURCE = "lighter:/api/v1/fundings"
RESOLUTION = "1h"
WINDOW_S = 700 * 3600          # the documented 750-row cap, with headroom
PAGE_THROTTLE_S = 0.05
EXPECTED_PERIOD_S = 3600


def paged_fundings(api, market_id: int, *, start_s: int, end_s: int, sleep=time.sleep,
                   throttle_s: float = PAGE_THROTTLE_S) -> list[dict]:
    seen: dict[int, dict] = {}
    t = start_s
    while t < end_s:
        upper = min(t + WINDOW_S, end_s)
        if upper - t < EXPECTED_PERIOD_S:
            # Under one funding period since the last settlement: nothing could have settled
            # yet. Lighter answers a sub-period window with a permanent 400 (measured
            # 2026-09-21: {"code":22400,"message":"invalid timestamps: end_timestamp must be
            # greater than start_timestamp"}), so this is skipped rather than requested.
            break
        rows = with_retries(
            lambda a=t, b=upper: api.fundings(market_id, RESOLUTION, a, b, 750), sleep=sleep)
        for row in rows:
            seen[row["timestamp"]] = row
        t = upper
        sleep(throttle_s)
    return [seen[k] for k in sorted(seen)]


def backfill_market(api, market: dict, *, end_s: int, start_s: int | None = None,
                    sleep=time.sleep) -> pl.DataFrame:
    begin = start_s if start_s is not None else int(market["created_at"]) // 1000
    rows = paged_fundings(api, market["market_id"], start_s=begin, end_s=end_s, sleep=sleep)
    if not rows:
        return pl.DataFrame()
    df = fundings_frame(rows, market["market_id"]).sort("timestamp")
    return df.with_columns(
        pl.lit(market.get("symbol")).alias("symbol"),
        # Lighter reports percent per hour; /100 puts it on HL's fraction basis (P5/P7).
        (pl.col("signed_rate") / 100).alias("signed_rate_fraction"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market-ids", nargs="*", type=int, default=None)
    args = ap.parse_args()

    api = LighterAPI()
    end_s = int(time.time())
    market_list = markets.discover_lighter_markets(api)
    if args.market_ids:
        market_list = [m for m in market_list if m["market_id"] in set(args.market_ids)]
    total, empty, odd_period = 0, [], []
    for i, m in enumerate(market_list, 1):
        mid = m["market_id"]
        existing = dataset.read_partition(DATASET, market_id=mid)
        cursor = dataset.resume_cursor(existing, "timestamp")
        fresh = backfill_market(api, m, end_s=end_s,
                                start_s=None if cursor is None else cursor + 1)
        if fresh.is_empty() and existing is None:
            empty.append((mid, m.get("symbol")))
            print(f"[{i}/{len(market_list)}] {m['symbol']} ({mid}): no history")
            continue
        merged = dataset.merge_partition(existing, fresh, key="timestamp", source=SOURCE)
        dataset.write_partition(DATASET, merged, source=SOURCE, market_id=mid)
        total += merged.height
        # Phase 1: the funding period is a per-market CONFIGURATION. Assert it, do not assume it.
        period = markets.funding_period_s(merged["timestamp"].to_list())
        if period not in (None, EXPECTED_PERIOD_S):
            odd_period.append((mid, m.get("symbol"), period))
        print(f"[{i}/{len(market_list)}] {m['symbol']} ({mid}): {merged.height} hours "
              f"(+{fresh.height} new, period {period}s) "
              f"({merged['settle_time'].min()} → {merged['settle_time'].max()})")
    dataset.record_run(DATASET, {"source": SOURCE, "markets": len(market_list),
                                 "rows": total, "markets_without_history": empty,
                                 "markets_off_hourly_period": odd_period,
                                 "fetched_at_ms": int(time.time() * 1000)},
                       partial=bool(args.market_ids))
    print(f"\n{DATASET}: {total} rows across {len(market_list)} markets; "
          f"{len(empty)} with no history; {len(odd_period)} not on a 1h period")
    if odd_period:
        print("NOT HOURLY — every phase that assumes an hourly grid must special-case these:")
        for mid, symbol, period in odd_period:
            print(f"  {symbol} ({mid}): {period}s")


if __name__ == "__main__":
    main()
