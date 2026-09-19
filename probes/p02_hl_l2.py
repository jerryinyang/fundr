"""P2: what one day's Hyperliquid L2 archive files look like, and what they cost to use."""
import json
import statistics

from fundr import store
from fundr.sources.hl_archive import ARCHIVE_BUCKET, HLArchive, decode_lz4

arc = HLArchive()
sample = [s["coin"] for s in store.load_json("p00", "sample.json")]
dates = arc.list_prefixes(ARCHIVE_BUCKET, "market_data/")
print(f"{len(dates)} dates: first {dates[0]}, last {dates[-1]}")
date = dates[-1]
hours = arc.list_prefixes(ARCHIVE_BUCKET, date)
print(f"{date}: {len(hours)} hour folders")
summary = {"dates_first": dates[0], "dates_last": dates[-1], "n_dates": len(dates), "date": date, "files": []}
first_record = None

for hour in hours[:2]:
    keys = {k["key"].split("/")[-1].removesuffix(".lz4"): k for k in arc.list_keys(ARCHIVE_BUCKET, f"{hour}l2Book/")}
    print(f"{hour}: {len(keys)} coins have files")
    for coin in sample:
        if coin not in keys:
            summary["files"].append({"hour": hour, "coin": coin, "present": False})
            continue
        path = arc.download(ARCHIVE_BUCKET, keys[coin]["key"])
        lines = decode_lz4(path).decode().splitlines()
        recs = [json.loads(x) for x in lines]
        first_record = first_record or recs[0]
        data = [r.get("raw", {}).get("data", r) for r in recs]
        times = [d.get("time") for d in data if d.get("time") is not None]
        levels = [d.get("levels") for d in data if d.get("levels")]
        spacing = statistics.median(b - a for a, b in zip(times, times[1:])) if len(times) > 1 else None
        summary["files"].append({
            "hour": hour, "coin": coin, "present": True,
            "compressed_bytes": keys[coin]["size"], "raw_bytes": sum(len(x) for x in lines),
            "records": len(recs), "median_spacing_ms": spacing,
            "levels_per_side": [len(levels[0][0]), len(levels[0][1])] if levels else None,
        })
        print(summary["files"][-1])

print("first record:", json.dumps(first_record)[:600] if first_record else None)
summary["first_record"] = first_record
store.save_json("p02", "summary.json", summary)
print("AWS spend so far: $%.4f" % arc.spent_usd())
