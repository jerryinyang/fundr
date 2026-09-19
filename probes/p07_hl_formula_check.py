"""P7 (HL): does the documented formula turn fundingHistory's hourly premium into its fundingRate?"""
import json
from pathlib import Path

import polars as pl

from fundr import store
from fundr.analysis import match_stats, reported_tolerance

INTEREST_8H, CLAMP, CAP_HOURLY, BASELINE = 0.0001, 0.0005, 0.04, 0.0000125


def candidate(p: pl.Expr) -> pl.Expr:
    f8 = p + (INTEREST_8H - p).clip(-CLAMP, CLAMP)
    return (f8 / 8).clip(-CAP_HOURLY, CAP_HOURLY)


df = pl.concat([pl.read_parquet(p) for p in sorted(store.probe_dir("p04").glob("settled_*.parquet"))])
df = df.with_columns(candidate(pl.col("premium")).alias("rebuilt"))
tol = reported_tolerance(df["funding_rate_str"].to_list())
off = df.filter(pl.col("funding_rate") != BASELINE)
print("all hours:", match_stats(df["rebuilt"], df["funding_rate"], tol))
print("off-baseline hours:", match_stats(off["rebuilt"], off["funding_rate"], tol))
print(off.filter((pl.col("rebuilt") - pl.col("funding_rate")).abs() > tol).head(10))

cases = pl.concat([off.head(15), df.filter(pl.col("funding_rate") == BASELINE).head(5)])
Path("tests/fixtures/hl_formula_cases.json").write_text(json.dumps([
    {"premium": r["premium"], "settled": r["funding_rate"], "settled_str": r["funding_rate_str"]}
    for r in cases.to_dicts()
], indent=1))
