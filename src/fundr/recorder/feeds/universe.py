"""Universe sweep: which markets exist, their status and listing dates, and Lighter's
per-market funding parameters. Runs every 5 minutes because it is the only source future
Lighter delisting timestamps will ever have — its cadence is their resolution."""
import asyncio
from collections.abc import Callable

from fundr.recorder import records
from fundr.recorder.clients import AsyncHLInfo, AsyncLighterREST
from fundr.recorder.config import Clock, Config
from fundr.recorder.health import Health
from fundr.recorder.writer import HourlyWriter

FEED = "universe"


class UniverseFeed:
    def __init__(self, cfg: Config, writer: HourlyWriter, health: Health, clock: Clock,
                 hl: AsyncHLInfo | None = None, lighter: AsyncLighterREST | None = None,
                 on_markets: Callable[[list[int]], None] | None = None):
        self._cfg = cfg
        self._w = writer
        self._health = health
        self._clock = clock
        self._hl = hl or AsyncHLInfo()
        self._lighter = lighter or AsyncLighterREST()
        self._on_markets = on_markets
        self._seq = records.Seq(FEED)

    async def _fetch(self, coro, venue: str, timeout_reason: str, error_reason: str,
                     t_ms: int, mono_ns: int):
        """One source, one timeout, one gap on failure. A hiccup on one venue must not
        discard data already fetched from another, nor block that other venue's coverage
        expectations from refreshing this cycle."""
        try:
            return await asyncio.wait_for(coro, timeout=self._cfg.watchdog_s)
        except TimeoutError:
            reason = timeout_reason
        except Exception:
            reason = error_reason
        self._w.write(records.gap(FEED, venue, self._seq.next(), t_ms=t_ms, mono_ns=mono_ns,
                                  from_ms=t_ms, to_ms=self._clock.now_ms(), reason=reason))
        return None

    async def cycle(self) -> list[int]:
        t_ms, mono_ns = self._clock.now_ms(), self._clock.mono_ns()
        hl_meta = await self._fetch(self._hl.meta(), "hl", "hl_meta_timeout", "hl_meta_error",
                                    t_ms, mono_ns)
        books = await self._fetch(self._lighter.order_books(), "lighter",
                                  "lighter_books_timeout", "lighter_books_error", t_ms, mono_ns)
        details = await self._fetch(self._lighter.order_book_details(), "lighter",
                                    "lighter_details_timeout", "lighter_details_error",
                                    t_ms, mono_ns)

        if hl_meta is None and books is None and details is None:
            return []

        partial = hl_meta is None or books is None or details is None
        payload = {"hl_universe": hl_meta["universe"] if hl_meta is not None else None,
                   "lighter_order_books": books, "lighter_details": details}
        extra = {"partial": True} if partial else {}
        self._w.write(records.envelope(FEED, "both", self._seq.next(), payload, t_ms=t_ms,
                                       mono_ns=mono_ns, trigger="sweep", **extra))
        self._health.record(FEED, "sweep", t_ms)
        self._health.expect(FEED, "sweep", 3600 // self._cfg.universe_s)

        # Coverage expectations come from the VENUES' market lists, not from the records we
        # write. This is the spec's Done-when verbatim, and it is what makes a silently dead
        # subscription visible: the market keeps its expectation and its received count falls.
        # Each expectation refreshes independently of the other venue's success this cycle.
        if hl_meta is not None:
            self._health.expect_markets(
                "hl_state", [m["name"] for m in hl_meta["universe"] if not m.get("isDelisted")],
                3600 // self._cfg.hl_poll_s)

        active: list[int] = []
        if books is not None:
            live = [b for b in books
                    if b.get("status") == "active" and b.get("market_type", "perp") == "perp"]
            active = sorted(b["market_id"] for b in live)
            self._health.expect_markets(
                "lighter_state", [b.get("symbol") or str(b["market_id"]) for b in live],
                3600 // self._cfg.lighter_heartbeat_s)
            if self._on_markets is not None:
                self._on_markets(active)
        return active

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            started = self._clock.mono_ns()
            await self.cycle()
            elapsed_s = (self._clock.mono_ns() - started) / 1e9
            await self._clock.sleep(max(0.0, self._cfg.universe_s - elapsed_s))
