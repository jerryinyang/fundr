import httpx
import pytest

from fundr.recorder.clients import AsyncHLInfo, AsyncLighterREST


@pytest.mark.asyncio
async def test_hl_info_posts_and_returns_json():
    seen = {}

    async def handler(request):
        import json
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=[{"universe": []}, []])

    api = AsyncHLInfo(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    out = await api.meta_and_asset_ctxs()
    assert seen["body"] == {"type": "metaAndAssetCtxs"}
    assert out[0] == {"universe": []}
    await api.aclose()


@pytest.mark.asyncio
async def test_lighter_raises_on_error_code():
    async def handler(request):
        return httpx.Response(200, json={"code": 400, "message": "bad"})

    api = AsyncLighterREST(httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                             base_url="https://x"))
    with pytest.raises(RuntimeError):
        await api.order_books()
    await api.aclose()


@pytest.mark.asyncio
async def test_lighter_order_book_details_unwraps_list():
    async def handler(request):
        return httpx.Response(200, json={"code": 200, "order_book_details": [{"market_id": 1}]})

    api = AsyncLighterREST(httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                             base_url="https://x"))
    assert await api.order_book_details() == [{"market_id": 1}]
    await api.aclose()
