"""P6: which Lighter market-state series are historical, and which are live snapshots only."""
import re
import time

import httpx

from fundr import store
from fundr.sources.lighter_api import BASE_URL

now, day = int(time.time()), 86400
sample = store.load_json("p00", "sample.json")
mid = next(s for s in sample if s["role"] == "mid")["lighter_market_id"]

CHECKS = [
    ("candles 1h one year ago", "/api/v1/candles",
     dict(market_id=1, resolution="1h", start_timestamp=now - 365 * day, end_timestamp=now - 364 * day, count_back=24)),
    ("candles 1m 180 days ago", "/api/v1/candles",
     dict(market_id=1, resolution="1m", start_timestamp=now - 180 * day, end_timestamp=now - 180 * day + 3600, count_back=60)),
    ("candles 1h mid-cap 90 days ago", "/api/v1/candles",
     dict(market_id=mid, resolution="1h", start_timestamp=now - 90 * day, end_timestamp=now - 89 * day, count_back=24)),
    ("orderBookDetails", "/api/v1/orderBookDetails", dict(market_id=mid)),
    ("exchangeStats", "/api/v1/exchangeStats", {}),
    ("funding-rates", "/api/v1/funding-rates", {}),
    ("funding-rates with a past timestamp", "/api/v1/funding-rates", dict(timestamp=now - day)),
    ("recentTrades", "/api/v1/recentTrades", dict(market_id=mid, limit=10)),
    ("trades", "/api/v1/trades", dict(market_id=mid, sort_by="timestamp", limit=10)),
    ("orderBooks (listing metadata)", "/api/v1/orderBooks", {}),
    # Step 1 additions: other public GET market-data endpoints from the SDK README not already above.
    ("fundings 1h one year ago (historical funding candles)", "/api/v1/fundings",
     dict(market_id=1, resolution="1h", start_timestamp=now - 365 * day, end_timestamp=now - 364 * day, count_back=24)),
    ("markPriceCandles 1h one year ago (mark price / premium history)", "/api/v1/markPriceCandles",
     dict(market_id=1, resolution="1h", start_timestamp=now - 365 * day, end_timestamp=now - 364 * day, count_back=24)),
    ("marketPriceCharts (last 24h, no time window param)", "/api/v1/marketPriceCharts", dict(market_ids=[mid])),
    ("exchangeMetrics", "/api/v1/exchangeMetrics", dict(period="1d", kind="volume")),
    ("executeStats", "/api/v1/executeStats", dict(period="1d")),
    ("assetDetails (listing metadata)", "/api/v1/assetDetails", {}),
]

client = httpx.Client(base_url=BASE_URL, timeout=30)
for label, path, params in CHECKS:
    r = client.get(path, params=params)
    try:
        body = r.json()
    except ValueError:
        body = r.text[:500]
    slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    store.save_json("p06", f"{slug}.json", {"path": path, "params": params, "status": r.status_code, "body": body})
    print(f"\n{label}: HTTP {r.status_code}")
    print(str(body)[:400])
