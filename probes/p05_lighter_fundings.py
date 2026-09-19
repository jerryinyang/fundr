"""P5: Lighter settled funding history — coverage, resolutions, paging, units, gaps."""
import time
from datetime import timedelta

import httpx
import polars as pl

from fundr import store
from fundr.analysis import gap_scan, reported_tolerance
from fundr.sources.lighter_api import LighterAPI, fundings_frame

api = LighterAPI()
sample = store.load_json("p00", "sample.json")
created = {b["market_id"]: int(b["created_at"]) // 1000 for b in api.order_books()}
now = int(time.time())

for s in sample:
    m = s["lighter_market_id"]
    rows = api.fundings_all(m, "1h", created[m], now)
    df = fundings_frame(rows, m)
    df.write_parquet(store.probe_dir("p05") / f"fundings_{m}.parquet")
    expected = (now - created[m]) // 3600
    gaps = gap_scan(df.with_columns(pl.lit(s["coin"]).alias("coin")), "settle_time", "coin", timedelta(hours=1))
    print(f"{s['coin']} (market {m}): {df.height} rows vs ~{expected} hours since listing; "
          f"first {df['settle_time'].min()}; gaps>1h {gaps.height}; "
          f"directions {df['direction'].value_counts().to_dicts()}; "
          f"tolerance {reported_tolerance(df['rate_str'].to_list()):g}")
    print(df.head(3))


def attempt(label, **kw):
    try:
        rows = api.fundings(**kw)
        ts = [r["timestamp"] for r in rows]
        print(f"{label}: {len(rows)} rows, first {min(ts) if ts else None}, last {max(ts) if ts else None}")
        return rows
    except (RuntimeError, httpx.HTTPStatusError) as e:
        print(f"{label}: error {e}")


day = 86400
attempt("1d, last 30 days", market_id=1, resolution="1d", start_s=now - 30 * day, end_s=now, count_back=30)
attempt("count_back=5 over 10 days (earliest or latest 5?)", market_id=1, resolution="1h",
        start_s=now - 10 * day, end_s=now, count_back=5)
attempt("count_back=1000 over 40 days (cap?)", market_id=1, resolution="1h",
        start_s=now - 40 * day, end_s=now, count_back=1000)
for res in ("1m", "5m", "15m"):
    attempt(f"resolution {res} (any sub-hour state?)", market_id=1, resolution=res,
            start_s=now - 3600 * 3, end_s=now, count_back=100)
print("All calls above were made without any auth header.")
