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


# --- the two flags: emitted, never used to delete a row --------------------------------------

#: Lighter's at-baseline rate for each of the venue's three base interest rates, and the spread
#: against Hyperliquid's untruncated 1.25e-5 that each produces at joint baseline.
BASE_0100, BASE_0032, BASE_0000 = 0.0100, 0.0032, 0.0
LIGHTER_BASELINE_0100 = 1.2e-5   # 0.01% / 8 = 0.00125%, truncated DOWN to 0.0012%
LIGHTER_BASELINE_0032 = 4e-6     # 0.0032% / 8 = 0.0004%, no truncation needed
CLAMP_BIG, LIGHTER_CEILING = 4.0, 5e-3   # 4.0% / 8 = 0.5% per hour


def _pair(rows) -> pl.DataFrame:
    """`rows` is (symbol, hour offset, hl_rate, lighter_rate, base_rate_pct, clamp_big_pct)."""
    names = ("symbol", "hour", "hl_rate", "lighter_rate",
             "lighter_base_rate_pct", "lighter_clamp_big_pct")
    frame = pl.DataFrame([dict(zip(names, r)) for r in rows],
                         schema={"symbol": pl.String, "hour": pl.Int64, "hl_rate": pl.Float64,
                                 "lighter_rate": pl.Float64,
                                 "lighter_base_rate_pct": pl.Float64,
                                 "lighter_clamp_big_pct": pl.Float64})
    return frame.with_columns((T0 + pl.col("hour") * HOUR).alias("hour"),
                              (pl.col("hl_rate") - pl.col("lighter_rate")).alias("spread"))


#: One row of each kind: joint baseline at base rate 0.01, at 0.0032, at 0 (the path the matched
#: set barely exercises), a clamped hour, and an ordinary hour that is neither.
MIXED = _pair([
    ("A", 0, targets.HL_BASELINE, LIGHTER_BASELINE_0100, BASE_0100, CLAMP_BIG),
    ("B", 0, targets.HL_BASELINE, LIGHTER_BASELINE_0032, BASE_0032, CLAMP_BIG),
    ("C", 0, targets.HL_BASELINE, 0.0, BASE_0000, CLAMP_BIG),
    ("D", 0, 1.0e-4, -LIGHTER_CEILING, BASE_0100, CLAMP_BIG),
    ("E", 0, 3.0e-5, 8.0e-6, BASE_0100, CLAMP_BIG),
])


def _flag(out: pl.DataFrame, symbol: str, flag: str) -> bool:
    return out.filter(pl.col("symbol") == symbol)[flag].item()


def test_joint_baseline_spread_is_five_e_minus_seven_at_the_usual_base_rate():
    """Hyperliquid's untruncated 1.25e-5 against Lighter's truncated 1.2e-5. The whole artifact
    is that `0.00125%` does not fit in four decimal places of percent."""
    out = targets.add_venue_flags(MIXED)
    row = out.filter(pl.col("symbol") == "A").to_dicts()[0]
    assert row["joint_baseline"] is True
    assert row["spread"] == pytest.approx(5e-7, abs=1e-18)


def test_joint_baseline_spread_is_one_two_five_e_minus_five_where_the_base_rate_is_zero():
    """27 Lighter markets carry `base_interest_rate_pct = 0`, so their baseline is exactly 0 and
    the spread is Hyperliquid's baseline entire. Only 4 of the 100 matched pairs are in this
    group (AI16Z, MKR, LAUNCHCOIN, YZY, 109 rows), so the matched set barely tests it."""
    out = targets.add_venue_flags(MIXED)
    row = out.filter(pl.col("symbol") == "C").to_dicts()[0]
    assert row["joint_baseline"] is True
    assert row["spread"] == pytest.approx(1.25e-5, abs=1e-18)


def test_joint_baseline_uses_each_market_s_own_base_rate_not_btc_s():
    """B sits at the baseline its own 0.0032% base rate implies. Flagged against BTC's 0.01% it
    would read as 8.5e-6 below baseline; flagged against its own it is exactly at baseline."""
    out = targets.add_venue_flags(MIXED)
    assert _flag(out, "B", "joint_baseline") is True
    assert out.filter(pl.col("symbol") == "B")["spread"].item() == pytest.approx(8.5e-6,
                                                                                abs=1e-18)


def test_a_rate_one_lattice_tick_off_baseline_is_not_joint_baseline():
    """The tolerance exists for float representation, not for rounding real hours in."""
    off = _pair([("A", 0, targets.HL_BASELINE, LIGHTER_BASELINE_0100 + 1e-6,
                  BASE_0100, CLAMP_BIG)])
    assert _flag(targets.add_venue_flags(off), "A", "joint_baseline") is False


def test_one_venue_at_baseline_is_not_joint_baseline():
    one = _pair([("A", 0, targets.HL_BASELINE, 3e-5, BASE_0100, CLAMP_BIG),
                 ("B", 0, 3e-5, LIGHTER_BASELINE_0100, BASE_0100, CLAMP_BIG)])
    out = targets.add_venue_flags(one)
    assert out["joint_baseline"].to_list() == [False, False]


def test_venue_clamped_fires_on_the_market_s_own_ceiling_either_sign():
    """Lighter clamps before the divide by 8, so the ceiling is `funding_clamp_big_pct / 8` --
    0.5% per hour on the 4.0% markets, which is all 100 matched pairs."""
    both = _pair([("A", 0, 1e-4, LIGHTER_CEILING, BASE_0100, CLAMP_BIG),
                  ("B", 0, 1e-4, -LIGHTER_CEILING, BASE_0100, CLAMP_BIG)])
    assert targets.add_venue_flags(both)["venue_clamped"].to_list() == [True, True]
    assert _flag(targets.add_venue_flags(MIXED), "E", "venue_clamped") is False


def test_venue_clamped_uses_each_market_s_own_clamp_not_a_venue_wide_constant():
    """RIVER's clamp is 16.0% and ARC's 20.0%, so their ceilings are 2.0e-2 and 2.5e-2. Judged
    against the 4.0% market's 5e-3 they would read as clamped for a third of their range."""
    wide = _pair([("RIVER", 0, 1e-4, 2.0e-2, BASE_0100, 16.0),
                  ("ARC", 0, 1e-4, 5e-3, BASE_0100, 20.0)])
    out = targets.add_venue_flags(wide)
    assert _flag(out, "RIVER", "venue_clamped") is True
    assert _flag(out, "ARC", "venue_clamped") is False


def test_a_non_positive_published_clamp_flags_nothing():
    """MKR reports 0.0 for every funding parameter while its own history reaches 6.36e-4. Read
    literally that clamp binds on all 4,463 of its hours; it is a delisted market's zeroed
    snapshot, not a zero clamp."""
    mkr = _pair([("MKR", 0, 1e-4, 0.0, BASE_0000, 0.0),
                 ("MKR", 1, 1e-4, 6.36e-4, BASE_0000, 0.0)])
    assert targets.add_venue_flags(mkr)["venue_clamped"].to_list() == [False, False]


def test_the_flags_never_remove_a_row():
    out = targets.add_venue_flags(MIXED)
    assert out.height == MIXED.height
    assert out["symbol"].to_list() == MIXED["symbol"].to_list()
    assert out["spread"].to_list() == MIXED["spread"].to_list()


def test_flagging_raises_rather_than_flagging_false_on_an_unjoined_market():
    """A left join that missed a market leaves null parameters, which would silently read as
    'not at baseline, not clamped' on every one of its hours."""
    orphan = _pair([("A", 0, targets.HL_BASELINE, LIGHTER_BASELINE_0100, None, CLAMP_BIG)])
    with pytest.raises(ValueError, match="null"):
        targets.add_venue_flags(orphan)


def test_flagging_raises_when_a_market_parameter_column_is_absent():
    with pytest.raises(ValueError, match="lighter_clamp_big_pct"):
        targets.add_venue_flags(MIXED.drop("lighter_clamp_big_pct"))


def test_skill_metric_rows_drops_only_the_arithmetic_hours():
    """D3: joint-baseline hours are excluded from every skill metric. The clamp hours are real
    market and stay in -- they are excluded from tuning, not from scoring."""
    scored = targets.skill_metric_rows(targets.add_venue_flags(MIXED))
    assert scored["symbol"].to_list() == ["D", "E"]


def test_tuning_rows_drops_both():
    """D4: a squared-error fit would bend the whole model to chase the clamp hours."""
    tuned = targets.tuning_rows(targets.add_venue_flags(MIXED))
    assert tuned["symbol"].to_list() == ["E"]


def test_the_exclusion_helpers_refuse_an_unflagged_frame():
    """Otherwise a caller who forgot to flag silently scores on the artifact hours."""
    for helper in (targets.skill_metric_rows, targets.tuning_rows):
        with pytest.raises(ValueError, match="add_venue_flags"):
            helper(MIXED)
