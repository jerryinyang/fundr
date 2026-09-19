"""P1: what the Hyperliquid asset_ctxs archive really holds, and how often it changes within an hour."""
from datetime import UTC, datetime, timedelta

import polars as pl

from fundr import store
from fundr.analysis import gap_scan, hourly_profile
from fundr.sources.hl_archive import ARCHIVE_BUCKET, HLArchive, parse_time, read_csv_lz4

CANDIDATE_FIELDS = ["funding", "premium", "open_interest", "mark_px", "oracle_px", "mid_px",
                    "impact_bid_px", "impact_ask_px", "day_ntl_vlm"]

arc = HLArchive()
sample = [s["coin"] for s in store.load_json("p00", "sample.json")]
keys = sorted(k["key"] for k in arc.list_keys(ARCHIVE_BUCKET, "asset_ctxs/"))
store.save_json("p01", "listing.json", keys)
dates = {"old": keys[0], "mid": keys[len(keys) // 2], "recent": keys[-1]}
store.save_json("p01", "dates.json", dates)
latest_day = datetime.strptime(keys[-1].split("/")[-1][:8], "%Y%m%d").replace(tzinfo=UTC)
print(f"{len(keys)} files, first {keys[0]}, last {keys[-1]}, upload lag {(datetime.now(UTC) - latest_day).days} days")



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
