"""P7 (Lighter): which formula placement reproduces settled funding from the hour's premium?

Input is the P9 live recording (`data/phase1/p09/live.jsonl`, `source == "ws_market_stats"`),
not 0xArchive: the vendor has no premium field for Lighter (P8) and Lighter publishes no
historical premium series (P6). Two premium inputs are tried per hour, because the websocket's
`premium` field turns out to be a *running* average since the last settlement, not an
instantaneous per-minute sample:
  p_mean  - mean of the per-minute snapshots in the hour (the brief's assumption)
  p_last  - the last snapshot in the hour (= the venue's own full-hour average)
Settled truth is Lighter's public `/api/v1/fundings`, signed via `direction` (P5/P9).
"""
import json
from pathlib import Path

import polars as pl

from fundr import store
from fundr.analysis import attach_settled, match_stats, reported_tolerance
from fundr.sources.lighter_api import LighterAPI, fundings_frame

# Live market parameters (P6 orderBookDetails, ENA market 29). All in percent.
details = store.load_json("p06", "orderbookdetails.json")["body"]["order_book_details"][0]
MULT_RAW = float(details["funding_premium_multiplier"])  # API reports 100
SMALL = float(details["funding_clamp_small"])  # 0.05 (%)
BIG = float(details["funding_clamp_big"])  # 4.0 (%)
INTEREST = float(details["base_interest_rate"])  # 0.01 (% per 8h)
BASELINE = 0.0012  # INTEREST / 8 = 0.00125, reported truncated to 4 dp (P5)
DP = 4  # `/fundings.rate` is reported to 4 decimal places
MIN_ROWS = 55  # near-complete minute coverage for the p_mean input (of ~60 samples/hour)
MAX_LAG_S = 120  # last snapshot must sit within 2 min of the hour boundary for p_last

# The brief's three candidates plus the docs reading (part A): the multiplier scales BOTH the
# averaged premium and the small clamp. `docs_both_mult_m1` is what the docs say for crypto
# markets (multiplier 1); `docs_both_mult_m100` takes the API's `100` literally.
CANDIDATES = {
    "no_multiplier": lambda p: ((p + (INTEREST - p).clip(-SMALL, SMALL)) / 8).clip(-BIG, BIG),
    "premium_div_mult": lambda p: ((p / MULT_RAW + (INTEREST - p / MULT_RAW).clip(-SMALL, SMALL)) / 8).clip(-BIG, BIG),
    "premium_x_mult": lambda p: ((p * MULT_RAW + (INTEREST - p * MULT_RAW).clip(-SMALL, SMALL)) / 8).clip(-BIG, BIG),
    "docs_both_mult_m100": lambda p: (
        (p * MULT_RAW + (INTEREST - p * MULT_RAW).clip(-SMALL * MULT_RAW, SMALL * MULT_RAW)).clip(-BIG, BIG) / 8
    ),
    "docs_both_mult_m1": lambda p: ((p + (INTEREST - p).clip(-SMALL, SMALL)).clip(-BIG, BIG) / 8),
}
# `1e-9` absorbs binary-float error so a value that is mathematically on a 4-dp boundary
# (e.g. 0.0033) is not truncated down a digit by a 1e-18 representation error.
ROUNDINGS = {
    "raw": lambda e: e,
    "round4": lambda e: (e * 10**DP).round() / 10**DP,
    "trunc4": lambda e: (e * 10**DP + 1e-9).floor() / 10**DP,
}


def premium_frame() -> pl.DataFrame:
    rows = []
    for line in (store.probe_dir("p09") / "live.jsonl").read_text().splitlines():
        r = json.loads(line)
        if r.get("source") == "ws_market_stats":
            rows.append(
                {
                    "t_ms": r["t_ms"],
                    "market_id": int(r["market_id"]),
                    "premium": float(r["premium"]),
                    "cfr": float(r["current_funding_rate"]),
                }
            )
    return pl.DataFrame(rows).with_columns(
        pl.from_epoch("t_ms", time_unit="ms").dt.cast_time_unit("ms").alias("t")
    ).with_columns(pl.col("t").dt.truncate("1h").alias("hour"))


def settled_frame(market_ids: list[int], start_s: int, end_s: int) -> pl.DataFrame:
    """P5's snapshot stops before the P9 window, so top it up from the public endpoint (cached)."""
    cache = store.probe_dir("p07") / "fundings_window.parquet"
    if cache.exists():
        fresh = pl.read_parquet(cache)
    else:
        api = LighterAPI()
        fresh = pl.concat([fundings_frame(api.fundings(m, "1h", start_s, end_s, 750), m) for m in market_ids])
        fresh.write_parquet(cache)
    old = [
        pl.read_parquet(p) for p in (store.probe_dir("p05") / f"fundings_{m}.parquet" for m in market_ids) if p.exists()
    ]
    return (
        pl.concat([f.with_columns(pl.col("settle_time").dt.cast_time_unit("ms")) for f in [*old, fresh]])
        .with_columns(pl.col("market_id").cast(pl.Int64))
        .unique(subset=["market_id", "settle_time"], keep="last")
    )


px = premium_frame()
hours = (
    px.sort("t")
    .group_by(["market_id", "hour"])
    .agg(
        pl.len().alias("n_rows"),
        pl.col("premium").mean().alias("p_mean"),
        pl.col("premium").last().alias("p_last"),
        pl.col("t").last().alias("t_last"),
    )
    .with_columns(((pl.col("hour") + pl.duration(hours=1) - pl.col("t_last")).dt.total_seconds()).alias("lag_s"))
    .sort(["market_id", "hour"])
)
market_ids = sorted(px["market_id"].unique().to_list())
start_s = int(px["t_ms"].min() // 1000) - 7200
end_s = int(px["t_ms"].max() // 1000) + 7200
j = attach_settled(hours, settled_frame(market_ids, start_s, end_s).select(
    "market_id", "settle_time", "signed_rate", "rate_str"), "market_id").drop_nulls("signed_rate")
tol = reported_tolerance(j["rate_str"].to_list())

print(f"markets={market_ids} hours paired={j.height} tol={tol}")
print(f"coverage filters: p_mean needs n_rows >= {MIN_ROWS}; p_last needs lag_s <= {MAX_LAG_S}")
for input_col, sub in (("p_last", j.filter(pl.col("lag_s") <= MAX_LAG_S)), ("p_mean", j.filter(pl.col("n_rows") >= MIN_ROWS))):
    off = sub["signed_rate"] != BASELINE
    print(f"\n=== input {input_col}: {sub.height} hours, {int(off.sum())} off-baseline")
    for name, fn in CANDIDATES.items():
        for rname, rfn in ROUNDINGS.items():
            rebuilt = sub.select(rfn(fn(pl.col(input_col))).alias("r"))["r"]
            a = match_stats(rebuilt, sub["signed_rate"], tol)
            o = match_stats(rebuilt.filter(off), sub["signed_rate"].filter(off), tol)
            exact = int(((rebuilt - sub["signed_rate"]).abs() < 1e-9).filter(off).sum())
            print(
                f"  {name:20s} {rname:7s} all {a['n_match']:>3}/{a['n']:<3} ({a['rate']:.3f})"
                f"  off-baseline {o['n_match']:>3}/{o['n']:<3} ({o['rate']:.3f})"
                f"  exact-off {exact:>3}/{o['n']:<3}  mean_signed_err {a['mean_signed_error']:+.2e}"
            )

BEST, BEST_ROUND, BEST_INPUT = "no_multiplier", "trunc4", "p_last"
best = j.filter(pl.col("lag_s") <= MAX_LAG_S).with_columns(
    ROUNDINGS[BEST_ROUND](CANDIDATES[BEST](pl.col(BEST_INPUT))).alias("rebuilt")
)
print(f"\nbest = {BEST} / {BEST_ROUND} / {BEST_INPUT}; mismatching hours:")
print(best.filter((pl.col("rebuilt") - pl.col("signed_rate")).abs() > tol).select(
    "market_id", "hour", "n_rows", BEST_INPUT, "rebuilt", "signed_rate", "rate_str"))

# Step 4: does the same formula on the running in-hour premium reproduce `current_funding_rate`?
live = px.with_columns(
    ROUNDINGS[BEST_ROUND](CANDIDATES[BEST](pl.col("premium"))).alias("pred"),
    ((pl.col("t") - pl.col("hour")).dt.total_seconds()).alias("sec_into_hour"),
).with_columns((pl.col("pred") - pl.col("cfr")).abs().alias("abs_err"))
print("\nStep 4 - candidate(running premium) vs live current_funding_rate, per minute:")
for lo, hi in [(0, 300), (300, 900), (900, 1800), (1800, 3000), (3000, 3600)]:
    w = live.filter((pl.col("sec_into_hour") >= lo) & (pl.col("sec_into_hour") < hi))
    print(f"  {lo:>4}-{hi:<4}s into hour: n={w.height:>4} exact={int((w['abs_err'] < 1e-9).sum()):>4}"
          f" within-1-unit={int((w['abs_err'] <= tol * (1 + 1e-9)).sum()):>4}")
for label, w in (("all minutes", live), ("minutes >= 900s into hour", live.filter(pl.col("sec_into_hour") >= 900))):
    print(f"  {label}: n={w.height} exact={int((w['abs_err'] < 1e-9).sum())}"
          f" within-1-unit={int((w['abs_err'] <= tol * (1 + 1e-9)).sum())}")

cases = pl.concat([
    best.filter(pl.col("signed_rate") != BASELINE).head(15),
    best.filter(pl.col("signed_rate") == BASELINE).head(5),
])
Path("tests/fixtures/lighter_formula_cases.json").write_text(json.dumps([
    {"premium": r[BEST_INPUT], "settled": r["signed_rate"], "settled_str": r["rate_str"]} for r in cases.to_dicts()
], indent=1))
print(f"\nwrote tests/fixtures/lighter_formula_cases.json ({cases.height} rows,"
      f" {int((cases['signed_rate'] != BASELINE).sum())} off-baseline)")
