"""P9: poll both venues about once a minute to see how 'current funding' behaves within the hour."""
import argparse
import asyncio
import time

from fundr import store
from fundr.sources.hl_api import HLInfo, asset_ctxs_frame
from fundr.sources.lighter_api import LighterAPI, market_stats_stream

ap = argparse.ArgumentParser()
ap.add_argument("--minutes", type=int, default=250)
args = ap.parse_args()

sample = store.load_json("p00", "sample.json")
coins = [s["coin"] for s in sample]
market_ids = [s["lighter_market_id"] for s in sample]
hl, lighter = HLInfo(), LighterAPI()
latest: dict[int, dict] = {}
window: dict[int, dict] = {m: {"n_msgs": 0, "cfr_values": set()} for m in market_ids}


def write(**rec):
    store.append_jsonl("p09", "live.jsonl", {"t_ms": int(time.time() * 1000), **rec})


def on_stats(stats: dict, ts: int | None):
    m = stats["market_id"]
    latest[m] = {**stats, "ws_ts": ts}
    window[m]["n_msgs"] += 1
    window[m]["cfr_values"].add(stats.get("current_funding_rate"))


async def lighter_ws(stop: asyncio.Event):
    while not stop.is_set():
        try:
            await market_stats_stream(market_ids, on_stats, stop)
        except Exception as e:  # broad on purpose: a 4-hour run must survive any ws error, which is logged in the data
            write(venue="lighter", source="ws_reconnect", error=repr(e))
            await asyncio.sleep(5)


def poll_once():
    ctxs = asset_ctxs_frame(hl.meta_and_asset_ctxs())
    for row in ctxs.filter(ctxs["coin"].is_in(coins)).to_dicts():
        write(venue="hl", source="metaAndAssetCtxs", **row)
    for coin, venues in hl.predicted_fundings():
        if coin in coins:
            write(venue="hl", source="predictedFundings", coin=coin, venues=venues)
    for m in market_ids:
        write(venue="lighter", source="orderBookDetails", **lighter.order_book_details(m))  # includes market_id
    rows = [r for r in lighter.funding_rates() if r["market_id"] in market_ids]
    write(venue="lighter", source="funding-rates", rows=rows)


def write_ws_snapshot():
    # Runs on the event loop (never concurrently with on_stats, which also runs on the
    # event loop via the websocket receive path), so the snapshot-read + reset here can't
    # race with a stats update landing in between.
    for m in market_ids:
        if m in latest:
            w = window[m]
            cfr_values = sorted(v for v in w["cfr_values"] if v is not None)
            write(venue="lighter", source="ws_market_stats",
                  **{**latest[m], "n_msgs": w["n_msgs"], "cfr_values": cfr_values})  # latest[m] includes market_id
        window[m] = {"n_msgs": 0, "cfr_values": set()}


async def main():
    stop = asyncio.Event()
    ws_task = asyncio.create_task(lighter_ws(stop))
    end = time.time() + args.minutes * 60
    while time.time() < end:
        started = time.time()
        try:
            await asyncio.to_thread(poll_once)
            write_ws_snapshot()
        except Exception as e:  # one failed poll must not end a 4-hour run; it is logged and visible in the data
            write(venue="probe", source="poll_error", error=repr(e))
        await asyncio.sleep(max(0.0, 60 - (time.time() - started)))
    stop.set()
    await ws_task


asyncio.run(main())
