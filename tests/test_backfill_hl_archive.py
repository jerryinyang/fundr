import lz4.frame
import polars as pl
import pytest

from scripts.backfill_hl_archive import (
    check_data_root, convert_day, day_completeness, merge_completeness, needs_fetch, parse_day)


def _raw(rows_per_coin: dict[str, int]) -> pl.DataFrame:
    rows = []
    for coin, n in rows_per_coin.items():
        for i in range(n):
            rows.append({"time": f"2026-09-18T{i // 60:02d}:{i % 60:02d}:00Z", "coin": coin,
                         "funding": 0.0000125, "open_interest": 1.0, "premium": 0.0001,
                         "mark_px": 100.0, "oracle_px": 100.0, "day_ntl_vlm": 5.0})
    return pl.DataFrame(rows)


def _completeness(date: str, coin: str, completeness: float) -> pl.DataFrame:
    return pl.DataFrame({"date": [date], "coin": [coin],
                         "minutes": [int(1440 * completeness)],
                         "completeness": [completeness]})


def test_parse_day_types_time_and_keeps_all_coins():
    df = parse_day(_raw({"BTC": 3, "ETH": 2}))
    assert str(df.schema["time"]).startswith("Datetime")
    assert sorted(df["coin"].unique().to_list()) == ["BTC", "ETH"]


def test_day_completeness_is_per_coin_minutes():
    df = parse_day(_raw({"BTC": 1440, "ETH": 720}))
    comp = day_completeness(df)
    assert comp.filter(pl.col("coin") == "BTC")["minutes"].item() == 1440
    assert comp.filter(pl.col("coin") == "ETH")["completeness"].item() == 0.5


def test_day_completeness_flags_a_short_day():
    df = parse_day(_raw({"BTC": 156}))
    assert day_completeness(df)["completeness"].item() < 0.15


def test_completeness_record_merges_across_runs():
    # The record Step 9 reports from must survive a run that only touched later days -- and a
    # re-fetched day must REPLACE its old row, not lose to it.
    existing = pl.concat([_completeness("2026-09-15", "BTC", 0.108),
                          _completeness("2026-09-17", "BTC", 1.0)])
    fresh = _completeness("2026-09-15", "BTC", 1.0)
    out = merge_completeness(existing, fresh)
    assert out.height == 2
    assert out.filter(pl.col("date") == "2026-09-15")["completeness"].item() == 1.0
    assert out.filter(pl.col("date") == "2026-09-17")["completeness"].item() == 1.0


def test_needs_fetch_is_false_for_a_recorded_complete_day(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNDR_PHASE2_DATA", str(tmp_path))
    path = tmp_path / "hl_asset_ctxs" / "date=2026-09-17" / "part.parquet"
    path.parent.mkdir(parents=True)
    pl.DataFrame({"x": [1]}).write_parquet(path)
    assert needs_fetch("2026-09-17", _completeness("2026-09-17", "BTC", 1.0)) is False


def test_needs_fetch_is_true_for_a_quarantined_short_day(tmp_path, monkeypatch):
    # 2026-09-15 really did hold 156 of 1440 rows. The handoff says quarantine and re-download
    # later to pick up the venue's backfill; skipping on file existence freezes it forever.
    monkeypatch.setenv("FUNDR_PHASE2_DATA", str(tmp_path))
    path = tmp_path / "hl_asset_ctxs" / "date=2026-09-15" / "part.parquet"
    path.parent.mkdir(parents=True)
    pl.DataFrame({"x": [1]}).write_parquet(path)
    assert needs_fetch("2026-09-15", _completeness("2026-09-15", "BTC", 0.108)) is True
    assert needs_fetch("2026-09-15", None) is True          # no record == unmeasured == fetch


def test_a_permanently_short_day_is_not_re_fetched(tmp_path, monkeypatch):
    # 2023-05-20 is the archive's first file and starts at 02:50:04Z: all 21 coins hold exactly
    # 1,270 of 1,440 minutes (0.8819, measured on the real file). No re-download will ever fill
    # it, so quarantining it means re-paying its egress on every run forever.
    monkeypatch.setenv("FUNDR_PHASE2_DATA", str(tmp_path))
    path = tmp_path / "hl_asset_ctxs" / "date=2023-05-20" / "part.parquet"
    path.parent.mkdir(parents=True)
    pl.DataFrame({"x": [1]}).write_parquet(path)
    assert needs_fetch("2023-05-20", _completeness("2023-05-20", "BTC", 0.8819)) is False
    # ... but it is still fetched the first time, when no parquet exists.
    other = tmp_path / "elsewhere"
    monkeypatch.setenv("FUNDR_PHASE2_DATA", str(other))
    assert needs_fetch("2023-05-20", None) is True


def test_check_data_root_refuses_a_split_tree(tmp_path, monkeypatch):
    # The guard that stops ~10 GB of .lz4 landing in data/phase1 because someone forgot an env
    # var: HLArchive caches under $FUNDR_DATA, everything else writes under $FUNDR_PHASE2_DATA.
    monkeypatch.setenv("FUNDR_PHASE2_DATA", str(tmp_path / "phase2"))
    monkeypatch.setenv("FUNDR_DATA", str(tmp_path / "phase1"))
    with pytest.raises(SystemExit, match="refusing to run"):
        check_data_root()
    monkeypatch.setenv("FUNDR_DATA", str(tmp_path / "phase2"))
    check_data_root()


def test_convert_day_deletes_the_lz4(tmp_path, monkeypatch):
    # Global constraint: the whole archive is 10.1 GB compressed. Keeping every .lz4 alongside
    # its parquet doubles the footprint for no benefit -- the parquet is the artefact.
    monkeypatch.setenv("FUNDR_PHASE2_DATA", str(tmp_path))
    csv = b"time,coin,funding\n2026-09-18T00:00:00Z,BTC,0.0000125\n"
    lz4_path = tmp_path / "20260918.csv.lz4"
    lz4_path.write_bytes(lz4.frame.compress(csv))
    df = convert_day(lz4_path, date="2026-09-18", source="test")
    assert df.height == 1
    assert not lz4_path.exists()
