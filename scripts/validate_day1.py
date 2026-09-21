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

A verdict of PASS requires a large-enough sample, not just n > 0: `fundr.analysis.rebuild_verdict`
uses `min_off_baseline=100` for exactly this reason, and the spec's Done-when clause demands >=100
off-baseline market-hours for Lighter's formula re-validation. A single surviving market-hour that
happens to match is not evidence a venue's capture is correct; below the threshold this script
reports a distinct INSUFFICIENT verdict (with the count) rather than printing PASS.
"""
import argparse
import gzip
import json
import sys
import time
from pathlib import Path

import httpx
import polars as pl

from fundr.analysis import attach_settled, epoch_ms, match_stats, reported_tolerance
from fundr.sources.hl_api import HLInfo, funding_history_frame
from fundr.sources.lighter_api import LighterAPI, fundings_frame

HOUR_MS = 3_600_000
MAX_LAG_S = 120  # P7's coverage filter: only a late-enough LAST reading invalidates an hour,
                 # because the running premium is cumulative.
MIN_N = 5        # plausible record count for a market-hour that was actually observed
BASELINE_ABS = 1.5e-5  # HL's per-hour interest floor is +-0.0000125; a few zero-interest
                       # markets settle at 0.0 -- both are "baseline" (see validate_hl below)
MIN_SAMPLE = 100  # fundr.analysis.rebuild_verdict's own min_off_baseline, and the spec's
                  # Done-when bar (>=100 off-baseline market-hours). Below this a PASS is not
                  # a defensible claim, whatever the match rate says.


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


def read(root: Path, feed: str) -> list[dict]:
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


def lighter_verdict(stats: dict) -> str:
    """Pure judgement over `match_stats`' output -- separated from I/O and network calls so the
    MIN_SAMPLE gate is unit-testable without a live venue or recorded data."""
    if stats["n"] < MIN_SAMPLE:
        return "INSUFFICIENT"
    return "PASS" if stats["rate"] == 1.0 else "FAIL"


def hl_verdict(off_stats: dict, median_resid: float | None) -> str:
    """Pure judgement over the off-baseline `match_stats` output and its residual median."""
    if off_stats["n"] < MIN_SAMPLE:
        return "INSUFFICIENT"
    if off_stats["rate"] == 0.0 and median_resid is not None and 1e-9 < median_resid < 1e-4:
        return "PASS"
    return "FAIL"


def _report_unsettled(joined_before: int, joined_after: int, label: str) -> None:
    """`attach_settled` left-joins onto the settled series; a kept hour with no matching
    settlement (venue API didn't return it, paging bug, market delisted mid-window, ...) must
    not vanish silently via drop_nulls -- report it so a real coverage gap in the SETTLED side
    is visible instead of just shrinking the denominator."""
    dropped = joined_before - joined_after
    if dropped:
        print(f"{label}: {dropped} kept hour(s) had no matching settlement and were dropped "
              f"(venue's settled series didn't cover them) -- investigate before trusting the rate")


def validate_lighter(root: Path) -> tuple[dict | None, str]:
    rows = read(root, "lighter_state")
    if not rows:
        print("Lighter: no data (feed produced zero non-gap records)")
        return None, "NO DATA"
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
        return None, "NO DATA"

    lapi = LighterAPI()
    t0, t1 = int(li["t_ms"].min()) // 1000, int(li["t_ms"].max()) // 1000
    # fundings_all pages internally (no fixed count cap) -- a fixed `count_back` on a single
    # `fundings()` call silently truncates the settled series once a market-hour window spans
    # more settlements than that count, which then drops kept hours out from under
    # attach_settled's join without any warning.
    settled = pl.concat([fundings_frame(lapi.fundings_all(m, "1h", t0 - 3600, t1 + 7200), m)
                         for m in li["market_id"].unique().to_list()])
    ltol = reported_tolerance(settled["rate_str"].to_list())
    j_all = attach_settled(li_last,
                           settled.select("market_id", "settle_time",
                                          pl.col("signed_rate").alias("settled")),
                           "market_id")
    j = j_all.drop_nulls("settled")
    _report_unsettled(len(j_all), len(j), "Lighter")
    lighter_stats = match_stats(j["cfr_last"], j["settled"], ltol)
    print("Lighter last-in-hour vs closing settlement:", lighter_stats)

    verdict = lighter_verdict(lighter_stats)
    if verdict == "INSUFFICIENT":
        print(f"Lighter: INSUFFICIENT sample -- {lighter_stats['n']} complete market-hours, "
              f"need >= {MIN_SAMPLE} to report a verdict")
    return lighter_stats, verdict


def validate_hl(root: Path) -> tuple[dict | None, str]:
    hl_rows = read(root, "hl_state")
    if not hl_rows:
        print("HL: no data (feed produced zero non-gap records)")
        return None, "NO DATA"
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
        return None, "NO DATA"

    api = HLInfo()
    # Query every coin that actually has a complete hour, not an arbitrary subset -- a fixed
    # small sample can be dominated by baseline (interest-floor) coins by chance and hide a
    # real problem in the off-baseline population.
    coins = hl_last["coin"].unique().to_list()
    hl_settled = funding_history_frame(
        [r for c in coins for r in _hl_funding_history_with_backoff(
            api, c, int(hl["t_ms"].min()) - 3_600_000, int(hl["t_ms"].max()) + 7_200_000)])
    htol = reported_tolerance(hl_settled["funding_rate_str"].to_list())
    hj_all = attach_settled(hl_last,
                            hl_settled.select("coin", "settle_time",
                                              pl.col("funding_rate").alias("settled")),
                            "coin")
    hj = hj_all.drop_nulls("settled")
    _report_unsettled(len(hj_all), len(hj), "HL")
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
        off_stats, resid_off = {"n": 0, "n_match": 0, "rate": float("nan"),
                                "mean_signed_error": float("nan")}, None
    else:
        off_stats = match_stats(off["funding_last"], off["settled"], htol)
        resid_off = (off["funding_last"] - off["settled"]).abs()
        print(f"HL off-baseline (|settled| > {BASELINE_ABS}):", off_stats)
        print("HL residual (off-baseline): median", float(resid_off.median()),
              "max", float(resid_off.max()), "min", float(resid_off.min()))

    median_resid = float(resid_off.median()) if resid_off is not None else None
    verdict = hl_verdict(off_stats, median_resid)
    if verdict == "INSUFFICIENT":
        print(f"HL: INSUFFICIENT sample -- {off_stats['n']} off-baseline market-hours, "
              f"need >= {MIN_SAMPLE} to report a verdict")
    return off_stats, verdict


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="directory holding recorder parts (hl_state/, lighter_state/, ...)")
    args = ap.parse_args(argv)
    root = Path(args.root)

    lighter_stats, lighter_verdict = validate_lighter(root)
    hl_stats, hl_verdict = validate_hl(root)
    print("LIGHTER", lighter_verdict)
    print("HL", hl_verdict)
    return 0 if (lighter_verdict == "PASS" and hl_verdict == "PASS") else 1


if __name__ == "__main__":
    sys.exit(main())
