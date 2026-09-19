import json

import httpx

from fundr.sources import hl_api

META = {"universe": [{"name": "BTC", "szDecimals": 5}, {"name": "OLD", "szDecimals": 0, "isDelisted": True}]}
CTXS = [
    {"funding": "0.0000125", "openInterest": "10", "prevDayPx": "1", "dayNtlVlm": "5", "premium": "0.0005",
     "oraclePx": "100", "markPx": "101", "midPx": "101", "impactPxs": ["100", "102"], "dayBaseVlm": "1"},
    {"funding": "0.0", "openInterest": "0", "prevDayPx": "1", "dayNtlVlm": "0", "premium": None,
     "oraclePx": "1", "markPx": "1", "midPx": None, "impactPxs": None, "dayBaseVlm": "0"},
]


def test_asset_ctxs_frame():
    df = hl_api.asset_ctxs_frame([META, CTXS])
    btc = df.row(0, named=True)
    assert btc["coin"] == "BTC" and btc["oi_notional_usd"] == 1010.0 and not btc["is_delisted"]
    assert df.row(1, named=True)["is_delisted"]


def test_funding_history_pages_until_empty():
    pages = [
        [{"coin": "ETH", "fundingRate": "0.0000125", "premium": "-0.0003", "time": 1000},
         {"coin": "ETH", "fundingRate": "0.0000125", "premium": "0.0001", "time": 3_601_000}],
        [],
    ]
    seen = []

    def handler(request):
        seen.append(json.loads(request.content)["startTime"])
        return httpx.Response(200, json=pages[len(seen) - 1])

    client = hl_api.HLInfo(httpx.Client(transport=httpx.MockTransport(handler)))
    rows = client.funding_history("ETH", 0, 10_000_000)
    assert len(rows) == 2
    assert seen == [0, 3_601_001]


def test_funding_history_frame_truncates_settle_time():
    df = hl_api.funding_history_frame(
        [{"coin": "ETH", "fundingRate": "0.0000125", "premium": "0.0001", "time": 1735689600054}]
    )
    row = df.row(0, named=True)
    assert row["funding_rate"] == 0.0000125
    assert row["funding_rate_str"] == "0.0000125"
    assert row["settle_time"].minute == 0 and row["settle_time"].second == 0
