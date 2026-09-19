"""P1: what the Hyperliquid asset_ctxs archive really holds, and how often it changes within an hour."""
from datetime import UTC, datetime, timedelta

import polars as pl

from fundr import store
from fundr.analysis import gap_scan, hourly_profile
from fundr.sources.hl_api import HLInfo
from fundr.sources.hl_archive import ARCHIVE_BUCKET, HLArchive, parse_time, read_csv_lz4

CANDIDATE_FIELDS = ["funding", "premium", "open_interest", "mark_px", "oracle_px", "mid_px",
                    "impact_bid_px", "impact_ask_px", "day_ntl_vlm"]


def key_day(key: str) -> str:
    return key.split("/")[-1][:8]


def day_dt(day: str) -> datetime:
    return datetime.strptime(day, "%Y%m%d").replace(tzinfo=UTC)


arc = HLArchive()
sample = [s["coin"] for s in store.load_json("p00", "sample.json")]
keys = sorted(k["key"] for k in arc.list_keys(ARCHIVE_BUCKET, "asset_ctxs/"))
store.save_json("p01", "listing.json", keys)
by_day = {key_day(k): k for k in keys}
sorted_days = sorted(by_day)
latest_day = day_dt(key_day(keys[-1]))
print(f"{len(keys)} files, first {keys[0]}, last {keys[-1]}, upload lag {(datetime.now(UTC) - latest_day).days} days")

# Controller ruling (Task 9 follow-up): keep the fixed 7-coin sample everywhere (no P0 swap).
# Instead pick "old" as the first archive date on which every sample coin except PONS has
# listing history, so the intra-hour analysis below actually has non-BTC coins to look at.
info = HLInfo()
now_ms = int(datetime.now(UTC).timestamp() * 1000)
listing_dates = {}
for coin in sample:
    if coin == "PONS":
        continue
    candles = info.candle_snapshot(coin, "1d", 0, now_ms)
    listing_dates[coin] = datetime.fromtimestamp(candles[0]["t"] / 1000, tz=UTC)
print("HL listing dates (excl. PONS):", {c: d.date().isoformat() for c, d in listing_dates.items()})

target_old_day = (max(listing_dates.values()) + timedelta(days=1)).strftime("%Y%m%d")
old_day = next((d for d in sorted_days if d >= target_old_day), sorted_days[-1])
old_key = by_day[old_day]
recent_key = keys[-1]
recent_day = key_day(recent_key)

mid_target = day_dt(old_day) + (day_dt(recent_day) - day_dt(old_day)) / 2
mid_day = min(sorted_days, key=lambda d: abs((day_dt(d) - mid_target).total_seconds()))
mid_key = by_day[mid_day]

dates = {"old": old_key, "mid": mid_key, "recent": recent_key}
store.save_json("p01", "dates.json", {**dates, "archive_start": keys[0]})
print(f"chosen dates: old={old_key} mid={mid_key} recent={recent_key} (archive_start={keys[0]}, not re-analysed)")

for label, key in dates.items():
    raw = read_csv_lz4(arc.download(ARCHIVE_BUCKET, key))
    day = key.split("/")[-1][:8]
    print(f"\n=== {label} {key}: {raw.height} rows, {raw['coin'].n_unique()} coins")
    print(raw.schema)
    print(raw.head(5))
    df = parse_time(raw).filter(pl.col("coin").is_in(sample))
    missing = sorted(set(sample) - set(df["coin"].unique()))
    print(f"sample coins missing on this date: {missing}")
    fields = [f for f in CANDIDATE_FIELDS if f in df.columns]
    prof = hourly_profile(df, "time", "coin", fields)
    df.write_parquet(store.probe_dir("p01") / f"asset_ctxs_{day}.parquet")
    prof.write_parquet(store.probe_dir("p01") / f"profile_{day}.parquet")
    interval = df.sort(["coin", "time"]).group_by("coin").agg(pl.col("time").diff().median().alias("median_interval"))
    print(interval)
    print("rows per hour (min/median/max):", prof["n_rows"].min(), prof["n_rows"].median(), prof["n_rows"].max())
    for f in fields:
        share = (prof[f"{f}_n_distinct"] > 1).mean()
        print(f"share of coin-hours where {f} changes within the hour: {share:.3f}")
    print("gaps > 5 min:", gap_scan(df, "time", "coin", timedelta(minutes=5)).height)
    print("null counts:", df.null_count())
