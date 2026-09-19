"""Hyperliquid public info endpoint."""
from typing import Any

import httpx
import polars as pl

from fundr.analysis import epoch_ms

INFO_URL = "https://api.hyperliquid.xyz/info"


class HLInfo:
    def __init__(self, client: httpx.Client | None = None):
        self._client = client or httpx.Client(timeout=30)

    def post(self, payload: dict) -> Any:
        r = self._client.post(INFO_URL, json=payload)
        r.raise_for_status()
        return r.json()

    def meta_and_asset_ctxs(self) -> list:
        return self.post({"type": "metaAndAssetCtxs"})

    def predicted_fundings(self) -> list:
        return self.post({"type": "predictedFundings"})

    def candle_snapshot(self, coin: str, interval: str, start_ms: int, end_ms: int) -> list:
        req = {"coin": coin, "interval": interval, "startTime": start_ms, "endTime": end_ms}
        return self.post({"type": "candleSnapshot", "req": req})

    def funding_history(self, coin: str, start_ms: int, end_ms: int) -> list[dict]:
        rows, cursor = [], start_ms
        while cursor < end_ms:
            page = self.post({"type": "fundingHistory", "coin": coin, "startTime": cursor, "endTime": end_ms})
            if not page:
                break
            rows += page
            cursor = page[-1]["time"] + 1
        return rows


def _f(x) -> float | None:
    return None if x is None else float(x)


def asset_ctxs_frame(raw: list) -> pl.DataFrame:
    meta, ctxs = raw
    records = [
        {
            "coin": u["name"],
            "is_delisted": bool(u.get("isDelisted", False)),
            "funding": _f(c.get("funding")),
            "premium": _f(c.get("premium")),
            "open_interest": _f(c.get("openInterest")),
            "mark_px": _f(c.get("markPx")),
            "oracle_px": _f(c.get("oraclePx")),
            "day_ntl_vlm": _f(c.get("dayNtlVlm")),
        }
        for u, c in zip(meta["universe"], ctxs)
    ]
    return pl.DataFrame(records).with_columns(
        (pl.col("open_interest") * pl.col("mark_px")).alias("oi_notional_usd")
    )


def funding_history_frame(rows: list[dict]) -> pl.DataFrame:
    df = pl.DataFrame(
        rows, schema={"coin": pl.String, "fundingRate": pl.String, "premium": pl.String, "time": pl.Int64}
    )
    return df.select(
        "coin",
        epoch_ms("time").alias("time"),
        epoch_ms("time").dt.truncate("1h").alias("settle_time"),
        pl.col("fundingRate").cast(pl.Float64).alias("funding_rate"),
        pl.col("premium").cast(pl.Float64),
        pl.col("fundingRate").alias("funding_rate_str"),
    )
