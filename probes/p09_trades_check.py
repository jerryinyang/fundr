"""P9 add-on: capture ~30s of live trades per venue to confirm a signed trade-flow source exists."""
import asyncio
import json
import time

import websockets

from fundr import store

sample = store.load_json("p00", "sample.json")
coin = next(s for s in sample if s["role"] == "mid")
HL_WS = "wss://api.hyperliquid.xyz/ws"
LIGHTER_WS = "wss://mainnet.zklighter.elliot.ai/stream"


async def capture(url: str, sub: dict, seconds: int = 30) -> list:
    out, end = [], time.time() + seconds
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps(sub))
        while time.time() < end:
            try:
                out.append(json.loads(await asyncio.wait_for(ws.recv(), 5)))
            except TimeoutError:
                continue
    return out


async def main():
    hl = await capture(HL_WS, {"method": "subscribe", "subscription": {"type": "trades", "coin": coin["coin"]}})
    li = await capture(LIGHTER_WS, {"type": "subscribe", "channel": f"trade/{coin['lighter_market_id']}"})
    store.save_json("p09", "trades_hl.json", hl)
    store.save_json("p09", "trades_lighter.json", li)
    print(f"{coin['coin']}: HL messages {len(hl)}, Lighter messages {len(li)}")
    print("HL sample:", json.dumps(hl[-1])[:400] if hl else None)
    print("Lighter sample:", json.dumps(li[-1])[:400] if li else None)


asyncio.run(main())
