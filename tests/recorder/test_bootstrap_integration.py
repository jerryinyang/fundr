"""The exact failure mode a reviewer found: `__main__._run()` calls the universe sweep once,
BEFORE the Supervisor exists to restart it on failure. If a venue changes its response shape,
an unguarded `cycle()` raises there, the whole process dies before any feed starts, systemd
restarts it into the same crash 10 seconds later, and BOTH `hl_state` and `lighter_state` record
nothing, forever -- requirement 1's failure mode reintroduced at the one call site no other test
reaches. `UniverseFeed.bootstrap()` (used by `__main__` instead of a bare `cycle()`) exists to
prevent exactly this. This test reproduces the shape `_run()` actually wires up (bootstrap sweep,
then hl_state + lighter_state supervised) and proves a malformed bootstrap response does not stop
either feed from running and recording afterwards."""
import asyncio
import gzip
import json

from fundr.recorder.config import Clock, Config
from fundr.recorder.feeds.hl_state import HLStateFeed
from fundr.recorder.feeds.lighter_state import LighterStateFeed
from fundr.recorder.feeds.universe import UniverseFeed
from fundr.recorder.health import Health
from fundr.recorder.supervisor import Supervisor
from fundr.recorder.writer import HourlyWriter


def _rows(root, feed):
    out = []
    for p in sorted((root / feed).rglob("*.jsonl.gz")):
        out += [json.loads(x) for x in gzip.open(p, "rt").read().splitlines()]
    return out


class MalformedHL:
    """The bootstrap-time universe.meta() call returns something that arrived fine over the
    network but no longer has the shape `cycle()` assumes -- the case `_fetch`'s try/except
    around the AWAIT cannot catch, because the failure is in what was returned, not in awaiting
    it."""
    async def meta(self):
        return {"NOT_universe_anymore": []}


class FakeLighterREST:
    async def order_books(self):
        return [{"symbol": "BTC", "market_id": 1, "status": "active", "created_at": "1"}]

    async def order_book_details(self):
        return [{"symbol": "BTC", "market_id": 1, "status": "active",
                 "funding_premium_multiplier": 100, "funding_clamp_small": "0.0500",
                 "funding_clamp_big": "4.0000", "base_interest_rate": "0.0100"}]


class FakeHLState:
    async def meta_and_asset_ctxs(self):
        return [{"universe": [{"name": "BTC"}]}, [{"funding": "0.0000125"}]]

    async def predicted_fundings(self):
        return []


class FakeSocket:
    def __init__(self):
        self.n = 0

    async def send(self, _payload):
        return None

    async def recv(self):
        await asyncio.sleep(0.02)
        self.n += 1
        return json.dumps({"market_stats": {"market_id": 1, "symbol": "BTC",
                                            "premium": f"0.{self.n:06d}",
                                            "current_funding_rate": "0.0012"}})

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


async def test_a_malformed_bootstrap_sweep_still_leaves_both_feeds_running_and_recording(tmp_path):
    cfg = Config(root=tmp_path, hl_poll_s=1, lighter_heartbeat_s=1, watchdog_s=1)
    clock = Clock()
    health = Health(clock)
    writers = {n: HourlyWriter(tmp_path, n) for n in ("hl_state", "lighter_state", "universe")}

    lighter = LighterStateFeed(cfg, writers["lighter_state"], health, clock,
                               connect=lambda: FakeSocket())
    universe = UniverseFeed(cfg, writers["universe"], health, clock,
                            MalformedHL(), FakeLighterREST(), on_markets=lighter.set_markets)

    # This is the exact line __main__._run() runs before the Supervisor exists. It must not
    # raise, even though the HL response is malformed.
    ids = await universe.bootstrap()
    assert ids == []          # the malformed venue contributed nothing this sweep...
    # ...but the OTHER venue's response was fine, so the periodic sweep already gave the
    # Lighter feed something to subscribe to -- bootstrap() degrading gracefully on one venue
    # must not also discard a healthy response from the other.
    lighter.mark_subscribed([1])

    hl = HLStateFeed(cfg, writers["hl_state"], health, clock, FakeHLState())

    stop = asyncio.Event()
    sup = Supervisor(cfg, clock, writers=writers, health=health,
                     feeds={"hl_state": lambda: hl.run(stop),
                            "lighter_state": lambda: lighter.run(stop)})
    task = asyncio.create_task(sup.run(stop))
    await asyncio.sleep(2.5)
    stop.set()
    await asyncio.wait_for(task, timeout=10)

    hl_rows = [r for r in _rows(tmp_path, "hl_state") if r.get("type") != "gap"]
    lighter_rows = [r for r in _rows(tmp_path, "lighter_state") if r.get("type") != "gap"]
    assert hl_rows, "hl_state must still record after a malformed bootstrap sweep"
    assert lighter_rows, "lighter_state must still record after a malformed bootstrap sweep"

    universe_gaps = [r for r in _rows(tmp_path, "universe") if r.get("type") == "gap"]
    assert any(g["reason"].startswith("bootstrap_error:KeyError") for g in universe_gaps), \
        "the bootstrap failure itself must be recorded, not just swallowed silently"
