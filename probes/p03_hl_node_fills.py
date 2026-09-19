"""P3: coverage and fields of Hyperliquid node fill/trade archives (signed trade flow source)."""
import json

from fundr import store
from fundr.sources.hl_archive import NODE_BUCKET, HLArchive, decode_lz4

MAX_DOWNLOAD_BYTES = 200 * 10**6  # bigger files: record size only, do not download
arc = HLArchive()
top = arc.list_prefixes(NODE_BUCKET, "")
print("top-level prefixes:", top)
summary = {"top": top, "datasets": {}}


def descend(prefix: str, depth: int = 4) -> list[str]:
    """Follow first and last sub-folder down to the files, to find the date range."""
    p_first, p_last = prefix, prefix
    for _ in range(depth):
        subs_f, subs_l = arc.list_prefixes(NODE_BUCKET, p_first), arc.list_prefixes(NODE_BUCKET, p_last)
        if not subs_f:
            break
        p_first, p_last = subs_f[0], subs_l[-1]
    return [p_first, p_last]


for ds in ("node_fills_by_block/", "node_fills/", "node_trades/"):
    if ds not in top:
        summary["datasets"][ds] = {"present": False}
        print(f"{ds}: not present")
        continue
    first, last = descend(ds)
    files = arc.list_keys(NODE_BUCKET, last, max_keys=100)
    info = {"present": True, "first_leaf": first, "last_leaf": last, "n_files_last_leaf": len(files)}
    if files:
        smallest = min(files, key=lambda k: k["size"])
        info["smallest_key"], info["smallest_bytes"] = smallest["key"], smallest["size"]
        info["median_bytes"] = sorted(k["size"] for k in files)[len(files) // 2]
        if smallest["size"] <= MAX_DOWNLOAD_BYTES:
            lines = decode_lz4(arc.download(NODE_BUCKET, smallest["key"])).decode().splitlines()
            info["n_lines"] = len(lines)
            info["first_record"] = json.loads(lines[0]) if lines else None
    summary["datasets"][ds] = info
    print(ds, json.dumps(info, default=str)[:1200])

store.save_json("p03", "summary.json", summary)
print("AWS spend so far: $%.4f" % arc.spent_usd())
