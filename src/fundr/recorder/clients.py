"""Async venue clients. fundr.sources' clients are synchronous: awaiting one from the event
loop blocks every feed task, which is exactly how Phase 1 lost 95 minutes."""
from typing import Any

import httpx

from fundr.recorder.config import HL_INFO_URL, LIGHTER_BASE_URL


class AsyncHLInfo:
    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client or httpx.AsyncClient(timeout=30)

    async def post(self, payload: dict) -> Any:
        r = await self._client.post(HL_INFO_URL, json=payload)
        r.raise_for_status()
        return r.json()

    async def meta_and_asset_ctxs(self) -> list:
        return await self.post({"type": "metaAndAssetCtxs"})

    async def predicted_fundings(self) -> list:
        return await self.post({"type": "predictedFundings"})

    async def meta(self) -> dict:
        return await self.post({"type": "meta"})

    async def aclose(self) -> None:
        await self._client.aclose()


class AsyncLighterREST:
    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client or httpx.AsyncClient(base_url=LIGHTER_BASE_URL, timeout=30)

    async def get(self, path: str, **params) -> dict:
        r = await self._client.get(path, params=params)
        r.raise_for_status()
        body = r.json()
        if body.get("code") != 200:
            raise RuntimeError(f"{path} {params}: {body}")
        return body

    async def order_books(self) -> list[dict]:
        return (await self.get("/api/v1/orderBooks"))["order_books"]

    async def order_book_details(self) -> list[dict]:
        return (await self.get("/api/v1/orderBookDetails"))["order_book_details"]

    async def aclose(self) -> None:
        await self._client.aclose()
