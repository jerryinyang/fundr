from datetime import datetime, timedelta

import polars as pl
import pytest

from scripts.qa_backfill import (
    alignment_verdict, coverage, coverage_row, cross_venue, lag_scan, match_symbols, roster_diff,
    vendor_crosscheck, worst_by_coverage)


def _funding(symbol, hours, rate=0.00001, t0=datetime(2026, 1, 1), vary=False):
    times = pl.datetime_range(t0, t0 + timedelta(hours=hours - 1), interval="1h",
                              eager=True).cast(pl.Datetime("ms"))
    # A varying series must also be NON-LINEAR: a straight ramp correlates 1.0 with itself at
    # every lag, so the lag scan below could not tell an aligned series from a shifted one.
    # This permutation cycle gives corr 1.0 at lag 0 and ~-0.40 at lag +-1 (measured).
    rates = [rate * (1 + (i * 37) % 11) for i in range(hours)] if vary else [rate] * hours
    return pl.DataFrame({"symbol": [symbol] * hours, "settle_time": times,
                         "signed_rate_fraction": rates})


def test_coverage_row_counts_hours_and_gaps():
    df = _funding("BTC", 10)
    df = df.filter(pl.col("settle_time") != df["settle_time"][5])   # punch one hole
    row = coverage_row("BTC", df, time_col="settle_time")
    assert row["rows"] == 9
    assert row["expected_hours"] == 10
    assert row["gaps"] == 1
    assert 0.89 < row["coverage"] < 0.91


def test_coverage_row_expected_hours_come_from_the_listing_not_the_data():
    # The market listed 5 hours before our first row: a fetch that started late must not score
    # 1.0 just because it is internally consistent.
    df = _funding("BTC", 10)
    row = coverage_row("BTC", df, time_col="settle_time",
                       expected_start=datetime(2025, 12, 31, 19), start_source="listing")
    assert row["expected_hours"] == 15
    assert row["coverage"] == pytest.approx(10 / 15)
    assert row["missing_head_hours"] == 5
    assert row["expected_start_source"] == "listing"


def test_coverage_row_counts_a_truncated_tail():
    df = _funding("BTC", 10)
    row = coverage_row("BTC", df, time_col="settle_time",
                       expected_end=datetime(2026, 1, 1, 12))
    assert row["missing_tail_hours"] == 3
    assert row["coverage"] < 1.0


def test_coverage_anchors_an_inactive_market_to_its_own_last_row():
    # Lighter 173 (SPACEX) settled 985 complete hours and stopped on 2026-06-18. Measured to
    # "now" it would read ~30% and head the worst-ten table forever; measured to its own end it
    # is what it actually is -- complete, and over.
    dead = {"SPACEX": _funding("SPACEX", 10)}
    now = datetime(2026, 6, 1)
    scored_to_now = coverage(dead, time_col="settle_time", expected_end=now)
    scored_to_its_end = coverage(dead, time_col="settle_time",
                                 expected_ends={"SPACEX": None}, expected_end=now)
    assert scored_to_now["coverage"].item() < 0.01
    assert scored_to_its_end["coverage"].item() == 1.0


def test_match_symbols_reports_unmatched_both_ways():
    m = match_symbols(["BTC", "kPEPE", "ETH"], ["BTC", "1000PEPE", "SOL"])
    assert m["matched"] == ["BTC"]
    assert "kPEPE" in m["hl_only"] and "ETH" in m["hl_only"]
    assert "1000PEPE" in m["lighter_only"] and "SOL" in m["lighter_only"]


def test_cross_venue_reports_overlap_and_correlation():
    # Rates must VARY or both standard deviations are zero, the correlation is undefined, and
    # the test proves nothing about the code path it claims to cover.
    hl = _funding("BTC", 24, rate=0.00001, vary=True)
    li = _funding("BTC", 24, rate=0.00002, vary=True)
    out = cross_venue({"BTC": hl}, {"BTC": li})
    row = out.row(0, named=True)
    assert row["symbol"] == "BTC"
    assert row["overlap_hours"] == 24
    assert row["corr_signed_rate"] == pytest.approx(1.0)


def test_lag_scan_prefers_lag_zero_on_aligned_series():
    hl = _funding("BTC", 48, vary=True)
    li = _funding("BTC", 48, vary=True)
    table = lag_scan({"BTC": hl}, {"BTC": li})
    best = table.sort("mean_corr", descending=True, nulls_last=True).row(0, named=True)
    assert best["lag_hours"] == 0
    assert alignment_verdict(table) == "ALIGNED"


def test_lag_scan_detects_a_one_hour_shift():
    hl = _funding("BTC", 48, vary=True)
    li = _funding("BTC", 48, vary=True, t0=datetime(2026, 1, 1, 1))
    table = lag_scan({"BTC": hl}, {"BTC": li})
    assert alignment_verdict(table).startswith("MISALIGNED")


def test_roster_diff_flags_coins_missing_from_meta():
    # Survivorship: if HL ever drops delisted entries from `meta`, the coins that exist only in
    # the archive are exactly the ones a meta-built universe silently loses.
    d = roster_diff(archive_coins=["BTC", "ETH", "GONE"], meta_coins=["BTC", "ETH", "NEW"])
    assert d["archive_only"] == ["GONE"]
    assert d["meta_only"] == ["NEW"]


def test_worst_by_coverage_puts_nulls_last():
    cov = pl.DataFrame({"key": ["a", "b", "c"], "coverage": [0.5, None, 0.9]})
    assert worst_by_coverage(cov, n=3)["key"].to_list() == ["a", "c", "b"]


def test_vendor_crosscheck_counts_matches_at_both_lags():
    ours = _funding("BTC", 5, rate=0.00001, vary=True)

    class FakeOX:
        def get_all(self, path, **params):
            return [{"timestamp": t, "funding_rate": r}
                    for t, r in zip(ours["settle_time"].dt.epoch("ms").to_list(),
                                    ours["signed_rate_fraction"].to_list())]

    out = vendor_crosscheck(FakeOX(), "hyperliquid", "BTC", ours, days=30)
    assert out["rows_vendor"] == 5
    assert out["matched_lag0"] == 5
    assert out["matched_lag0"] >= out["matched_lag_minus_1h"]
