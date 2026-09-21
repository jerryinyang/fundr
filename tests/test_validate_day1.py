"""Gates in scripts/validate_day1.py that require a large-enough sample before printing PASS.
`fundr.analysis.rebuild_verdict` already uses `min_off_baseline=100` for the same reason, and the
spec's Done-when clause demands >=100 off-baseline market-hours -- a single surviving market-hour
that happens to match is not evidence a venue's capture is correct."""
import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validate_day1.py"
_spec = importlib.util.spec_from_file_location("validate_day1", SCRIPT)
validate_day1 = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = validate_day1
_spec.loader.exec_module(validate_day1)


def _stats(n, rate, n_match=None):
    n_match = n_match if n_match is not None else round(n * rate)
    return {"n": n, "n_match": n_match, "rate": rate, "mean_signed_error": 0.0}


# --- Lighter: exact-match venue -----------------------------------------------------------

def test_lighter_insufficient_below_min_sample_even_with_perfect_rate():
    stats = _stats(n=validate_day1.MIN_SAMPLE - 1, rate=1.0)
    assert validate_day1.lighter_verdict(stats) == "INSUFFICIENT"


def test_lighter_pass_at_min_sample_with_perfect_rate():
    stats = _stats(n=validate_day1.MIN_SAMPLE, rate=1.0)
    assert validate_day1.lighter_verdict(stats) == "PASS"


def test_lighter_fail_above_min_sample_with_imperfect_rate():
    stats = _stats(n=validate_day1.MIN_SAMPLE, rate=0.99)
    assert validate_day1.lighter_verdict(stats) == "FAIL"


def test_lighter_one_market_hour_is_not_a_pass():
    # the exact regression this gate exists for: n=1, rate=1.0 used to read PASS
    stats = _stats(n=1, rate=1.0)
    assert validate_day1.lighter_verdict(stats) == "INSUFFICIENT"


# --- HL: near-miss venue -------------------------------------------------------------------

def test_hl_insufficient_below_min_sample():
    off_stats = _stats(n=70, rate=0.0)  # today's real HL off-baseline count -- must stay honest
    assert validate_day1.hl_verdict(off_stats, median_resid=7.4e-8) == "INSUFFICIENT"


def test_hl_pass_at_min_sample_with_expected_residual():
    off_stats = _stats(n=validate_day1.MIN_SAMPLE, rate=0.0)
    assert validate_day1.hl_verdict(off_stats, median_resid=7.4e-8) == "PASS"


def test_hl_fail_on_exact_match():
    off_stats = _stats(n=validate_day1.MIN_SAMPLE, rate=0.05)  # some off-baseline hours matched exactly
    assert validate_day1.hl_verdict(off_stats, median_resid=7.4e-8) == "FAIL"


def test_hl_fail_on_residual_out_of_band_too_small():
    off_stats = _stats(n=validate_day1.MIN_SAMPLE, rate=0.0)
    assert validate_day1.hl_verdict(off_stats, median_resid=1e-11) == "FAIL"


def test_hl_fail_on_residual_out_of_band_too_large():
    off_stats = _stats(n=validate_day1.MIN_SAMPLE, rate=0.0)
    assert validate_day1.hl_verdict(off_stats, median_resid=1.0) == "FAIL"


def test_hl_fail_when_no_off_baseline_hours_exist_at_all():
    off_stats = {"n": 0, "n_match": 0, "rate": float("nan"), "mean_signed_error": float("nan")}
    assert validate_day1.hl_verdict(off_stats, median_resid=None) == "INSUFFICIENT"


# --- complete_hours: the P7 lag filter ------------------------------------------------------

def test_complete_hours_excludes_low_count_and_late_last_reading():
    import polars as pl
    from datetime import datetime, timezone

    hour = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    # polars' dt.epoch("ms") reads a naive Datetime as UTC; compute the boundary the same way
    # rather than through python's timestamp() (which would apply the local timezone instead).
    hour_ms = int(hour.timestamp() * 1000)
    df = pl.DataFrame([
        # kept: enough records, last reading 12s before the boundary
        {"hour": hour, "n": 10, "last_t_ms": hour_ms + 3_600_000 - 12_000},
        # excluded: too few records
        {"hour": hour, "n": 2, "last_t_ms": hour_ms + 3_600_000 - 12_000},
        # excluded: last reading far short of the boundary (a partial/open hour)
        {"hour": hour, "n": 10, "last_t_ms": hour_ms + 1_000_000},
    ]).with_columns(pl.col("hour").dt.replace_time_zone(None).cast(pl.Datetime("ms")))
    kept = validate_day1.complete_hours(df, "test")
    assert len(kept) == 1
