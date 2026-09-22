"""Task 3's tests: the cumulative-level targets, their cross-sectional rank, and the benchmark.

The fixture is a ten-hour, four-pair miniature carrying every shape the real panel has and the
target construction has to survive: a single-hour hole inside a series, a series tail, an exact
tie between two symbols, a crossover where the future ranks the cross-section differently from
the present, and one alias pair whose Lighter leg lives under a different name. Small on purpose
-- every assertion below is a number a reader can work out by hand from the tables at the top.

Nothing here touches the network or the real `data/phase2/`: every test writes its own miniature
panel and funding partitions under `tmp_path` and points `FUNDR_PHASE2_DATA` at it.
"""
from datetime import datetime, timedelta

import polars as pl
import pytest

from scripts import build_targets

H = timedelta(hours=1)
T0 = datetime(2026, 1, 1)
HOURS = 10

#: Lighter sits at one constant baseline on every matched market, so the spread is Hyperliquid's
#: rate minus a constant and each test's arithmetic stays legible. Both legs are per-hour signed
#: fractions -- the common basis -- and neither is a multiple of Lighter's 1e-4 percent lattice,
#: which is what `assert_common_basis` looks for.
LIGHTER_RATE = 1.2e-5

#: Hyperliquid's rate, per matched symbol, hour by hour.
#: - AAA rises, DDD is AAA exactly (a tie in every hour),
#: - BBB falls through zero (the spread is signed, as funding is),
#: - kPEPE is flat and then jumps at hour 5, so the hour-4 cross-section ranks one way on the
#:   present and the other way on the future. That crossover is what proves `benchmark_rank` is
#:   a real second ranking and not a copy of `y_rank`.
HL_RATE = {"AAA": [(h + 1) * 1e-5 for h in range(HOURS)],
           "DDD": [(h + 1) * 1e-5 for h in range(HOURS)],
           "BBB": [-(h + 1) * 1e-5 for h in range(HOURS)],
           "kPEPE": [5e-6] * 5 + [1e-3] * 5}
PAIRS = {"AAA": "AAA", "DDD": "DDD", "BBB": "BBB", "kPEPE": "1000PEPE"}

#: BBB has no hour 5 on either venue -- the 213 single-hour holes in Hyperliquid's real history,
#: in miniature. A one-row `shift` would pair hour 4 with hour 6 and call it a lag.
HOLE = ("BBB", 5)

#: One venue exclusive per side, so the per-venue view cannot be mistaken for the cross-venue one.
HL_ONLY, LIGHTER_ONLY = "HLONLY", "LIONLY"
HL_ONLY_RATE, LIGHTER_ONLY_RATE = 5e-4, -3e-5


def _hours(symbol: str) -> list[int]:
    return [h for h in range(HOURS) if (symbol, h) != HOLE]


def spread(symbol: str, h: int) -> float:
    return HL_RATE[symbol][h] - LIGHTER_RATE


def _write(root, name, df, **keys) -> None:
    path = root.joinpath(name, *(f"{k}={v}" for k, v in keys.items()), "part.parquet")
    path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(path)


def _funding(root, name, symbol_col, partition, rates: dict[str, list[float]]) -> None:
    for i, (symbol, values) in enumerate(rates.items()):
        hours = _hours(symbol)
        _write(root, name, pl.DataFrame(
            {symbol_col: [symbol] * len(hours),
             "settle_time": [T0 + h * H for h in hours],
             "signed_rate_fraction": [values[h] for h in hours]},
            schema={symbol_col: pl.String, "settle_time": pl.Datetime("ms"),
                    "signed_rate_fraction": pl.Float64}),
            **{partition: symbol if partition == "coin" else i})


def _panel(root) -> None:
    cross = [("cross_venue", "cross", hl, li, h)
             for hl, li in PAIRS.items() for h in _hours(hl)]
    per_venue = ([("per_venue", "hl", hl, None, h)
                  for hl in list(HL_RATE) + [HL_ONLY] for h in _hours(hl)]
                 + [("per_venue", "lighter", li, li, h)
                    for li in list(PAIRS.values()) + [LIGHTER_ONLY] for h in _hours(li)])
    for view, rows in (("cross_venue", cross), ("per_venue", per_venue)):
        _write(root, "panel", pl.DataFrame(
            {"view": [r[0] for r in rows], "venue": [r[1] for r in rows],
             "symbol": [r[2] for r in rows], "lighter_symbol": [r[3] for r in rows],
             "hour": [T0 + r[4] * H for r in rows]},
            schema={"view": pl.String, "venue": pl.String, "symbol": pl.String,
                    "lighter_symbol": pl.String, "hour": pl.Datetime("ms")}), view=view)


def _build_fixture(root, *, lighter_rates=None) -> None:
    lighter = lighter_rates if lighter_rates is not None else {
        **{li: [LIGHTER_RATE] * HOURS for li in PAIRS.values()},
        LIGHTER_ONLY: [LIGHTER_ONLY_RATE] * HOURS}
    _funding(root, "hl_funding", "coin", "coin",
             {**HL_RATE, HL_ONLY: [HL_ONLY_RATE] * HOURS})
    _funding(root, "lighter_funding", "symbol", "market_id", lighter)
    _panel(root)


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNDR_PHASE2_DATA", str(tmp_path))
    _build_fixture(tmp_path)
    return tmp_path


@pytest.fixture
def built(data_root):
    return build_targets.build(horizons=(1, 3))


def _at(frame: pl.DataFrame, symbol: str, hour: int, **where) -> dict:
    rows = frame.filter((pl.col("symbol") == symbol) & (pl.col("hour") == T0 + hour * H))
    for col, value in where.items():
        rows = rows.filter(pl.col(col) == value)
    return rows.to_dicts()[0]


def _hour(frame: pl.DataFrame, hour: int, **where) -> pl.DataFrame:
    rows = frame.filter(pl.col("hour") == T0 + hour * H)
    for col, value in where.items():
        rows = rows.filter(pl.col(col) == value)
    return rows.sort("symbol")


# --- y_cum_level: the level over the hold, never a change and never imputed ------------------

def test_y_cum_level_at_h1_is_next_hours_spread(built):
    """H = 1 is the design's original horizon, kept for comparison. It must be exactly S_{t+1}."""
    cross = built["cross_venue", 1]
    for hour in range(HOURS - 1):
        assert _at(cross, "AAA", hour)["y_cum_level"] == pytest.approx(spread("AAA", hour + 1))
    assert _at(cross, "AAA", 0)["spread"] == pytest.approx(spread("AAA", 0))


def test_y_cum_level_sums_the_hold_rather_than_differencing_it(built):
    """The target is Sigma_{h=1..H} S_{t+h} -- what the position is paid, not how S moved."""
    row = _at(built["cross_venue", 3], "AAA", 2)
    assert row["n_hours_used"] == 3
    assert row["y_cum_level"] == pytest.approx(sum(spread("AAA", h) for h in (3, 4, 5)))


def test_a_hole_shortens_the_window_and_is_never_filled_in(built):
    """BBB has no hour 5. The window over hours 4..6 has two hours in it, not three, and the
    missing hour is counted rather than substituted by whatever row comes next."""
    row = _at(built["cross_venue", 3], "BBB", 3)
    assert row["n_hours_used"] == 2
    assert row["y_cum_level"] == pytest.approx(spread("BBB", 4) + spread("BBB", 6))


def test_a_series_tail_is_short_data_not_missing_data(built):
    """The last hours of a series legitimately have fewer forward hours. They stay, with the
    count on the row, for Phase 6 to decide about."""
    cross = built["cross_venue", 3]
    assert _at(cross, "AAA", HOURS - 3)["n_hours_used"] == 2
    assert _at(cross, "AAA", HOURS - 2)["n_hours_used"] == 1
    assert _at(cross, "AAA", HOURS - 2)["y_cum_level"] == pytest.approx(spread("AAA", HOURS - 1))
    assert set(cross["symbol"]) == set(PAIRS)


def test_no_forward_hour_at_all_gives_a_null_target_not_a_zero(built):
    """`sum_horizontal` over an all-null window returns 0.0, which would read as "the hold paid
    nothing" instead of "there is no hold here"."""
    last = _at(built["cross_venue", 1], "AAA", HOURS - 1)
    assert last["n_hours_used"] == 0
    assert last["y_cum_level"] is None
    beside_the_hole = _at(built["cross_venue", 1], "BBB", 4)
    assert beside_the_hole["n_hours_used"] == 0
    assert beside_the_hole["y_cum_level"] is None


def test_n_hours_used_never_exceeds_the_horizon(built):
    for (_, h), frame in built.items():
        assert frame["n_hours_used"].max() <= h
        assert frame["n_hours_used"].min() >= 0


# --- y_rank: dense, within the hour, ties explicit -------------------------------------------

def test_y_rank_is_dense_within_the_hour_and_ties_share_a_rank(built):
    """AAA and DDD carry identical rates, so their targets tie exactly. A dense rank gives them
    the same number and does NOT leave a gap behind them -- the tied zeros the 40.27% atom
    produces on real data are ties, which is the point of ranking at all."""
    hour = _hour(built["cross_venue", 1], 0)
    ranks = dict(zip(hour["symbol"], hour["y_rank"]))
    assert ranks["AAA"] == ranks["DDD"] == 1
    assert ranks["kPEPE"] == 2
    assert ranks["BBB"] == 3
    assert sorted(set(hour["y_rank"])) == [1, 2, 3]


def test_y_rank_is_taken_inside_the_hour_not_pooled_over_the_panel(built):
    """Every hour's cross-section starts again at 1, because Phase 9 chooses within an hour."""
    cross = built["cross_venue", 1]
    for hour in range(HOURS - 1):
        assert _hour(cross, hour)["y_rank"].min() == 1


def test_a_row_with_no_target_is_not_ranked_and_does_not_consume_a_rank(built):
    """BBB at hour 4 sits beside the hole and has no target. It must not be ranked last -- that
    would be an imputation with a different name."""
    hour = _hour(built["cross_venue", 1], 4)
    ranks = dict(zip(hour["symbol"], hour["y_rank"]))
    assert ranks["BBB"] is None
    assert sorted(r for r in ranks.values() if r is not None) == [1, 2, 2]
    assert _at(built["cross_venue", 1], "BBB", 4)["n_ranked_this_hour"] == 3


def test_the_cross_section_width_travels_with_every_rank(built):
    """A rank of 3 means nothing without the denominator: the real panel runs from 14 live pairs
    to 101, and Phase 3 caveat 8 forbids a cross-sectional statistic without the width."""
    assert _at(built["cross_venue", 1], "AAA", 0)["n_ranked_this_hour"] == 4
    assert _at(built["cross_venue", 1], "AAA", HOURS - 1)["n_ranked_this_hour"] == 0


# --- the benchmark, which D1 makes mandatory -------------------------------------------------

def test_the_no_change_benchmark_is_present_on_every_row(built):
    """A level target scores ~0.79 R2 from persistence alone, so the benchmark ships with the
    target rather than being remembered later."""
    for frame in built.values():
        assert {"benchmark_cum_level", "benchmark_rank"} <= set(frame.columns)


def test_the_no_change_benchmark_repeats_todays_level_for_the_hours_actually_held(built):
    """No-change over a three-hour hold is 3 x S_t -- but only two of BBB's three hours exist,
    so its benchmark is 2 x S_t. Scoring a two-hour sum against a three-hour prediction would
    hand the model a free win on every short window."""
    row = _at(built["cross_venue", 3], "AAA", 2)
    assert row["benchmark_cum_level"] == pytest.approx(3 * spread("AAA", 2))
    hole = _at(built["cross_venue", 3], "BBB", 3)
    assert hole["n_hours_used"] == 2
    assert hole["benchmark_cum_level"] == pytest.approx(2 * spread("BBB", 3))
    assert _at(built["cross_venue", 3], "AAA", HOURS - 1)["benchmark_cum_level"] is None


def test_the_benchmark_rank_ranks_by_todays_spread_and_is_not_a_copy_of_y_rank(built):
    """kPEPE's spread jumps at hour 5. Ranked on the present it sits second at hour 4; ranked on
    what the next hour actually pays it sits first. A model that cannot beat the left-hand column
    has demonstrated persistence, not forecasting."""
    hour = _hour(built["cross_venue", 1], 4)
    benchmark = dict(zip(hour["symbol"], hour["benchmark_rank"]))
    realised = dict(zip(hour["symbol"], hour["y_rank"]))
    assert benchmark["kPEPE"] == 2 and benchmark["AAA"] == 1
    assert realised["kPEPE"] == 1 and realised["AAA"] == 2
    assert benchmark["BBB"] is None      # no target, so it is in neither ranking


def test_the_benchmark_rank_is_dense_over_the_same_rows_as_y_rank(built):
    for frame in built.values():
        assert frame.filter(pl.col("y_rank").is_null()
                            != pl.col("benchmark_rank").is_null()).is_empty()


# --- the per-venue view is two cross-sections, not one ---------------------------------------

def test_per_venue_carries_both_venues_and_their_exclusives(built):
    per_venue = built["per_venue", 1]
    assert set(per_venue.filter(pl.col("venue") == "hl")["symbol"]) == set(HL_RATE) | {HL_ONLY}
    assert (set(per_venue.filter(pl.col("venue") == "lighter")["symbol"])
            == set(PAIRS.values()) | {LIGHTER_ONLY})


def test_a_per_venue_window_never_crosses_a_venue(built):
    """AAA exists on both venues with different rates. A window keyed on the symbol alone would
    sum one venue's hour onto the other's."""
    row = _at(built["per_venue", 1], "AAA", 0, venue="lighter")
    assert row["rate"] == pytest.approx(LIGHTER_RATE)
    assert row["y_cum_level"] == pytest.approx(LIGHTER_RATE)
    assert _at(built["per_venue", 1], "AAA", 0, venue="hl")["y_cum_level"] == pytest.approx(
        HL_RATE["AAA"][1])


def test_per_venue_ranks_inside_a_venue_not_across_both(built):
    """Target A is per venue. Ranking Hyperliquid's rates against Lighter's would make the rank
    a statement about the venue, not about the symbol."""
    hl = _hour(built["per_venue", 1], 0, venue="hl")
    lighter = _hour(built["per_venue", 1], 0, venue="lighter")
    assert hl["n_ranked_this_hour"].unique().to_list() == [5]
    assert lighter["n_ranked_this_hour"].unique().to_list() == [5]
    assert dict(zip(hl["symbol"], hl["y_rank"]))[HL_ONLY] == 1
    # Four matched markets sit on one baseline, so they tie; the exclusive is below them.
    assert dict(zip(lighter["symbol"], lighter["y_rank"])) == {
        "AAA": 1, "BBB": 1, "DDD": 1, "1000PEPE": 1, LIGHTER_ONLY: 2}


# --- the basis, which is the silent bug this phase was warned about --------------------------

def test_the_alias_pair_reads_its_lighter_leg_under_the_lighter_name(built):
    """`kPEPE` on Hyperliquid is `1000PEPE` on Lighter. Joining on the canonical name would drop
    the pair entirely -- and a dropped pair looks exactly like a pair that never listed."""
    assert _at(built["cross_venue", 1], "kPEPE", 0)["spread"] == pytest.approx(spread("kPEPE", 0))


def test_lighter_raw_percent_stops_the_build_rather_than_scaling_the_spread(tmp_path,
                                                                           monkeypatch):
    """Phase 1 named this the single most likely silent bug in Phase 4: used as-is Lighter's
    `rate` is 100x too large and unsigned. `assert_common_basis` must fail loudly here."""
    monkeypatch.setenv("FUNDR_PHASE2_DATA", str(tmp_path))
    raw_percent = {**{li: [0.0012] * HOURS for li in PAIRS.values()},
                   LIGHTER_ONLY: [0.0004] * HOURS}
    _build_fixture(tmp_path, lighter_rates=raw_percent)
    with pytest.raises(ValueError, match="percent"):
        build_targets.build(horizons=(1,))


# --- what lands on disk ----------------------------------------------------------------------

def test_every_view_and_horizon_is_written_with_provenance(built, data_root):
    build_targets.write(built)
    for view in ("cross_venue", "per_venue"):
        for h in (1, 3):
            path = data_root / "targets" / f"view={view}" / f"horizon={h}" / "part.parquet"
            assert path.exists()
            written = pl.read_parquet(path)
            assert written.height == built[view, h].height
            assert written["_source"].unique().to_list() == ["phase4:build_targets"]
            assert written["_fetched_at_ms"].null_count() == 0


def test_the_written_schema_is_the_documented_one(built):
    cross, per_venue = built["cross_venue", 1], built["per_venue", 1]
    assert cross.columns == ["symbol", "hour", "spread", "y_cum_level", "n_hours_used",
                             "y_rank", "n_ranked_this_hour", "benchmark_cum_level",
                             "benchmark_rank"]
    assert per_venue.columns == ["venue", "symbol", "hour", "rate", "y_cum_level",
                                 "n_hours_used", "y_rank", "n_ranked_this_hour",
                                 "benchmark_cum_level", "benchmark_rank"]


def test_the_report_states_both_gates(built):
    lines = "\n".join(build_targets.report(built))
    assert "exact-zero share" in lines and "lag-1" in lines
