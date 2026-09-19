"""P9 analysis: does each venue's current funding move within the hour, and does its
last in-hour value equal the rate that then settles?"""
import json

import polars as pl

from fundr import store
from fundr.analysis import attach_settled, epoch_ms, hourly_profile, match_stats, reported_tolerance
from fundr.sources.hl_api import HLInfo, funding_history_frame
from fundr.sources.lighter_api import LighterAPI, fundings_frame

lines = [json.loads(x) for x in (store.probe_dir("p09") / "live.jsonl").read_text().splitlines()]
by_source: dict[str, list] = {}
for rec in lines:
    by_source.setdefault(rec["source"], []).append(rec)
print({k: len(v) for k, v in by_source.items()})
t0, t1 = min(r["t_ms"] for r in lines), max(r["t_ms"] for r in lines)

# --- Hyperliquid ---
hl = pl.DataFrame(by_source["metaAndAssetCtxs"]).with_columns(epoch_ms("t_ms").alias("time"))
hl_prof = hourly_profile(hl, "time", "coin", ["funding", "premium", "open_interest"])
hl_prof.write_parquet(store.probe_dir("p09") / "hl_profile.parquet")
api = HLInfo()
settled = funding_history_frame([r for c in hl["coin"].unique() for r in api.funding_history(c, t0 - 3_600_000, t1 + 7_200_000)])
tol = reported_tolerance(settled["funding_rate_str"].to_list())
s = settled.select("coin", "settle_time", pl.col("funding_rate").alias("settled"))
full = hl_prof.filter(pl.col("n_rows") >= 50)  # only hours fully observed
print("\nHL share of hours where funding moves within the hour:", (full["funding_n_distinct"] > 1).mean())
a = attach_settled(full, s, "coin").drop_nulls("settled")
print("HL last-in-hour vs closing settlement:", match_stats(a["funding_last"], a["settled"], tol))
b = full.join(s.rename({"settle_time": "hour"}), on=["coin", "hour"], how="inner")
print("HL last-in-hour vs opening settlement:", match_stats(b["funding_last"], b["settled"], tol))
pred = [(r["coin"], v[1]["fundingRate"]) for r in by_source["predictedFundings"] for v in r["venues"] if v[0] == "HlPerp"]
print("predictedFundings HlPerp sample:", pred[:5])

# --- Lighter ---
li = pl.DataFrame(by_source["ws_market_stats"]).with_columns(
    epoch_ms("t_ms").alias("time"),
    *[pl.col(c).cast(pl.Float64) for c in ("current_funding_rate", "funding_rate", "premium")],
)
li_prof = hourly_profile(li, "time", "market_id", ["current_funding_rate", "funding_rate", "premium", "funding_timestamp"])
li_prof.write_parquet(store.probe_dir("p09") / "lighter_profile.parquet")
print("\nLighter max distinct current_funding_rate values inside one minute:",
      li.select(pl.col("cfr_values").list.len().max()).item())
lapi = LighterAPI()
lset = pl.concat([
    fundings_frame(lapi.fundings(m, "1h", t0 // 1000 - 3600, t1 // 1000 + 7200, 20), m)
    for m in li["market_id"].unique()
])
ltol = reported_tolerance(lset["rate_str"].to_list())
lfull = li_prof.filter(pl.col("n_rows") >= 50)
print("Lighter share of hours where current_funding_rate moves:", (lfull["current_funding_rate_n_distinct"] > 1).mean())
for col in ("signed_rate", "rate"):
    ls = lset.select("market_id", "settle_time", pl.col(col).alias("settled"))
    a = attach_settled(lfull, ls, "market_id").drop_nulls("settled")
    print(f"Lighter current_funding_rate last-in-hour vs closing settlement ({col}):",
          match_stats(a["current_funding_rate_last"], a["settled"], ltol))
    b = lfull.join(ls.rename({"settle_time": "hour"}), on=["market_id", "hour"], how="inner")
    print(f"Lighter funding_rate field vs opening settlement ({col}):",
          match_stats(b["funding_rate_last"], b["settled"], ltol))
neg = li.filter(pl.col("current_funding_rate") < 0)
print("minutes with negative current_funding_rate:", neg.height, "(sign check: compare with /fundings direction)")
print(lset.select("market_id", "settle_time", "rate", "direction").tail(10))

fr = [row for r in by_source["funding-rates"] for row in r["rows"]]
print("\n/funding-rates exchanges seen:", sorted({row["exchange"] for row in fr}))
print("recorder fields available live:",
      {src: sorted(by_source[src][0].keys()) for src in ("metaAndAssetCtxs", "ws_market_stats", "orderBookDetails")})
print("poll errors:", len(by_source.get("poll_error", [])), "ws reconnects:", len(by_source.get("ws_reconnect", [])))
