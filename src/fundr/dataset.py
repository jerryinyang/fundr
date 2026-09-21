"""Where Phase 2b's historical datasets live, and how provenance travels with them.

The research design requires every dataset to carry a source field so later phases can tell a
venue-native observation from a vendor-supplied or reconstructed one. Provenance here is
per-row: a merge keeps the stamps already on disk and stamps only the rows this run fetched, so
`_fetched_at_ms` answers "when was this row observed", not "when was this file last rewritten"."""
import json
import os
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path

import polars as pl

MANIFEST = "manifest.json"
PROVENANCE = ("_source", "_fetched_at_ms", "_git_sha")


def root() -> Path:
    return Path(os.environ.get("FUNDR_PHASE2_DATA", "data/phase2"))


def _git_sha() -> str:
    env = os.environ.get("FUNDR_GIT_SHA")
    if env:
        return env
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return "unknown"


def partition_path(name: str, **keys) -> Path:
    parts = [f"{k}={v}" for k, v in keys.items()]
    return root().joinpath(name, *parts, "part.parquet")


def stamp(df: pl.DataFrame, *, source: str) -> pl.DataFrame:
    """Fill provenance where it is missing, never overwrite it where it is present."""
    if df.is_empty():
        return df
    now, sha = int(time.time() * 1000), _git_sha()
    if not set(PROVENANCE) <= set(df.columns):
        return df.with_columns(pl.lit(source).alias("_source"),
                               pl.lit(now).alias("_fetched_at_ms"),
                               pl.lit(sha).alias("_git_sha"))
    return df.with_columns(pl.col("_source").fill_null(source),
                           pl.col("_fetched_at_ms").fill_null(now),
                           pl.col("_git_sha").fill_null(sha))


def write_partition(name: str, df: pl.DataFrame, *, source: str, **keys) -> Path:
    path = partition_path(name, **keys)
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp(df, source=source).write_parquet(path)
    return path


def read_partition(name: str, **keys) -> pl.DataFrame | None:
    path = partition_path(name, **keys)
    return pl.read_parquet(path) if path.exists() else None


def resume_cursor(existing: pl.DataFrame | None, col: str) -> int | None:
    """Where to resume: UTC epoch ms for a Datetime column, the raw max for an integer one.

    Every frame in this phase stores naive UTC Datetimes, so `Series.max().timestamp()` would
    apply the MACHINE's zone: on this machine (WAT, UTC+1) it lands an hour behind and harmlessly
    refetches, but on any negative-offset machine it lands AHEAD of the true epoch and the
    backfill silently skips hours it never collected, forever. `dt.epoch("ms")` is zone-free."""
    if existing is None or existing.is_empty() or col not in existing.columns:
        return None
    if existing.schema[col].is_temporal():
        return int(existing.select(pl.col(col).dt.epoch("ms").max()).item())
    return int(existing[col].max())


def merge_partition(existing: pl.DataFrame | None, fresh: pl.DataFrame | None, *,
                    key: str | Sequence[str], source: str) -> pl.DataFrame:
    """Idempotent merge. Existing rows win on a key collision, so their provenance survives.

    Settled funding never changes once settled, so preferring the first observation loses
    nothing and makes a re-run a genuine no-op. Datasets that legitimately get REPLACED — a
    quarantined archive day re-downloaded after the venue backfilled it — drop the affected
    keys from `existing` before calling this, rather than inverting the rule here."""
    subset = [key] if isinstance(key, str) else list(key)
    frames = [f for f in (existing, fresh) if f is not None and not f.is_empty()]
    if not frames:
        return pl.DataFrame()
    merged = frames[0] if len(frames) == 1 else pl.concat(frames, how="diagonal")
    return (stamp(merged, source=source)
            .unique(subset=subset, keep="first", maintain_order=True)
            .sort(subset))


def manifest() -> dict:
    path = root() / MANIFEST
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def update_manifest(name: str, entry: dict) -> None:
    m = manifest()
    m.setdefault(name, {}).update(entry)
    path = root() / MANIFEST
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(m, indent=1, sort_keys=True))
    os.replace(tmp, path)
