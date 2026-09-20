"""Lighter public REST API and market_stats websocket."""
import asyncio
import json
from collections.abc import Callable

import httpx
import polars as pl
import websockets

from fundr.analysis import epoch_s

BASE_URL = "https://mainnet.zklighter.elliot.ai"
WS_URL = "wss://mainnet.zklighter.elliot.ai/stream"
_PERIOD_S = {"1h": 3600, "1d": 86400}
_CHUNK_PERIODS = 700  # stays under the documented 750-row cap per call


class LighterAPI:
    def __init__(self, client: httpx.Client | None = None):
        self._client = client or httpx.Client(base_url=BASE_URL, timeout=30)

    def get(self, path: str, **params) -> dict:
        r = self._client.get(path, params=params)
        r.raise_for_status()
        body = r.json()
        if body.get("code") != 200:
            raise RuntimeError(f"{path} {params}: {body}")
        return body

    def order_books(self) -> list[dict]:
        return self.get("/api/v1/orderBooks")["order_books"]

    def order_book_details(self, market_id: int) -> dict:
        return self.get("/api/v1/orderBookDetails", market_id=market_id)["order_book_details"][0]

    def funding_rates(self) -> list[dict]:
        return self.get("/api/v1/funding-rates")["funding_rates"]

    def fundings(self, market_id: int, resolution: str, start_s: int, end_s: int, count_back: int) -> list[dict]:
        return self.get(
            "/api/v1/fundings", market_id=market_id, resolution=resolution,
            start_timestamp=start_s, end_timestamp=end_s, count_back=count_back,
        )["fundings"]

    def fundings_all(self, market_id: int, resolution: str, start_s: int, end_s: int) -> list[dict]:
        step = _PERIOD_S[resolution] * _CHUNK_PERIODS
        seen: dict[int, dict] = {}
        t = start_s
        while t < end_s:
            for row in self.fundings(market_id, resolution, t, min(t + step, end_s), 750):
                seen[row["timestamp"]] = row
            t += step
        return [seen[k] for k in sorted(seen)]

    def candles(self, market_id: int, resolution: str, start_s: int, end_s: int, count_back: int) -> list[dict]:
        return self.get(
            "/api/v1/candles", market_id=market_id, resolution=resolution,
            start_timestamp=start_s, end_timestamp=end_s, count_back=count_back,
        )["c"]


async def market_stats_stream(
    market_ids: list[int], on_stats: Callable[[dict, int | None], None], stop: asyncio.Event
) -> None:
    async with websockets.connect(WS_URL) as ws:
        for m in market_ids:
            await ws.send(json.dumps({"type": "subscribe", "channel": f"market_stats/{m}"}))
        while not stop.is_set():
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), 5))
            except TimeoutError:
                continue
            if "market_stats" in msg:
                on_stats(msg["market_stats"], msg.get("timestamp"))


def lighter_signed_rate(rate: float, direction: str) -> float:
    """Verified (P5/P9, market 212 CAP): direction 'long' means longs pay (positive rate)."""
    return rate if direction == "long" else -rate


def fundings_frame(rows: list[dict], market_id: int) -> pl.DataFrame:
    df = pl.DataFrame(
        rows, schema={"timestamp": pl.Int64, "value": pl.String, "rate": pl.String, "direction": pl.String}
    )
    return df.select(
        pl.lit(market_id).alias("market_id"),
        "timestamp",
        epoch_s("timestamp").alias("settle_time"),
        pl.col("rate").alias("rate_str"),
        pl.col("rate").cast(pl.Float64).alias("rate"),
        "direction",
        pl.struct("rate", "direction")
        .map_elements(lambda s: lighter_signed_rate(float(s["rate"]), s["direction"]), return_dtype=pl.Float64)
        .alias("signed_rate"),
        "value",
    )
