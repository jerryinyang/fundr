"""Entry point: `python -m fundr.recorder`."""
import asyncio
import signal

from fundr.recorder.clients import AsyncHLInfo, AsyncLighterREST
from fundr.recorder.config import Clock, Config
from fundr.recorder.feeds.hl_state import HLStateFeed
from fundr.recorder.feeds.lighter_state import LighterStateFeed
from fundr.recorder.feeds.universe import UniverseFeed
from fundr.recorder.health import Health
from fundr.recorder.supervisor import Supervisor
from fundr.recorder.writer import HourlyWriter


async def _run() -> None:
    cfg = Config.from_env()
    clock = Clock()
    health = Health(clock)
    writers = {n: HourlyWriter(cfg.root, n) for n in ("hl_state", "lighter_state", "universe")}
    # One client per task, never shared: a shared connection pool is a shared queue, and a
    # stalled universe sweep sitting on it would block the HL poll — the coupling the spec's
    # architecture rules out ("each task with its own client and connection pool").
    hl_client = AsyncHLInfo()
    uni_hl_client = AsyncHLInfo()
    uni_lighter_client = AsyncLighterREST()
    hl = HLStateFeed(cfg, writers["hl_state"], health, clock, hl_client)
    lighter = LighterStateFeed(cfg, writers["lighter_state"], health, clock)
    universe = UniverseFeed(cfg, writers["universe"], health, clock, uni_hl_client,
                            uni_lighter_client, on_markets=lighter.set_markets)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    # The universe sweep runs first so the Lighter feed knows what to subscribe to.
    await universe.cycle()
    sup = Supervisor(cfg, clock, writers=writers, health=health,
                     feeds={"hl_state": lambda: hl.run(stop),
                            "lighter_state": lambda: lighter.run(stop),
                            "universe": lambda: universe.run(stop)})
    try:
        await sup.run(stop)
    finally:
        # An unexpected raise out of sup.run() must not leak these HTTP clients.
        await hl_client.aclose()
        await uni_hl_client.aclose()
        await uni_lighter_client.aclose()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
