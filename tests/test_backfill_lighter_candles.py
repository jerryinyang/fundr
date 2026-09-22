from scripts.backfill_lighter_candles import (
    backfill_candles, candles_frame, mark_candles_frame)

MARKET = {"market_id": 1, "symbol": "BTC", "created_at": "0"}
RAW = [{"t": 1_789_808_400_000, "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5, "v": 10.0, "V": 15.0,
        "i": 31_369_348_944},
       {"t": 1_789_812_000_000, "o": 1.5, "h": 2.5, "l": 1.0, "c": 2.0, "v": 20.0, "V": 30.0,
        "i": 31_379_915_246}]
MARK_RAW = [{"t": 1_789_808_400_000, "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5, "sc": 924_848}]


class FakeLighter:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = 0

    def candles(self, market_id, resolution, start_s, end_s, count_back):
        self.calls += 1
        return self.pages.pop(0) if self.pages else []


def test_candles_frame_types_and_names_columns():
    df = candles_frame(RAW, market_id=1, symbol="BTC")
    assert df["market_id"].unique().to_list() == [1]
    assert df["symbol"].unique().to_list() == ["BTC"]
    assert str(df.schema["time"]).startswith("Datetime")
    assert df["base_volume"].to_list() == [10.0, 20.0]
    assert df["quote_volume"].to_list() == [15.0, 30.0]


def test_mark_price_candles_frame_has_no_volume_columns():
    # Measured 2026-09-21: markPriceCandles returns t,o,h,l,c,sc -- no v/V.
    df = mark_candles_frame(MARK_RAW, market_id=1, symbol="BTC")
    assert "base_volume" not in df.columns
    assert df["sc"].to_list() == [924_848]
    assert str(df.schema["time"]).startswith("Datetime")


def test_backfill_pages_past_an_empty_first_window():
    # A market can be silent for its first windows -- candles exist only where trades happened.
    # Breaking on the first empty window drops the market entirely.
    api = FakeLighter([[], [], RAW, []])
    df = backfill_candles(api, MARKET, end_s=1_789_900_000, sleep=lambda s: None)
    assert df.height == 2
    assert api.calls >= 3


def test_backfill_stops_after_max_empty_windows():
    api = FakeLighter([[] for _ in range(50)])
    df = backfill_candles(api, MARKET, end_s=1_789_900_000, max_empty_windows=3,
                          sleep=lambda s: None)
    assert df.is_empty()
    assert api.calls == 3


def test_backfill_dedupes_overlapping_pages():
    api = FakeLighter([RAW, RAW, []])
    df = backfill_candles(api, MARKET, end_s=1_789_900_000, sleep=lambda s: None)
    assert df.height == 2
    assert df["time"].is_sorted()


def test_backfill_drops_the_in_progress_bucket():
    # end_s lands inside the bucket stamped 1_789_812_000_000: that hour has not closed, so the
    # bar is partial and must not be written -- the merge keeps the first version it sees.
    api = FakeLighter([RAW, []])
    df = backfill_candles(api, MARKET, end_s=1_789_812_000 + 1800, sleep=lambda s: None)
    assert df["time"].dt.epoch("ms").to_list() == [1_789_808_400_000]


def test_no_candles_returns_empty():
    df = backfill_candles(FakeLighter([[]]), MARKET, end_s=1_789_900_000, max_empty_windows=1,
                          sleep=lambda s: None)
    assert df.is_empty()
