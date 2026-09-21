import asyncio
import gzip
import json

from fundr.recorder.config import Clock, Config
from fundr.recorder.feeds.lighter_state import LighterStateFeed
from fundr.recorder.health import Health
from fundr.recorder.writer import HourlyWriter

HOUR = 3_600_000


def _stats(market_id=1, premium="0.0010", cfr="0.0012", symbol="BTC", ws_ts=1789812743):
    return {"channel": f"market_stats:{market_id}",
            "market_stats": {"market_id": market_id, "symbol": symbol, "premium": premium,
                             "current_funding_rate": cfr, "funding_rate": "0.0011",
                             "funding_timestamp": HOUR * 100, "open_interest": "5"},
            "timestamp": ws_ts, "type": "update/market_stats"}


def _rows(root):
    out = []
    for p in sorted((root / "lighter_state").rglob("*.jsonl.gz")):
        out += [json.loads(x) for x in gzip.open(p, "rt").read().splitlines()]
    return out


def _feed(tmp_path, now_ms=HOUR * 100, markets=(1,)):
    """The fake clock's sleep advances the fake wall clock, so a test that drives the timers
    experiences real elapsed time. The fixture also calls mark_subscribed: without it the
    feed's pending lists never settle and every subscription assertion is meaningless."""
    state = {"wall": now_ms}

    async def sleep(seconds: float) -> None:
        state["wall"] += int(seconds * 1000)
        await asyncio.sleep(0)

    clock = Clock(now_ms=lambda: state["wall"], mono_ns=lambda: 0, sleep=sleep)
    w = HourlyWriter(tmp_path, "lighter_state")
    feed = LighterStateFeed(Config(root=tmp_path), w, Health(clock), clock)
    feed.set_markets(list(markets))
    feed.mark_subscribed(list(markets))
    return feed, w, state


async def test_value_change_writes_immediately(tmp_path):
    feed, w, _ = _feed(tmp_path)
    feed.on_message(_stats(premium="0.0010"))
    feed.on_message(_stats(premium="0.0010"))   # unchanged: no new record
    feed.on_message(_stats(premium="0.0020"))   # changed: record
    w.close()
    rows = [r for r in _rows(tmp_path) if r.get("trigger") == "change"]
    assert [r["payload"]["premium"] for r in rows] == ["0.0010", "0.0020"]
    assert rows[-1]["n_funding_changes"] >= 1


async def test_boundary_snapshot_captures_the_last_pre_settlement_value(tmp_path):
    feed, w, state = _feed(tmp_path, now_ms=HOUR * 100)
    feed.on_message(_stats(premium="0.0010"))
    state["wall"] = HOUR * 101 - 30_000        # HH:59:30
    feed.on_message(_stats(premium="0.0099"))  # the value that will settle
    feed.snapshot("boundary")
    w.close()
    boundary = [r for r in _rows(tmp_path) if r["trigger"] == "boundary"]
    assert boundary and boundary[-1]["payload"]["premium"] == "0.0099"
    assert boundary[-1]["stale"] is False


async def test_records_carry_age_and_the_venue_message_timestamp(tmp_path):
    """The venue stamps its own message; keeping only market_stats threw that away for good,
    and capture time is not the same thing."""
    feed, w, state = _feed(tmp_path)
    feed.on_message(_stats(ws_ts=1789812743))
    state["wall"] += 9_000
    feed.snapshot("heartbeat")
    w.close()
    hb = [r for r in _rows(tmp_path) if r["trigger"] == "heartbeat"][-1]
    assert hb["ws_ts"] == 1789812743
    assert hb["ws_type"] == "update/market_stats"
    assert hb["age_ms"] == 9_000


async def test_two_silent_heartbeat_intervals_go_stale_and_force_a_reconnect(tmp_path):
    feed, w, state = _feed(tmp_path)
    feed.on_message(_stats())
    feed.snapshot("heartbeat")                  # interval had messages
    state["wall"] += 60_000
    feed.snapshot("heartbeat")                  # silent interval 1
    state["wall"] += 60_000
    feed.snapshot("heartbeat")                  # silent interval 2 -> stale + reconnect
    w.close()
    hb = [r for r in _rows(tmp_path) if r["trigger"] == "heartbeat"]
    assert hb[0]["n_msgs"] >= 1 and hb[0]["stale"] is False
    assert hb[-1]["stale"] is True and hb[-1]["age_ms"] == 120_000
    assert feed.needs_reconnect is True


async def test_boundary_snapshots_do_not_trip_the_silence_rule(tmp_path):
    """The two boundary snapshots are 22 s apart, which is not a heartbeat interval. Counting
    them as silent intervals would force a reconnect at every hour boundary."""
    feed, w, state = _feed(tmp_path, now_ms=HOUR * 101 - 30_000)
    feed.on_message(_stats())
    feed.snapshot("boundary")                   # HH:59:30
    state["wall"] += 22_000
    feed.snapshot("boundary")                   # HH:59:52, no messages in between
    assert feed.needs_reconnect is False


async def test_one_active_market_does_not_mask_a_silent_one(tmp_path):
    """Staleness is scoped per market: a single chatty market must not make a dead
    subscription look alive."""
    feed, w, state = _feed(tmp_path, markets=(1, 2))
    feed.on_message(_stats(market_id=1, symbol="BTC"))
    feed.on_message(_stats(market_id=2, symbol="ETH"))
    for i in range(3):                          # market 1 keeps talking, market 2 goes quiet
        state["wall"] += 60_000
        feed.on_message(_stats(market_id=1, symbol="BTC", premium=f"0.002{i}"))
        feed.snapshot("heartbeat")
    w.close()
    last = {r["payload"]["market_id"]: r
            for r in _rows(tmp_path) if r["trigger"] == "heartbeat"}
    assert last[1]["stale"] is False and last[1]["age_ms"] == 0
    assert last[2]["stale"] is True and last[2]["age_ms"] == 180_000
    assert feed.needs_reconnect is False        # the socket itself is alive


async def test_set_markets_adds_and_removes(tmp_path):
    feed, w, _ = _feed(tmp_path)                # fixture has already subscribed market 1
    feed.set_markets([1, 2])
    assert feed.pending_subscribe == [2] and feed.pending_unsubscribe == []
    feed.mark_subscribed([2])
    feed.set_markets([2])
    assert feed.pending_unsubscribe == [1] and feed.pending_subscribe == []


async def test_next_boundary_sleep_is_before_the_hour_ends(tmp_path):
    feed, _, state = _feed(tmp_path, now_ms=HOUR * 100)
    state["wall"] = HOUR * 100 + 1000           # 1s into the hour
    assert 3500 < feed.next_boundary_sleep_s() < 3600
    state["wall"] = HOUR * 101 - 20_000         # HH:59:40, past the first offset (30 s)
    assert 11 < feed.next_boundary_sleep_s() <= 12      # the second offset is at HH:59:52


async def test_timers_label_the_pre_boundary_snapshots_boundary(tmp_path):
    """Drives _timers, not snapshot(), across an hour boundary. Deciding the trigger by
    re-querying next_boundary_sleep_s() after the sleep labels the hour's critical record
    'heartbeat' — and Task 9's soak gate and Task 11's diagnostic both look for 'boundary'."""
    state = {"wall": HOUR * 101 - 150_000}      # HH:57:30
    stop = asyncio.Event()

    async def sleep(seconds: float) -> None:
        state["wall"] += int(seconds * 1000)
        if state["wall"] >= HOUR * 101 + 30_000:
            stop.set()
        await asyncio.sleep(0)

    clock = Clock(now_ms=lambda: state["wall"], mono_ns=lambda: 0, sleep=sleep)
    w = HourlyWriter(tmp_path, "lighter_state")
    feed = LighterStateFeed(Config(root=tmp_path), w, Health(clock), clock)
    feed.set_markets([1])
    feed.mark_subscribed([1])
    feed.on_message(_stats(premium="0.0099"))

    await asyncio.wait_for(feed._timers(stop), timeout=5)
    w.close()
    rows = _rows(tmp_path)
    triggers = [r["trigger"] for r in rows]
    assert triggers.count("boundary") == 2, triggers    # HH:59:30 and HH:59:52
    boundary = [r for r in rows if r["trigger"] == "boundary"]
    assert boundary[-1]["payload"]["premium"] == "0.0099"
    # Both land in the closing hour's partition, not the next one.
    assert all(r["t_ms"] < HOUR * 101 for r in boundary)


class _FakeConn:
    """Async context manager mimicking a websockets connection: __aenter__ yields an object
    with async send/recv, __aexit__ performs the (bounded) close. This is the shape
    LighterStateFeed actually needs from `connect` -- not the send/recv/close object the
    module's old docstring wording implied."""

    def __init__(self, tag, on_recv=None):
        self.tag = tag
        self.sent: list[str] = []
        self._recv_calls = 0
        self._on_recv = on_recv

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def send(self, msg: str) -> None:
        self.sent.append(msg)
        await asyncio.sleep(0)

    async def recv(self) -> str:
        self._recv_calls += 1
        if self._on_recv is not None:
            self._on_recv(self)
        if self.tag == "first":
            if self._recv_calls == 1:
                return json.dumps(_stats(market_id=1, premium="0.0099"))
            raise ConnectionError("dropped")   # simulate the socket dying
        await asyncio.Future()                 # second connection: never delivers another
                                                # frame; only cancellation ends this await


async def test_connect_subscribe_drop_reconnect_boundary_still_fires(tmp_path):
    """Full connection-path coverage (run/_connections/_reader/_sync_subscriptions), which had
    zero tests before this fix round. It exercises: a sibling task rebinding the wanted-market
    set mid-sync (the aliasing bug in finding 1), a socket drop forcing a reconnect, and proves
    the boundary snapshot still fires across that reconnect because the timers never lived
    inside the connection loop."""
    state = {"wall": HOUR * 101 - 200_000}      # HH:56:40
    stop = asyncio.Event()

    async def sleep(seconds: float) -> None:
        state["wall"] += int(seconds * 1000)
        if state["wall"] >= HOUR * 101 + 30_000:
            stop.set()
        await asyncio.sleep(0)

    clock = Clock(now_ms=lambda: state["wall"], mono_ns=lambda: 0, sleep=sleep)
    w = HourlyWriter(tmp_path, "lighter_state")
    feed = LighterStateFeed(Config(root=tmp_path), w, Health(clock), clock)
    feed.set_markets([1, 2])

    connections: list[_FakeConn] = []
    captured: dict = {}

    def on_recv(conn):
        # By the time recv() is ever called, _sync_subscriptions has already fully run for
        # this connection. Capture its result here, before the drop, so a fix regression
        # (finding 1's aliasing) is visible even though the impending reconnect would
        # otherwise erase the evidence by clearing _subscribed again.
        if conn.tag == "first" and conn._recv_calls == 1:
            captured["subscribed_after_first_sync"] = sorted(feed._subscribed)
            captured["pending_after_first_sync"] = list(feed.pending_subscribe)
            captured["first_sent"] = list(conn.sent)

    def connect():
        tag = "first" if not connections else "second"
        conn = _FakeConn(tag, on_recv=on_recv)
        connections.append(conn)
        if tag == "first":
            # Simulate a sibling task (the universe sweep) rebinding the wanted-market set
            # while `_sync_subscriptions` is still mid-flight, awaiting an earlier send.
            real_send = conn.send

            async def send(msg: str) -> None:
                await real_send(msg)
                if '"market_stats/2"' in msg:
                    feed.set_markets([1, 2, 300])

            conn.send = send
        return conn

    feed._connect = connect

    await asyncio.wait_for(feed.run(stop), timeout=5)
    w.close()

    # finding 1: only the markets actually sent-for on the first connection ended up marked
    # subscribed; the market injected mid-sync must not be marked subscribed for free.
    assert captured["subscribed_after_first_sync"] == [1, 2]
    assert captured["pending_after_first_sync"] == [300]
    sent_channels = {json.loads(m)["channel"] for m in captured["first_sent"]}
    assert sent_channels == {"market_stats/1", "market_stats/2"}

    # The socket dropped after the first message and had to reconnect (a second connection
    # was actually opened).
    assert len(connections) == 2
    assert connections[1].tag == "second"

    # The boundary snapshot for the closing hour still fired, across that reconnect, because
    # the timers live outside the connection loop.
    rows = _rows(tmp_path)
    boundary = [r for r in rows if r.get("trigger") == "boundary"]
    assert boundary, [r.get("trigger") for r in rows]
    assert boundary[-1]["payload"]["premium"] == "0.0099"

    # The drop was recorded with a real (non-zero) width, not from_ms == to_ms.
    gaps = [r for r in rows if r.get("type") == "gap"]
    assert any(g["to_ms"] > g["from_ms"] for g in gaps), gaps
