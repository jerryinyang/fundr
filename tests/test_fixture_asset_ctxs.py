from pathlib import Path

from fundr.sources.hl_archive import parse_time, read_csv_lz4

FIXTURE = Path(__file__).parent / "fixtures" / "hl_asset_ctxs_sample.csv.lz4"


def test_real_asset_ctxs_fixture_parses():
    df = read_csv_lz4(FIXTURE)
    assert {"time", "coin", "funding"} <= set(df.columns)
    assert df.height > 0


def test_real_asset_ctxs_time_parses():
    df = parse_time(read_csv_lz4(FIXTURE))
    assert str(df.schema["time"]).startswith("Datetime")
