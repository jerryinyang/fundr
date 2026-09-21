"""Backfill Hyperliquid settled hourly funding for every market, from each market's listing.

Free, unauthenticated. Resumable: an existing partition is extended, not refetched.

Paging is done here rather than through `HLInfo.funding_history` so each page can be retried on
its own: that method pages internally, so a 429 on page 40 of BTC's ~29,000 hours would discard
39 pages and start the coin again."""
import argparse
import time

import polars as pl

from fundr import dataset, markets
from fundr.retry import with_retries
from fundr.sources.hl_api import HLInfo, funding_history_frame

DATASET = "hl_funding"
SOURCE = "hl:fundingHistory"
# Measured 2026-09-21: HL's own settled funding starts 2023-05-12T00:00:00.048Z for BTC and ETH
# (fundingHistory from startTime=0) -- eight days BEFORE the S3 archive's first day, 2023-05-20.
# Starting earlier than the true first hour costs one empty page; starting later would silently
# truncate the oldest markets.
DEFAULT_START_MS = 1_672_531_200_000  # 2023-01-01
PAGE_THROTTLE_S = 0.1                 # proactive, on top of the reactive backoff


def paged_funding_history(hl, coin: str, *, start_ms: int, end_ms: int,
                          sleep=time.sleep, throttle_s: float = PAGE_THROTTLE_S) -> list[dict]:
    rows: list[dict] = []
    cursor = start_ms
    while cursor < end_ms:
        page = with_retries(
            lambda c=cursor: hl.post({"type": "fundingHistory", "coin": coin,
                                      "startTime": c, "endTime": end_ms}), sleep=sleep)
        if not page:
            break
        rows += page
        cursor = page[-1]["time"] + 1
        sleep(throttle_s)
    return rows


def backfill_coin(hl, coin: str, *, start_ms: int, end_ms: int, sleep=time.sleep) -> pl.DataFrame:
    rows = paged_funding_history(hl, coin, start_ms=start_ms, end_ms=end_ms, sleep=sleep)
    if not rows:
        return pl.DataFrame()
    df = funding_history_frame(rows).unique(subset=["time"], keep="first").sort("time")
    # HL's fundingRate is already a per-hour signed fraction (Phase 1, P4): positive = longs pay.
    return df.with_columns(pl.col("funding_rate").alias("signed_rate_fraction"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start-ms", type=int, default=DEFAULT_START_MS)
    ap.add_argument("--coins", nargs="*", default=None, help="default: every market")
    args = ap.parse_args()

    hl = HLInfo()
    end_ms = int(time.time() * 1000)
    coins = args.coins or markets.discover_hl_markets(hl)
    total = 0
    for i, coin in enumerate(coins, 1):
        existing = dataset.read_partition(DATASET, coin=coin)
        cursor = dataset.resume_cursor(existing, "time")
        start = args.start_ms if cursor is None else cursor + 1
        fresh = backfill_coin(hl, coin, start_ms=start, end_ms=end_ms)
        if fresh.is_empty() and existing is None:
            print(f"[{i}/{len(coins)}] {coin}: no history")
            continue
        merged = dataset.merge_partition(existing, fresh, key="settle_time", source=SOURCE)
        dataset.write_partition(DATASET, merged, source=SOURCE, coin=coin)
        total += merged.height
        print(f"[{i}/{len(coins)}] {coin}: {merged.height} hours (+{fresh.height} new) "
              f"({merged['settle_time'].min()} → {merged['settle_time'].max()})")
    dataset.update_manifest(DATASET, {"source": SOURCE, "markets": len(coins), "rows": total,
                                      "fetched_at_ms": int(time.time() * 1000)})
    print(f"\n{DATASET}: {total} rows across {len(coins)} markets")


if __name__ == "__main__":
    main()
