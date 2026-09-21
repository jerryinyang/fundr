import httpx
import pytest

from scripts.backfill_hl_funding import backfill_coin


def _row(t, rate="0.0000125", premium="0.0001"):
    return {"coin": "BTC", "fundingRate": rate, "premium": premium, "time": t}


class FakeHL:
    """Pages like the real endpoint: at most `page_size` rows at or after startTime."""

    def __init__(self, rows=None, page_size=2, fail_on_call=()):
        self.rows = sorted(rows or [], key=lambda r: r["time"])
        self.page_size = page_size
        self.fail_on_call = set(fail_on_call)
        self.calls = 0

    def post(self, payload):
        self.calls += 1
        if self.calls in self.fail_on_call:
            request = httpx.Request("POST", "https://api.hyperliquid.xyz/info")
            raise httpx.HTTPStatusError("429", request=request,
                                        response=httpx.Response(429, request=request))
        lo, hi = payload["startTime"], payload["endTime"]
        return [r for r in self.rows if lo <= r["time"] <= hi][:self.page_size]


def test_backfill_coin_returns_typed_frame_with_signed_fraction():
    hl = FakeHL([_row(3_600_000), _row(7_200_000, rate="-0.00002")])
    df = backfill_coin(hl, "BTC", start_ms=0, end_ms=10_000_000, sleep=lambda s: None)
    assert df.height == 2
    assert df["signed_rate_fraction"].to_list() == [0.0000125, -0.00002]
    assert str(df.schema["settle_time"]).startswith("Datetime")


def test_backfill_coin_pages_and_concatenates():
    rows = [_row(3_600_000 * i) for i in range(1, 6)]
    hl = FakeHL(rows, page_size=2)
    df = backfill_coin(hl, "BTC", start_ms=0, end_ms=10_000_000_000, sleep=lambda s: None)
    assert df.height == 5
    assert hl.calls >= 3                      # 2 + 2 + 1, then one empty page to stop
    assert df["time"].is_sorted()


def test_retry_is_per_page_not_per_coin():
    # A 429 on the second page must resume AT the second page. Five rows at page_size 2 take
    # exactly 3 content pages + 1 terminating empty page; with one injected 429 that is 5 calls.
    # Had the retry wrapped the whole paging loop, it would have restarted from startTime=0 and
    # re-fetched page 1, costing a sixth call.
    rows = [_row(3_600_000 * i) for i in range(1, 6)]
    hl = FakeHL(rows, page_size=2, fail_on_call=[2])
    df = backfill_coin(hl, "BTC", start_ms=0, end_ms=10_000_000_000, sleep=lambda s: None)
    assert df.height == 5
    assert hl.calls == 5


def test_backfill_coin_is_empty_not_error_when_no_history():
    df = backfill_coin(FakeHL([]), "BTC", start_ms=0, end_ms=10_000_000,
                       sleep=lambda s: None)
    assert df.is_empty()
