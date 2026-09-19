"""Hyperliquid hourly funding from the hourly average premium. Confirmed in P7
(docs/phase1/evidence/P7-funding-formulas.md)."""
import polars as pl

INTEREST_8H = 0.0001
CLAMP_8H = 0.0005
CAP_HOURLY = 0.04
BASELINE_HOURLY = INTEREST_8H / 8


def hourly_rate(p: pl.Expr) -> pl.Expr:
    f8 = p + (INTEREST_8H - p).clip(-CLAMP_8H, CLAMP_8H)
    return (f8 / 8).clip(-CAP_HOURLY, CAP_HOURLY)
