"""Lighter market state over websocket.

Capture rule (docs/superpowers/specs/2026-09-20-phase2-recorder-design.md): write on every
change of premium / current_funding_rate, on a heartbeat, and — the part that matters — twice
in the final seconds of each hour. Lighter's premium is a running mean that resets at the
boundary and only its last in-hour value reproduces the settled rate.

Structure, and why it is this shape:
- the timer task runs OUTSIDE the connection loop, so a reconnect at HH:59:4x cannot cost the
  hour its boundary snapshot;
- the trigger label comes from the instant we slept TO, never from re-querying the clock after
  the sleep (which would answer with the *next* offset and mislabel the critical record);
- reader and timers live in a TaskGroup, so neither can be orphaned writing through a dead
  socket into the shared writer;
- freshness is per market, with age_ms on every record;
- every send and recv is bounded by asyncio.wait_for."""
import asyncio
import json

import websockets

from fundr.recorder import records
from fundr.recorder.config import Clock, Config, LIGHTER_WS_URL
from fundr.recorder.health import Health
from fundr.recorder.writer import HourlyWriter

FEED = "lighter_state"
VENUE = "lighter"
HOUR_MS = 3_600_000
WATCHED = ("premium", "current_funding_rate")


class LighterStateFeed:
    def __init__(self, cfg: Config, writer: HourlyWriter, health: Health, clock: Clock,
                 connect=None):
        self._cfg = cfg
        self._w = writer
        self._health = health
        self._clock = clock
        self._connect = connect or (lambda: websockets.connect(LIGHTER_WS_URL))
        self._seq = records.Seq(FEED)
        self._latest: dict[int, dict] = {}
        self._last_msg_ms: dict[int, int] = {}   # per market: one busy market must not
        self._ws_ts: dict[int, object] = {}      # make 245 silent ones look alive
        self._ws_type: dict[int, object] = {}
        self._watched: dict[int, tuple] = {}
        self._n_msgs: dict[int, int] = {}
        self._n_changes: dict[int, int] = {}
        self._silent_intervals = 0               # whole-socket silence, heartbeats only
        self.needs_reconnect = False
        self._subscribed: set[int] = set()
        self._wanted: set[int] = set()
        self.pending_subscribe: list[int] = []
        self.pending_unsubscribe: list[int] = []

    # --- market set -------------------------------------------------------
    def set_markets(self, market_ids: list[int]) -> None:
        self._wanted = set(market_ids)
        self.pending_subscribe = sorted(self._wanted - self._subscribed)
        self.pending_unsubscribe = sorted(self._subscribed - self._wanted)

    def mark_subscribed(self, market_ids: list[int]) -> None:
        self._subscribed |= set(market_ids)
        self.set_markets(sorted(self._wanted))

    def mark_unsubscribed(self, market_ids: list[int]) -> None:
        self._subscribed -= set(market_ids)
        self.set_markets(sorted(self._wanted))

    # --- capture ----------------------------------------------------------
    def on_message(self, msg: dict) -> None:
        stats = msg.get("market_stats")
        if not stats:
            return
        mid = stats["market_id"]
        t_ms = self._clock.now_ms()
        self._n_msgs[mid] = self._n_msgs.get(mid, 0) + 1
        self._latest[mid] = stats
        self._last_msg_ms[mid] = t_ms
        # The venue stamps its own message; keeping only market_stats discards it for good,
        # and capture time is not the same quantity.
        self._ws_ts[mid] = msg.get("timestamp")
        self._ws_type[mid] = msg.get("type")
        # Coverage counts VENUE messages, never our own timer. Counting our writes would give
        # a market whose subscription died silently a perfect score forever.
        self._health.record(FEED, stats.get("symbol") or str(mid), t_ms)
        key = tuple(stats.get(f) for f in WATCHED)
        if self._watched.get(mid) != key:
            self._watched[mid] = key
            self._n_changes[mid] = self._n_changes.get(mid, 0) + 1
            self._write(mid, "change")

    def snapshot(self, trigger: str) -> None:
        if trigger == "heartbeat":
            # Only heartbeats are a fixed 60 s apart. The two boundary snapshots are 22 s
            # apart, and counting them as intervals would force a reconnect every hour.
            if sum(self._n_msgs.values()) == 0:
                self._silent_intervals += 1
                if self._silent_intervals >= 2:
                    self.needs_reconnect = True
            else:
                self._silent_intervals = 0
        for mid in sorted(self._latest):
            self._write(mid, trigger)
        self._n_msgs.clear()
        self._n_changes.clear()

    def _age_ms(self, mid: int, t_ms: int) -> int | None:
        last = self._last_msg_ms.get(mid)
        return None if last is None else t_ms - last

    def _is_stale(self, mid: int, t_ms: int) -> bool:
        """Per market, and age-based rather than interval-counted: it stays correct across a
        reconnect and across the irregular spacing of the boundary snapshots."""
        age = self._age_ms(mid, t_ms)
        return age is None or age >= 2 * self._cfg.lighter_heartbeat_s * 1000

    def _write(self, mid: int, trigger: str) -> None:
        stats = self._latest.get(mid)
        if stats is None:
            return      # never heard from: write nothing, and let health report the zero
        t_ms = self._clock.now_ms()
        self._w.write(records.envelope(
            FEED, VENUE, self._seq.next(), stats, t_ms=t_ms, mono_ns=self._clock.mono_ns(),
            trigger=trigger, n_msgs=self._n_msgs.get(mid, 0),
            n_funding_changes=self._n_changes.get(mid, 0),
            ws_ts=self._ws_ts.get(mid), ws_type=self._ws_type.get(mid),
            age_ms=self._age_ms(mid, t_ms), stale=self._is_stale(mid, t_ms)))

    # --- timing -----------------------------------------------------------
    def next_boundary_sleep_s(self) -> float:
        """Seconds until the next forced pre-boundary snapshot."""
        now_ms = self._clock.now_ms()
        into_hour = now_ms % HOUR_MS
        for offset in sorted(self._cfg.boundary_offsets_s, reverse=True):
            at = HOUR_MS - offset * 1000
            if into_hour < at:
                return (at - into_hour) / 1000
        return (HOUR_MS - into_hour + (HOUR_MS - max(self._cfg.boundary_offsets_s) * 1000)) / 1000

    # --- run --------------------------------------------------------------
    async def run(self, stop: asyncio.Event) -> None:
        """The timer task is deliberately OUTSIDE the connection loop: a reconnect at
        HH:59:4x must not cost the hour its boundary snapshot, the one record this feed
        exists to produce. TaskGroup and not gather(): gather propagates one child's failure
        without cancelling the other, which would leave an orphaned reader writing through a
        dead connection into the shared writer."""
        async with asyncio.TaskGroup() as tg:
            tg.create_task(self._timers(stop))
            tg.create_task(self._connections(stop))

    async def _connections(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                async with self._connect() as ws:
                    self._subscribed.clear()
                    self.set_markets(sorted(self._wanted))
                    await self._sync_subscriptions(ws)
                    self.needs_reconnect = False
                    self._silent_intervals = 0
                    await self._reader(ws, stop)
                if self.needs_reconnect and not stop.is_set():
                    t_ms = self._clock.now_ms()
                    self._w.write(records.gap(FEED, VENUE, self._seq.next(), t_ms=t_ms,
                                              mono_ns=self._clock.mono_ns(), from_ms=t_ms,
                                              to_ms=t_ms, reason="stall_reconnect"))
                    self._health.reconnect(FEED)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # deliberate: a 24/7 feed must survive every ws error,
                # and the error is recorded in the data rather than lost to a crash.
                t_ms = self._clock.now_ms()
                self._w.write(records.gap(FEED, VENUE, self._seq.next(), t_ms=t_ms,
                                          mono_ns=self._clock.mono_ns(), from_ms=t_ms,
                                          to_ms=t_ms, reason=f"ws_error:{type(e).__name__}"))
                self._health.reconnect(FEED)
                await self._clock.sleep(5)

    async def _send(self, ws, obj: dict) -> None:
        # Global Constraint: every network call is bounded out-of-band. A reconnect issues
        # ~214 of these, and one unbounded send stalls the whole feed.
        await asyncio.wait_for(ws.send(json.dumps(obj)), timeout=self._cfg.watchdog_s)

    async def _sync_subscriptions(self, ws) -> None:
        for mid in self.pending_subscribe:
            await self._send(ws, {"type": "subscribe", "channel": f"market_stats/{mid}"})
        self.mark_subscribed(self.pending_subscribe)
        for mid in self.pending_unsubscribe:
            await self._send(ws, {"type": "unsubscribe", "channel": f"market_stats/{mid}"})
        self.mark_unsubscribed(self.pending_unsubscribe)

    async def _reader(self, ws, stop: asyncio.Event) -> None:
        while not stop.is_set() and not self.needs_reconnect:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=self._cfg.watchdog_s)
            except TimeoutError:
                continue    # re-check stop / needs_reconnect; the timers judge silence
            self.on_message(json.loads(raw))
            if self.pending_subscribe or self.pending_unsubscribe:
                await self._sync_subscriptions(ws)   # new listings, without dropping the socket

    async def _timers(self, stop: asyncio.Event) -> None:
        """Heartbeat and forced pre-boundary snapshots. Runs whatever the socket is doing.

        The trigger is decided from the instant we are sleeping TO, not by re-querying
        next_boundary_sleep_s() afterwards: standing at HH:59:30 that call already answers
        with the *next* offset (~22 s), so a `<= 1.0` re-check labels the hour's critical
        record `heartbeat` and the soak gate and day-one diagnostic never see a boundary."""
        while not stop.is_set():
            boundary_s = self.next_boundary_sleep_s()
            if boundary_s <= self._cfg.lighter_heartbeat_s:
                wait_s, trigger = boundary_s, "boundary"
            else:
                wait_s, trigger = float(self._cfg.lighter_heartbeat_s), "heartbeat"
            await self._clock.sleep(max(0.0, wait_s))
            if stop.is_set():
                return
            self.snapshot(trigger)
