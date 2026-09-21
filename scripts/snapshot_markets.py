"""Dated market-metadata snapshots for both venues. Free, unauthenticated, seconds to run.

"Market metadata" is a core Phase 2 variable in the research design. Phase 3 dates listings from
Lighter `created_at` and HL delisting flags; Phase 9 prices fees and size limits from here; and
Phases 5/7/11 read the per-market funding parameters instead of assuming BTC's. Snapshots are
dated because all of this changes: markets list, delist, freeze and re-price."""
import argparse
import time
from datetime import UTC, datetime

from fundr import dataset, markets
from fundr.sources.hl_api import HLInfo
from fundr.sources.lighter_api import LighterAPI

DATASET = "markets"


def snapshot_date(now_ms: int | None = None) -> str:
    ms = now_ms if now_ms is not None else int(time.time() * 1000)
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--venue", choices=("hl", "lighter", "both"), default="both")
    args = ap.parse_args()
    date = snapshot_date()

    if args.venue in ("hl", "both"):
        hl = markets.hl_market_metadata(HLInfo())
        dataset.write_partition(DATASET, hl, source=markets.HL_SOURCE, venue="hl", date=date)
        print(f"hl: {hl.height} markets, {int(hl['is_delisted'].sum())} delisted")
        dataset.update_manifest(f"{DATASET}_hl", {
            "source": markets.HL_SOURCE, "markets": hl.height,
            "delisted": int(hl["is_delisted"].sum()), "snapshot_date": date,
            "fetched_at_ms": int(time.time() * 1000)})

    if args.venue in ("lighter", "both"):
        li = markets.lighter_market_metadata(LighterAPI())
        dataset.write_partition(DATASET, li, source=markets.LIGHTER_SOURCE,
                                venue="lighter", date=date)
        off_mult = li.filter(li["off_default_multiplier"])
        print(f"lighter: {li.height} perp markets, "
              f"{int((li['status'] == 'active').sum())} active; "
              f"{off_mult.height} with funding_premium_multiplier != 100")
        print(li.group_by("funding_premium_multiplier").len().sort("funding_premium_multiplier"))
        print(li.group_by("base_interest_rate_pct").len().sort("base_interest_rate_pct"))
        dataset.update_manifest(f"{DATASET}_lighter", {
            "source": markets.LIGHTER_SOURCE, "markets": li.height,
            "active": int((li["status"] == "active").sum()),
            "off_default_multiplier": off_mult.height, "snapshot_date": date,
            "fetched_at_ms": int(time.time() * 1000)})


if __name__ == "__main__":
    main()
