import datetime as dt
import time

import polars as pl

from fundr import dataset


def test_partition_path_is_key_partitioned(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNDR_PHASE2_DATA", str(tmp_path))
    p = dataset.partition_path("hl_funding", coin="BTC")
    assert p == tmp_path / "hl_funding" / "coin=BTC" / "part.parquet"


def test_write_partition_stamps_provenance(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNDR_PHASE2_DATA", str(tmp_path))
    df = pl.DataFrame({"t": [1, 2], "rate": [0.1, 0.2]})
    dataset.write_partition("hl_funding", df, source="hl:fundingHistory", coin="BTC")
    back = dataset.read_partition("hl_funding", coin="BTC")
    assert back.height == 2
    assert back["_source"].unique().to_list() == ["hl:fundingHistory"]
    assert back["_fetched_at_ms"].min() > 0
    assert back["_git_sha"].null_count() == 0


def test_read_missing_partition_is_none(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNDR_PHASE2_DATA", str(tmp_path))
    assert dataset.read_partition("hl_funding", coin="NOPE") is None


def test_manifest_round_trips_and_merges(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNDR_PHASE2_DATA", str(tmp_path))
    dataset.update_manifest("hl_funding", {"rows": 10, "coverage_start_ms": 1})
    dataset.update_manifest("lighter_funding", {"rows": 5})
    dataset.update_manifest("hl_funding", {"rows": 20})
    m = dataset.manifest()
    assert m["hl_funding"]["rows"] == 20
    assert m["lighter_funding"]["rows"] == 5
    assert m["hl_funding"]["coverage_start_ms"] == 1  # merged, not replaced


def test_resume_cursor_is_utc_not_local_time():
    # 2026-09-21T12:00:00Z as a NAIVE Datetime, which is how every frame in this phase stores
    # time. `Series.max().timestamp()` would apply the machine's local zone here.
    df = pl.DataFrame({"time": [dt.datetime(2026, 9, 21, 12)]}).with_columns(
        pl.col("time").cast(pl.Datetime("ms")))
    assert dataset.resume_cursor(df, "time") == 1_789_992_000_000


def test_resume_cursor_survives_a_negative_utc_offset(monkeypatch):
    # The bug this guards: on a negative-offset machine `.timestamp()` returns a value AHEAD of
    # the true epoch, so the next fetch starts after hours that were never collected and the gap
    # is never revisited. Forced here so the test fails on a UTC machine too.
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    try:
        df = pl.DataFrame({"time": [dt.datetime(2026, 9, 21, 12)]}).with_columns(
            pl.col("time").cast(pl.Datetime("ms")))
        assert dataset.resume_cursor(df, "time") == 1_789_992_000_000
    finally:
        monkeypatch.delenv("TZ", raising=False)
        time.tzset()


def test_resume_cursor_on_integer_column():
    df = pl.DataFrame({"timestamp": [1, 7200, 3600]})
    assert dataset.resume_cursor(df, "timestamp") == 7200


def test_resume_cursor_none_when_nothing_to_resume():
    assert dataset.resume_cursor(None, "time") is None
    assert dataset.resume_cursor(pl.DataFrame(), "time") is None
    assert dataset.resume_cursor(pl.DataFrame({"other": [1]}), "time") is None


def test_merge_partition_dedupes_and_keeps_existing_rows_provenance():
    existing = pl.DataFrame({"k": [1, 2], "v": ["old", "old"], "_source": ["s1", "s1"],
                             "_fetched_at_ms": [111, 111], "_git_sha": ["aaa", "aaa"]})
    fresh = pl.DataFrame({"k": [2, 3], "v": ["new", "new"]})
    out = dataset.merge_partition(existing, fresh, key="k", source="s2")
    assert out["k"].to_list() == [1, 2, 3]
    assert out["v"].to_list() == ["old", "old", "new"]      # settled rows never change
    assert out["_source"].to_list() == ["s1", "s1", "s2"]   # only the new row is re-stamped
    assert out["_fetched_at_ms"].to_list()[:2] == [111, 111]
    assert out["_fetched_at_ms"][2] > 111


def test_merge_partition_without_existing_stamps_everything():
    fresh = pl.DataFrame({"k": [2, 1], "v": ["b", "a"]})
    out = dataset.merge_partition(None, fresh, key="k", source="s")
    assert out["k"].to_list() == [1, 2]
    assert out["_source"].to_list() == ["s", "s"]
    assert dataset.merge_partition(None, pl.DataFrame(), key="k", source="s").is_empty()
