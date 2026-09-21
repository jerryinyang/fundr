import asyncio
import gzip
import json

from fundr.recorder.config import Clock, Config
from fundr.recorder.feeds.universe import UniverseFeed
from fundr.recorder.health import Health
from fundr.recorder.writer import HourlyWriter

HOUR = 3_600_000


class FakeHL:
    async def meta(self):
        return {"universe": [{"name": "BTC"}, {"name": "OLD", "isDelisted": True}]}


class FakeLighter:
    async def order_books(self):
        return [{"symbol": "BTC", "market_id": 1, "status": "active", "created_at": "1737098461107"},
                {"symbol": "DEAD", "market_id": 9, "status": "inactive", "created_at": "1"}]

    async def order_book_details(self):
        return [{"symbol": "BTC", "market_id": 1, "status": "active",
                 "funding_premium_multiplier": 100, "funding_clamp_small": "0.0500",
                 "funding_clamp_big": "4.0000", "base_interest_rate": "0.0100"}]


class FailingHL:
    """meta() either hangs (to trip the watchdog) or raises, depending on construction."""
    def __init__(self, hang: bool = False, fail: bool = False):
        self.hang, self.fail = hang, fail

    async def meta(self):
        if self.fail:
            raise RuntimeError("boom")
        if self.hang:
            await asyncio.sleep(3600)
        return {"universe": [{"name": "BTC"}]}


class FailingDetailsLighter(FakeLighter):
    async def order_book_details(self):
        raise RuntimeError("details boom")


class FailingLighter:
    """Both Lighter calls either hang or raise."""
    def __init__(self, hang: bool = False, fail: bool = False):
        self.hang, self.fail = hang, fail

    async def order_books(self):
        if self.fail:
            raise RuntimeError("books boom")
        if self.hang:
            await asyncio.sleep(3600)
        return []

    async def order_book_details(self):
        if self.fail:
            raise RuntimeError("details boom")
        if self.hang:
            await asyncio.sleep(3600)
        return []


def _rows(tmp_path):
    out = []
    for p in sorted((tmp_path / "universe").rglob("*.jsonl.gz")):
        out += [json.loads(x) for x in gzip.open(p, "rt").read().splitlines()]
    return out


def _feed(tmp_path, on_markets=None, hl=None, lighter=None, cfg=None):
    clock = Clock(now_ms=lambda: HOUR * 100, mono_ns=lambda: 0, sleep=asyncio.sleep)
    w = HourlyWriter(tmp_path, "universe")
    feed = UniverseFeed(cfg or Config(root=tmp_path), w, Health(clock), clock,
                        hl or FakeHL(), lighter or FakeLighter(), on_markets)
    return feed, w


async def test_sweep_records_all_three_sources_including_funding_parameters(tmp_path):
    feed, w = _feed(tmp_path)
    await feed.cycle()
    w.close()
    p = next((tmp_path / "universe").rglob("*.jsonl.gz"))
    rec = json.loads(gzip.open(p, "rt").read().splitlines()[0])
    pay = rec["payload"]
    assert [m["name"] for m in pay["hl_universe"]] == ["BTC", "OLD"]
    assert {b["symbol"] for b in pay["lighter_order_books"]} == {"BTC", "DEAD"}
    # Stored raw: 100 means a multiplier of 1.0 (P7), and Phase 4 does the division, once.
    assert pay["lighter_details"][0]["funding_premium_multiplier"] == 100
    assert rec["trigger"] == "sweep"


async def test_active_markets_are_published_to_the_feed(tmp_path):
    seen = []
    feed, w = _feed(tmp_path, on_markets=seen.append)
    ids = await feed.cycle()
    w.close()
    assert ids == [1]          # only the active market
    assert seen == [[1]]


async def test_coverage_expectations_come_from_the_venue_market_lists(tmp_path):
    """The spec's Done-when: expected counts derive from the universe feed. A market that is
    listed but silent must show up at zero received, not vanish from health entirely."""
    clock = Clock(now_ms=lambda: HOUR * 100, mono_ns=lambda: 0, sleep=asyncio.sleep)
    w = HourlyWriter(tmp_path, "universe")
    health = Health(clock)
    feed = UniverseFeed(Config(root=tmp_path), w, health, clock, FakeHL(), FakeLighter())
    await feed.cycle()
    w.close()
    feeds = health.snapshot(HOUR * 100)["feeds"]
    assert set(feeds["hl_state"]["markets"]) == {"BTC"}          # OLD is delisted
    assert set(feeds["lighter_state"]["markets"]) == {"BTC"}     # DEAD is inactive
    assert feeds["lighter_state"]["markets"]["BTC"]["expected_hour"] == 60
    assert feeds["lighter_state"]["markets"]["BTC"]["received_window"] == 0


async def test_details_failure_still_writes_hl_and_books_partial(tmp_path):
    seen = []
    feed, w = _feed(tmp_path, on_markets=seen.append, lighter=FailingDetailsLighter())
    ids = await feed.cycle()
    w.close()
    rows = _rows(tmp_path)
    envelopes = [r for r in rows if r.get("type") != "gap"]
    assert len(envelopes) == 1
    pay = envelopes[0]["payload"]
    assert [m["name"] for m in pay["hl_universe"]] == ["BTC", "OLD"]
    assert {b["symbol"] for b in pay["lighter_order_books"]} == {"BTC", "DEAD"}
    assert pay["lighter_details"] is None
    assert envelopes[0]["partial"] is True
    gaps = [r for r in rows if r.get("type") == "gap"]
    assert [g["reason"] for g in gaps] == ["lighter_details_error"]
    assert ids == [1]
    assert seen == [[1]]


async def test_hl_meta_timeout_still_publishes_lighter_markets(tmp_path):
    seen = []
    cfg = Config(root=tmp_path, watchdog_s=1)
    feed, w = _feed(tmp_path, on_markets=seen.append, hl=FailingHL(hang=True), cfg=cfg)
    ids = await asyncio.wait_for(feed.cycle(), timeout=5)
    w.close()
    rows = _rows(tmp_path)
    gaps = [r for r in rows if r.get("type") == "gap"]
    assert any(g["reason"] == "hl_meta_timeout" for g in gaps)
    envelopes = [r for r in rows if r.get("type") != "gap"]
    assert len(envelopes) == 1
    pay = envelopes[0]["payload"]
    assert pay["hl_universe"] is None
    assert {b["symbol"] for b in pay["lighter_order_books"]} == {"BTC", "DEAD"}
    assert envelopes[0]["partial"] is True
    assert ids == [1]
    assert seen == [[1]]


async def test_total_failure_returns_empty_list_and_writes_three_gaps(tmp_path):
    feed, w = _feed(tmp_path, hl=FailingHL(fail=True), lighter=FailingLighter(fail=True))
    ids = await feed.cycle()
    w.close()
    rows = _rows(tmp_path)
    envelopes = [r for r in rows if r.get("type") != "gap"]
    gaps = [r for r in rows if r.get("type") == "gap"]
    assert envelopes == []
    assert ids == []
    assert {g["reason"] for g in gaps} == {"hl_meta_error", "lighter_books_error",
                                            "lighter_details_error"}


class MalformedHL:
    """A response that HAS arrived (so `_fetch`'s try/except never fires) but no longer has the
    shape `cycle()` assumes -- the scenario `_fetch`'s own guard cannot catch, because the
    failure is not in awaiting the coroutine, it is in what the coroutine returned."""
    async def meta(self):
        return {"NOT_universe_anymore": []}


async def test_bootstrap_survives_a_malformed_response_and_returns_empty_universe(tmp_path):
    """This is the bug the reviewer found: `cycle()` indexes `hl_meta["universe"]` with no
    guard once `_fetch` has returned a non-None response, so a venue that changes its response
    shape raises a bare KeyError. `cycle()` itself is meant to be run under supervision (which
    restarts on any exception) -- but `bootstrap()` is the one call site that runs BEFORE the
    supervisor exists. It must swallow the same failure, not propagate it."""
    feed, w = _feed(tmp_path, hl=MalformedHL())
    ids = await feed.bootstrap()
    w.close()
    assert ids == []
    rows = _rows(tmp_path)
    gaps = [r for r in rows if r.get("type") == "gap"]
    assert any(g["reason"].startswith("bootstrap_error:KeyError") for g in gaps)


async def test_bootstrap_passes_through_a_normal_sweep_unchanged(tmp_path):
    """`bootstrap()` must not change behaviour on the happy path -- it is a supervision wrapper
    around `cycle()`, not a different sweep."""
    seen = []
    feed, w = _feed(tmp_path, on_markets=seen.append)
    ids = await feed.bootstrap()
    w.close()
    assert ids == [1]
    assert seen == [[1]]
    rows = _rows(tmp_path)
    assert not any(r.get("type") == "gap" for r in rows)
