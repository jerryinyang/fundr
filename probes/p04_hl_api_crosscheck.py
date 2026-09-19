"""P4: is archived `funding` the settled rate, a running estimate, or something else?
Also: predictedFundings shape, candle lookback limit, funding units and precision."""
import time
from datetime import datetime, timedelta

import polars as pl

from fundr import store
from fundr.analysis import attach_settled, match_stats, reported_tolerance
from fundr.sources.hl_api import HLInfo, funding_history_frame

hl = HLInfo()
sample = [s["coin"] for s in store.load_json("p00", "sample.json")]
dates = store.load_json("p01", "dates.json")

# Controller ruling: loop only over old, mid, recent — archive_start (2023-05-20) is
# coverage history from P1 only, not re-analysed here.
dates = {k: v for k, v in dates.items() if k != "archive_start"}

for label, key in dates.items():
    day = key.split("/")[-1][:8]
    start = datetime.strptime(day, "%Y%m%d")
    start_ms = int((start - datetime(1970, 1, 1)).total_seconds() * 1000)
    end_ms = start_ms + int(timedelta(hours=26).total_seconds() * 1000)
    rows = [r for c in sample for r in hl.funding_history(c, start_ms, end_ms)]
    settled = funding_history_frame(rows)
    settled.write_parquet(store.probe_dir("p04") / f"settled_{day}.parquet")
    tol = reported_tolerance(settled["funding_rate_str"].to_list())
    prof = pl.read_parquet(store.probe_dir("p01") / f"profile_{day}.parquet")
    s = settled.select("coin", "settle_time", pl.col("funding_rate").alias("settled"))
    print(f"\n=== {label} {day}: {settled.height} settled rows, tolerance {tol:g}")
    # Alignment A: last archived value in hour H vs the settlement that closes H (H+1h).
    a = attach_settled(prof, s, "coin").drop_nulls("settled")
    print("last-in-hour vs closing settlement:", match_stats(a["funding_last"], a["settled"], tol))
    # Alignment B: archived value in hour H vs the settlement at the start of H (archive lags settled).
    b = prof.join(s.rename({"settle_time": "hour"}), on=["coin", "hour"], how="inner")
    print("last-in-hour vs opening settlement:", match_stats(b["funding_last"], b["settled"], tol))
    print("settled premium sample:", settled.select("coin", "time", "premium").head(3))

store.save_json("p04", "predicted_fundings.json", hl.predicted_fundings())

now = int(time.time() * 1000)
day_ms = 86_400_000
recent = hl.candle_snapshot("BTC", "1m", now - 5 * day_ms, now)
old = hl.candle_snapshot("BTC", "1m", now - 30 * day_ms, now - 29 * day_ms)
store.save_json("p04", "candles_recent_1m.json", recent)
store.save_json("p04", "candles_old_1m.json", old)
print(f"\ncandles 1m last 5 days: {len(recent)} (5000 cap => ~3.5 days); 30 days ago window: {len(old)}")
