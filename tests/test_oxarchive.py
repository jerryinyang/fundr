import httpx

from fundr.sources import oxarchive


def _client(handler):
    http = httpx.Client(transport=httpx.MockTransport(handler), base_url=oxarchive.BASE_URL)
    return oxarchive.OXArchive(client=http, key="test")


def test_get_logs_call_with_credit_headers():
    def handler(request):
        return httpx.Response(
            200, json={"success": True, "data": [], "meta": {"request_id": "r1"}},
            headers={"x-credits-remaining": "49990", "content-type": "application/json"},
        )

    api = _client(handler)
    api.get("/v1/lighter/funding/BTC", start=1, end=2)
    call = api.calls[0]
    assert call["path"] == "/v1/lighter/funding/BTC" and call["request_id"] == "r1"
    assert call["headers"] == {"x-credits-remaining": "49990"}


def test_get_all_follows_cursor():
    pages = {None: (["a"], "c1"), "c1": (["b"], None)}

    def handler(request):
        data, nxt = pages[request.url.params.get("cursor")]
        return httpx.Response(200, json={"success": True, "data": data, "meta": {"next_cursor": nxt}})

    assert _client(handler).get_all("/v1/x") == ["a", "b"]


def test_key_accepts_either_env_spelling(monkeypatch):
    # The docs and Phase 1 standardised on OXARCHIVE_API_KEY, but the project's own .env
    # spells it ARCHIVE_OX_API_KEY. Reading only one name left Task 8's vendor cross-check
    # unrunnable with a key sitting right there, so both are accepted.
    for name in ("OXARCHIVE_API_KEY", "ARCHIVE_OX_API_KEY"):
        monkeypatch.delenv("OXARCHIVE_API_KEY", raising=False)
        monkeypatch.delenv("ARCHIVE_OX_API_KEY", raising=False)
        monkeypatch.setenv(name, f"key-from-{name}")
        api = oxarchive.OXArchive(client=httpx.Client(base_url=oxarchive.BASE_URL))
        assert api._client.headers["X-API-Key"] == f"key-from-{name}"
