"""Backfill Hyperliquid's per-minute market state (`asset_ctxs`) from the requester-pays archive.

Billed: $0.922 for the full range as measured 2026-09-21 (1,218 files, 10.109 GB at $0.09/GB --
`hyperliquid-archive` is in us-east-1, confirmed by `x-amz-bucket-region` -- plus $0.012 of
requests). Run it with FUNDR_DATA=data/phase2: `HLArchive` caches downloads under
`store.data_root()`, which defaults to data/phase1, the one directory this phase must not write
to, and ~10 GB would land there. `check_data_root()` refuses to start otherwise rather than
trusting whoever typed the command to remember.

Resumable, and NOT merely file-existence-resumable: Phase 1 found recent days can be badly
incomplete (2026-09-15 held 156 of 1,440 minutes), and the handoff requires quarantining short
days and re-downloading them later to pick up the venue's backfill. A day is skipped only when
its RECORDED completeness clears the bar -- unless it is short for a STRUCTURAL reason it will
never outgrow, which no amount of re-downloading fixes and which would otherwise re-pay its
egress on every run. Each .lz4 is deleted once its parquet is written; the parquet is the
artefact and the compressed cache would otherwise reach 10 GB.

One date has no key at all and never will: 2026-07-08 (verified against the live listing and
Phase 1's saved listing). Treat it as missing, not as zero."""
import argparse
import time
from pathlib import Path

import polars as pl

from fundr import dataset, store
from fundr.sources.hl_archive import ARCHIVE_BUCKET, HLArchive, parse_time, read_csv_lz4

DATASET = "hl_asset_ctxs"
COMPLETENESS = "hl_asset_ctxs_completeness"
SOURCE = "hl:s3:asset_ctxs"
MINUTES_PER_DAY = 1440
QUARANTINE_BELOW = 0.99
# Days that are short for a reason that will never change. Measured on the real file, not
# assumed: 2023-05-20 holds 26,670 rows for 21 coins, every one of them exactly 1,270 minutes
# (0.8819), and the file starts at 02:50:04Z -- the archive simply begins mid-morning. Without
# this marker the quarantine re-downloads it on every single run, forever, for nothing.
PERMANENT_SHORT = {"2023-05-20": "the archive's first file begins 02:50:04Z (measured: 0.8819)"}


def ledger_path() -> Path:
    """The spend ledger belongs beside the data it paid for, wherever that root points."""
    return dataset.root() / "aws_ledger.jsonl"


def check_data_root() -> None:
    """Refuse to run unless downloads and datasets land in the same Phase 2b tree.

    `HLArchive.download` caches under `store.data_root()` ($FUNDR_DATA, default data/phase1)
    while everything else here writes under `dataset.root()` ($FUNDR_PHASE2_DATA, default
    data/phase2). If they disagree, a forgotten env var quietly puts ~10 GB of .lz4 into Phase
    1's irreplaceable directory -- the one thing this plan's global constraints forbid."""
    cache, data = store.data_root().resolve(), dataset.root().resolve()
    if cache != data:
        raise SystemExit(
            f"refusing to run: downloads would cache under {cache} while datasets go to {data}.\n"
            f"Re-run with FUNDR_DATA={dataset.root()} so both land in Phase 2b's tree.")


def parse_day(raw: pl.DataFrame) -> pl.DataFrame:
    return parse_time(raw)


def day_completeness(df: pl.DataFrame) -> pl.DataFrame:
    return (df.group_by("coin")
            .agg(pl.len().alias("minutes"))
            .with_columns((pl.col("minutes") / MINUTES_PER_DAY).alias("completeness"))
            .sort("coin"))


def convert_day(lz4_path: Path, *, date: str, source: str, delete_lz4: bool = True) -> pl.DataFrame:
    df = parse_day(read_csv_lz4(lz4_path))
    dataset.write_partition(DATASET, df, source=source, date=date)
    if delete_lz4:
        Path(lz4_path).unlink(missing_ok=True)
    return df


def needs_fetch(date: str, recorded: pl.DataFrame | None, *,
                threshold: float = QUARANTINE_BELOW) -> bool:
    """A day is done only if its parquet exists AND its recorded completeness clears the bar.

    The day's MEDIAN per-coin completeness is used, not its minimum: a coin listed mid-day is
    legitimately short and would quarantine every day forever. A day in PERMANENT_SHORT is
    short by construction and is never re-fetched once present."""
    if not dataset.partition_path(DATASET, date=date).exists():
        return True
    if date in PERMANENT_SHORT:
        return False
    if recorded is None or recorded.is_empty():
        return True
    day = recorded.filter(pl.col("date") == date)
    if day.is_empty():
        return True
    return float(day["completeness"].median()) < threshold


def merge_completeness(existing: pl.DataFrame | None, fresh: pl.DataFrame) -> pl.DataFrame:
    """Re-measured dates replace their old rows; every other date is preserved."""
    if existing is None or existing.is_empty():
        return dataset.merge_partition(None, fresh, key=["date", "coin"], source=SOURCE)
    kept = existing.filter(~pl.col("date").is_in(fresh["date"].unique().to_list()))
    return dataset.merge_partition(kept, fresh, key=["date", "coin"], source=SOURCE)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-date", default=None, help="YYYYMMDD; default: the archive's start")
    ap.add_argument("--to-date", default=None)
    ap.add_argument("--limit", type=int, default=None, help="stop after N days (for a trial run)")
    ap.add_argument("--ledger", default=None, help="default: <dataset root>/aws_ledger.jsonl")
    args = ap.parse_args()

    check_data_root()
    ledger = Path(args.ledger) if args.ledger else ledger_path()
    arc = HLArchive(ledger=ledger)
    keys = sorted(k["key"] for k in arc.list_keys(ARCHIVE_BUCKET, "asset_ctxs/"))
    if args.from_date:
        keys = [k for k in keys if k.split("/")[-1][:8] >= args.from_date]
    if args.to_date:
        keys = [k for k in keys if k.split("/")[-1][:8] <= args.to_date]

    recorded = dataset.read_partition(COMPLETENESS, kind="daily")
    pending = []
    for key in keys:
        day = key.split("/")[-1][:8]
        date = f"{day[:4]}-{day[4:6]}-{day[6:]}"
        if needs_fetch(date, recorded):
            pending.append((date, key))
    requarantined = sum(1 for date, _ in pending
                        if dataset.partition_path(DATASET, date=date).exists())
    skipped = len(keys) - len(pending)
    if args.limit:
        pending = pending[:args.limit]
    print(f"{len(keys)} keys; {skipped} already complete; {len(pending)} to fetch "
          f"({requarantined} of them re-fetched from quarantine)")

    done, completeness_rows = 0, []
    for i, (date, key) in enumerate(pending, 1):
        df = convert_day(arc.download(ARCHIVE_BUCKET, key), date=date, source=f"{SOURCE}:{key}")
        comp = day_completeness(df).with_columns(pl.lit(date).alias("date"))
        completeness_rows.append(comp)
        done += 1
        print(f"[{i}/{len(pending)}] {date}: {df.height} rows, {comp.height} coins, "
              f"median completeness {comp['completeness'].median():.3f}, "
              f"spend ${arc.spent_usd():.4f}", flush=True)

    if completeness_rows:
        merged = merge_completeness(recorded, pl.concat(completeness_rows))
        dataset.write_partition(COMPLETENESS, merged, source=SOURCE, kind="daily")
        by_day = (merged.group_by("date")
                  .agg(pl.col("completeness").median().alias("median_completeness"),
                       pl.len().alias("coins"))
                  .sort("median_completeness"))
        print("\nWorst twenty days by median per-coin completeness:")
        print(by_day.head(20))
        still_short = by_day.filter(
            (pl.col("median_completeness") < QUARANTINE_BELOW)
            & ~pl.col("date").is_in(list(PERMANENT_SHORT)))
        print(f"days still below {QUARANTINE_BELOW} and eligible for re-fetch: "
              f"{still_short.height} (excluding {len(PERMANENT_SHORT)} permanently short)")
    # A run filtered to part of the range measures part of the dataset, so it must not overwrite
    # the manifest's coverage totals -- `record_run` supersedes calling `update_manifest` here.
    dataset.record_run(DATASET, {"source": SOURCE, "days_written": done,
                                 "days_skipped": skipped,
                                 "days_requarantined": requarantined,
                                 "missing_from_archive": ["2026-07-08"],
                                 "spend_usd": arc.spent_usd(),
                                 "ledger": str(ledger),
                                 "permanently_short": sorted(PERMANENT_SHORT),
                                 "fetched_at_ms": int(time.time() * 1000)},
                       partial=bool(args.limit or args.from_date or args.to_date))
    print(f"\n{DATASET}: {done} days written, {skipped} already complete, "
          f"total Phase 2b spend ${arc.spent_usd():.4f}")


if __name__ == "__main__":
    main()
