import asyncio

import pytest

from fundr.recorder.config import Clock, Config
from fundr.recorder.feeds.hl_state import HLStateFeed
from fundr.recorder.health import Health
from fundr.recorder.writer import HourlyWriter

META = {"universe": [{"name": "BTC"}, {"name": "OLD", "isDelisted": True}]}
CTXS = [{"funding": "0.0000125", "openInterest": "10", "markPx": "100", "premium": "0.0005"},
        {"funding": "0.0", "openInterest": "0", "markPx": "1", "premium": None}]
PRED = [["BTC", [["HlPerp", {"fundingRate": "0.0000125", "nextFundingTime": 1}]]]]


class FakeHL:
    def __init__(self, hang: bool = False, fail: bool = False):
        self.hang, self.fail, self.calls = hang, fail, 0

    async def meta_and_asset_ctxs(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("boom")
        if self.hang:
            await asyncio.sleep(3600)
        return [META, CTXS]

    async def predicted_fundings(self):
        return PRED


def _rows(path_root, feed="hl_state"):
    import gzip, json
    out = []
    for p in sorted((path_root / feed).rglob("*.jsonl.gz")):
        out += [json.loads(x) for x in gzip.open(p, "rt").read().splitlines()]
    return out


def _feed(tmp_path, client, now_ms=3_600_000 * 100, mono_ns=0, watchdog_s=1):
    cfg = Config(root=tmp_path, watchdog_s=watchdog_s)
    clock = Clock(now_ms=lambda: now_ms, mono_ns=lambda: mono_ns, sleep=asyncio.sleep)
    w = HourlyWriter(tmp_path, "hl_state")
    return HLStateFeed(cfg, w, Health(clock), clock, client), w


async def test_cycle_writes_one_record_per_live_market(tmp_path):
    feed, w = _feed(tmp_path, FakeHL())
    await feed.cycle()
    w.close()
    rows = _rows(tmp_path)
    assert [r["payload"]["coin"] for r in rows] == ["BTC", "OLD"]
    btc = rows[0]
    assert btc["payload"]["ctx"]["funding"] == "0.0000125"
    assert btc["payload"]["predicted"][0][0] == "HlPerp"
    assert btc["payload"]["meta"].get("isDelisted") in (None, False)
    assert rows[1]["payload"]["meta"]["isDelisted"] is True
    assert [r["seq"] for r in rows] == [1, 2]


async def test_hung_call_is_bounded_and_writes_a_gap(tmp_path):
    feed, w = _feed(tmp_path, FakeHL(hang=True), watchdog_s=1)
    await asyncio.wait_for(feed.cycle(), timeout=5)  # must return, not hang
    w.close()
    rows = _rows(tmp_path)
    assert [r["type"] for r in rows] == ["gap"]
    assert rows[0]["reason"] == "watchdog_timeout"
    assert rows[0]["seq"] == 1


async def test_failed_call_writes_a_gap_and_seq_stays_continuous(tmp_path):
    client = FakeHL(fail=True)
    feed, w = _feed(tmp_path, client)
    await feed.cycle()
    client.fail = False
    await feed.cycle()
    w.close()
    rows = _rows(tmp_path)
    assert rows[0]["type"] == "gap" and rows[0]["reason"] == "poll_error"
    # seq is continuous across the gap record and the error envelope, so a reader can tell a
    # declared hole from a lost record. run_id is constant within a process.
    assert [r["seq"] for r in rows] == list(range(1, len(rows) + 1))
    assert len({r["run_id"] for r in rows}) == 1


async def test_monotonic_jump_is_recorded_as_suspension(tmp_path):
    cfg = Config(root=tmp_path, hl_poll_s=60)
    state = {"mono": 0, "wall": 3_600_000 * 100}
    clock = Clock(now_ms=lambda: state["wall"], mono_ns=lambda: state["mono"],
                  sleep=asyncio.sleep)
    w = HourlyWriter(tmp_path, "hl_state")
    feed = HLStateFeed(cfg, w, Health(clock), clock, FakeHL())
    await feed.cycle()
    state["mono"] += 600 * 1_000_000_000   # 10 minutes of monotonic time
    state["wall"] += 600_000
    await feed.cycle()
    w.close()
    gaps = [r for r in _rows(tmp_path) if r.get("type") == "gap"]
    assert gaps and gaps[0]["reason"] == "suspend_or_hang"


async def test_wall_clock_only_jump_is_a_clock_step(tmp_path):
    cfg = Config(root=tmp_path, hl_poll_s=60)
    state = {"mono": 0, "wall": 3_600_000 * 100}
    clock = Clock(now_ms=lambda: state["wall"], mono_ns=lambda: state["mono"],
                  sleep=asyncio.sleep)
    w = HourlyWriter(tmp_path, "hl_state")
    feed = HLStateFeed(cfg, w, Health(clock), clock, FakeHL())
    await feed.cycle()
    state["mono"] += 60 * 1_000_000_000    # one normal interval
    state["wall"] += 3_600_000             # but an hour of wall clock
    await feed.cycle()
    w.close()
    gaps = [r for r in _rows(tmp_path) if r.get("type") == "gap"]
    assert gaps and gaps[0]["reason"] == "clock_step"
