from datetime import datetime, timedelta

import polars as pl
import pytest

from fundr import targets

HOUR = timedelta(hours=1)
T0 = datetime(2026, 1, 1)


def _series(rows) -> pl.DataFrame:
    """`rows` is (symbol, hour offset from T0, spread)."""
    return pl.DataFrame(
        {"symbol": [r[0] for r in rows],
         "hour": [T0 + r[1] * HOUR for r in rows],
         "spread": [r[2] for r in rows]},
        schema={"symbol": pl.String, "hour": pl.Datetime("ms"), "spread": pl.Float64})


def _at(out: pl.DataFrame, symbol: str, offset: int) -> dict:
    return out.filter((pl.col("symbol") == symbol)
                      & (pl.col("hour") == T0 + offset * HOUR)).to_dicts()[0]


# --- forward_window: the gaps a one-row shift would bridge -----------------------------------

def test_single_hour_hole_is_never_bridged():
    """Hour 2 is missing. A `shift` pairs hour 1 with hour 3 -- two hours apart, silently."""
    out = targets.forward_window(_series([("A", 0, 1.0), ("A", 1, 2.0), ("A", 3, 4.0)]), h=1)
    row = _at(out, "A", 1)
    assert row["spread_h1"] is None
    assert row["n_hours_used"] == 0
    assert _at(out, "A", 0)["spread_h1"] == 2.0


def test_eight_hour_interval_is_never_bridged():
    """The pre-2023-06-08 grid. A `shift` pairs hour 0 with hour 8."""
    df = _series([("A", 0, 1.0), ("A", 8, 9.0)])
    at_h1 = _at(targets.forward_window(df, h=1), "A", 0)
    assert at_h1["spread_h1"] is None
    assert at_h1["n_hours_used"] == 0

    wide = _at(targets.forward_window(df, h=8), "A", 0)
    assert wide["n_hours_used"] == 1
    assert wide["spread_h8"] == 9.0
    assert all(wide[f"spread_h{k}"] is None for k in range(1, 8))


def test_hole_inside_the_window_is_counted_not_imputed():
    out = targets.forward_window(_series([("A", 0, 1.0), ("A", 1, 2.0), ("A", 3, 4.0)]), h=3)
    row = _at(out, "A", 0)
    assert row["n_hours_used"] == 2
    assert row["spread_h1"] == 2.0
    assert row["spread_h2"] is None
    assert row["spread_h3"] == 4.0


def test_complete_window_uses_every_hour():
    out = targets.forward_window(_series([("A", k, float(k)) for k in range(6)]), h=3)
    row = _at(out, "A", 0)
    assert row["n_hours_used"] == 3
    assert [row[f"spread_h{k}"] for k in (1, 2, 3)] == [1.0, 2.0, 3.0]


def test_tail_row_survives_with_a_short_window():
    """A row whose window runs off the end is legitimate data, not a row to drop or impute."""
    df = _series([("A", k, float(k)) for k in range(4)])
    out = targets.forward_window(df, h=3)
    assert out.height == df.height
    assert _at(out, "A", 3)["n_hours_used"] == 0
    assert _at(out, "A", 2)["n_hours_used"] == 1
    assert _at(out, "A", 2)["spread_h2"] is None


def test_window_never_crosses_symbols():
    out = targets.forward_window(
        _series([("A", 0, 1.0), ("B", 1, 2.0), ("B", 2, 3.0)]), h=1)
    assert _at(out, "A", 0)["n_hours_used"] == 0
    assert _at(out, "B", 1)["spread_h1"] == 3.0


def test_duplicate_hours_raise_rather_than_multiply_rows():
    with pytest.raises(ValueError, match="duplicate"):
        targets.forward_window(_series([("A", 0, 1.0), ("A", 0, 2.0)]), h=1)


def test_h_must_be_at_least_one():
    with pytest.raises(ValueError, match="h"):
        targets.forward_window(_series([("A", 0, 1.0)]), h=0)


# --- non_adjacent_pairs: the count that proves the join is not bridging ----------------------

def test_non_adjacent_pairs_reports_each_gap_and_its_size():
    pairs = targets.non_adjacent_pairs(
        _series([("A", 0, 1.0), ("A", 1, 2.0), ("A", 3, 4.0),
                 ("B", 0, 1.0), ("B", 8, 2.0)]))
    assert sorted(pairs["gap_hours"].to_list()) == [2, 8]
    assert pairs.filter(pl.col("gap_hours") == 8)["symbol"].to_list() == ["B"]


def test_a_uniform_grid_has_no_non_adjacent_pairs():
    pairs = targets.non_adjacent_pairs(_series([("A", k, float(k)) for k in range(5)]))
    assert pairs.is_empty()


# --- assert_common_basis: catch Lighter's raw percent, not a column name ---------------------

def _rates(values, symbols=None, column="rate") -> pl.DataFrame:
    n = len(values)
    return pl.DataFrame({"symbol": symbols or ["A"] * n, column: values},
                        schema={"symbol": pl.String, column: pl.Float64})


#: What Lighter's API actually serves: percent, truncated to four decimal places, unsigned.
RAW_PERCENT = [0.0012, 0.0004, 0.0125, 0.0008, 0.0002, 0.0016, 0.0004, 0.0009, 0.0012, 0.0]


def test_raises_on_lighter_raw_percent():
    with pytest.raises(ValueError, match="percent"):
        targets.assert_common_basis(_rates(RAW_PERCENT))


def test_accepts_the_same_rates_on_the_common_basis():
    targets.assert_common_basis(_rates([v / 100 for v in RAW_PERCENT]))
    targets.assert_common_basis(_rates([-v / 100 for v in RAW_PERCENT]))


def test_raises_on_a_rate_too_large_to_be_an_hourly_fraction():
    """Lighter's clamp is 0.005/hour and Hyperliquid's 0.04; 0.5 is a percent wearing a
    fraction's name."""
    with pytest.raises(ValueError, match="too large"):
        targets.assert_common_basis(_rates([0.5, -0.000012], column="signed_rate_fraction"))


def test_raises_when_a_cross_section_carries_no_negative_rate():
    """Funding is signed. Across a panel this wide, no negative means the sign was dropped."""
    symbols = [f"S{i}" for i in range(targets.MIN_SIGN_SYMBOLS)]
    values = [1.25e-5 + (i % 7) * 3e-6 for i in range(targets.MIN_SIGN_ROWS)]
    df = _rates(values, symbols=[symbols[i % len(symbols)] for i in range(len(values))],
                column="signed_rate_fraction")
    with pytest.raises(ValueError, match="negative"):
        targets.assert_common_basis(df)


def test_a_single_symbol_with_no_negative_rate_is_left_alone():
    """13 Lighter markets (TTWO, GME, ARM, ...) have no negative hour in their whole history.
    The sign test needs a cross-section or it fires on real data."""
    values = [1.25e-5 + (i % 7) * 3e-6 for i in range(targets.MIN_SIGN_ROWS)]
    targets.assert_common_basis(_rates(values, column="signed_rate_fraction"))


def test_raises_when_there_is_no_rate_column_to_check():
    """The assert must not silently pass a frame it never looked at."""
    with pytest.raises(ValueError, match="no column"):
        targets.assert_common_basis(pl.DataFrame({"symbol": ["A"], "hour": [T0]}))
