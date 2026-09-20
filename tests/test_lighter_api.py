import httpx
import pytest

from fundr.sources import lighter_api


def _client(handler):
    return lighter_api.LighterAPI(
        httpx.Client(transport=httpx.MockTransport(handler), base_url=lighter_api.BASE_URL)
    )


def test_get_raises_on_error_code():
    api = _client(lambda r: httpx.Response(200, json={"code": 400, "message": "bad"}))
    with pytest.raises(RuntimeError):
        api.get("/api/v1/fundings")


def test_fundings_all_chunks_and_dedupes():
    calls = []

    def handler(request):
        p = request.url.params
        calls.append((int(p["start_timestamp"]), int(p["end_timestamp"])))
        start = int(p["start_timestamp"])
        rows = [{"timestamp": start, "value": "1", "rate": "0.0012", "direction": "long"},
                {"timestamp": 0, "value": "1", "rate": "0.0012", "direction": "long"}]
        return httpx.Response(200, json={"code": 200, "resolution": "1h", "fundings": rows})

    rows = _client(handler).fundings_all(1, "1h", 0, 3600 * 1500)
    assert calls[0] == (0, 3600 * 700)
    assert len(calls) == 3
    assert [r["timestamp"] for r in rows] == sorted({0, 3600 * 700, 3600 * 1400})


def test_signed_rate_verified():
    assert lighter_api.lighter_signed_rate(0.0012, "long") == 0.0012
    assert lighter_api.lighter_signed_rate(0.0012, "short") == -0.0012


def test_fundings_frame():
    df = lighter_api.fundings_frame(
        [{"timestamp": 1789804800, "value": "0.97", "rate": "0.0012", "direction": "short"}], 1
    )
    row = df.row(0, named=True)
    assert row["market_id"] == 1 and row["signed_rate"] == -0.0012 and row["rate_str"] == "0.0012"
    assert row["settle_time"].minute == 0
