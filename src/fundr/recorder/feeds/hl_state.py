"""Hyperliquid market state, polled. Owns its own timer, client and writer: nothing here can
be blocked by, or block, another feed."""
import asyncio

from fundr.recorder import records
from fundr.recorder.clients import AsyncHLInfo
from fundr.recorder.config import Clock, Config
from fundr.recorder.health import Health
from fundr.recorder.writer import HourlyWriter

FEED = "hl_state"
VENUE = "hl"


class HLStateFeed:
    def __init__(self, cfg: Config, writer: HourlyWriter, health: Health, clock: Clock,
                 client: AsyncHLInfo | None = None):
        self._cfg = cfg
        self._w = writer
        self._health = health
        self._clock = clock
        self._client = client or AsyncHLInfo()
        self._seq = records.Seq(FEED)
        self._last_mono_ns: int | None = None
        self._last_wall_ms: int | None = None

    def _check_clocks(self, t_ms: int, mono_ns: int) -> None:
        """A monotonic gap means we were suspended or hung; a wall-only jump means the clock
        was stepped. Phase 1 could not tell these apart."""
        if self._last_mono_ns is None:
            return
        mono_delta_ms = (mono_ns - self._last_mono_ns) / 1_000_000
        wall_delta_ms = t_ms - self._last_wall_ms
        limit = 2 * self._cfg.hl_poll_s * 1000
        if mono_delta_ms > limit:
            self._gap(t_ms, mono_ns, self._last_wall_ms, t_ms, "suspend_or_hang")
        elif abs(wall_delta_ms - mono_delta_ms) > limit:
            self._gap(t_ms, mono_ns, self._last_wall_ms, t_ms, "clock_step")

    def _gap(self, t_ms: int, mono_ns: int, from_ms: int, to_ms: int, reason: str) -> None:
        self._w.write(records.gap(FEED, VENUE, self._seq.next(), t_ms=t_ms, mono_ns=mono_ns,
                                  from_ms=from_ms, to_ms=to_ms, reason=reason))
        self._health.gap_open(FEED, from_ms)

    async def cycle(self) -> None:
        t_ms, mono_ns = self._clock.now_ms(), self._clock.mono_ns()
        self._check_clocks(t_ms, mono_ns)
        self._last_mono_ns, self._last_wall_ms = mono_ns, t_ms
        try:
            raw = await asyncio.wait_for(self._client.meta_and_asset_ctxs(),
                                         timeout=self._cfg.watchdog_s)
            predicted = await asyncio.wait_for(self._client.predicted_fundings(),
                                               timeout=self._cfg.watchdog_s)
        except TimeoutError:
            self._gap(t_ms, mono_ns, t_ms, self._clock.now_ms(), "watchdog_timeout")
            return
        except Exception as e:  # a bad response must not end the feed; it is recorded instead
            self._gap(t_ms, mono_ns, t_ms, self._clock.now_ms(), "poll_error")
            self._w.write(records.envelope(FEED, VENUE, self._seq.next(), {"error": repr(e)},
                                           t_ms=t_ms, mono_ns=mono_ns, trigger="error"))
            return
        self._health.gap_close(FEED, t_ms)
        meta, ctxs = raw[0], raw[1]
        pred = {coin: venues for coin, venues in predicted}
        for m, ctx in zip(meta["universe"], ctxs):
            coin = m["name"]
            payload = {"coin": coin, "meta": m, "ctx": ctx, "predicted": pred.get(coin)}
            self._w.write(records.envelope(FEED, VENUE, self._seq.next(), payload,
                                           t_ms=t_ms, mono_ns=mono_ns, trigger="poll"))
            # Coverage counts what the VENUE returned. Expectations are NOT set here: they
            # come from the universe sweep (Task 6), so a coin that drops out of the
            # response shows as missing coverage instead of quietly losing its expectation.
            self._health.record(FEED, coin, t_ms)

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            started = self._clock.mono_ns()
            await self.cycle()
            elapsed_s = (self._clock.mono_ns() - started) / 1e9
            await self._clock.sleep(max(0.0, self._cfg.hl_poll_s - elapsed_s))
