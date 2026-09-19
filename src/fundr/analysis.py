"""Helpers shared by probes: intra-hour profiles, gaps, and settled-value matching."""
from collections.abc import Iterable
from datetime import timedelta

import polars as pl


def epoch_ms(col: str) -> pl.Expr:
    return pl.from_epoch(pl.col(col), time_unit="ms")


def epoch_s(col: str) -> pl.Expr:
    return pl.from_epoch(pl.col(col) * 1000, time_unit="ms")


def hourly_profile(df: pl.DataFrame, time_col: str, key_col: str, value_cols: list[str]) -> pl.DataFrame:
    """Per key and UTC hour: row count, distinct-value count and last value of each column."""
    aggs = [pl.len().alias("n_rows")]
    for c in value_cols:
        aggs += [pl.col(c).n_unique().alias(f"{c}_n_distinct"), pl.col(c).last().alias(f"{c}_last")]
    return (
        df.sort(time_col)
        .with_columns(pl.col(time_col).dt.truncate("1h").alias("hour"))
        .group_by([key_col, "hour"], maintain_order=True)
        .agg(aggs)
        .sort([key_col, "hour"])
    )


def gap_scan(df: pl.DataFrame, time_col: str, key_col: str, max_gap: timedelta) -> pl.DataFrame:
    return (
        df.sort([key_col, time_col])
        .with_columns(pl.col(time_col).shift(1).over(key_col).alias("gap_start"))
        .with_columns((pl.col(time_col) - pl.col("gap_start")).alias("gap"))
        .filter(pl.col("gap") > max_gap)
        .select(key_col, "gap_start", pl.col(time_col).alias("gap_end"), "gap")
    )


def reported_tolerance(values: Iterable[str]) -> float:
    """One unit in the last decimal place, from values exactly as the venue reported them."""
    places = max(len(v.split(".")[1]) if "." in v else 0 for v in values)
    return 10.0 ** -places


def match_stats(pred: pl.Series, actual: pl.Series, tol: float) -> dict:
    err = pred - actual
    n = len(err)
    # Tiny slack so a difference of exactly one reported unit survives float rounding.
    n_match = int((err.abs() <= tol * (1 + 1e-9)).sum())
    return {
        "n": n,
        "n_match": n_match,
        "rate": n_match / n if n else float("nan"),
        "mean_signed_error": float(err.mean()) if n else float("nan"),
    }


def rebuild_verdict(
    rebuilt: pl.Series, settled: pl.Series, baseline: pl.Series, tol: float, min_off_baseline: int = 100
) -> dict:
    """Spec P10 rule. `baseline` marks hours where settled funding sits at the interest baseline or a clamp."""
    all_ = match_stats(rebuilt, settled, tol)
    off = match_stats(rebuilt.filter(~baseline), settled.filter(~baseline), tol)
    if off["n"] < min_off_baseline:
        verdict = "insufficient_sample"
    elif all_["rate"] >= 0.99 and off["rate"] >= 0.99 and abs(all_["mean_signed_error"]) <= tol / 10:
        verdict = "pass"
    elif min(all_["rate"], off["rate"]) >= 0.95:
        verdict = "near_miss"
    else:
        verdict = "fail"
    return {"verdict": verdict, "all": all_, "off_baseline": off, "tol": tol}


def attach_settled(profile: pl.DataFrame, settled: pl.DataFrame, key_col: str) -> pl.DataFrame:
    """Pair each hour with the settlement that closes it (settle_time = hour + 1h).
    `settled` needs columns key_col and settle_time (Datetime ms)."""
    return profile.with_columns((pl.col("hour") + pl.duration(hours=1)).alias("settle_time")).join(
        settled, on=[key_col, "settle_time"], how="left"
    )
