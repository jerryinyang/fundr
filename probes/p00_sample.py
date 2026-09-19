"""P0: choose the fixed probe market sample and save it for every later probe."""
import argparse
from datetime import UTC, datetime

from fundr import store
from fundr.sample import pick_sample
from fundr.sources.hl_api import HLInfo, asset_ctxs_frame
from fundr.sources.lighter_api import LighterAPI

ap = argparse.ArgumentParser()
ap.add_argument("--exclude", nargs="*", default=[], help="coins to swap out (missing on a sampled date)")
args = ap.parse_args()

raw_hl = HLInfo().meta_and_asset_ctxs()
books = LighterAPI().order_books()
store.save_json("p00", "hl_meta_and_asset_ctxs.json", raw_hl)
store.save_json("p00", "lighter_order_books.json", books)

lighter = {b["symbol"]: b["market_id"] for b in books if b["market_type"] == "perp" and b["status"] == "active"}
hl = asset_ctxs_frame(raw_hl)
sample = pick_sample(hl, set(lighter), frozenset(args.exclude))
for s in sample:
    s["lighter_market_id"] = lighter.get(s["coin"])
store.save_json("p00", "sample.json", sample)

n_both = hl.filter(~hl["is_delisted"] & hl["coin"].is_in(list(lighter))).height
print(f"run at {datetime.now(UTC).isoformat()}; markets on both venues: {n_both}")
print(f"HL-only symbols (name mismatches?): {sorted(set(hl['coin']) - set(lighter))[:40]}")
for s in sample:
    print(s)
