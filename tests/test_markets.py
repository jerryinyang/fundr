import polars as pl

from fundr import markets

META = {"universe": [
    {"name": "BTC", "szDecimals": 5, "maxLeverage": 40, "marginTableId": 56},
    {"name": "OLD", "szDecimals": 2, "maxLeverage": 3, "marginTableId": 5, "isDelisted": True,
     "marginMode": "isolated"},
    {"name": "ETH", "szDecimals": 4, "maxLeverage": 25, "marginTableId": 55},
]}
CTXS = [{"funding": "0.0000125"}, {"funding": "0.0"}, {"funding": "0.0000125"}]

BOOKS = [
    {"symbol": "BTC", "market_id": 1, "status": "active", "market_type": "perp",
     "created_at": "1737098461107", "maker_fee": "0.0000", "taker_fee": "0.0000",
     "liquidation_fee": "1.0000", "min_base_amount": "0.00007", "min_quote_amount": "10.000000",
     "supported_size_decimals": 5, "supported_price_decimals": 1, "is_frozen": False,
     "start_timestamp": 0, "end_timestamp": 0, "settlement_type": 0, "settlement_price": 0,
     "settlement_cap": 0},
    {"symbol": "AMD", "market_id": 138, "status": "active", "market_type": "perp",
     "created_at": "1770669512153", "maker_fee": "0.0000", "taker_fee": "0.0000",
     "liquidation_fee": "1.0000", "min_base_amount": "0.0100", "min_quote_amount": "10.000000",
     "supported_size_decimals": 4, "supported_price_decimals": 2, "is_frozen": False},
    {"symbol": "DEAD", "market_id": 9, "status": "inactive", "market_type": "perp",
     "created_at": "1700000000000"},
    {"symbol": "SPOTX", "market_id": 50, "status": "active", "market_type": "spot",
     "created_at": "1700000000000"},
]
DETAILS = [
    {"market_id": 1, "symbol": "BTC", "funding_premium_multiplier": 100,
     "base_interest_rate": "0.0100", "funding_clamp_big": "4.0000",
     "funding_clamp_small": "0.0500"},
    {"market_id": 138, "symbol": "AMD", "funding_premium_multiplier": 50,
     "base_interest_rate": "0.0032", "funding_clamp_big": "4.0000",
     "funding_clamp_small": "0.0500"},
    {"market_id": 9, "symbol": "DEAD", "funding_premium_multiplier": 100,
     "base_interest_rate": "0.0000", "funding_clamp_big": "4.0000",
     "funding_clamp_small": "0.0500"},
]


class FakeHL:
    def meta_and_asset_ctxs(self):
        return [META, CTXS]


class FakeLighter:
    def order_books(self):
        return BOOKS

    def order_book_details_all(self):
        return DETAILS


def test_discover_hl_markets_includes_delisted():
    # A delisted market still has history worth collecting; dropping it makes the universe
    # survivorship-biased, which is exactly what Phase 3 must not inherit.
    assert markets.discover_hl_markets(FakeHL()) == ["BTC", "ETH", "OLD"]


def test_hl_market_metadata_carries_listing_fields():
    df = markets.hl_market_metadata(FakeHL())
    row = df.filter(pl.col("coin") == "OLD").row(0, named=True)
    assert row["is_delisted"] is True
    assert row["sz_decimals"] == 2 and row["max_leverage"] == 3 and row["margin_table_id"] == 5
    assert row["margin_mode"] == "isolated"
    assert df.filter(pl.col("coin") == "BTC")["is_delisted"].item() is False


def test_discover_lighter_markets_keeps_perps_including_inactive_and_skips_spot():
    got = markets.discover_lighter_markets(FakeLighter())
    assert [m["market_id"] for m in got] == [1, 9, 138]


def test_lighter_market_metadata_joins_funding_parameters():
    df = markets.lighter_market_metadata(FakeLighter())
    btc = df.filter(pl.col("market_id") == 1).row(0, named=True)
    assert btc["funding_premium_multiplier"] == 100
    assert btc["effective_multiplier"] == 1.0          # the API reports hundredths (P7)
    assert btc["base_interest_rate_pct"] == 0.01
    assert btc["taker_fee"] == 0.0 and btc["min_base_amount"] == 0.00007
    assert str(df.schema["listed_at"]).startswith("Datetime")
    # Lighter dates no delisting, so the settlement block is the only exit information there is.
    assert {"start_timestamp", "end_timestamp", "settlement_type", "settlement_price",
            "settlement_cap"} <= set(df.columns)


def test_lighter_market_metadata_flags_off_default_funding_parameters():
    df = markets.lighter_market_metadata(FakeLighter())
    flagged = df.filter(pl.col("off_default_multiplier"))["symbol"].to_list()
    assert flagged == ["AMD"]                          # multiplier 50, not 100
    off_rate = df.filter(pl.col("off_default_interest"))["symbol"].to_list()
    assert sorted(off_rate) == ["AMD", "DEAD"]         # 0.0032 and 0.0000, not 0.0100


def test_funding_period_s_measures_the_modal_gap():
    hourly = [3600 * i for i in range(10)]
    assert markets.funding_period_s(hourly) == 3600
    four_hourly = [14400 * i for i in range(10)]
    assert markets.funding_period_s(four_hourly) == 14400
    assert markets.funding_period_s([3600]) is None    # not enough to measure
