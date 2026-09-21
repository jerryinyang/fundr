import asyncio
import gzip
import json

from fundr.recorder.config import Clock, Config
from fundr.recorder.feeds.hl_state import HLStateFeed
from fundr.recorder.feeds.lighter_state import LighterStateFeed
from fundr.recorder.health import Health
from fundr.recorder.supervisor import Supervisor, supervise
from fundr.recorder.writer import HourlyWriter


def _rows(root, feed):
    out = []
    for p in sorted((root / feed).rglob("*.jsonl.gz")):
        out += [json.loads(x) for x in gzip.open(p, "rt").read().splitlines()]
    return out


async def test_supervise_restarts_a_failing_task():
    calls = {"n": 0}
    stop = asyncio.Event()

    async def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("boom")
        stop.set()

    clock = Clock(sleep=lambda s: asyncio.sleep(0))
    await asyncio.wait_for(supervise("flaky", flaky, stop, clock), timeout=5)
    assert calls["n"] == 3


class HangingHL:
    """A REST client whose call never returns — Phase 1's 84-minute read, in miniature."""

    def __init__(self):
        self.started = 0
        self.finished = 0

    async def meta_and_asset_ctxs(self):
        self.started += 1
        await asyncio.sleep(3600)
        self.finished += 1

    async def predicted_fundings(self):
        return []


class FakeSocket:
    """A Lighter socket that keeps delivering changing premia."""

    def __init__(self):
        self.n = 0

    async def send(self, _payload):
        return None

    async def recv(self):
        await asyncio.sleep(0.02)
        self.n += 1
        return json.dumps({"channel": "market_stats:1", "timestamp": self.n,
                           "type": "update/market_stats",
                           "market_stats": {"market_id": 1, "symbol": "BTC",
                                            "premium": f"0.{self.n:06d}",
                                            "current_funding_rate": "0.0012"}})

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


async def test_a_hung_rest_feed_never_stops_the_websocket_feed(tmp_path):
    """Phase 1's actual failure, against the real feed classes rather than two synthetic
    coroutines: one hung REST call silently ended websocket recording. Lighter records must
    keep appearing throughout, AND the hung poll must be cancelled by its own watchdog."""
    cfg = Config(root=tmp_path, hl_poll_s=1, lighter_heartbeat_s=1, watchdog_s=1)
    clock = Clock()                                   # real time: this test is about timing
    health = Health(clock)
    writers = {n: HourlyWriter(tmp_path, n) for n in ("hl_state", "lighter_state")}

    hl_client = HangingHL()
    hl = HLStateFeed(cfg, writers["hl_state"], health, clock, hl_client)
    socket = FakeSocket()
    lighter = LighterStateFeed(cfg, writers["lighter_state"], health, clock,
                               connect=lambda: socket)
    lighter.set_markets([1])
    lighter.mark_subscribed([1])

    stop = asyncio.Event()
    sup = Supervisor(cfg, clock, writers=writers, health=health,
                     feeds={"hl_state": lambda: hl.run(stop),
                            "lighter_state": lambda: lighter.run(stop)})
    task = asyncio.create_task(sup.run(stop))
    await asyncio.sleep(2.5)
    stop.set()
    await asyncio.wait_for(task, timeout=10)

    lighter_rows = _rows(tmp_path, "lighter_state")
    assert len(lighter_rows) >= 5, "the websocket feed must keep recording while REST is hung"
    assert any(r["payload"]["premium"] != lighter_rows[0]["payload"]["premium"]
               for r in lighter_rows), "the records must be fresh, not one value repeated"

    hl_rows = _rows(tmp_path, "hl_state")
    assert hl_client.started >= 2, "the poll must be retried, not left hanging forever"
    assert hl_client.finished == 0
    assert any(r.get("reason") == "watchdog_timeout" for r in hl_rows), \
        "the hung poll must be cancelled by its watchdog and recorded as a gap"


async def test_health_file_is_written(tmp_path):
    stop = asyncio.Event()

    async def idle():
        await asyncio.sleep(3600)

    sup = Supervisor(Config(root=tmp_path), feeds={"hl_state": idle})
    task = asyncio.create_task(sup.run(stop))
    await asyncio.sleep(0.2)
    stop.set()
    await asyncio.wait_for(task, timeout=5)
    body = json.loads((tmp_path / "health.json").read_text())
    assert body["status"] in {"ok", "degraded", "broken"}
