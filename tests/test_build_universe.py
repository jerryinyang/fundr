"""Task 3's tests: the panel spine, its two views, and the invariants that must survive them.

The fixture is a 72-hour, 15-market-per-venue miniature of the real thing: enough markets that
a top-10 boundary exists, enough hours that a daily rebalance has a previous day to rank on, and
one venue exclusive on each side so the two views cannot be confused for one another.
"""
from datetime import datetime, timedelta

import polars as pl
import pytest

from fundr import alias
from scripts import build_universe

H = timedelta(hours=1)
T0 = datetime(2026, 1, 1)
HOURS = 72                      # 2026-01-01 .. 2026-01-03
START, END = T0 + 24 * H, T0 + (HOURS - 1) * H

# BTC dwarfs everything; the ten fillers occupy ranks 2..11; the four small names sit outside
# the top 10 on both venues. Without them every row would be inside the top 10 and the
# "nothing is dropped for being top-10" test would prove nothing. The two venues' fillers are
# deliberately named differently so they stay venue exclusives rather than matched pairs.
HL_SIZE = {"BTC": 9e9, **{f"F{i}": 1e6 * (10 - i) for i in range(10)},
           "AAA": 5.0, "kPEPE": 4.0, "NOT": 3.0, "HLONLY": 2.0}
LI_SIZE = {"BTC": 9e9, **{f"G{i}": 1e6 * (10 - i) for i in range(10)},
           "AAA": 5.0, "1000PEPE": 4.0, "1000NOT": 3.0, "LIONLY": 2.0}
# AAA lists late on Lighter, so the concurrent cross-section widens inside the window and the
# pair's age starts at its first CONCURRENT hour rather than at either venue's listing.
LI_FIRST = {"AAA": 30}
PAIRS = {"BTC": "BTC", "AAA": "AAA", "kPEPE": "1000PEPE", "NOT": "1000NOT"}


def _write(root, name, df, **keys) -> None:
    path = root.joinpath(name, *(f"{k}={v}" for k, v in keys.items()), "part.parquet")
    path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(path)


def _hours(first: int) -> list[datetime]:
    return [T0 + h * H for h in range(first, HOURS)]


def _build_fixture(root, *, hl_sizes=None) -> None:
    hl_sizes = HL_SIZE if hl_sizes is None else hl_sizes
    for symbol in HL_SIZE:
        hours = _hours(0)
        _write(root, "hl_funding", pl.DataFrame(
            {"coin": [symbol] * len(hours), "settle_time": hours,
             "signed_rate_fraction": [0.0] * len(hours)},
            schema={"coin": pl.String, "settle_time": pl.Datetime("ms"),
                    "signed_rate_fraction": pl.Float64}), coin=symbol)
    for i, symbol in enumerate(LI_SIZE):
        hours = _hours(LI_FIRST.get(symbol, 0))
        _write(root, "lighter_funding", pl.DataFrame(
            {"symbol": [symbol] * len(hours), "settle_time": hours,
             "signed_rate_fraction": [0.0] * len(hours)},
            schema={"symbol": pl.String, "settle_time": pl.Datetime("ms"),
                    "signed_rate_fraction": pl.Float64}), market_id=i)

    # One asset-ctx print per hour: `hl_size` takes the hour's median, and the median of one
    # value is that value, so the fixture stays small without changing what is measured.
    for day in range(HOURS // 24):
        rows = [(T0 + (day * 24 + h) * H, symbol, size, 1.0)
                for h in range(24) for symbol, size in hl_sizes.items()]
        _write(root, "hl_asset_ctxs", pl.DataFrame(
            {"time": [r[0] for r in rows], "coin": [r[1] for r in rows],
             "open_interest": [r[2] for r in rows], "mark_px": [r[3] for r in rows]},
            schema={"time": pl.Datetime("ms"), "coin": pl.String,
                    "open_interest": pl.Float64, "mark_px": pl.Float64}),
            date=(T0 + day * 24 * H).strftime("%Y-%m-%d"))

    # Candles run a day early: `lighter_size` refuses to emit a partial trailing window, so
    # without 24 completed bars the window's first hours would have no Lighter rank at all.
    for i, (symbol, size) in enumerate(LI_SIZE.items()):
        times = [T0 - 24 * H + h * H for h in range(24 + HOURS)]
        _write(root, "lighter_candles", pl.DataFrame(
            {"symbol": [symbol] * len(times), "time": times,
             "quote_volume": [size / 24] * len(times)},
            schema={"symbol": pl.String, "time": pl.Datetime("ms"),
                    "quote_volume": pl.Float64}), market_id=i)


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNDR_PHASE2_DATA", str(tmp_path))
    _build_fixture(tmp_path)
    return tmp_path


@pytest.fixture
def built(data_root):
    return build_universe.build(start=START, end=END, rebalance="daily", pool="venue")


def _row(df: pl.DataFrame, **where) -> dict:
    for col, value in where.items():
        df = df.filter(pl.col(col) == value)
    return df.sort("hour").to_dicts()[0]


# --- the two views are different views ------------------------------------------------------

def test_per_venue_keeps_the_venue_exclusives_that_cross_venue_cannot_have(built):
    per_venue, cross = built["per_venue"], built["cross_venue"]
    hl = set(per_venue.filter(pl.col("venue") == "hl")["symbol"])
    lighter = set(per_venue.filter(pl.col("venue") == "lighter")["symbol"])
    assert "HLONLY" in hl and "LIONLY" in lighter
    assert hl == set(HL_SIZE) and lighter == set(LI_SIZE)
    assert set(cross["symbol"]) == set(PAIRS)
    assert not {"HLONLY", "LIONLY"} & set(cross["symbol"])


def test_cross_venue_is_the_concurrent_intersection_not_the_union(built):
    cross = built["cross_venue"]
    aaa = cross.filter(pl.col("symbol") == "AAA")
    # AAA settles on Hyperliquid from hour 0 but on Lighter only from hour 30. The hours in
    # between are not cross-venue hours, however much Hyperliquid history exists for them.
    assert aaa["hour"].min() == T0 + 30 * H
    assert aaa.height == HOURS - 30
    assert cross.height == sum(HOURS - max(LI_FIRST.get(li, 0), 24) for li in PAIRS.values())


def test_the_cross_venue_leg_map_uses_the_alias_table_not_the_symbol_name(built):
    cross = built["cross_venue"]
    legs = dict(zip(cross["symbol"], cross["lighter_symbol"]))
    assert legs == PAIRS


# --- pairs_live_this_hour, the D5 obligation ------------------------------------------------

def test_pairs_live_this_hour_is_on_every_cross_venue_row_and_never_null(built):
    cross = built["cross_venue"]
    assert cross["pairs_live_this_hour"].null_count() == 0
    live = dict(zip(cross["hour"], cross["pairs_live_this_hour"]))
    assert live[T0 + 24 * H] == 3          # AAA has not listed on Lighter yet
    assert live[T0 + 30 * H] == 4


def test_per_venue_rows_also_carry_a_width_count(built):
    per_venue = built["per_venue"]
    assert per_venue["pairs_live_this_hour"].null_count() == 0
    hl = per_venue.filter((pl.col("venue") == "hl") & (pl.col("hour") == START))
    assert hl["pairs_live_this_hour"].unique().to_list() == [len(HL_SIZE)]


# --- pair_age_hours, added for the basis-risk interaction -----------------------------------

def test_pair_age_hours_counts_from_the_pairs_first_concurrent_hour(built):
    cross = built["cross_venue"]
    aaa = cross.filter(pl.col("symbol") == "AAA").sort("hour")
    assert aaa["pair_age_hours"][0] == 0            # its first concurrent hour is hour 30
    assert aaa["pair_age_hours"][5] == 5
    assert cross["pair_age_hours"].null_count() == 0


def test_pair_age_hours_is_not_reset_by_a_later_start(built):
    # BTC has been concurrent since hour 0. A panel that starts at hour 24 must still call it
    # 24 hours old, or every `--start` sweep in Task 5 would manufacture a fresh young cohort.
    btc = built["cross_venue"].filter(pl.col("symbol") == "BTC").sort("hour")
    assert btc["hour"][0] == START
    assert btc["pair_age_hours"][0] == 24


def test_pair_age_hours_is_an_explicit_hour_difference_over_a_hole(data_root):
    # No `shift`: the age of the hour after a hole must jump by the size of the hole.
    root = data_root
    path = root / "lighter_funding" / "market_id=0" / "part.parquet"
    df = pl.read_parquet(path)
    df.filter(~pl.col("settle_time").is_between(T0 + 40 * H, T0 + 42 * H)).write_parquet(path)
    cross = build_universe.build(start=START, end=END, rebalance="daily",
                                 pool="venue")["cross_venue"]
    btc = cross.filter(pl.col("symbol") == "BTC").sort("hour")
    ages = dict(zip(btc["hour"], btc["pair_age_hours"]))
    assert T0 + 41 * H not in ages
    assert ages[T0 + 39 * H] == 39 and ages[T0 + 43 * H] == 43


# --- the rank columns are columns, not filters ----------------------------------------------

def test_no_row_is_dropped_for_being_in_the_top_10(built):
    cross = built["cross_venue"]
    btc = cross.filter(pl.col("symbol") == "BTC")
    assert btc.height == HOURS - 24
    assert btc["in_hl_top10"].all() and btc["in_lighter_top10"].all()
    outside = cross.filter(pl.col("symbol") == "AAA")
    assert not outside["in_hl_top10"].any() and not outside["in_lighter_top10"].any()


def test_both_rank_columns_are_carried_on_cross_venue_rows(built):
    row = _row(built["cross_venue"], symbol="AAA")
    assert row["hl_rank"] == 12 and row["lighter_rank"] == 12
    assert row["in_hl_top10"] is False and row["in_lighter_top10"] is False


def test_cross_venue_top10_is_null_only_where_rank_unavailable(built):
    # The plan's verify gate. On a cross-venue row the Hyperliquid leg governs (D1), so
    # `rank_unavailable` and the HL leg are the same column here.
    cross = built["cross_venue"]
    assert cross.filter(pl.col("in_hl_top10").is_null() & ~pl.col("rank_unavailable")).height == 0
    assert cross.filter(pl.col("in_hl_top10").is_not_null()
                        & pl.col("rank_unavailable")).height == 0


def test_each_top10_flag_is_null_exactly_where_its_own_leg_is_unavailable(built):
    # The gate generalised per leg, which is the form that holds on every row of both views.
    for view in ("per_venue", "cross_venue"):
        panel = built[view]
        for flag, leg in (("in_hl_top10", "hl"), ("in_lighter_top10", "lighter")):
            unavailable = pl.col(f"{leg}_rank_unavailable")
            assert panel.filter(pl.col(flag).is_null() & ~unavailable).height == 0, (view, leg)
            assert panel.filter(pl.col(flag).is_not_null() & unavailable).height == 0, (view, leg)


def test_rank_unavailable_follows_the_leg_that_governs_the_row(built):
    per_venue, cross = built["per_venue"], built["cross_venue"]
    assert cross["rank_unavailable"].to_list() == cross["hl_rank_unavailable"].to_list()
    hl = per_venue.filter(pl.col("venue") == "hl")
    assert hl["rank_unavailable"].to_list() == hl["hl_rank_unavailable"].to_list()
    lighter = per_venue.filter(pl.col("venue") == "lighter")
    assert lighter["rank_unavailable"].to_list() == lighter["lighter_rank_unavailable"].to_list()


def test_the_natural_filter_does_not_delete_target_as_lighter_panel(built):
    # The regression guard. An earlier draft defined `rank_unavailable` as the Hyperliquid leg
    # on every row, so `filter(~rank_unavailable)` -- the filter anyone would write -- silently
    # deleted all 1,585,687 per-venue Lighter rows. A Lighter market has no Hyperliquid rank;
    # that is a fact about the other venue, not a reason to drop the row.
    lighter = built["per_venue"].filter(pl.col("venue") == "lighter")
    assert lighter["hl_rank_unavailable"].all()             # no HL leg, as expected
    kept = lighter.filter(~pl.col("rank_unavailable")).height
    assert kept > 0.9 * lighter.height, (kept, lighter.height)


def test_a_market_with_no_size_at_all_is_flagged_rather_than_admitted(tmp_path, monkeypatch):
    # The failure this phase exists to prevent: a null rank makes `rank <= 10` false, which
    # ADMITS the market. HLONLY has no open-interest row anywhere, so it must read unavailable.
    monkeypatch.setenv("FUNDR_PHASE2_DATA", str(tmp_path))
    _build_fixture(tmp_path, hl_sizes={k: v for k, v in HL_SIZE.items() if k != "HLONLY"})
    per_venue = build_universe.build(start=START, end=END, rebalance="daily",
                                     pool="venue")["per_venue"]
    rows = per_venue.filter(pl.col("symbol") == "HLONLY")
    assert rows.height == HOURS - 24
    assert rows["rank_unavailable"].all()
    assert rows["hl_rank"].null_count() == rows.height
    assert rows["in_hl_top10"].null_count() == rows.height


# --- denomination: each leg carries its OWN measured multiplier ------------------------------

def test_the_alias_table_multiplier_is_per_venue_leg_not_per_pair():
    # Measured 2026-09-22: the four `k` markets read ~1.0 against their `1000` counterparts, so
    # both legs quote 1000 tokens. `NOT` reads 0.000999 -- Hyperliquid lists NOT, not kNOT.
    for lighter_symbol, (hl_symbol, _) in alias.ALIASES.items():
        assert alias.size_multiplier(lighter_symbol, "lighter") == 1000, lighter_symbol
    for hl_symbol in ("kBONK", "kFLOKI", "kPEPE", "kSHIB"):
        assert alias.size_multiplier(hl_symbol, "hl") == 1000, hl_symbol
    assert alias.size_multiplier("NOT", "hl") == 1


def test_cross_venue_rows_carry_each_legs_own_multiplier(built):
    kpepe = _row(built["cross_venue"], symbol="kPEPE")
    assert (kpepe["size_multiplier"], kpepe["lighter_size_multiplier"]) == (1000, 1000)
    assert kpepe["is_alias_pair"] is True
    # The row the plan's original text would have got wrong: forcing 1000 onto the Hyperliquid
    # leg of NOT would scale every price built from it by 1000.
    not_row = _row(built["cross_venue"], symbol="NOT")
    assert (not_row["size_multiplier"], not_row["lighter_size_multiplier"]) == (1, 1000)
    assert not_row["is_alias_pair"] is True
    ordinary = _row(built["cross_venue"], symbol="AAA")
    assert (ordinary["size_multiplier"], ordinary["lighter_size_multiplier"]) == (1, 1)
    assert ordinary["is_alias_pair"] is False


def test_per_venue_rows_carry_their_own_venues_multiplier(built):
    per_venue = built["per_venue"]
    assert _row(per_venue, venue="hl", symbol="NOT")["size_multiplier"] == 1
    assert _row(per_venue, venue="lighter", symbol="1000NOT")["size_multiplier"] == 1000
    assert _row(per_venue, venue="hl", symbol="kPEPE")["size_multiplier"] == 1000
    assert _row(per_venue, venue="lighter", symbol="1000PEPE")["size_multiplier"] == 1000
    assert _row(per_venue, venue="hl", symbol="AAA")["size_multiplier"] == 1
    assert _row(per_venue, venue="hl", symbol="NOT")["is_alias_pair"] is True
    assert _row(per_venue, venue="hl", symbol="AAA")["is_alias_pair"] is False


def test_the_multipliers_are_never_applied_to_the_size_measure(built):
    # Both size measures are notional (USD) and therefore denomination-invariant. Multiplying
    # one by 1000 would be a 1000x bug, not a correction.
    hl = built["universe_hl"]
    assert _row(hl, symbol="kPEPE")["size_value"] == HL_SIZE["kPEPE"]
    lighter = built["universe_lighter"]
    assert _row(lighter, symbol="1000PEPE")["size_value"] == pytest.approx(LI_SIZE["1000PEPE"])


# --- the universe dataset -------------------------------------------------------------------

def test_universe_carries_the_measure_and_the_basis_it_was_ranked_on(built):
    hl, lighter = built["universe_hl"], built["universe_lighter"]
    assert hl["size_measure"].unique().to_list() == ["oi_notional"]
    assert lighter["size_measure"].unique().to_list() == ["trailing_quote_volume_24h"]
    assert hl["rank_basis"].unique().to_list() == ["daily"]
    assert set(hl.columns) >= {"venue", "symbol", "hour", "size_measure", "size_value",
                               "size_rank", "rank_basis", "rank_is_carried_forward",
                               "rank_stale_hours", "rank_unavailable"}
    assert hl["hour"].min() >= START and hl["hour"].max() <= END


# --- knobs ----------------------------------------------------------------------------------

def test_the_defaults_are_the_recorded_decisions():
    args = build_universe.parse_args([])
    assert (args.pool, args.rebalance, args.start) == ("venue", "daily", datetime(2025, 1, 17))


def test_every_knob_is_overridable(data_root):
    out = build_universe.build(start=START, end=END, rebalance="hourly", pool="matched")
    assert out["universe_hl"]["rank_basis"].unique().to_list() == ["hourly"]
    # `--pool matched` ranks a market only against the other matched markets, so the venue
    # exclusives leave the ranking pool -- while staying in the panel, unranked.
    assert set(out["universe_hl"]["symbol"]) == set(PAIRS)
    assert "HLONLY" in set(out["per_venue"]["symbol"])
    assert _row(out["cross_venue"], symbol="AAA")["hl_rank"] == 2   # BTC only, above it


def test_matched_pool_does_not_change_the_panels_row_count(data_root):
    venue = build_universe.build(start=START, end=END, rebalance="daily", pool="venue")
    matched = build_universe.build(start=START, end=END, rebalance="daily", pool="matched")
    assert venue["cross_venue"].height == matched["cross_venue"].height
    assert venue["per_venue"].height == matched["per_venue"].height


# --- what lands on disk ---------------------------------------------------------------------

def test_main_writes_both_views_and_both_venues_with_provenance(data_root, capsys):
    assert build_universe.main(["--start", START.isoformat(), "--end", END.isoformat()]) == 0
    for name, key, values in (("universe", "venue", ("hl", "lighter")),
                              ("panel", "view", ("per_venue", "cross_venue"))):
        for value in values:
            path = data_root / name / f"{key}={value}" / "part.parquet"
            assert path.exists(), path
            df = pl.read_parquet(path)
            assert df.height > 0
            assert {"_source", "_fetched_at_ms", "_git_sha"} <= set(df.columns)
            assert df["_source"].null_count() == 0
    out = capsys.readouterr().out
    assert "cross_venue" in out and "per_venue" in out
