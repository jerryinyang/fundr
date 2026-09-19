from datetime import datetime, timedelta

import polars as pl
import pytest

from fundr import analysis as a


def _df():
    t = [datetime(2026, 1, 1, 0, m) for m in (0, 20, 40)] + [datetime(2026, 1, 1, 1, 0)]
    return pl.DataFrame(
        {"t": t, "coin": ["X"] * 4, "funding": [1.0, 1.0, 2.0, 3.0]}
    ).with_columns(pl.col("t").cast(pl.Datetime("ms")))


def test_hourly_profile_counts_distinct_and_last():
    out = a.hourly_profile(_df(), "t", "coin", ["funding"])
    first = out.row(0, named=True)
    assert first["n_rows"] == 3
    assert first["funding_n_distinct"] == 2
    assert first["funding_last"] == 2.0
    assert out.height == 2


def test_gap_scan_finds_long_gap():
    df = _df()
    gaps = a.gap_scan(df, "t", "coin", timedelta(minutes=20))
    assert gaps.height == 0
    gaps = a.gap_scan(df, "t", "coin", timedelta(minutes=10))
    assert gaps.height == 3


def test_reported_tolerance_uses_most_decimals():
    assert a.reported_tolerance(["0.0000125", "0.001"]) == pytest.approx(1e-7)
    assert a.reported_tolerance(["0.0012"]) == pytest.approx(1e-4)


def test_match_stats_counts_one_unit_as_match():
    pred = pl.Series([0.0000125, 0.0000124, 0.0000200])
    act = pl.Series([0.0000125, 0.0000125, 0.0000125])
    s = a.match_stats(pred, act, 1e-7)
    assert s["n"] == 3 and s["n_match"] == 2


def test_rebuild_verdict_pass_and_insufficient():
    n = 200
    settled = pl.Series([0.00002] * n)
    baseline = pl.Series([False] * n)
    ok = a.rebuild_verdict(settled, settled, baseline, 1e-7)
    assert ok["verdict"] == "pass"
    few = a.rebuild_verdict(settled, settled, pl.Series([True] * n), 1e-7)
    assert few["verdict"] == "insufficient_sample"


def test_rebuild_verdict_near_miss_and_fail():
    settled = pl.Series([0.00002] * 200)
    baseline = pl.Series([False] * 200)
    near = pl.Series([0.00002] * 194 + [0.0001] * 6)  # 97% match
    assert a.rebuild_verdict(near, settled, baseline, 1e-7)["verdict"] == "near_miss"
    bad = pl.Series([0.00002] * 100 + [0.0001] * 100)  # 50%
    assert a.rebuild_verdict(bad, settled, baseline, 1e-7)["verdict"] == "fail"


def test_attach_settled_pairs_hour_with_next_settlement():
    prof = a.hourly_profile(_df(), "t", "coin", ["funding"])
    settled = pl.DataFrame(
        {"coin": ["X"], "settle_time": [datetime(2026, 1, 1, 1, 0)], "settled": [9.0]}
    ).with_columns(pl.col("settle_time").cast(pl.Datetime("ms")))
    out = a.attach_settled(prof, settled, "coin")
    assert out.row(0, named=True)["settled"] == 9.0
