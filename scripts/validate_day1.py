"""Day-one correctness: does the recorder's running funding behave as Phase 1 measured?

Lighter must converge exactly on the settlement that closes each hour; HL must not.

Correction over the original brief draft (established in review): the true last
pre-settlement reading may arrive as a `change` record, not a `boundary` one -- a reviewer
demonstrated a case where keying on `trigger == "boundary"` reconstructs the wrong value
(0.0090 instead of the correct 0.0099). So this script takes the LAST RECORD OF EACH HOUR BY
`t_ms`, whatever its trigger, never filtering on `trigger` to find "the" pre-settlement value.

Complete hours only, mirroring P7: a download always has a partial first hour (recorder
started mid-hour) and a partial last hour (download/query cut it off). Judged without a filter
those two hours fail by construction and the script would print a false FAIL on a healthy
recorder. Excluding hours whose last record lands more than MAX_LAG_S before the hour boundary
fixes this: the running premium is cumulative, so a late START doesn't matter, only a
late-enough LAST reading does (P7, "coverage filters").
"""
import argparse
import gzip
import json
import time
from pathlib import Path

import httpx
import polars as pl

from fundr.analysis import attach_settled, epoch_ms, match_stats, reported_tolerance
from fundr.sources.hl_api import HLInfo, funding_history_frame
from fundr.sources.lighter_api import LighterAPI, fundings_frame

ap = argparse.ArgumentParser()
ap.add_argument("--root", required=True, help="directory holding recorder parts (hl_state/, lighter_state/, ...)")
args = ap.parse_args()
root = Path(args.root)


HOUR_MS = 3_600_000
MAX_LAG_S = 120  # P7's coverage filter: only a late-enough LAST reading invalidates an hour,
                 # because the running premium is cumulative.
MIN_N = 5        # plausible record count for a market-hour that was actually observed
BASELINE_ABS = 1.5e-5  # HL's per-hour interest floor is +-0.0000125; a few zero-interest
                       # markets settle at 0.0 -- both are "baseline" (see validate_hl below)


def _hl_funding_history_with_backoff(api: HLInfo, coin: str, start_ms: int, end_ms: int) -> list[dict]:
    """A day-one check queries every complete-hour coin sequentially against HL's public
    endpoint (no API key), which is rate-limited (429) well before ~200 coins finish. Back off
    and retry rather than let one throttled coin abort the whole verdict."""
    delay = 1.0
    for attempt in range(6):
        try:
            return api.funding_history(coin, start_ms, end_ms)
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 429 or attempt == 5:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 20.0)
    return []  # unreachable; loop always returns or raises


def _read_gz(path: Path) -> list[str]:
    """Read whatever complete lines exist in a .jsonl.gz, even if the file is still being
    appended to by the recorder (a truncated gzip stream raises EOFError only after yielding
    every line it could decompress -- reading line-by-line preserves those lines instead of
    discarding the whole file)."""
    lines: list[str] = []
    try:
        with gzip.open(path, "rt") as f:
            for line in f:
                line = line.strip()
                if line:
                    lines.append(line)
    except EOFError:
        pass  # last (currently-open) part of an hour still being written: keep what we got
    return lines


def read(feed: str) -> list[dict]:
    out = []
    for p in sorted((root / feed).rglob("*.jsonl.gz")):
        for line in _read_gz(p):
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a bad/partial trailing line from a live file; skip it
    return [r for r in out if r.get("type") != "gap"]


def complete_hours(df: pl.DataFrame, label: str) -> pl.DataFrame:
    """Drop hours whose last recorded reading is more than MAX_LAG_S before the boundary.
    Without this the partial first and last hours of any download fail by construction and
    the verdict is a false alarm."""
    if df.is_empty():
        print(f"{label}: 0 complete market-hours kept, 0 excluded (no data)")
        return df
    df = df.with_columns(
        ((pl.col("hour").dt.epoch("ms") + HOUR_MS - pl.col("last_t_ms")) / 1000).alias("lag_s"))
    kept = df.filter((pl.col("n") >= MIN_N) & (pl.col("lag_s").is_between(0, MAX_LAG_S)))
    print(f"{label}: {len(kept)} complete market-hours kept, "
          f"{len(df) - len(kept)} excluded (n < {MIN_N} or lag_s outside [0, {MAX_LAG_S}]s)")
    return kept


def validate_lighter(root: Path) -> tuple[dict | None, bool]:
    rows = read("lighter_state")
    if not rows:
        print("Lighter: no data (feed produced zero non-gap records)")
        return None, False
    li = pl.DataFrame([{"market_id": r["payload"]["market_id"],
                        "symbol": r["payload"].get("symbol"),
                        "t_ms": r["t_ms"],
                        "cfr": float(r["payload"]["current_funding_rate"])}
                       for r in rows])
    li = li.with_columns(epoch_ms("t_ms").alias("time"))
    li_last = (li.sort("time")
                 .group_by("market_id", pl.col("time").dt.truncate("1h").alias("hour"))
                 .agg(pl.col("cfr").last().alias("cfr_last"), pl.len().alias("n"),
                      pl.col("t_ms").max().alias("last_t_ms")))
    li_last = complete_hours(li_last, "Lighter")
    if li_last.is_empty():
        print("Lighter: no complete market-hours to check")
        return None, False

    lapi = LighterAPI()
    t0, t1 = int(li["t_ms"].min()) // 1000, int(li["t_ms"].max()) // 1000
    settled = pl.concat([fundings_frame(lapi.fundings(m, "1h", t0 - 3600, t1 + 7200, 30), m)
                         for m in li["market_id"].unique().to_list()])
    ltol = reported_tolerance(settled["rate_str"].to_list())
    j = attach_settled(li_last,
                       settled.select("market_id", "settle_time",
                                      pl.col("signed_rate").alias("settled")),
                       "market_id").drop_nulls("settled")
    lighter_stats = match_stats(j["cfr_last"], j["settled"], ltol)
    print("Lighter last-in-hour vs closing settlement:", lighter_stats)
    lighter_ok = lighter_stats["n"] > 0 and lighter_stats["rate"] == 1.0
    return lighter_stats, lighter_ok


def validate_hl(root: Path) -> tuple[dict | None, bool]:
    hl_rows = read("hl_state")
    if not hl_rows:
        print("HL: no data (feed produced zero non-gap records)")
        return None, False
    hl = pl.DataFrame([{"coin": r["payload"]["coin"], "t_ms": r["t_ms"],
                        "funding": float(r["payload"]["ctx"]["funding"])}
                       for r in hl_rows if r["payload"]["ctx"].get("funding") is not None])
    hl = hl.with_columns(epoch_ms("t_ms").alias("time"))
    hl_last = (hl.sort("time")
                 .group_by("coin", pl.col("time").dt.truncate("1h").alias("hour"))
                 .agg(pl.col("funding").last().alias("funding_last"), pl.len().alias("n"),
                      pl.col("t_ms").max().alias("last_t_ms")))
    hl_last = complete_hours(hl_last, "HL")
    if hl_last.is_empty():
        print("HL: no complete market-hours to check")
        return None, False

    api = HLInfo()
    # Query every coin that actually has a complete hour, not an arbitrary subset -- a fixed
    # small sample can be dominated by baseline (interest-floor) coins by chance and hide a
    # real problem in the off-baseline population.
    coins = hl_last["coin"].unique().to_list()
    hl_settled = funding_history_frame(
        [r for c in coins for r in _hl_funding_history_with_backoff(
            api, c, int(hl["t_ms"].min()) - 3_600_000, int(hl["t_ms"].max()) + 7_200_000)])
    htol = reported_tolerance(hl_settled["funding_rate_str"].to_list())
    hj = attach_settled(hl_last,
                        hl_settled.select("coin", "settle_time",
                                          pl.col("funding_rate").alias("settled")),
                        "coin").drop_nulls("settled")
    hl_stats = match_stats(hj["funding_last"], hj["settled"], htol)
    resid = (hj["funding_last"] - hj["settled"]).abs()
    print("HL last-in-hour vs closing settlement (all coin-hours):", hl_stats)
    if hl_stats["n"] > 0:
        print("HL residual (all): median", float(resid.median()), "max", float(resid.max()))

    # P4/P9's split: a baseline hour (settled funding pinned at HL's interest floor
    # +-0.0000125, or 0 for a handful of zero-interest markets) matches trivially -- a
    # constant value "matches itself" at every sample, which is not evidence the LIVE value
    # ever reaches the settled rate. Only the off-baseline population is informative, and P4/P9
    # both found it NEVER matches exactly, with a residual of roughly 1e-7 to 1e-6. Reporting
    # only the aggregate (baseline-included) median can silently read as 0 -- as it did here --
    # purely because a majority of hours happened to be baseline, not because the recorder is
    # broken; splitting the two makes the actual signal visible instead of masked.
    off_mask = hj["settled"].abs() > BASELINE_ABS
    off = hj.filter(off_mask)
    if off.is_empty():
        print("HL: no off-baseline coin-hours in this sample -- cannot judge the behaviour that "
              "actually matters (every kept hour happened to settle at the interest floor).")
        off_stats, resid_off = None, None
    else:
        off_stats = match_stats(off["funding_last"], off["settled"], htol)
        resid_off = (off["funding_last"] - off["settled"]).abs()
        print(f"HL off-baseline (|settled| > {BASELINE_ABS}):", off_stats)
        print("HL residual (off-baseline): median", float(resid_off.median()),
              "max", float(resid_off.max()), "min", float(resid_off.min()))

    hl_ok = (off_stats is not None and off_stats["n"] > 0 and off_stats["rate"] == 0.0
              and 1e-9 < float(resid_off.median()) < 1e-4)
    return off_stats if off_stats is not None else hl_stats, hl_ok


def main() -> int:
    lighter_stats, lighter_ok = validate_lighter(root)
    hl_stats, hl_ok = validate_hl(root)
    print("LIGHTER", "PASS" if lighter_ok else ("FAIL" if lighter_stats is not None else "NO DATA"))
    print("HL", "PASS" if hl_ok else ("FAIL" if hl_stats is not None else "NO DATA"))
    return 0 if (lighter_ok and hl_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
