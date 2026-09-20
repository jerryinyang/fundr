"""0xArchive REST client. Free tier: last 30 days of data; credits/month is plan-dependent
(observed 200k on this account, not the 50k sometimes quoted -- verify against your own plan).
Every call is logged so P8 can report credit use."""
import os

import httpx

BASE_URL = "https://api.0xarchive.io"
_KEEP_HEADERS = ("credit", "ratelimit", "rate-limit", "request-id")


class OXArchive:
    def __init__(self, client: httpx.Client | None = None, key: str | None = None):
        key = key or os.environ["OXARCHIVE_API_KEY"]
        self._client = client or httpx.Client(base_url=BASE_URL, timeout=60)
        self._client.headers["X-API-Key"] = key
        self.calls: list[dict] = []

    def get(self, path: str, **params) -> dict:
        r = self._client.get(path, params=params)
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        self.calls.append({
            "path": path,
            "params": params,
            "status": r.status_code,
            "request_id": (body.get("meta") or {}).get("request_id") or r.headers.get("x-request-id"),
            "headers": {k: v for k, v in r.headers.items() if any(s in k.lower() for s in _KEEP_HEADERS)},
        })
        r.raise_for_status()
        return body

    def get_all(self, path: str, max_pages: int = 20, **params) -> list:
        # WARNING: truncates silently at max_pages -- if the vendor has more pages than that,
        # you get a partial result with no error or flag indicating it was cut short.
        out, cursor = [], None
        for _ in range(max_pages):
            body = self.get(path, **params, **({"cursor": cursor} if cursor else {}))
            out += body.get("data") or []
            cursor = (body.get("meta") or {}).get("next_cursor")
            if not cursor:
                break
        return out
