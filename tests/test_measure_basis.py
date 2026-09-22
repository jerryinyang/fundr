"""Task 5's tests: the three traps that have already been hit, each made impossible here.

Every statistic in `scripts/measure_basis.py` is a difference between two venues' marks, so the
ways it can be silently wrong are all about *which* number got differenced against *which*:

1. **Alignment.** Lighter's candle stamped `T` closes at the end of hour `T`, so the Hyperliquid
   side must be that hour's **last** per-minute mark. An hour-*median* against an hour-*close*
   sits half an hour behind and measures price-timing noise -- 98 bp of 24-hour "drift" that is
   not basis at all. The loader here must return both, so the artifact can be reproduced once as
   proof of alignment and then discarded, and the report must state which one it used.
2. **Stale marks.** Hyperliquid's `mark_px` freezes on a delisted market rather than stopping.
   A frozen series is a straight line against a live one, which manufactures both an enormous
   level and near-perfect persistence.
3. **Age origin.** `lighter_mark_candles` begins 2025-08-25. Measuring a pair's age from the
   first hour of the mark-joined panel relabels every mature market as week-1 and halves the
   young-bucket figure. Age comes from `panel.pair_age_hours` -- the pair's first *concurrent
   funding* hour -- and nothing else.

Everything is tested on hand-built frames whose right answer is known in advance; the
end-to-end path runs on a miniature panel written to a temporary data root, so the report's
shape is exercised without touching the real one.
"""
import math
from datetime import datetime, timedelta

import polars as pl
import pytest

from scripts import measure_basis as mb

H = timedelta(hours=1)
T0 = datetime(2026, 1, 1)


# --- trap 1: hour-close against hour-close ----------------------------------------------------

def _minutes(root, day: datetime, symbol: str, prices) -> None:
    """One day of per-minute Hyperliquid context rows for `symbol`."""
    rows = [(day + timedelta(minutes=m), symbol, px) for m, px in enumerate(prices)]
    path = root / "hl_asset_ctxs" / f"date={day:%Y-%m-%d}" / "part.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"time": [r[0] for r in rows], "coin": [r[1] for r in rows],
                  "mark_px": [r[2] for r in rows]},
                 schema={"time": pl.Datetime("ms"), "coin": pl.String,
                         "mark_px": pl.Float64}).write_parquet(path)


def test_the_hyperliquid_mark_is_the_hours_last_print_and_the_median_travels_beside_it(
        tmp_path, monkeypatch):
    monkeypatch.setenv("FUNDR_PHASE2_DATA", str(tmp_path))
    # A rising hour: the last minute is 100.59, the median of the 60 minutes is 100.295.
    _minutes(tmp_path, T0, "AAA", [100.0 + m / 100 for m in range(60)])
    marks = mb.hl_hour_marks(T0, T0)
    row = marks.filter(pl.col("hour") == T0).to_dicts()[0]
    assert row["hl_close"] == pytest.approx(100.59)
    assert row["hl_median"] == pytest.approx(100.295)
    assert row["hl_close"] != pytest.approx(row["hl_median"])
    assert row["n_prints"] == 60


def test_the_alignment_check_stops_when_the_level_has_no_persistence():
    # The whole measurement rests on this: hour-close against hour-close gives a level lag-1
    # near 1; anything near zero means the two sides are not the same instant, and the run must
    # stop rather than publish a timing artifact as a venue basis.
    mb.check_alignment(0.9894)
    with pytest.raises(ValueError, match="alignment"):
        mb.check_alignment(0.03)


# --- trap 2: a frozen mark is not a price -----------------------------------------------------

def test_a_frozen_mark_run_is_flagged_and_a_live_one_is_not():
    hours = [T0 + h * H for h in range(60)]
    frozen = [0.12555] * 60                          # AI, frozen since 2025-08-25
    live = [100.0 + (h % 7) * 0.01 for h in range(60)]
    frame = pl.DataFrame({"symbol": ["AI"] * 60 + ["BTC"] * 60,
                          "hour": hours + hours,
                          "hl_close": frozen + live,
                          "lighter_close": live + live})
    flagged = mb.with_stale(mb.densify(frame), window=mb.STALE_HOURS)
    by_symbol = dict(flagged.group_by("symbol").agg(pl.col("is_stale").sum()).iter_rows())
    assert by_symbol["AI"] > 0
    assert by_symbol["BTC"] == 0


def test_a_hole_breaks_a_stale_run_rather_than_extending_it_across_the_gap():
    # Hours 0..29 and 40..69 both hold one price. Neither run may borrow the other's length
    # through the ten-hour hole, so with a 35-hour window nothing is stale.
    hours = [T0 + h * H for h in list(range(30)) + list(range(40, 70))]
    frame = pl.DataFrame({"symbol": ["A"] * 60, "hour": hours, "hl_close": [5.0] * 60,
                          "lighter_close": [5.0] * 60})
    flagged = mb.with_stale(mb.densify(frame), window=35)
    assert int(flagged["is_stale"].sum()) == 0
    assert int(mb.with_stale(mb.densify(frame), window=25)["is_stale"].sum()) > 0


# --- trap 3: age comes from the panel, never from the mark join -------------------------------

def test_age_is_read_from_the_panel_not_from_the_first_hour_of_the_mark_panel():
    # A pair 200 days old whose mark history starts today. Measured from the mark panel it is
    # "week 1"; measured from `pair_age_hours` -- the pair's first concurrent funding hour --
    # it is what it is, three months and older.
    frame = pl.DataFrame({"symbol": ["OLD", "NEW"], "hour": [T0, T0],
                          "pair_age_hours": [4800, 3]})
    bucketed = mb.with_age_bucket(frame)
    assert dict(zip(bucketed["symbol"], bucketed["age_bucket"])) == {
        "OLD": "3 months+", "NEW": "week 1"}


def test_the_age_bucket_cuts_sit_where_the_plan_puts_them():
    frame = pl.DataFrame({"pair_age_hours": [0, 167, 168, 719, 720, 2159, 2160]})
    assert mb.with_age_bucket(frame)["age_bucket"].to_list() == [
        "week 1", "week 1", "weeks 2-4", "weeks 2-4", "months 1-3", "months 1-3", "3 months+"]


# --- the basis itself -------------------------------------------------------------------------

def test_both_legs_are_divided_by_their_own_size_multiplier():
    # `NOT` measures one token on Hyperliquid against 1000 on Lighter's `1000NOT`. Unscaled,
    # the difference reads as a ~200,000 bp basis; scaled, the two venues agree.
    panel = pl.DataFrame({"symbol": ["NOT"], "lighter_symbol": ["1000NOT"], "hour": [T0],
                          "pair_age_hours": [5000], "size_multiplier": [1],
                          "lighter_size_multiplier": [1000]})
    hl = pl.DataFrame({"hour": [T0], "symbol": ["NOT"], "hl_close": [0.0021],
                       "hl_median": [0.0021], "n_prints": [60]})
    li = pl.DataFrame({"lighter_symbol": ["1000NOT"], "hour": [T0], "lighter_close": [2.1]})
    row = mb.basis_frame(panel, hl, li).to_dicts()[0]
    assert row["basis_bp"] == pytest.approx(0.0, abs=1e-6)


def test_the_basis_is_hyperliquid_minus_lighter_over_the_midpoint():
    panel = pl.DataFrame({"symbol": ["AAA"], "lighter_symbol": ["AAA"], "hour": [T0],
                          "pair_age_hours": [5000], "size_multiplier": [1],
                          "lighter_size_multiplier": [1]})
    hl = pl.DataFrame({"hour": [T0], "symbol": ["AAA"], "hl_close": [101.0],
                       "hl_median": [100.5], "n_prints": [60]})
    li = pl.DataFrame({"lighter_symbol": ["AAA"], "hour": [T0], "lighter_close": [100.0]})
    row = mb.basis_frame(panel, hl, li).to_dicts()[0]
    assert row["basis_bp"] == pytest.approx(1.0 / 100.5 * 1e4)
    assert row["basis_bp"] > 0                       # Hyperliquid above Lighter is positive
    # The median-aligned basis travels beside it, unused, so the artifact can be reproduced.
    assert row["basis_median_bp"] == pytest.approx(0.5 / 100.25 * 1e4)


def test_panel_hours_before_the_mark_history_are_counted_not_silently_dropped():
    # `lighter_mark_candles` starts 2025-08-25, 212 days after the trade candles. A join that
    # quietly loses those hours would report a coverage figure it never measured.
    hours = [T0 + h * H for h in range(4)]
    panel = pl.DataFrame({"symbol": ["AAA"] * 4, "lighter_symbol": ["AAA"] * 4, "hour": hours,
                          "pair_age_hours": [5000, 5001, 5002, 5003],
                          "size_multiplier": [1] * 4, "lighter_size_multiplier": [1] * 4})
    hl = pl.DataFrame({"hour": hours, "symbol": ["AAA"] * 4, "hl_close": [100.0] * 4,
                       "hl_median": [100.0] * 4, "n_prints": [60] * 4})
    li = pl.DataFrame({"lighter_symbol": ["AAA"] * 2, "hour": hours[2:],
                       "lighter_close": [100.0] * 2})
    out = mb.basis_frame(panel, hl, li)
    assert out.height == 2                            # only the hours with both marks
    assert mb.coverage(panel, out) == pytest.approx(0.5)


# --- forward windows: a hole is a null, never a bridge ----------------------------------------

def test_a_forward_change_never_reaches_across_a_missing_hour():
    hours = [T0 + h * H for h in (0, 1, 2, 4, 5)]
    frame = pl.DataFrame({"symbol": ["A"] * 5, "hour": hours,
                          "basis_bp": [1.0, 2.0, 3.0, 10.0, 11.0]})
    out = mb.with_forward_change(mb.densify(frame), "basis_bp", (2,))
    got = {row["hour"]: row["basis_bp_d2"] for row in out.to_dicts()}
    # Hour 1 -> hour 3 does not exist, so it is null. A `shift(2)` on the compacted rows would
    # have paired hour 1 with hour 4 and printed a drift of 8 bp.
    assert got[T0 + H] is None
    assert got[T0] == pytest.approx(2.0)              # hour 0 -> hour 2 is real
    assert got[T0 + 2 * H] == pytest.approx(7.0)      # hour 2 -> hour 4 is real


def test_a_forward_sum_needs_every_hour_of_the_hold_or_it_produces_nothing():
    hours = [T0 + h * H for h in (0, 1, 2, 4)]
    frame = pl.DataFrame({"symbol": ["A"] * 4, "hour": hours,
                          "spread_bp": [1.0, 2.0, 3.0, 5.0]})
    out = mb.with_forward_sum(mb.densify(frame), "spread_bp", (2,))
    got = {row["hour"]: row["spread_bp_c2"] for row in out.to_dicts()}
    assert got[T0] == pytest.approx(5.0)              # hours 1 and 2 both settled
    assert got[T0 + H] is None                        # hour 3 never settled: no short sums
    assert got[T0 + 2 * H] is None


# --- the shape of the answer -------------------------------------------------------------------

def test_the_reversion_exponent_reads_one_half_for_a_random_walk():
    sds = {h: 10.0 * math.sqrt(h) for h in (1, 6, 24, 72, 168)}
    assert mb.reversion_exponent(sds) == pytest.approx(0.5, abs=1e-9)
    # A basis that does not compound at all has an exponent of zero.
    assert mb.reversion_exponent({h: 20.0 for h in (1, 6, 24, 72, 168)}) == pytest.approx(0.0)


def test_the_level_summary_reports_persistence_and_the_per_symbol_spread():
    hours = [T0 + h * H for h in range(200)]
    values = [10.0 + 5.0 * math.sin(h / 6) for h in range(200)]
    frame = mb.densify(pl.DataFrame({"symbol": ["A"] * 200, "hour": hours,
                                     "basis_bp": values}))
    stats = mb.level_stats(frame)
    assert stats["rows"] == 200 and stats["symbols"] == 1
    assert stats["mean_bp"] == pytest.approx(10.0, abs=0.5)
    assert stats["lag1"] > 0.9                        # a smooth series is highly persistent
    assert stats["sd_bp"] == pytest.approx(stats["symbol_sd_median_bp"], rel=0.05)


# --- the end-to-end report ---------------------------------------------------------------------

SYMBOLS = ("AAA", "BBB", "FROZEN")
HOURS = 24 * 20


def _write(root, name, df, **keys) -> None:
    path = root.joinpath(name, *(f"{k}={v}" for k, v in keys.items()), "part.parquet")
    path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(path)


def _mini_root(root) -> None:
    """Three pairs over 20 days: two live, one with a frozen Hyperliquid mark."""
    hours = [T0 + h * H for h in range(HOURS)]
    panel = pl.DataFrame(
        {"symbol": [s for s in SYMBOLS for _ in hours],
         "lighter_symbol": [s for s in SYMBOLS for _ in hours],
         "hour": list(hours) * len(SYMBOLS),
         "pair_age_hours": [4000 + h for _ in SYMBOLS for h in range(HOURS)],
         "size_multiplier": [1] * (HOURS * len(SYMBOLS)),
         "lighter_size_multiplier": [1] * (HOURS * len(SYMBOLS)),
         "hl_rank": [5] * (HOURS * len(SYMBOLS)),
         "rank_unavailable": [False] * (HOURS * len(SYMBOLS))},
        schema_overrides={"hour": pl.Datetime("ms")})
    _write(root, "panel", panel, view="cross_venue")

    for day in range(HOURS // 24):
        rows = []
        for h in range(24):
            hour = day * 24 + h
            for minute in range(0, 60, 15):
                for symbol in SYMBOLS:
                    px = (0.12555 if symbol == "FROZEN"
                          else 100.0 + math.sin(hour / 5 + len(symbol)) + minute / 600)
                    rows.append((T0 + hour * H + timedelta(minutes=minute), symbol, px))
        _write(root, "hl_asset_ctxs", pl.DataFrame(
            {"time": [r[0] for r in rows], "coin": [r[1] for r in rows],
             "mark_px": [r[2] for r in rows]},
            schema={"time": pl.Datetime("ms"), "coin": pl.String, "mark_px": pl.Float64}),
            date=(T0 + day * 24 * H).strftime("%Y-%m-%d"))

    for i, symbol in enumerate(SYMBOLS):
        _write(root, "lighter_mark_candles", pl.DataFrame(
            {"symbol": [symbol] * HOURS, "time": hours,
             "close": [(0.1256 if symbol == "FROZEN"
                        else 100.0 + math.sin(h / 5 + len(symbol)) + math.cos(h / 3) / 50)
                       for h in range(HOURS)]},
            schema={"symbol": pl.String, "time": pl.Datetime("ms"), "close": pl.Float64}),
            market_id=i)
        _write(root, "hl_funding", pl.DataFrame(
            {"coin": [symbol] * HOURS, "settle_time": hours,
             "signed_rate_fraction": [((i * 7 + h * 13) % 21 - 10) / 1e5 for h in range(HOURS)]},
            schema={"coin": pl.String, "settle_time": pl.Datetime("ms"),
                    "signed_rate_fraction": pl.Float64}), coin=symbol)
        _write(root, "lighter_funding", pl.DataFrame(
            {"symbol": [symbol] * HOURS, "settle_time": hours,
             "signed_rate_fraction": [((i * 11 + h * 5) % 19 - 9) / 1e5 for h in range(HOURS)]},
            schema={"symbol": pl.String, "settle_time": pl.Datetime("ms"),
                    "signed_rate_fraction": pl.Float64}), market_id=i)


@pytest.fixture
def report(tmp_path, monkeypatch) -> str:
    monkeypatch.setenv("FUNDR_PHASE2_DATA", str(tmp_path))
    _mini_root(tmp_path)
    out = tmp_path / "qa" / "basis_drift.md"
    assert mb.main(["--out", str(out), "--min-level-lag1", "0.0"]) == 0
    return out.read_text()


def test_the_report_says_the_number_is_mark_based_and_not_realizable_pnl(report):
    low = report.lower()
    assert "mark" in low and "traded" in low
    # The one sentence this task exists to protect: these are marks off two different venue
    # references, and the realizable figure needs traded prices, which are not on disk.
    assert "not on disk" in low
    assert "oracle" in low and "index" in low
    assert "p&l" in low


def test_the_report_labels_the_circulating_carry_figures_as_selective_entry(report):
    assert "selective-entry" in report.lower()
    for recorded in ("6.21", "31.33", "18.91"):
        assert recorded in report
    for unconditional in ("1.03", "3.70", "9.78"):
        assert unconditional in report


def test_the_report_carries_the_alignment_proof_and_the_discarded_artifact(report):
    low = report.lower()
    assert "hour-median" in low and "artifact" in low
    assert "hour-close against hour-close" in low


def test_the_report_states_both_the_filtered_and_the_unfiltered_persistence(report):
    low = report.lower()
    assert "unfiltered" in low and "frozen" in low
    assert "lag-1" in low


def test_the_report_carries_the_age_table_and_the_majors_row(report):
    for bucket in ("week 1", "weeks 2-4", "months 1-3", "3 months+"):
        assert bucket in report
    assert "BTC/ETH/SOL" in report


def test_the_report_covers_every_horizon_the_plan_asks_for(report):
    for horizon in mb.HORIZONS:
        assert f"| {horizon} " in report
