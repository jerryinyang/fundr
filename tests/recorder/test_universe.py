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


def _feed(tmp_path, on_markets=None):
    clock = Clock(now_ms=lambda: HOUR * 100, mono_ns=lambda: 0, sleep=asyncio.sleep)
    w = HourlyWriter(tmp_path, "universe")
    feed = UniverseFeed(Config(root=tmp_path), w, Health(clock), clock,
                        FakeHL(), FakeLighter(), on_markets)
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
