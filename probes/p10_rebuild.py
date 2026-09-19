"""P10: can each venue's hourly funding be rebuilt from archived premium inputs? Spec pass rule."""
import argparse
from datetime import datetime, timedelta

import polars as pl

from fundr import store
from fundr.analysis import attach_settled, rebuild_verdict, reported_tolerance
from fundr.funding import hl_formula
from fundr.sources.hl_api import HLInfo, funding_history_frame
from fundr.sources.hl_archive import ARCHIVE_BUCKET, HLArchive, parse_time, read_csv_lz4

HL_SAMPLE_SECONDS = 5  # HL averages premium samples taken every 5 s (P7)
LIGHTER_SAMPLE_SECONDS = 60  # set from the P7 note
PREMIUM_SCALE = 1.0  # set from P8 Step 3: 0xArchive premium → Lighter percent units

ap = argparse.ArgumentParser()
ap.add_argument("--venue", choices=["hl", "lighter"], required=True)
args = ap.parse_args()

sample = [s for s in store.load_json("p00", "sample.json") if s["role"] == "mid"]
day_ms = 86_400_000


def to_ms(d: datetime) -> int:
    return int((d - datetime(1970, 1, 1)).total_seconds() * 1000)


def hourly_avg(df: pl.DataFrame, key: str) -> pl.DataFrame:
    return df.group_by(key, pl.col("time").dt.truncate("1h").alias("hour")).agg(
        pl.col("premium").mean().alias("p_avg"), pl.len().alias("n_rows")).sort([key, "hour"])


if args.venue == "hl":
    arc, api = HLArchive(), HLInfo()
    listing = set(store.load_json("p01", "listing.json"))
    dates = store.load_json("p01", "dates.json")
    coins = [s["coin"] for s in sample]
    frames, settled, windows = [], [], []
    for label in ("old", "recent"):
        start = datetime.strptime(dates[label].split("/")[-1][:8], "%Y%m%d")
        if label == "recent":
            start -= timedelta(days=3)  # keep the window inside the archive
        days = [start + timedelta(days=i) for i in range(4)]
        windows.append([d.date().isoformat() for d in days])
        for d in days:
            key = f"asset_ctxs/{d:%Y%m%d}.csv.lz4"
            if key not in listing:
                print(f"missing archive day {key}; recorded as a gap")
                continue
            frames.append(parse_time(read_csv_lz4(arc.download(ARCHIVE_BUCKET, key))).filter(pl.col("coin").is_in(coins)))
        rows = [r for c in coins for r in api.funding_history(c, to_ms(days[0]), to_ms(days[-1]) + day_ms + 3_600_000)]
        settled.append(funding_history_frame(rows))
    inputs = pl.concat(frames)
    st = pl.concat(settled)
    tol = reported_tolerance(st["funding_rate_str"].to_list())
    s = st.select("coin", "settle_time", pl.col("funding_rate").alias("settled"))
    key, rebuilt_expr, sample_s = "coin", hl_formula.hourly_rate(pl.col("p_avg")), HL_SAMPLE_SECONDS
    baseline_expr = (pl.col("settled") == hl_formula.BASELINE_HOURLY) | (pl.col("settled").abs() == hl_formula.CAP_HOURLY)
    n_baseline_exact = st.filter(pl.col("funding_rate") == hl_formula.BASELINE_HOURLY).height
    n_baseline_tol = st.filter((pl.col("funding_rate") - hl_formula.BASELINE_HOURLY).abs() < 1e-12).height
    print(f"baseline count (exact ==): {n_baseline_exact}; baseline count (tol < 1e-12): {n_baseline_tol}")
    if n_baseline_exact == 0 and n_baseline_tol > 0:
        # Exact float equality found 0 rows despite the data clearly having 0.0000125-hour
        # baseline rows; use a tolerance compare instead (sanctioned deviation, task-17 dispatch).
        baseline_expr = (pl.col("settled") - hl_formula.BASELINE_HOURLY).abs() < 1e-12
        baseline_expr = baseline_expr | (pl.col("settled").abs() == hl_formula.CAP_HOURLY)
else:
    # Lighter half is out of scope for this dispatch (lighter_formula/OXArchive do not exist
    # yet); imported here so `--venue hl` never touches this branch.
    from fundr.funding import lighter_formula
    from fundr.sources.oxarchive import OXArchive

    ox = OXArchive()
    details = store.load_json("p06", "orderbookdetails.json")["body"]["order_book_details"][0]
    params = dict(interest=float(details["base_interest_rate"]), clamp_small=float(details["funding_clamp_small"]),
                  clamp_big=float(details["funding_clamp_big"]), multiplier=float(details["funding_premium_multiplier"]))
    now = to_ms(datetime.utcnow())
    win = [(now - 29 * day_ms, now - 25 * day_ms), (now - 6 * day_ms, now - 2 * day_ms)]  # free tier: last 30 days
    windows = [[datetime.utcfromtimestamp(a / 1000).isoformat(), datetime.utcfromtimestamp(b / 1000).isoformat()] for a, b in win]
    frames = []
    for s_ in sample:
        for a, b in win:
            rows = ox.get_all(f"/v1/lighter/funding/{s_['coin']}", max_pages=200, start=a, end=b, limit=1000)
            frames.append(pl.DataFrame(rows).select(
                pl.col("timestamp").str.to_datetime(time_unit="ms").dt.replace_time_zone(None).alias("time"),
                (pl.col("premium").cast(pl.Float64) * PREMIUM_SCALE).alias("premium"),
                pl.lit(s_["lighter_market_id"]).alias("market_id")))
    store.save_json("p10", "oxarchive_calls.json", ox.calls)
    inputs = pl.concat(frames)
    st = pl.concat([pl.read_parquet(store.probe_dir("p05") / f"fundings_{s_['lighter_market_id']}.parquet") for s_ in sample])
    tol = reported_tolerance(st["rate_str"].to_list())
    s = st.select("market_id", "settle_time", pl.col("signed_rate").alias("settled"))
    key, rebuilt_expr, sample_s = "market_id", lighter_formula.hourly_rate(pl.col("p_avg"), **params), LIGHTER_SAMPLE_SECONDS
    baseline = round(params["interest"] / 8, 4)
    baseline_expr = (pl.col("settled") == baseline) | (pl.col("settled").abs() == params["clamp_big"])

cadence = inputs.sort([key, "time"]).group_by(key).agg(pl.col("time").diff().median().alias("c"))["c"].max()
attemptable = cadence.total_seconds() <= sample_s
j = attach_settled(hourly_avg(inputs, key).filter(pl.col("n_rows") >= 0.9 * 3600 / max(cadence.total_seconds(), 1)),
                   s, key).drop_nulls("settled").with_columns(rebuilt_expr.alias("rebuilt"), baseline_expr.alias("baseline"))
v = rebuild_verdict(j["rebuilt"], j["settled"], j["baseline"], tol)
out = {**v, "verdict": v["verdict"] if attemptable else "not_attemptable", "raw_verdict": v["verdict"],
       "attemptable": attemptable, "input_cadence": str(cadence), "windows": windows}
store.save_json("p10", f"verdict_{args.venue}.json", out)
print(out)
