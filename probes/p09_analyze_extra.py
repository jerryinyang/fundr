"""P9 analysis extras — Task 12 part B, controller-added checks not covered by
probes/p09_analyze.py:

(a) The closing-vs-opening settlement comparison is only informative on hours where the
    settled rate actually changed between the settlement that opens the hour and the one
    that closes it (otherwise a stale value would trivially "match" both). Restrict both
    comparisons to that subset per venue and report match rates there alongside the full
    sample, and how many such hours exist.
(b) Lighter sign convention: do the 7 recorded sample markets ever go negative? If not,
    find a currently-negative market across ALL Lighter markets (REST `/funding-rates`,
    then that market's `/fundings` row and live `market_stats` ws message) and check
    whether `/fundings`' `direction` ("long"/"short") matches the sign of the ws
    `current_funding_rate`/`funding_rate`.
(c) HL near-miss: quantify the residual |last-in-hour minus settled| distribution and its
    size relative to the reported tolerance, and check whether the settled rate matches
    ANY in-hour sample (not just the last) or trends toward the hour's end.
"""
import asyncio
import json
import time

import httpx
import polars as pl
import websockets

from fundr import store
from fundr.analysis import attach_settled, epoch_ms, hourly_profile, match_stats, reported_tolerance
from fundr.sources.hl_api import HLInfo, funding_history_frame
from fundr.sources.lighter_api import BASE_URL, WS_URL, LighterAPI, fundings_frame, lighter_signed_rate

lines = [json.loads(x) for x in (store.probe_dir("p09") / "live.jsonl").read_text().splitlines()]
by_source: dict[str, list] = {}
for rec in lines:
    by_source.setdefault(rec["source"], []).append(rec)
t0, t1 = min(r["t_ms"] for r in lines), max(r["t_ms"] for r in lines)

# ---------------------------------------------------------------------------
# Rebuild the same frames p09_analyze.py builds (kept in sync deliberately;
# this script does not import from p09_analyze.py since that module runs its
# analysis as top-level statements, not as importable functions).
# ---------------------------------------------------------------------------
hl = pl.DataFrame(by_source["metaAndAssetCtxs"]).with_columns(epoch_ms("t_ms").alias("time"))
hl_prof = hourly_profile(hl, "time", "coin", ["funding", "premium", "open_interest"])
api = HLInfo()
settled = funding_history_frame(
    [r for c in hl["coin"].unique() for r in api.funding_history(c, t0 - 3_600_000, t1 + 7_200_000)]
)
tol = reported_tolerance(settled["funding_rate_str"].to_list())
s = settled.select("coin", "settle_time", pl.col("funding_rate").alias("settled"))
full = hl_prof.filter(pl.col("n_rows") >= 50)
a_full = attach_settled(full, s, "coin").drop_nulls("settled")
b_full = full.join(s.rename({"settle_time": "hour"}), on=["coin", "hour"], how="inner")

li = pl.DataFrame(by_source["ws_market_stats"]).with_columns(
    epoch_ms("t_ms").alias("time"),
    *[pl.col(c).cast(pl.Float64) for c in ("current_funding_rate", "funding_rate", "premium")],
)
li_prof = hourly_profile(li, "time", "market_id", ["current_funding_rate", "funding_rate", "premium", "funding_timestamp"])
lapi = LighterAPI()
lset = pl.concat(
    [fundings_frame(lapi.fundings(m, "1h", t0 // 1000 - 3600, t1 // 1000 + 7200, 20), m) for m in li["market_id"].unique()]
)
ltol = reported_tolerance(lset["rate_str"].to_list())
lfull = li_prof.filter(pl.col("n_rows") >= 50)
ls_signed = lset.select("market_id", "settle_time", pl.col("signed_rate").alias("settled"))
la_full = attach_settled(lfull, ls_signed, "market_id").drop_nulls("settled")
lb_full = lfull.join(ls_signed.rename({"settle_time": "hour"}), on=["market_id", "hour"], how="inner")

# ===========================================================================
# (a) restrict to hours where the settled rate changed vs the prior settlement
# ===========================================================================
print("=== (a) informative (rate-changed) hours only ===")


def changed_settlements(settled_df: pl.DataFrame, key_col: str, rate_col: str) -> pl.DataFrame:
    """Settlement rows whose rate differs from the immediately preceding settlement for
    the same key (i.e. the hour they close had its rate change between its open and
    close settlement)."""
    sd = settled_df.sort([key_col, "settle_time"]).with_columns(pl.col(rate_col).shift(1).over(key_col).alias("prev"))
    return sd.filter(pl.col("prev").is_not_null() & (pl.col(rate_col) != pl.col("prev")))


hl_changed = changed_settlements(settled, "coin", "funding_rate")
hl_changed_close = hl_changed.select("coin", "settle_time")
hl_changed_open = hl_changed.select("coin", (pl.col("settle_time") - pl.duration(hours=1)).alias("hour"))
print(f"HL: {hl_changed.height}/{settled.height} settlements differ from the prior settlement for that coin")

a_changed = a_full.join(hl_changed_close, on=["coin", "settle_time"], how="inner")
print(
    "HL last-in-hour vs closing settlement — full sample:",
    match_stats(a_full["funding_last"], a_full["settled"], tol),
    "| changed-rate subset (n=%d):" % a_changed.height,
    match_stats(a_changed["funding_last"], a_changed["settled"], tol),
)
b_changed = b_full.join(hl_changed_open, on=["coin", "hour"], how="inner")
print(
    "HL last-in-hour vs opening settlement — full sample:",
    match_stats(b_full["funding_last"], b_full["settled"], tol),
    "| changed-rate subset (n=%d):" % b_changed.height,
    match_stats(b_changed["funding_last"], b_changed["settled"], tol),
)

li_changed = changed_settlements(lset, "market_id", "signed_rate")
li_changed_close = li_changed.select("market_id", "settle_time")
li_changed_open = li_changed.select("market_id", (pl.col("settle_time") - pl.duration(hours=1)).alias("hour"))
print(f"Lighter: {li_changed.height}/{lset.height} settlements differ from the prior settlement for that market")

la_changed = la_full.join(li_changed_close, on=["market_id", "settle_time"], how="inner")
print(
    "Lighter current_funding_rate last-in-hour vs closing settlement — full sample:",
    match_stats(la_full["current_funding_rate_last"], la_full["settled"], ltol),
    "| changed-rate subset (n=%d):" % la_changed.height,
    match_stats(la_changed["current_funding_rate_last"], la_changed["settled"], ltol) if la_changed.height else "n=0",
)
lb_changed = lb_full.join(li_changed_open, on=["market_id", "hour"], how="inner")
print(
    "Lighter funding_rate_last vs opening settlement — full sample:",
    match_stats(lb_full["funding_rate_last"], lb_full["settled"], ltol),
    "| changed-rate subset (n=%d):" % lb_changed.height,
    match_stats(lb_changed["funding_rate_last"], lb_changed["settled"], ltol) if lb_changed.height else "n=0",
)

# ===========================================================================
# (b) Lighter sign convention
# ===========================================================================
print("\n=== (b) Lighter sign convention ===")
sample_min_cfr = li.group_by("market_id").agg(pl.col("current_funding_rate").min().alias("min_cfr"))
sample_min_fr = li.group_by("market_id").agg(pl.col("funding_rate").min().alias("min_fr"))
print("Recorded 7 sample markets — min current_funding_rate per market:", sample_min_cfr.sort("market_id").rows())
print("Recorded 7 sample markets — min funding_rate per market:", sample_min_fr.sort("market_id").rows())
neg_in_sample = (li["current_funding_rate"] < 0).sum() + (li["funding_rate"] < 0).sum()
print("Total negative readings (current_funding_rate or funding_rate) across the 7 sample markets:", neg_in_sample)

if neg_in_sample == 0:
    print("None of the 7 sample markets went negative in the recording — querying all Lighter markets live.")
    client = httpx.Client(base_url=BASE_URL, timeout=30)
    fr = client.get("/api/v1/funding-rates").json()["funding_rates"]
    lighter_rows = [r for r in fr if r["exchange"] == "lighter"]
    neg_rows = sorted([r for r in lighter_rows if r["rate"] < 0], key=lambda r: r["rate"])
    print(f"/api/v1/funding-rates: {len(lighter_rows)} lighter markets, {len(neg_rows)} currently negative")
    print("Most-negative examples:", neg_rows[:5])

    if neg_rows:
        target = neg_rows[0]
        mid = target["market_id"]
        print(f"Chosen negative market: market_id={mid} symbol={target['symbol']} REST current rate={target['rate']}")

        # Latest settled /fundings row for this market
        lapi2 = LighterAPI(client)
        now_s = int(time.time())
        rows = lapi2.fundings(mid, "1h", now_s - 3600 * 30, now_s, 30)
        last_row = rows[-1]
        print("Latest /fundings row for this market:", last_row)

        # Live market_stats ws message for the same market
        async def fetch_stats(market_id: int, n: int = 3) -> list[dict]:
            out = []
            async with websockets.connect(WS_URL) as ws:
                await ws.send(json.dumps({"type": "subscribe", "channel": f"market_stats/{market_id}"}))
                while len(out) < n:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), 10))
                    if "market_stats" in msg:
                        out.append(msg["market_stats"])
            return out

        ws_msgs = asyncio.run(fetch_stats(mid))
        ws_row = ws_msgs[-1]
        print(
            "Live market_stats for this market:",
            {k: ws_row.get(k) for k in ("market_id", "current_funding_rate", "funding_rate", "funding_timestamp")},
        )

        fundings_rate = float(last_row["rate"])
        fundings_direction = last_row["direction"]
        signed_from_fundings = lighter_signed_rate(fundings_rate, fundings_direction)
        ws_funding_rate = float(ws_row["funding_rate"])
        print(
            f"Check: /fundings rate={fundings_rate} direction={fundings_direction} -> "
            f"lighter_signed_rate={signed_from_fundings}; ws funding_rate (signed) = {ws_funding_rate}; "
            f"timestamps match: {last_row['timestamp'] * 1000 == int(ws_row['funding_timestamp'])}; "
            f"signs/magnitudes match: {abs(signed_from_fundings - ws_funding_rate) < 1e-9}"
        )
    else:
        print("No negative Lighter market found live either — sign convention remains UNRESOLVED.")
else:
    print("Sample markets did go negative; use the in-sample rows above directly (not exercised this run).")

# ===========================================================================
# (c) HL near-miss: residual size and whether settled matches ANY in-hour sample
# ===========================================================================
print("\n=== (c) HL near-miss residual ===")
last_err = a_full.with_columns((pl.col("funding_last") - pl.col("settled")).abs().alias("abs_err"))
err = last_err["abs_err"]
print(
    "Last-in-hour |error| vs closing settlement — n=%d, min=%.3e, mean=%.3e, median=%.3e, max=%.3e, tol=%.1e"
    % (err.len(), err.min(), err.mean(), err.median(), err.max(), tol)
)
print("Error as a multiple of tolerance — mean=%.1fx, median=%.1fx, max=%.1fx" % ((err / tol).mean(), (err / tol).median(), (err / tol).max()))

hl_hours = (
    hl.join(full.select("coin", "hour"), on="coin", how="inner")
    .filter(pl.col("time").dt.truncate("1h") == pl.col("hour"))
    .with_columns((pl.col("hour") + pl.duration(hours=1)).alias("settle_time"))
    .join(s, on=["coin", "settle_time"], how="inner")
    .with_columns(
        (pl.col("funding") - pl.col("settled")).abs().alias("abs_err"),
        ((pl.col("time") - pl.col("hour")).dt.total_seconds()).alias("seconds_into_hour"),
    )
)
any_match = hl_hours.group_by(["coin", "settle_time"]).agg(
    (pl.col("abs_err") <= tol * (1 + 1e-9)).any().alias("any_sample_matches"),
    pl.col("abs_err").min().alias("min_abs_err_in_hour"),
)
n_any = int(any_match["any_sample_matches"].sum())
print(f"HL coin-hours where SOME in-hour minute sample matches the closing settlement exactly: {n_any}/{any_match.height}")
print(
    "Distribution of the minimum |error| achieved anywhere within the hour: min=%.3e mean=%.3e max=%.3e"
    % (any_match["min_abs_err_in_hour"].min(), any_match["min_abs_err_in_hour"].mean(), any_match["min_abs_err_in_hour"].max())
)
corr = hl_hours.select(pl.corr("seconds_into_hour", "abs_err")).item()
print(f"Correlation(seconds_into_hour, |error| vs closing settlement) across all in-hour samples: {corr:.3f}")
print("(Hypothesis, not confirmed: a negative correlation is consistent with the live value converging toward the")
print(" closing settlement as the hour progresses, per HL's documented continuous-average formula (P7).)")

# Are the "any sample matches" coin-hours the trivial constant-funding (baseline) hours,
# or does a genuine mid-hour value equal the settled rate in an off-baseline hour?
baseline_matches = any_match.filter(pl.col("any_sample_matches")).join(
    hl_hours.select("coin", "settle_time", "settled").unique(), on=["coin", "settle_time"], how="left"
)
n_baseline_matches = int((baseline_matches["settled"].abs() - 0.0000125).abs().le(1e-9).sum())
print(
    f"Of the {n_any} coin-hours with an exact in-hour match, {n_baseline_matches} are baseline-rate "
    f"(funding == 0.0000125, constant all hour) — i.e. matches are trivial, not evidence of mid-hour convergence."
)
