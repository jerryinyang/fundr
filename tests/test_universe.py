from datetime import datetime, timedelta

import polars as pl
import pytest

from fundr import dataset, universe

H = timedelta(hours=1)
T0 = datetime(2026, 1, 1)


def _size(rows, measure=universe.HL_MEASURE) -> pl.DataFrame:
    """`rows` is (symbol, hour offset from T0, value); a None value is a present-but-unusable row."""
    return pl.DataFrame(
        {"symbol": [r[0] for r in rows],
         "hour": [T0 + r[1] * H for r in rows],
         measure: [r[2] for r in rows]},
        schema={"symbol": pl.String, "hour": pl.Datetime("ms"), measure: pl.Float64})


def _at(out: pl.DataFrame, symbol: str, offset: int) -> dict:
    return out.filter((pl.col("symbol") == symbol)
                      & (pl.col("hour") == T0 + offset * H)).to_dicts()[0]


def _write(name: str, df: pl.DataFrame, root, **keys) -> None:
    path = root.joinpath(name, *(f"{k}={v}" for k, v in keys.items()), "part.parquet")
    path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(path)


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNDR_PHASE2_DATA", str(tmp_path))
    assert dataset.root() == tmp_path
    return tmp_path


# --- rank: dense, point-in-time, fail-closed ------------------------------------------------

def test_rank_is_dense_and_point_in_time():
    # B overtakes A at hour 1 and falls back at hour 2. Each hour must be ranked on its OWN
    # size, so the verdict flips with it rather than being smeared across the window.
    out = universe.rank(_size([("A", 0, 3.0), ("B", 0, 1.0),
                               ("A", 1, 1.0), ("B", 1, 3.0),
                               ("A", 2, 3.0), ("B", 2, 1.0)]),
                        basis="hourly", pool="venue")
    assert out.height == 6                                  # dense: every symbol, every hour
    assert [_at(out, "A", h)["size_rank"] for h in (0, 1, 2)] == [1, 2, 1]
    assert [_at(out, "B", h)["size_rank"] for h in (0, 1, 2)] == [2, 1, 2]
    assert not out["rank_is_carried_forward"].any()
    assert not out["rank_unavailable"].any()
    assert out["rank_stale_hours"].to_list() == [0] * 6


def test_a_three_hour_gap_carries_the_rank_forward_and_flags_all_three():
    rows = [("B", h, 1.0) for h in range(8)] + [("A", h, 3.0) for h in (0, 1, 2, 6, 7)]
    out = universe.rank(_size(rows), basis="hourly", pool="venue")
    for h in (3, 4, 5):
        row = _at(out, "A", h)
        assert row["size_rank"] == 1, h
        assert row["rank_is_carried_forward"] is True, h
        assert row["rank_unavailable"] is False, h
        assert row["rank_stale_hours"] == h - 2, h          # 1, 2, 3 hours old -- not just "True"
    assert _at(out, "A", 6)["rank_stale_hours"] == 0        # fresh again
    assert _at(out, "A", 6)["rank_is_carried_forward"] is False


def test_a_leading_gap_sets_rank_unavailable_and_not_a_rank():
    # A's first hour has no usable size, so there is no prior rank to carry. The whole point of
    # the rule: leaving this null would make `rank <= 10` false and ADMIT the market.
    out = universe.rank(_size([("A", 0, None), ("A", 1, 3.0), ("B", 0, 1.0), ("B", 1, 1.0)]),
                        basis="hourly", pool="venue")
    lead = _at(out, "A", 0)
    assert lead["rank_unavailable"] is True
    assert lead["size_rank"] is None
    assert lead["rank_is_carried_forward"] is False
    assert lead["rank_stale_hours"] is None                 # never had a rank to be stale from
    assert _at(out, "A", 1)["size_rank"] == 1 and _at(out, "A", 1)["rank_unavailable"] is False


def test_a_carry_older_than_a_day_is_unavailable_rather_than_a_rank():
    # The guard that would have caught the 200-hour Lighter candle holes: a boolean cannot tell a
    # one-hour carry from an eight-day one, so staleness is counted and capped.
    last = universe.MAX_STALE_HOURS + 6
    rows = [("B", h, 1.0) for h in range(last + 1)] + [("A", 0, 3.0), ("A", last, 3.0)]
    out = universe.rank(_size(rows), basis="hourly", pool="venue")
    edge = _at(out, "A", universe.MAX_STALE_HOURS)
    assert edge["rank_stale_hours"] == universe.MAX_STALE_HOURS
    assert edge["size_rank"] == 1 and edge["rank_unavailable"] is False
    beyond = _at(out, "A", universe.MAX_STALE_HOURS + 1)
    assert beyond["rank_stale_hours"] == universe.MAX_STALE_HOURS + 1
    assert beyond["rank_unavailable"] is True
    assert beyond["size_rank"] is None
    assert beyond["rank_is_carried_forward"] is False       # nothing is being carried any more
    assert _at(out, "A", last)["rank_unavailable"] is False


def test_rank_never_carries_across_symbols():
    out = universe.rank(_size([("A", 0, 3.0), ("B", 0, 1.0), ("B", 1, 1.0)]),
                        basis="hourly", pool="venue")
    assert _at(out, "A", 1)["rank_is_carried_forward"] is True
    assert _at(out, "B", 1)["rank_is_carried_forward"] is False


# --- rank: basis and pool ------------------------------------------------------------------

def test_daily_basis_uses_the_previous_day_never_the_current_one():
    # Day 0 A is the giant; day 1 the sizes swap. Under a daily basis, day 1 must still rank A
    # first -- ranking it on its own day's size would be reading the future.
    rows = [("A", h, 3.0) for h in range(24)] + [("B", h, 1.0) for h in range(24)] \
        + [("A", 24 + h, 1.0) for h in range(24)] + [("B", 24 + h, 3.0) for h in range(24)] \
        + [("A", 48, 1.0), ("B", 48, 3.0)]
    out = universe.rank(_size(rows), basis="daily", pool="venue")
    assert _at(out, "A", 30)["size_rank"] == 1              # day 1 ranked on day 0
    assert _at(out, "B", 30)["size_rank"] == 2
    assert _at(out, "A", 48)["size_rank"] == 2              # day 2 ranked on day 1, now flipped
    assert _at(out, "B", 48)["size_rank"] == 1
    assert _at(out, "A", 30)["rank_is_carried_forward"] is False   # the basis is a lag, not a carry


def test_daily_basis_has_no_previous_day_on_the_first_day():
    rows = [("A", h, 3.0) for h in range(25)] + [("B", h, 1.0) for h in range(25)]
    out = universe.rank(_size(rows), basis="daily", pool="venue")
    assert _at(out, "A", 0)["rank_unavailable"] is True
    assert _at(out, "A", 0)["size_rank"] is None
    assert _at(out, "A", 24)["size_rank"] == 1


def test_monthly_basis_uses_the_previous_month():
    jan = [(s, h, v) for h in range(0, 24 * 31, 24) for s, v in (("A", 3.0), ("B", 1.0))]
    feb = [(s, 24 * 31, v) for s, v in (("A", 1.0), ("B", 3.0))]
    out = universe.rank(_size(jan + feb), basis="monthly", pool="venue")
    assert _at(out, "A", 24 * 31)["size_rank"] == 1         # February ranked on January
    assert _at(out, "B", 24 * 31)["size_rank"] == 2


def test_pool_restricts_who_competes_for_a_rank():
    rows = [("BTC", 0, 9.0), ("A", 0, 3.0), ("B", 0, 1.0)]
    venue = universe.rank(_size(rows), basis="hourly", pool="venue")
    assert _at(venue, "A", 0)["size_rank"] == 2
    matched = universe.rank(_size(rows), basis="hourly", pool=["A", "B"])
    assert _at(matched, "A", 0)["size_rank"] == 1           # BTC is not in the pool
    assert set(matched["symbol"]) == {"A", "B"}


def test_rank_rejects_an_unknown_basis():
    with pytest.raises(ValueError):
        universe.rank(_size([("A", 0, 1.0)]), basis="weekly", pool="venue")


# --- hl_size -------------------------------------------------------------------------------

def _ctxs(rows) -> pl.DataFrame:
    return pl.DataFrame(
        {"time": [r[0] for r in rows], "coin": [r[1] for r in rows],
         "open_interest": [r[2] for r in rows], "mark_px": [r[3] for r in rows]},
        schema={"time": pl.Datetime("ms"), "coin": pl.String,
                "open_interest": pl.Float64, "mark_px": pl.Float64})


def test_notional_not_base_units_decides_the_order(data_root):
    # 1,000,000 PEPE at $0.001 is $1,000; 10 BTC at $100,000 is $1,000,000. Ranking base units
    # would call PEPE the giant.
    rows = [(T0 + m * timedelta(minutes=1), c, oi, px)
            for m in range(3) for c, oi, px in (("PEPE", 1e6, 0.001), ("BTC", 10.0, 1e5))]
    _write("hl_asset_ctxs", _ctxs(rows), data_root, date="2026-01-01")
    size = universe.hl_size(T0, T0)
    assert dict(zip(size["symbol"], size[universe.HL_MEASURE])) == {"PEPE": 1e3, "BTC": 1e6}
    out = universe.rank(size, basis="hourly", pool="venue")
    assert _at(out, "BTC", 0)["size_rank"] == 1
    assert _at(out, "PEPE", 0)["size_rank"] == 2


def test_hl_size_takes_the_hours_median_not_a_single_minute(data_root):
    # A single minute can be a stale print or a spike; the median of the hour is the level.
    rows = [(T0 + m * timedelta(minutes=1), "A", oi, 1.0)
            for m, oi in enumerate([10.0, 10.0, 10.0, 1e9])]
    _write("hl_asset_ctxs", _ctxs(rows), data_root, date="2026-01-01")
    assert universe.hl_size(T0, T0)[universe.HL_MEASURE].to_list() == [10.0]


def test_hl_size_reads_only_the_days_the_window_spans(data_root):
    # 275M rows across 1,218 daily files: a window that materialises days it does not span is a
    # bug whether or not the answer is right.
    for day, t in (("2026-01-01", T0), ("2026-01-02", T0 + 24 * H)):
        _write("hl_asset_ctxs", _ctxs([(t, "A", 1.0, 1.0)]), data_root, date=day)
    assert universe.hl_size(T0, T0)["hour"].to_list() == [T0]
    assert universe.hl_size(T0, T0 + 24 * H).height == 2


def test_hl_size_is_empty_when_no_day_partition_is_in_range(data_root):
    _write("hl_asset_ctxs", _ctxs([(T0, "A", 1.0, 1.0)]), data_root, date="2026-01-01")
    empty = universe.hl_size(T0 + 240 * H, T0 + 264 * H)
    assert empty.is_empty() and empty.columns == ["hour", "symbol", universe.HL_MEASURE]


# --- lighter_size --------------------------------------------------------------------------

def _candles(symbol, first, volumes) -> pl.DataFrame:
    return pl.DataFrame(
        {"symbol": [symbol] * len(volumes),
         "time": [first + i * H for i in range(len(volumes))],
         "quote_volume": [float(v) for v in volumes]},
        schema={"symbol": pl.String, "time": pl.Datetime("ms"), "quote_volume": pl.Float64})


def test_lighter_size_sums_a_completed_trailing_day(data_root):
    _write("lighter_candles", _candles("A", T0, [1.0] * 48), data_root, market_id=1)
    size = universe.lighter_size(T0, T0 + 47 * H)
    by_hour = dict(zip(size["hour"], size[universe.LIGHTER_MEASURE]))
    # The candle stamped T covers [T, T+1), so the first fully completed 24 bars label hour 24.
    assert min(by_hour) == T0 + 24 * H
    assert by_hour[T0 + 24 * H] == 24.0
    assert by_hour[T0 + 47 * H] == 24.0


def test_lighter_size_excludes_the_hour_it_labels(data_root):
    # The bar stamped T is still forming at T. If it leaked in, the value labelling hour 24 would
    # read 1023 instead of 24 and the rank would be reading the future.
    _write("lighter_candles", _candles("A", T0, [1.0] * 24 + [1000.0]), data_root, market_id=1)
    size = universe.lighter_size(T0, T0 + 24 * H)
    assert dict(zip(size["hour"], size[universe.LIGHTER_MEASURE]))[T0 + 24 * H] == 24.0


def test_lighter_size_needs_a_full_window_so_a_short_history_is_not_ranked_small(data_root):
    # A partial window understates volume, which would rank a young market as tiny and quietly
    # admit it. No full day of bars, no size row -- `rank` then fails it closed.
    _write("lighter_candles", _candles("A", T0, [1.0] * 10), data_root, market_id=1)
    assert universe.lighter_size(T0, T0 + 10 * H).is_empty()


def test_lighter_size_uses_quote_volume_so_denomination_cannot_reorder_it(data_root):
    _write("lighter_candles", _candles("1000PEPE", T0, [1.0] * 25), data_root, market_id=1)
    _write("lighter_candles", _candles("BTC", T0, [2.0] * 25), data_root, market_id=2)
    out = universe.rank(universe.lighter_size(T0, T0 + 24 * H), basis="hourly", pool="venue")
    assert _at(out, "BTC", 24)["size_rank"] == 1
    assert _at(out, "1000PEPE", 24)["size_rank"] == 2


# --- giant_regressors ----------------------------------------------------------------------

def test_giant_regressors_is_one_row_per_hour_with_both_measures(data_root):
    hours = [T0, T0 + H]
    for g, rate in zip(universe.GIANTS, [0.1, 0.2, 0.3, 0.4, 0.5]):
        _write("hl_funding", pl.DataFrame(
            {"coin": [g] * 2, "settle_time": hours, "signed_rate_fraction": [rate, rate]},
            schema={"coin": pl.String, "settle_time": pl.Datetime("ms"),
                    "signed_rate_fraction": pl.Float64}), data_root, coin=g)
    _write("hl_asset_ctxs", _ctxs([(h, g, 2.0, 3.0) for h in hours for g in universe.GIANTS]
                                  + [(h, "OTHER", 9.0, 9.0) for h in hours]),
           data_root, date="2026-01-01")
    out = universe.giant_regressors(T0, T0 + H)
    assert out.height == 2 and out["hour"].to_list() == hours
    assert out["funding_BTC"].to_list() == [0.1, 0.1]
    assert out["oi_notional_HYPE"].to_list() == [6.0, 6.0]
    assert not any(c.endswith("_OTHER") for c in out.columns)   # giants only
