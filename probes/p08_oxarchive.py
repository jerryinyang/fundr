"""P8: what 0xArchive holds for our sample markets, at what cadence, and at what credit cost.
Free tier: data calls must stay inside the last 30 days; coverage routes give full-history dates."""
import json

import polars as pl

from fundr import store
from fundr.analysis import hourly_profile
from fundr.sources.oxarchive import OXArchive

ox = OXArchive()
sample = store.load_json("p00", "sample.json")
live = [json.loads(x) for x in (store.probe_dir("p09") / "live.jsonl").read_text().splitlines()]
t0, t1 = min(r["t_ms"] for r in live), max(r["t_ms"] for r in live)


def save(name, obj):
    store.save_json("p08", name, obj)


save("symbols.json", ox.get("/v1/symbols"))
save("lighter_instruments.json", ox.get("/v1/lighter/instruments"))

for s in sample:
    for venue in ("lighter", "hyperliquid"):
        try:
            cov = ox.get(f"/v1/data-quality/coverage/{venue}/{s['coin']}")
        except Exception as e:  # record route/plan refusals as findings, keep going
            cov = {"error": repr(e)}
        save(f"coverage_{venue}_{s['coin']}.json", cov)
        for dtype, info in (cov.get("data_types") or {}).items():
            print(f"{venue} {s['coin']} {dtype}: {info.get('earliest')} → {info.get('latest')}, "
                  f"cadence {info.get('cadence')}, completeness {info.get('completeness')}, gaps {len(info.get('gaps') or [])}")


def funding_raw(venue: str, coin: str) -> pl.DataFrame:
    rows = ox.get_all(f"/v1/{venue}/funding/{coin}", max_pages=30, start=t0, end=t1, limit=1000)
    df = pl.DataFrame(rows)
    # DEVIATION from brief: the Lighter funding route does not return a "premium" field
    # (only coin, symbol, timestamp, funding_rate); Hyperliquid's does. Fill with null
    # rather than erroring, so both venues share a schema for pl.concat below.
    if "premium" not in df.columns:
        df = df.with_columns(pl.lit(None, dtype=pl.Float64).alias("premium"))
    # DEVIATION from brief: HL timestamps sometimes lack fractional seconds
    # ("...T02:50:04Z" vs "...T12:01:43.945Z"); an explicit format is needed or
    # polars' auto-inference errors on the mixed precision.
    df = df.with_columns(
        pl.col("timestamp").str.to_datetime(format="%Y-%m-%dT%H:%M:%S%.fZ", time_unit="ms")
        .dt.replace_time_zone(None).alias("time"),  # naive UTC
        pl.col("funding_rate").cast(pl.Float64),
        pl.col("premium").cast(pl.Float64),
        pl.lit(coin).alias("coin"),
    )
    return df


# Raw funding over the P9 window: does it change within the hour, and does it equal what P9 saw live?
probe_coins = [sample[0]["coin"], "BTC"]
for venue in ("lighter", "hyperliquid"):
    df = pl.concat([funding_raw(venue, c) for c in probe_coins])
    df.write_parquet(store.probe_dir("p08") / f"{'hl' if venue == 'hyperliquid' else venue}_funding_raw.parquet")
    prof = hourly_profile(df, "time", "coin", ["funding_rate", "premium"])
    print(f"\n{venue} raw funding rows {df.height}; median spacing {df.sort('time')['time'].diff().median()}")
    print(f"{venue} share of hours where funding_rate moves:", (prof["funding_rate_n_distinct"] > 1).mean())

save("lighter_oi_page.json", ox.get(f"/v1/lighter/openinterest/{sample[0]['coin']}", start=t1 - 86_400_000, end=t1, limit=100))
save("hl_oi_page.json", ox.get(f"/v1/hyperliquid/openinterest/{sample[0]['coin']}", start=t1 - 86_400_000, end=t1, limit=100))
save("lighter_trades_page.json", ox.get(f"/v1/lighter/trades/{sample[0]['coin']}", start=t1 - 3_600_000, end=t1, limit=100))
save("hl_trades_page.json", ox.get(f"/v1/hyperliquid/trades/{sample[0]['coin']}", start=t1 - 3_600_000, end=t1, limit=100))
save("lighter_l3_current.json", ox.get(f"/v1/lighter/l3orderbook/{sample[0]['coin']}"))

save("calls.json", ox.calls)
print(f"\n{len(ox.calls)} calls; credit headers on last call: {ox.calls[-1]['headers']}")
