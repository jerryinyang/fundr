"""Task 5's tests: the size-rule measurement, its clustering, its grid and its report.

The statistical machinery is tested on hand-built frames where the right answer is known in
advance -- a correlation whose observations are copied within an hour must not get more precise
as the copies multiply -- and the end-to-end path is tested on the same 15-market miniature
Task 3 uses, so the report's shape is exercised without touching the real panel.
"""
from datetime import datetime, timedelta

import polars as pl
import pytest

from scripts import build_universe, universe_diagnostics as diag

H = timedelta(hours=1)
T0 = datetime(2026, 1, 1)
HOURS = 120                     # 2026-01-01 .. 2026-01-05
START, END = T0 + 24 * H, T0 + (HOURS - 1) * H
LATE_START = T0 + 48 * H

HL_SIZE = {"BTC": 9e9, **{f"F{i}": 1e6 * (10 - i) for i in range(10)},
           "AAA": 5.0, "kPEPE": 4.0, "NOT": 3.0, "HLONLY": 2.0}
LI_SIZE = {"BTC": 9e9, **{f"G{i}": 1e6 * (10 - i) for i in range(10)},
           "AAA": 5.0, "1000PEPE": 4.0, "1000NOT": 3.0, "LIONLY": 2.0}
PAIRS = {"BTC": "BTC", "AAA": "AAA", "kPEPE": "1000PEPE", "NOT": "1000NOT"}


# --- the clustered standard error, which is the point of output 1 ----------------------------

def _clustered_frame(hours: int, copies: int) -> pl.DataFrame:
    """`copies` identical rows in each of `hours` hours.

    Identical copies carry no new information, so an honest standard error must not shrink as
    `copies` grows. This is the 978,572-pair-hours-are-not-independent problem in miniature."""
    rows = []
    for h in range(hours):
        x = ((h * 37) % 101) / 101 - 0.5
        y = 0.6 * x + (((h * 53) % 97) / 97 - 0.5) * 0.4
        rows += [{"hour": T0 + h * H, "x": x, "y": y}] * copies
    return pl.DataFrame(rows)


def test_clustering_is_on_the_hour_so_copied_rows_buy_no_precision():
    one = diag.clustered_corr(_clustered_frame(400, 1), "x", "y")
    five = diag.clustered_corr(_clustered_frame(400, 5), "x", "y")
    assert one.n == 400 and five.n == 2000
    assert one.clusters == five.clusters == 400
    assert five.rho == pytest.approx(one.rho, abs=1e-9)
    # The clustered error is the same on 2,000 copied rows as on the 400 real ones ...
    assert five.se == pytest.approx(one.se, rel=0.05)
    # ... while the textbook error claims sqrt(5)x the precision it has.
    assert five.se_iid == pytest.approx(one.se_iid / 5 ** 0.5, rel=0.05)
    assert five.se > 1.8 * five.se_iid


def test_the_cluster_column_is_the_hour_not_the_symbol():
    # Guard against clustering on whatever column happens to be first: giving every row its own
    # hour must collapse the clustered error back onto the iid one.
    frame = _clustered_frame(400, 5).with_columns(
        pl.int_range(pl.len()).cast(pl.Int64).alias("i"))
    frame = frame.with_columns((pl.lit(T0) + pl.duration(hours=pl.col("i"))).alias("hour"))
    fit = diag.clustered_corr(frame, "x", "y")
    assert fit.clusters == 2000
    assert fit.se == pytest.approx(fit.se_iid, rel=0.25)


def test_the_gap_between_two_cohorts_carries_its_own_clustered_error():
    frame = _clustered_frame(400, 4).with_columns(
        (pl.int_range(pl.len()) % 4 < 2).alias("cohort"))
    gap = diag.clustered_gap(frame.filter(pl.col("cohort")),
                             frame.filter(~pl.col("cohort")), "x", "y")
    # Both cohorts are drawn from the same copied rows, so the gap is exactly zero and its
    # error is finite -- an error computed as if the two were independent samples would not be.
    assert gap.value == pytest.approx(0.0, abs=1e-9)
    assert gap.se >= 0.0


# --- lags are explicit hour differences, never `shift` ---------------------------------------

def test_a_lag_never_bridges_a_hole_in_the_hour_grid():
    frame = pl.DataFrame({"symbol": ["A"] * 5,
                          "hour": [T0 + h * H for h in (0, 1, 2, 4, 5)],
                          "spread_bp": [1.0, 2.0, 3.0, 4.0, 5.0]})
    lagged = diag.with_lags(frame)
    kept = dict(zip(lagged["hour"], lagged["spread_bp"]))
    # Hour 1 is the only hour with both a real neighbour before and a real neighbour after,
    # because hour 3 is missing. A `shift` would have paired hour 2 with hour 4.
    assert set(kept) == {T0 + H}
    assert lagged["s_next"].to_list() == [3.0] and lagged["s_prev"].to_list() == [1.0]


# --- the decile split ------------------------------------------------------------------------

def test_deciles_run_from_smallest_to_largest_and_are_cut_within_the_hour():
    frame = pl.DataFrame({"hour": [T0] * 20 + [T0 + H] * 10,
                          "hl_rank": list(range(1, 21)) + list(range(1, 11))})
    out = diag.with_deciles(frame)
    first = out.filter(pl.col("hour") == T0).sort("hl_rank")
    # D10 holds the largest markets, which are the smallest ranks. This is the convention
    # `decisions.md`'s cost table uses (D10 = 1.07 bp = the cheapest = the biggest names).
    assert first["decile"].to_list() == sorted(first["decile"].to_list(), reverse=True)
    assert first["decile"][0] == 10 and first["decile"][-1] == 1
    assert set(out["decile"].unique()) == set(range(1, 11))
    # Cut inside the hour: the second hour has 10 rows and still spans all ten deciles.
    assert out.filter(pl.col("hour") == T0 + H)["decile"].n_unique() == 10


def test_unrankable_rows_are_dropped_rather_than_tested_against_ten():
    frame = pl.DataFrame({"symbol": ["A", "B", "C"], "hour": [T0] * 3,
                          "hl_rank": [1, None, 30],
                          "rank_unavailable": [False, True, False]})
    out = diag.usable(frame)
    assert out["symbol"].to_list() == ["A", "C"]


# --- the knob grid ---------------------------------------------------------------------------

def test_the_grid_enumerates_exactly_twenty_four_cells():
    cells = diag.grid_cells(datetime(2025, 1, 17), datetime(2025, 7, 29))
    assert len(cells) == 24
    assert len({(c.pool, c.rebalance, c.start, c.lighter_leg) for c in cells}) == 24
    assert {c.pool for c in cells} == {"venue", "matched"}
    assert {c.rebalance for c in cells} == {"hourly", "daily", "monthly"}
    assert {c.start for c in cells} == {datetime(2025, 1, 17), datetime(2025, 7, 29)}
    assert {c.lighter_leg for c in cells} == {"column", "gate"}


def test_the_lighter_gate_removes_rows_and_the_lighter_column_does_not():
    frame = pl.DataFrame({"symbol": list("ABC"), "in_lighter_top10": [True, False, None]})
    assert diag.apply_lighter_leg(frame, "column").height == 3
    # Gating drops the Lighter top 10 and also the rows whose Lighter rank is unavailable:
    # `in_lighter_top10 is null` must never read as "not in the top 10".
    assert diag.apply_lighter_leg(frame, "gate")["symbol"].to_list() == ["B"]


# --- the end-to-end report --------------------------------------------------------------------

def _write(root, name, df, **keys) -> None:
    path = root.joinpath(name, *(f"{k}={v}" for k, v in keys.items()), "part.parquet")
    path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(path)


def _rate(symbol: str, h: int) -> float:
    """A persistent, symbol-specific funding rate, so autocorrelation is defined and non-zero."""
    seed = sum(ord(c) for c in symbol)
    return ((seed * 7 + h * 13) % 21 - 10) / 1e5


def _fixture(root) -> None:
    for symbol in HL_SIZE:
        hours = [T0 + h * H for h in range(HOURS)]
        _write(root, "hl_funding", pl.DataFrame(
            {"coin": [symbol] * len(hours), "settle_time": hours,
             "signed_rate_fraction": [_rate(symbol, h) for h in range(HOURS)]},
            schema={"coin": pl.String, "settle_time": pl.Datetime("ms"),
                    "signed_rate_fraction": pl.Float64}), coin=symbol)
    for i, symbol in enumerate(LI_SIZE):
        hours = [T0 + h * H for h in range(HOURS)]
        _write(root, "lighter_funding", pl.DataFrame(
            {"symbol": [symbol] * len(hours), "settle_time": hours,
             "signed_rate_fraction": [_rate(symbol + "L", h) for h in range(HOURS)]},
            schema={"symbol": pl.String, "settle_time": pl.Datetime("ms"),
                    "signed_rate_fraction": pl.Float64}), market_id=i)
    for day in range(HOURS // 24):
        rows = [(T0 + (day * 24 + h) * H, symbol, size, 100.0 + (h % 5) - (day % 3))
                for h in range(24) for symbol, size in HL_SIZE.items()]
        _write(root, "hl_asset_ctxs", pl.DataFrame(
            {"time": [r[0] for r in rows], "coin": [r[1] for r in rows],
             "open_interest": [r[2] for r in rows], "mark_px": [r[3] for r in rows],
             "impact_bid_px": [r[3] * 0.999 for r in rows],
             "impact_ask_px": [r[3] * 1.001 for r in rows]},
            schema={"time": pl.Datetime("ms"), "coin": pl.String, "open_interest": pl.Float64,
                    "mark_px": pl.Float64, "impact_bid_px": pl.Float64,
                    "impact_ask_px": pl.Float64}),
            date=(T0 + day * 24 * H).strftime("%Y-%m-%d"))
    for i, (symbol, size) in enumerate(LI_SIZE.items()):
        times = [T0 - 24 * H + h * H for h in range(24 + HOURS)]
        _write(root, "lighter_candles", pl.DataFrame(
            {"symbol": [symbol] * len(times), "time": times,
             "quote_volume": [size / 24] * len(times)},
            schema={"symbol": pl.String, "time": pl.Datetime("ms"),
                    "quote_volume": pl.Float64}), market_id=i)
        _write(root, "lighter_mark_candles", pl.DataFrame(
            {"symbol": [symbol] * len(times), "time": times,
             "close": [100.0 + (h % 7) * 0.01 for h in range(len(times))]},
            schema={"symbol": pl.String, "time": pl.Datetime("ms"), "close": pl.Float64}),
            market_id=i)


@pytest.fixture
def built_root(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNDR_PHASE2_DATA", str(tmp_path))
    _fixture(tmp_path)
    build_universe.main(["--start", START.isoformat(), "--end", END.isoformat()])
    return tmp_path


@pytest.fixture
def report(built_root) -> str:
    assert diag.main(["--start", START.isoformat(), "--late-start", LATE_START.isoformat(),
                      "--end", END.isoformat()]) == 0
    return (built_root / "qa" / "universe_diagnostics.md").read_text()


def test_the_report_lands_where_the_plan_says_it_does(report, built_root):
    assert (built_root / "qa" / "universe_diagnostics.md").exists()
    assert report.startswith("# Universe diagnostics")


def test_the_report_carries_all_three_outputs_the_plan_asks_for(report):
    for heading in ("Forecastability by size decile", "knob grid",
                    "basis risk and cost", "pair age"):
        assert heading in report, heading


def test_the_report_states_where_a_break_is_and_which_deciles_are_the_extremes(report):
    assert "strongest" in report and "weakest" in report
    assert "break" in report


def test_the_report_states_the_grid_dispersion_against_the_decile_spread(report):
    assert "dispersion" in report and "24 cells" in report


def test_the_report_reports_measured_values_not_pass_fail_verdicts(report):
    # The plan's instruction, made testable: this measurement decides F1, and a verdict word
    # would let a reader take the conclusion without the number it rests on.
    for verdict in ("PASS", "FAIL", "pass/fail", "verdict", "✅", "❌"):
        assert verdict not in report, verdict


def test_the_basis_and_cost_columns_are_on_both_the_decile_and_the_age_split(report):
    for column in ("24h basis drift sd", "round turn", "24h carry", "net"):
        assert report.count(column) >= 2, column


def test_the_report_names_the_decile_convention_so_d10_is_not_read_as_the_smallest(report):
    assert "D10" in report and "largest" in report
