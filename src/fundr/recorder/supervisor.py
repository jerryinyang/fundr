"""Task supervision. Each feed runs as its own task: one dying, hanging or erroring must never
stop another — that coupling is what cost Phase 1 95 minutes of Lighter data."""
import asyncio
import contextlib
from collections.abc import Awaitable, Callable

from fundr.recorder.config import Clock, Config
from fundr.recorder.health import Health
from fundr.recorder.writer import HourlyWriter

RESTART_DELAY_S = 5
HEALTH_INTERVAL_S = 60


async def supervise(name: str, factory: Callable[[], Awaitable[None]], stop: asyncio.Event,
                    clock: Clock, on_restart: Callable[[str], None] | None = None) -> None:
    while not stop.is_set():
        try:
            await factory()
            return
        except asyncio.CancelledError:
            raise
        except Exception:  # deliberate: a 24/7 service restarts its parts rather than dying
            if on_restart is not None:
                on_restart(name)
            await clock.sleep(RESTART_DELAY_S)


class Supervisor:
    def __init__(self, cfg: Config, clock: Clock | None = None,
                 feeds: dict[str, Callable[[], Awaitable[None]]] | None = None,
                 writers: dict[str, HourlyWriter] | None = None,
                 health: Health | None = None):
        self._cfg = cfg
        self._clock = clock or Clock()
        self.health = health or Health(self._clock)
        self.writers = writers or {
            name: HourlyWriter(cfg.root, name) for name in ("hl_state", "lighter_state", "universe")
        }
        self._feeds = feeds or {}

    def set_feeds(self, feeds: dict[str, Callable[[], Awaitable[None]]]) -> None:
        self._feeds = feeds

    async def _health_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            self.health.write(self._cfg.root / "health.json", self._clock.now_ms())
            await self._clock.sleep(HEALTH_INTERVAL_S)

    async def _flush_loop(self, stop: asyncio.Event) -> None:
        """Flush FIRST, then sleep. Sleeping first means nothing on disk is complete — and so
        nothing is uploadable — for the first `upload_s`, which turns the spec's "instance
        loss bounded to minutes" into an hour-plus on a freshly started recorder."""
        while not stop.is_set():
            for w in self.writers.values():
                w.flush()
            await self._clock.sleep(self._cfg.upload_s)

    async def run(self, stop: asyncio.Event) -> None:
        tasks = [asyncio.create_task(
            supervise(name, factory, stop, self._clock, self.health.reconnect))
            for name, factory in self._feeds.items()]
        tasks.append(asyncio.create_task(self._health_loop(stop)))
        tasks.append(asyncio.create_task(self._flush_loop(stop)))
        await stop.wait()
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t
        self.health.write(self._cfg.root / "health.json", self._clock.now_ms())
        for w in self.writers.values():
            w.close()
