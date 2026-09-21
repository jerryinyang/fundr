"""Run the real recorder against the real venues for N minutes, then judge the result.
This is the gate before deploying: it proves the feeds, timers and health accounting work
against live data, and measures memory so the instance can be sized honestly."""
import argparse
import asyncio
import gzip
import json
import resource
import tempfile
from collections import Counter
from pathlib import Path

from fundr.recorder.clients import AsyncHLInfo, AsyncLighterREST
from fundr.recorder.config import Clock, Config
from fundr.recorder.feeds.hl_state import HLStateFeed
from fundr.recorder.feeds.lighter_state import LighterStateFeed
from fundr.recorder.feeds.universe import UniverseFeed
from fundr.recorder.health import Health
from fundr.recorder.supervisor import Supervisor
from fundr.recorder.writer import HourlyWriter

ap = argparse.ArgumentParser()
ap.add_argument("--minutes", type=int, default=60)
ap.add_argument("--root", default=None)
args = ap.parse_args()

root = Path(args.root or tempfile.mkdtemp(prefix="fundr-soak-"))


async def main() -> int:
    cfg = Config(root=root)
    clock = Clock()
    health = Health(clock)
    writers = {n: HourlyWriter(root, n) for n in ("hl_state", "lighter_state", "universe")}
    # One client per task, exactly as __main__ does it: the soak must exercise production's
    # isolation, not a shared pool that production does not have.
    hl_client, uni_hl_client, uni_lighter_client = AsyncHLInfo(), AsyncHLInfo(), AsyncLighterREST()
    hl = HLStateFeed(cfg, writers["hl_state"], health, clock, hl_client)
    lighter = LighterStateFeed(cfg, writers["lighter_state"], health, clock)
    universe = UniverseFeed(cfg, writers["universe"], health, clock, uni_hl_client,
                            uni_lighter_client, on_markets=lighter.set_markets)
    await universe.cycle()
    stop = asyncio.Event()
    sup = Supervisor(cfg, clock, writers=writers, health=health,
                     feeds={"hl_state": lambda: hl.run(stop),
                            "lighter_state": lambda: lighter.run(stop),
                            "universe": lambda: universe.run(stop)})
    task = asyncio.create_task(sup.run(stop))
    await asyncio.sleep(args.minutes * 60)
    stop.set()
    await task
    await hl_client.aclose()
    await uni_hl_client.aclose()
    await uni_lighter_client.aclose()

    rows: Counter = Counter()
    triggers: Counter = Counter()
    gaps = []
    zero_msgs = 0
    for p in root.rglob("*.jsonl.gz"):
        for line in gzip.open(p, "rt").read().splitlines():
            r = json.loads(line)
            rows[r["feed"]] += 1
            if r.get("type") == "gap":
                gaps.append(r)
            else:
                triggers[r.get("trigger")] += 1
                if r["feed"] == "lighter_state" and r.get("trigger") == "heartbeat" \
                        and r.get("n_msgs") == 0:
                    zero_msgs += 1
    peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)
    health_body = json.loads((root / "health.json").read_text())

    reasons = Counter(g["reason"] for g in gaps)
    # `not gaps` is over-strict against two live venues for an hour: a single dropped
    # websocket frame or one slow REST response is normal operation, and the recorder is
    # designed to record it and carry on. What must never appear is evidence that the HOST
    # stalled — that is the Phase 1 failure and it invalidates the run.
    fatal = {r: n for r, n in reasons.items() if r in {"suspend_or_hang", "clock_step"}}
    MAX_TRANSIENT_GAPS = 3

    print(f"root: {root}")
    print("records per feed:", dict(rows))
    print("triggers:", dict(triggers))
    print("gap records:", len(gaps), dict(reasons))
    print("lighter heartbeats with zero messages:", zero_msgs)
    print(f"peak RSS: {peak_mb:.0f} MB")
    print("health status:", health_body["status"])

    ok = (rows["hl_state"] > 0 and rows["lighter_state"] > 0 and rows["universe"] > 0
          and not fatal
          and len(gaps) <= MAX_TRANSIENT_GAPS
          and triggers.get("boundary", 0) >= (1 if args.minutes >= 60 else 0)
          and health_body["status"] == "ok")
    if fatal:
        print("FATAL gap reasons (host stall or clock step):", fatal)
    if len(gaps) > MAX_TRANSIENT_GAPS:
        print(f"too many transient gaps: {len(gaps)} > {MAX_TRANSIENT_GAPS}")
    print("SOAK PASS" if ok else "SOAK FAIL")
    return 0 if ok else 1


raise SystemExit(asyncio.run(main()))
