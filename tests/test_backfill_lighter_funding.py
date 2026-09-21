import httpx
import pytest

from scripts.backfill_lighter_funding import backfill_market

MARKET = {"market_id": 1, "symbol": "BTC", "created_at": "0"}


def _row(ts, rate="0.0012", direction="long"):
    return {"timestamp": ts, "value": "1.0", "rate": rate, "direction": direction}


class FakeLighter:
    def __init__(self, rows=None, fail_on_call=()):
        self.rows = rows or []
        self.fail_on_call = set(fail_on_call)
        self.calls = 0
        self.windows = []

    def fundings(self, market_id, resolution, start_s, end_s, count_back):
        self.calls += 1
        self.windows.append((start_s, end_s))
        if self.calls in self.fail_on_call:
            request = httpx.Request("GET", "https://mainnet.zklighter.elliot.ai/api/v1/fundings")
            raise httpx.HTTPStatusError("429", request=request,
                                        response=httpx.Response(429, request=request))
        return [r for r in self.rows if start_s <= r["timestamp"] < end_s]


def test_signed_fraction_is_percent_divided_by_100():
    api = FakeLighter([_row(3600, "0.0012", "long"), _row(7200, "0.0478", "short")])
    df = backfill_market(api, MARKET, end_s=10_000, sleep=lambda s: None)
    assert df["signed_rate"].to_list() == [0.0012, -0.0478]
    assert df["signed_rate_fraction"].to_list() == pytest.approx([0.000012, -0.000478])


def test_settle_time_is_datetime_and_sorted():
    api = FakeLighter([_row(7200), _row(3600)])
    df = backfill_market(api, MARKET, end_s=10_000, sleep=lambda s: None)
    assert str(df.schema["settle_time"]).startswith("Datetime")
    assert df["timestamp"].to_list() == [3600, 7200]


def test_empty_history_returns_empty_frame():
    # A market that has stopped settling returns zero rows for any recent window -- market 173
    # (SPACEX) last settled 2026-06-18, though its 985-hour history is complete and intact. An
    # empty frame is something to report, never something to raise on.
    df = backfill_market(FakeLighter([]), MARKET, end_s=10_000, sleep=lambda s: None)
    assert df.is_empty()


def test_retry_is_per_window_not_per_market():
    rows = [_row(3600 * i) for i in range(1, 4)]   # 3600, 7200, 10800 -- all inside end_s
    api = FakeLighter(rows, fail_on_call=[1])
    df = backfill_market(api, MARKET, end_s=20_000, sleep=lambda s: None)
    assert df.height == 3
    assert api.calls == 2                    # the failed window, then the same window again
    assert api.windows[0] == api.windows[1]  # it resumed AT the failure, not before it


def test_sub_period_window_is_skipped_not_requested():
    # Measured 2026-09-21: resuming a market seconds after its last settlement, with under an
    # hour left before "now", asks Lighter for a window under 3600s. Lighter answers that with a
    # permanent HTTP 400 {"code":22400,"message":"invalid timestamps: end_timestamp must be
    # greater than start_timestamp"} -- not retryable, and not a real gap: nothing could have
    # settled in under one funding period. The script must recognise this and skip the call.
    api = FakeLighter([])
    df = backfill_market(api, MARKET, start_s=9_000, end_s=9_500, sleep=lambda s: None)
    assert df.is_empty()
    assert api.calls == 0
