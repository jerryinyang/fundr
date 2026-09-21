# Phase 2b Historical Backfill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the historical dataset Targets A and B rest on — settled hourly funding for every market on both venues, plus the per-minute Hyperliquid market state and Lighter price/volume history the later phases need — with provenance on every row and a coverage report that says exactly what is missing.

**Architecture:** A small `fundr.dataset` module owns where data lands and how provenance travels; one backfill script per source, each resumable and each writing partitioned parquet; a QA script that measures coverage, gaps and cross-venue alignment and writes a report. Existing clients (`fundr.sources.hl_api`, `lighter_api`, `hl_archive`) are reused; only a retry wrapper is added.

**Tech Stack:** Python 3.13, uv, polars, httpx, boto3, lz4, pytest.

**Spec:** none — this plan is written directly from the research design and Phase 1's findings, per the user's instruction (the components already exist). Authorities: `docs/funding_research_design.md` (Phase 2 "Historical Data Collection"), `docs/phase1/handoff.md` §2 and §5, `docs/phase1/data_audit.md` §1 and §4, `docs/phase2/dependency_map.md`.

## Global Constraints

- Python 3.13 via `uv`. Run everything as `uv run ...`.
- **Never write to `data/phase1/`** — it holds Phase 1's irreplaceable recording. Phase 2b writes only under `data/phase2/` (gitignored).
- **Never commit data.** Commit code, tests and documents only.
- Every dataset carries provenance: source name, endpoint or S3 key, fetch timestamp, and the code version that wrote it. The research design requires distinguishing native observations from vendor-supplied or reconstructed ones.
- **Raw as received, then typed.** Backfills store the venues' own values; unit normalisation is applied only in a clearly-labelled derived column, never in place.
- Funding conventions established in Phase 1, to be honoured, not re-derived: Hyperliquid `fundingRate` is a **per-hour fraction**, positive = longs pay; Lighter `/api/v1/fundings` `rate` is **percent per hour**, unsigned, with `direction` giving the sign (`short` → shorts pay longs → negative); a Lighter row timestamped `T` covers the interval `[T-1h, T)`; Hyperliquid's settled row at `T` closes the hour ending at `T`. Cross-venue comparison uses **per-hour signed fraction**.
- Backfills are **resumable and idempotent**: re-running must not duplicate rows or refetch what is already complete.
- Respect rate limits. Hyperliquid's info endpoint returned HTTP 429 during Phase 2a's validation over ~234 sequential calls; a retry with exponential backoff is mandatory for bulk work.
- AWS: the archive is requester-pays. `fundr.sources.hl_archive.BUDGET_USD` is currently `0.80`; the measured full-archive cost is **$0.91**, so Task 5 raises the cap deliberately and only with the user's explicit approval.

## Measured facts (2026-09-21, from live listing/API calls)

- `s3://hyperliquid-archive/asset_ctxs/`: **1,218 files, 2023-05-20 → 2026-09-19, 10.11 GB compressed** (2023 0.69, 2024 2.64, 2025 3.95, 2026 2.82). Egress at $0.09/GB ⇒ **$0.91** plus negligible request cost.
- Hyperliquid universe: 234 markets (56 flagged delisted). Lighter: 246 markets, 214 active perps.
- Lighter settled funding per market runs from its listing: BTC 14,644 hours from 2025-01-17, down to newly-listed markets with a few hundred.
- Known archive gaps from Phase 1: recent days can be badly incomplete (2026-09-15 156/1440 rows, 2026-09-16 505/1440, 2026-09-18 truncated at 09:55). The backfill must measure completeness per day rather than assume it.

## File map

| File | Responsibility |
|---|---|
| `src/fundr/dataset.py` | dataset roots, partition paths, parquet write with provenance, manifest read/write |
| `src/fundr/retry.py` | `with_retries` — exponential backoff for rate-limited HTTP |
| `scripts/backfill_hl_funding.py` | settled hourly funding, every HL market, resumable |
| `scripts/backfill_lighter_funding.py` | settled hourly funding, every Lighter market, resumable |
| `scripts/backfill_lighter_candles.py` | 1h price/volume per Lighter market |
| `scripts/backfill_hl_archive.py` | `asset_ctxs` per-minute market state (billed; approval-gated) |
| `scripts/qa_backfill.py` | coverage, gaps, cross-venue alignment; writes the report |
| `docs/phase2/datasets.md` | what exists, provenance, coverage, known gaps |
| `tests/test_dataset.py`, `tests/test_retry.py` | offline tests |

## Dataset layout (all under `data/phase2/`)

```
data/phase2/
  hl_funding/coin=<COIN>/part.parquet          # settled hourly funding
  lighter_funding/market_id=<ID>/part.parquet   # settled hourly funding
  lighter_candles/market_id=<ID>/part.parquet   # 1h OHLCV
  hl_asset_ctxs/date=<YYYY-MM-DD>/part.parquet  # per-minute market state, all coins
  manifest.json                                 # per dataset: source, rows, coverage, fetched_at, git_sha
  qa/coverage_report.md                         # written by scripts/qa_backfill.py
```

## Execution order

Task 1 (dataset + retry) is the foundation. Tasks 2 and 3 (the two free funding backfills) are the critical path for Targets A and B and can be done in either order. Task 4 (QA) needs 2 and 3. Task 5 (the billed archive) is independent and approval-gated. Task 6 (Lighter candles) is free and independent. Task 7 documents whatever exists.

---

### Task 1: Dataset module and retry helper

**Files:**
- Create: `src/fundr/dataset.py`, `src/fundr/retry.py`
- Test: `tests/test_dataset.py`, `tests/test_retry.py`

**Interfaces:**
- Produces:
  - `dataset.root() -> Path` — `$FUNDR_PHASE2_DATA` or `data/phase2`
  - `dataset.partition_path(name: str, **keys) -> Path` — e.g. `partition_path("hl_funding", coin="BTC")` → `<root>/hl_funding/coin=BTC/part.parquet`
  - `dataset.write_partition(name: str, df: pl.DataFrame, *, source: str, **keys) -> Path` — writes parquet, stamps `_source`, `_fetched_at_ms`, `_git_sha` columns if absent
  - `dataset.read_partition(name: str, **keys) -> pl.DataFrame | None`
  - `dataset.update_manifest(name: str, entry: dict) -> None`, `dataset.manifest() -> dict`
  - `retry.with_retries(fn, *, attempts=6, base_delay=1.0, retry_on=(...)) -> Any` — exponential backoff with jitter; re-raises the last error after the final attempt
- The provenance columns exist so a later phase can tell native data from vendor or reconstructed data, as the research design requires.

- [ ] **Step 1: Write the failing tests** `tests/test_dataset.py`

```python
import polars as pl

from fundr import dataset


def test_partition_path_is_key_partitioned(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNDR_PHASE2_DATA", str(tmp_path))
    p = dataset.partition_path("hl_funding", coin="BTC")
    assert p == tmp_path / "hl_funding" / "coin=BTC" / "part.parquet"


def test_write_partition_stamps_provenance(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNDR_PHASE2_DATA", str(tmp_path))
    df = pl.DataFrame({"t": [1, 2], "rate": [0.1, 0.2]})
    dataset.write_partition("hl_funding", df, source="hl:fundingHistory", coin="BTC")
    back = dataset.read_partition("hl_funding", coin="BTC")
    assert back.height == 2
    assert back["_source"].unique().to_list() == ["hl:fundingHistory"]
    assert back["_fetched_at_ms"].min() > 0
    assert back["_git_sha"].null_count() == 0


def test_read_missing_partition_is_none(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNDR_PHASE2_DATA", str(tmp_path))
    assert dataset.read_partition("hl_funding", coin="NOPE") is None


def test_manifest_round_trips_and_merges(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNDR_PHASE2_DATA", str(tmp_path))
    dataset.update_manifest("hl_funding", {"rows": 10, "coverage_start_ms": 1})
    dataset.update_manifest("lighter_funding", {"rows": 5})
    dataset.update_manifest("hl_funding", {"rows": 20})
    m = dataset.manifest()
    assert m["hl_funding"]["rows"] == 20
    assert m["lighter_funding"]["rows"] == 5
    assert m["hl_funding"]["coverage_start_ms"] == 1  # merged, not replaced
```

- [ ] **Step 2: Write the failing tests** `tests/test_retry.py`

```python
import httpx
import pytest

from fundr.retry import with_retries


def test_returns_immediately_on_success():
    calls = {"n": 0}

    def ok():
        calls["n"] += 1
        return "fine"

    assert with_retries(ok, sleep=lambda s: None) == "fine"
    assert calls["n"] == 1


def test_retries_then_succeeds():
    calls = {"n": 0}
    slept = []

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.HTTPStatusError("429", request=None, response=None)
        return "ok"

    assert with_retries(flaky, sleep=slept.append) == "ok"
    assert calls["n"] == 3
    assert len(slept) == 2 and slept[1] > slept[0]   # backoff grows


def test_reraises_after_attempts():
    def always():
        raise httpx.HTTPStatusError("429", request=None, response=None)

    with pytest.raises(httpx.HTTPStatusError):
        with_retries(always, attempts=3, sleep=lambda s: None)


def test_does_not_retry_unlisted_errors():
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise ValueError("not retryable")

    with pytest.raises(ValueError):
        with_retries(boom, sleep=lambda s: None)
    assert calls["n"] == 1
```

- [ ] **Step 3: Run, expect failure**

Run: `uv run pytest tests/test_dataset.py tests/test_retry.py -v`
Expected: FAIL — `ModuleNotFoundError: fundr.dataset`.

- [ ] **Step 4: Implement** `src/fundr/retry.py`

```python
"""Exponential backoff for bulk historical fetches. Hyperliquid's info endpoint returned
HTTP 429 during Phase 2a over ~234 sequential calls; a backfill makes tens of thousands."""
import random
import time
from collections.abc import Callable
from typing import Any

import httpx

RETRY_ON = (httpx.HTTPStatusError, httpx.TransportError, TimeoutError)


def with_retries(fn: Callable[[], Any], *, attempts: int = 6, base_delay: float = 1.0,
                 retry_on: tuple = RETRY_ON, sleep: Callable[[float], None] = time.sleep) -> Any:
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except retry_on as e:
            last = e
            if i == attempts - 1:
                break
            sleep(base_delay * (2 ** i) + random.uniform(0, 0.25))
    raise last
```

- [ ] **Step 5: Implement** `src/fundr/dataset.py`

```python
"""Where Phase 2b's historical datasets live, and how provenance travels with them.

The research design requires every dataset to carry a source field so later phases can tell a
venue-native observation from a vendor-supplied or reconstructed one."""
import json
import os
import subprocess
import time
from pathlib import Path

import polars as pl

MANIFEST = "manifest.json"


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


def write_partition(name: str, df: pl.DataFrame, *, source: str, **keys) -> Path:
    path = partition_path(name, **keys)
    path.parent.mkdir(parents=True, exist_ok=True)
    stamped = df
    if "_source" not in df.columns:
        stamped = stamped.with_columns(
            pl.lit(source).alias("_source"),
            pl.lit(int(time.time() * 1000)).alias("_fetched_at_ms"),
            pl.lit(_git_sha()).alias("_git_sha"))
    stamped.write_parquet(path)
    return path


def read_partition(name: str, **keys) -> pl.DataFrame | None:
    path = partition_path(name, **keys)
    return pl.read_parquet(path) if path.exists() else None


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
```

- [ ] **Step 6: Run, expect pass**

Run: `uv run pytest tests/test_dataset.py tests/test_retry.py -v`
Expected: 8 passed.

- [ ] **Step 7: Commit**

```bash
git add src/fundr/dataset.py src/fundr/retry.py tests/test_dataset.py tests/test_retry.py
git commit -m "feat: phase 2b dataset layout, provenance and retry helper"
```

---

### Task 2: Hyperliquid settled funding backfill

**Files:**
- Create: `scripts/backfill_hl_funding.py`
- Test: `tests/test_backfill_hl_funding.py`

**Interfaces:**
- Consumes: `HLInfo.meta_and_asset_ctxs`, `HLInfo.funding_history`, `funding_history_frame`, `dataset`, `retry.with_retries`.
- Produces: `data/phase2/hl_funding/coin=<COIN>/part.parquet` with columns `coin, time, settle_time, funding_rate, premium, funding_rate_str, signed_rate_fraction` plus provenance; a manifest entry per run.
- Produces the importable functions `discover_markets(hl) -> list[str]` and `backfill_coin(hl, coin, *, start_ms, end_ms) -> pl.DataFrame` so the tests can drive them without network.
- `signed_rate_fraction` is the cross-venue column: for HL it equals `funding_rate` (already a per-hour signed fraction). It exists so the two venues' tables share one comparable column name.

**Resumability:** if a partition exists, resume from its last `settle_time` rather than refetching; merge and de-duplicate on `settle_time`.

- [ ] **Step 1: Write the failing test** `tests/test_backfill_hl_funding.py`

```python
import polars as pl

from scripts.backfill_hl_funding import backfill_coin, discover_markets

META = {"universe": [{"name": "BTC"}, {"name": "OLD", "isDelisted": True}, {"name": "ETH"}]}
CTXS = [{"funding": "0.0000125"}, {"funding": "0.0"}, {"funding": "0.0000125"}]


class FakeHL:
    def __init__(self, rows=None, fail_times=0):
        self.rows = rows or []
        self.calls = 0
        self.fail_times = fail_times

    def meta_and_asset_ctxs(self):
        return [META, CTXS]

    def funding_history(self, coin, start_ms, end_ms):
        self.calls += 1
        if self.calls <= self.fail_times:
            import httpx
            raise httpx.HTTPStatusError("429", request=None, response=None)
        return [r for r in self.rows if start_ms <= r["time"] <= end_ms]


def _row(t, rate="0.0000125", premium="0.0001"):
    return {"coin": "BTC", "fundingRate": rate, "premium": premium, "time": t}


def test_discover_markets_includes_delisted():
    # A delisted market still has history worth collecting; the research must not silently
    # drop it, or the universe becomes survivorship-biased.
    assert discover_markets(FakeHL()) == ["BTC", "ETH", "OLD"]


def test_backfill_coin_returns_typed_frame_with_signed_fraction():
    hl = FakeHL([_row(3_600_000), _row(7_200_000, rate="-0.00002")])
    df = backfill_coin(hl, "BTC", start_ms=0, end_ms=10_000_000)
    assert df.height == 2
    assert df["signed_rate_fraction"].to_list() == [0.0000125, -0.00002]
    assert str(df.schema["settle_time"]).startswith("Datetime")


def test_backfill_coin_retries_rate_limits():
    hl = FakeHL([_row(3_600_000)], fail_times=2)
    df = backfill_coin(hl, "BTC", start_ms=0, end_ms=10_000_000, sleep=lambda s: None)
    assert df.height == 1
    assert hl.calls == 3


def test_backfill_coin_is_empty_not_error_when_no_history():
    df = backfill_coin(FakeHL([]), "BTC", start_ms=0, end_ms=10_000_000)
    assert df.is_empty()
```

- [ ] **Step 2: Run, expect failure**

Run: `uv run pytest tests/test_backfill_hl_funding.py -v`
Expected: FAIL — `ModuleNotFoundError: scripts.backfill_hl_funding`.
If `scripts` is not importable, add an empty `scripts/__init__.py` in this task and note it.

- [ ] **Step 3: Implement** `scripts/backfill_hl_funding.py`

```python
"""Backfill Hyperliquid settled hourly funding for every market, from each market's listing.

Free, unauthenticated. Resumable: an existing partition is extended, not refetched."""
import argparse
import time

import polars as pl

from fundr import dataset
from fundr.retry import with_retries
from fundr.sources.hl_api import HLInfo, funding_history_frame

DATASET = "hl_funding"
SOURCE = "hl:fundingHistory"
# HL's own history starts 2023-05-20 (the archive's first day); starting earlier costs nothing
# but empty pages, and starting later would silently truncate older markets.
DEFAULT_START_MS = 1_672_531_200_000  # 2023-01-01


def discover_markets(hl) -> list[str]:
    meta, _ = hl.meta_and_asset_ctxs()
    return sorted(m["name"] for m in meta["universe"])


def backfill_coin(hl, coin: str, *, start_ms: int, end_ms: int, sleep=time.sleep) -> pl.DataFrame:
    rows = with_retries(lambda: hl.funding_history(coin, start_ms, end_ms), sleep=sleep)
    if not rows:
        return pl.DataFrame()
    df = funding_history_frame(rows)
    # HL's fundingRate is already a per-hour signed fraction (Phase 1, P4): positive = longs pay.
    return df.with_columns(pl.col("funding_rate").alias("signed_rate_fraction"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start-ms", type=int, default=DEFAULT_START_MS)
    ap.add_argument("--coins", nargs="*", default=None, help="default: every market")
    args = ap.parse_args()

    hl = HLInfo()
    end_ms = int(time.time() * 1000)
    coins = args.coins or discover_markets(hl)
    total = 0
    for i, coin in enumerate(coins, 1):
        existing = dataset.read_partition(DATASET, coin=coin)
        start = args.start_ms
        if existing is not None and existing.height:
            start = int(existing["time"].max().timestamp() * 1000) + 1
        fresh = backfill_coin(hl, coin, start_ms=start, end_ms=end_ms)
        if fresh.is_empty() and existing is None:
            print(f"[{i}/{len(coins)}] {coin}: no history")
            continue
        merged = fresh if existing is None else pl.concat([existing.drop(
            [c for c in ("_source", "_fetched_at_ms", "_git_sha") if c in existing.columns]),
            fresh], how="diagonal")
        merged = merged.unique(subset=["settle_time"], keep="last").sort("settle_time")
        dataset.write_partition(DATASET, merged, source=SOURCE, coin=coin)
        total += merged.height
        print(f"[{i}/{len(coins)}] {coin}: {merged.height} hours "
              f"({merged['settle_time'].min()} → {merged['settle_time'].max()})")
    dataset.update_manifest(DATASET, {"source": SOURCE, "markets": len(coins), "rows": total,
                                      "fetched_at_ms": int(time.time() * 1000)})
    print(f"\n{DATASET}: {total} rows across {len(coins)} markets")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run, expect pass**

Run: `uv run pytest tests/test_backfill_hl_funding.py -v`
Expected: 4 passed.

- [ ] **Step 5: Run it for real, on three markets first**

Run: `uv run python scripts/backfill_hl_funding.py --coins BTC ETH ENA`
Expected: three partitions with plausible hour counts (BTC should reach back to 2023). Check one: `uv run python -c "from fundr import dataset; d=dataset.read_partition('hl_funding', coin='BTC'); print(d.height, d['settle_time'].min(), d['settle_time'].max())"`

- [ ] **Step 6: Run the full backfill**

Run: `uv run python scripts/backfill_hl_funding.py`
Expected: every market, no unhandled 429s. Record the wall time and the total row count. Re-run it once and confirm it is idempotent (row counts unchanged, no duplicates).

- [ ] **Step 7: Commit**

```bash
git add scripts/backfill_hl_funding.py tests/test_backfill_hl_funding.py
git commit -m "feat: backfill Hyperliquid settled funding"
```

---

### Task 3: Lighter settled funding backfill

**Files:**
- Create: `scripts/backfill_lighter_funding.py`
- Test: `tests/test_backfill_lighter_funding.py`

**Interfaces:**
- Consumes: `LighterAPI.order_books`, `LighterAPI.fundings_all`, `fundings_frame`, `lighter_signed_rate`, `dataset`, `retry.with_retries`.
- Produces: `data/phase2/lighter_funding/market_id=<ID>/part.parquet` with `market_id, symbol, timestamp, settle_time, rate_str, rate, direction, signed_rate, value, signed_rate_fraction` plus provenance.
- Produces `discover_markets(api) -> list[dict]` (every market, active and inactive, with `market_id`, `symbol`, `created_at`) and `backfill_market(api, market, *, end_s) -> pl.DataFrame`.
- **`signed_rate_fraction` = `signed_rate / 100`** — Lighter reports percent per hour (Phase 1, P5/P7), so dividing by 100 puts it on Hyperliquid's per-hour-fraction basis. This is the one normalisation Phase 2b performs, and it is an added column, never a replacement.

- [ ] **Step 1: Write the failing test** `tests/test_backfill_lighter_funding.py`

```python
import polars as pl
import pytest

from scripts.backfill_lighter_funding import backfill_market, discover_markets

BOOKS = [
    {"symbol": "BTC", "market_id": 1, "status": "active", "market_type": "perp",
     "created_at": "1737098461107"},
    {"symbol": "DEAD", "market_id": 9, "status": "inactive", "market_type": "perp",
     "created_at": "1700000000000"},
    {"symbol": "SPOTX", "market_id": 50, "status": "active", "market_type": "spot",
     "created_at": "1700000000000"},
]


class FakeLighter:
    def __init__(self, rows=None):
        self.rows = rows or []

    def order_books(self):
        return BOOKS

    def fundings_all(self, market_id, resolution, start_s, end_s):
        return self.rows


def _row(ts, rate="0.0012", direction="long"):
    return {"timestamp": ts, "value": "1.0", "rate": rate, "direction": direction}


def test_discover_markets_keeps_perps_including_inactive_and_skips_spot():
    got = discover_markets(FakeLighter())
    assert [m["market_id"] for m in got] == [1, 9]


def test_signed_fraction_is_percent_divided_by_100():
    api = FakeLighter([_row(3600, "0.0012", "long"), _row(7200, "0.0478", "short")])
    df = backfill_market(api, {"market_id": 1, "symbol": "BTC", "created_at": "0"}, end_s=10_000)
    assert df["signed_rate"].to_list() == [0.0012, -0.0478]
    assert df["signed_rate_fraction"].to_list() == pytest.approx([0.000012, -0.000478])


def test_settle_time_is_datetime_and_sorted():
    api = FakeLighter([_row(7200), _row(3600)])
    df = backfill_market(api, {"market_id": 1, "symbol": "BTC", "created_at": "0"}, end_s=10_000)
    assert str(df.schema["settle_time"]).startswith("Datetime")
    assert df["timestamp"].to_list() == [3600, 7200]


def test_empty_history_returns_empty_frame():
    df = backfill_market(FakeLighter([]), {"market_id": 1, "symbol": "X", "created_at": "0"},
                         end_s=10_000)
    assert df.is_empty()
```

- [ ] **Step 2: Run, expect failure**

Run: `uv run pytest tests/test_backfill_lighter_funding.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement** `scripts/backfill_lighter_funding.py`

```python
"""Backfill Lighter settled hourly funding for every perp market, from its listing.

Free, no auth. Lighter reports percent per hour with an unsigned rate plus a direction field
(Phase 1, P5); `signed_rate_fraction` converts to Hyperliquid's per-hour-fraction basis."""
import argparse
import time

import polars as pl

from fundr import dataset
from fundr.retry import with_retries
from fundr.sources.lighter_api import LighterAPI, fundings_frame

DATASET = "lighter_funding"
SOURCE = "lighter:/api/v1/fundings"


def discover_markets(api) -> list[dict]:
    books = api.order_books()
    perps = [b for b in books if b.get("market_type", "perp") == "perp"]
    return sorted(perps, key=lambda b: b["market_id"])


def backfill_market(api, market: dict, *, end_s: int, start_s: int | None = None,
                    sleep=time.sleep) -> pl.DataFrame:
    begin = start_s if start_s is not None else int(market["created_at"]) // 1000
    rows = with_retries(
        lambda: api.fundings_all(market["market_id"], "1h", begin, end_s), sleep=sleep)
    if not rows:
        return pl.DataFrame()
    df = fundings_frame(rows, market["market_id"]).sort("timestamp")
    return df.with_columns(
        pl.lit(market.get("symbol")).alias("symbol"),
        # Lighter reports percent per hour; /100 puts it on HL's fraction basis (P5/P7).
        (pl.col("signed_rate") / 100).alias("signed_rate_fraction"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market-ids", nargs="*", type=int, default=None)
    args = ap.parse_args()

    api = LighterAPI()
    end_s = int(time.time())
    markets = discover_markets(api)
    if args.market_ids:
        markets = [m for m in markets if m["market_id"] in set(args.market_ids)]
    total = 0
    for i, m in enumerate(markets, 1):
        mid = m["market_id"]
        existing = dataset.read_partition(DATASET, market_id=mid)
        start_s = None
        if existing is not None and existing.height:
            start_s = int(existing["timestamp"].max()) + 1
        fresh = backfill_market(api, m, end_s=end_s, start_s=start_s)
        if fresh.is_empty() and existing is None:
            print(f"[{i}/{len(markets)}] {m['symbol']} ({mid}): no history")
            continue
        merged = fresh if existing is None else pl.concat([existing.drop(
            [c for c in ("_source", "_fetched_at_ms", "_git_sha") if c in existing.columns]),
            fresh], how="diagonal")
        merged = merged.unique(subset=["timestamp"], keep="last").sort("timestamp")
        dataset.write_partition(DATASET, merged, source=SOURCE, market_id=mid)
        total += merged.height
        print(f"[{i}/{len(markets)}] {m['symbol']} ({mid}): {merged.height} hours "
              f"({merged['settle_time'].min()} → {merged['settle_time'].max()})")
    dataset.update_manifest(DATASET, {"source": SOURCE, "markets": len(markets), "rows": total,
                                      "fetched_at_ms": int(time.time() * 1000)})
    print(f"\n{DATASET}: {total} rows across {len(markets)} markets")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run, expect pass**

Run: `uv run pytest tests/test_backfill_lighter_funding.py -v`
Expected: 4 passed.

- [ ] **Step 5: Run it for real, three markets first, then all**

Run: `uv run python scripts/backfill_lighter_funding.py --market-ids 1 29 38`
then: `uv run python scripts/backfill_lighter_funding.py`
Expected: BTC (market 1) ≈ 14,600+ hours from 2025-01-17; every active perp present. Re-run once to confirm idempotence.

- [ ] **Step 6: Commit**

```bash
git add scripts/backfill_lighter_funding.py tests/test_backfill_lighter_funding.py
git commit -m "feat: backfill Lighter settled funding"
```

---

### Task 4: Coverage, gaps and cross-venue alignment QA

**Files:**
- Create: `scripts/qa_backfill.py`
- Test: `tests/test_qa_backfill.py`
- Create (after run): `data/phase2/qa/coverage_report.md` (not committed) and `docs/phase2/backfill_coverage.md` (committed summary)

**Interfaces:**
- Consumes: `dataset`, `fundr.analysis.gap_scan`.
- Produces:
  - `coverage(name: str, key: str, time_col: str) -> pl.DataFrame` — per partition: rows, first, last, expected hours, coverage ratio, gap count
  - `cross_venue(hl: pl.DataFrame, li: pl.DataFrame) -> pl.DataFrame` — per symbol pair: overlapping hours, hours present on both, correlation of `signed_rate_fraction`
  - a written report
- **Symbol matching is a known hazard** (Phase 1): Hyperliquid uses `kPEPE`-style prefixes where Lighter uses `1000PEPE`. The QA must report unmatched symbols on both sides explicitly rather than silently intersecting — Phase 3 owns the alias table, but Phase 2b must surface how many markets are affected.

- [ ] **Step 1: Write the failing test** `tests/test_qa_backfill.py`

```python
from datetime import datetime, timedelta

import polars as pl

from scripts.qa_backfill import coverage_row, cross_venue, match_symbols


def _funding(symbol, hours, rate=0.00001):
    t0 = datetime(2026, 1, 1)
    times = pl.datetime_range(t0, t0 + timedelta(hours=hours - 1), interval="1h", eager=True)
    return pl.DataFrame({
        "symbol": [symbol] * hours,
        "settle_time": times,
        "signed_rate_fraction": [rate] * hours,
    })


def test_coverage_row_counts_hours_and_gaps():
    df = _funding("BTC", 10)
    df = df.filter(pl.col("settle_time") != df["settle_time"][5])   # punch one hole
    row = coverage_row("BTC", df, time_col="settle_time")
    assert row["rows"] == 9
    assert row["expected_hours"] == 10
    assert row["gaps"] == 1
    assert 0.89 < row["coverage"] < 0.91


def test_match_symbols_reports_unmatched_both_ways():
    m = match_symbols(["BTC", "kPEPE", "ETH"], ["BTC", "1000PEPE", "SOL"])
    assert m["matched"] == ["BTC"]
    assert "kPEPE" in m["hl_only"] and "ETH" in m["hl_only"]
    assert "1000PEPE" in m["lighter_only"] and "SOL" in m["lighter_only"]


def test_cross_venue_reports_overlap_and_correlation():
    hl = _funding("BTC", 24, rate=0.00001)
    li = _funding("BTC", 24, rate=0.00002)
    out = cross_venue({"BTC": hl}, {"BTC": li})
    row = out.row(0, named=True)
    assert row["symbol"] == "BTC"
    assert row["overlap_hours"] == 24
```

- [ ] **Step 2: Run, expect failure**

Run: `uv run pytest tests/test_qa_backfill.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement** `scripts/qa_backfill.py`

```python
"""Measure what the backfill actually got: coverage per market, gaps, and how far the two
venues overlap. A dataset nobody has measured is a dataset nobody should model on."""
import argparse
from datetime import timedelta

import polars as pl

from fundr import dataset
from fundr.analysis import gap_scan

HOUR = timedelta(hours=1)


def coverage_row(key: str, df: pl.DataFrame, *, time_col: str) -> dict:
    first, last = df[time_col].min(), df[time_col].max()
    expected = int((last - first).total_seconds() // 3600) + 1 if df.height else 0
    gaps = gap_scan(df.with_columns(pl.lit(key).alias("_k")), time_col, "_k", HOUR)
    return {"key": key, "rows": df.height, "first": first, "last": last,
            "expected_hours": expected,
            "coverage": df.height / expected if expected else None,
            "gaps": gaps.height}


def match_symbols(hl_symbols: list[str], lighter_symbols: list[str]) -> dict:
    hl, li = set(hl_symbols), set(lighter_symbols)
    return {"matched": sorted(hl & li), "hl_only": sorted(hl - li),
            "lighter_only": sorted(li - hl)}


def cross_venue(hl_by_symbol: dict[str, pl.DataFrame],
                li_by_symbol: dict[str, pl.DataFrame]) -> pl.DataFrame:
    rows = []
    for symbol in sorted(set(hl_by_symbol) & set(li_by_symbol)):
        h = hl_by_symbol[symbol].select("settle_time",
                                        pl.col("signed_rate_fraction").alias("hl"))
        l = li_by_symbol[symbol].select("settle_time",
                                        pl.col("signed_rate_fraction").alias("li"))
        j = h.join(l, on="settle_time", how="inner")
        corr = None
        if j.height > 2 and j["hl"].std() and j["li"].std():
            corr = float(pl.DataFrame(j).select(pl.corr("hl", "li")).item())
        rows.append({"symbol": symbol, "overlap_hours": j.height,
                     "hl_hours": h.height, "lighter_hours": l.height,
                     "corr_signed_rate": corr,
                     "overlap_start": j["settle_time"].min() if j.height else None,
                     "overlap_end": j["settle_time"].max() if j.height else None})
    return pl.DataFrame(rows)


def _load(name: str, key: str) -> dict[str, pl.DataFrame]:
    out = {}
    base = dataset.root() / name
    if not base.exists():
        return out
    for part in sorted(base.glob(f"{key}=*/part.parquet")):
        df = pl.read_parquet(part)
        symbol = df["symbol"][0] if "symbol" in df.columns and df.height else \
            part.parent.name.split("=", 1)[1]
        out[symbol] = df
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    hl = _load("hl_funding", "coin")
    li = _load("lighter_funding", "market_id")
    hl_cov = pl.DataFrame([coverage_row(k, v, time_col="settle_time") for k, v in hl.items()]) \
        if hl else pl.DataFrame()
    li_cov = pl.DataFrame([coverage_row(k, v, time_col="settle_time") for k, v in li.items()]) \
        if li else pl.DataFrame()
    symbols = match_symbols(list(hl), list(li))
    xv = cross_venue(hl, li)

    lines = ["# Phase 2b backfill coverage", ""]
    for name, cov in (("Hyperliquid funding", hl_cov), ("Lighter funding", li_cov)):
        if cov.is_empty():
            lines += [f"## {name}", "", "no data", ""]
            continue
        worst = cov.sort("coverage").head(10)
        lines += [f"## {name}", "",
                  f"- markets: {cov.height}",
                  f"- rows: {int(cov['rows'].sum())}",
                  f"- earliest: {cov['first'].min()}  latest: {cov['last'].max()}",
                  f"- markets with gaps: {int((cov['gaps'] > 0).sum())}",
                  f"- median coverage: {cov['coverage'].median():.4f}", "",
                  "Worst ten by coverage:", "", _md_table(worst), ""]
    lines += ["## Cross-venue symbol matching", "",
              f"- matched on symbol: {len(symbols['matched'])}",
              f"- Hyperliquid only: {len(symbols['hl_only'])}",
              f"- Lighter only: {len(symbols['lighter_only'])}", "",
              "Unmatched symbols are not necessarily absent from the other venue — Phase 1 found "
              "denomination prefixes differ (`kPEPE` vs `1000PEPE`). Phase 3 owns the alias "
              "table; this is the size of the problem.", "",
              f"- Hyperliquid only: {', '.join(symbols['hl_only'][:40])}",
              f"- Lighter only: {', '.join(symbols['lighter_only'][:40])}", ""]
    if not xv.is_empty():
        lines += ["## Cross-venue overlap (matched symbols)", "",
                  f"- pairs: {xv.height}",
                  f"- total overlapping hours: {int(xv['overlap_hours'].sum())}",
                  f"- median overlap per pair: {xv['overlap_hours'].median()}", "",
                  _md_table(xv.sort("overlap_hours", descending=True).head(20)), ""]

    out = args.out or (dataset.root() / "qa" / "coverage_report.md")
    from pathlib import Path
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines))
    print("\n".join(lines[:40]))
    print(f"\nreport written to {out}")


if __name__ == "__main__":
    main()
```

**No pandas.** The repo does not depend on pandas and this plan does not add it; render tables with the helper below instead of `to_markdown`, and use it everywhere a table is written:

```python
def _md_table(df: pl.DataFrame) -> str:
    """Minimal markdown table — avoids pulling pandas/tabulate in for a report."""
    if df.is_empty():
        return "_(empty)_"
    cols = df.columns
    head = "| " + " | ".join(cols) + " |"
    rule = "|" + "|".join("---" for _ in cols) + "|"
    rows = ["| " + " | ".join("" if v is None else str(v) for v in row) + " |"
            for row in df.rows()]
    return "\n".join([head, rule, *rows])
```

- [ ] **Step 4: Run, expect pass**

Run: `uv run pytest tests/test_qa_backfill.py -v`
Expected: 3 passed.

- [ ] **Step 5: Run it against the real backfill**

Run: `uv run python scripts/qa_backfill.py`
Expected: a report naming per-venue coverage, the gap counts, the matched/unmatched symbol counts, and the cross-venue overlap. **Read it.** Flag anything surprising — particularly any market with coverage far below 1.0, and how many symbols fail to match across venues.

- [ ] **Step 6: Write** `docs/phase2/backfill_coverage.md` — a committed summary of the report: what was collected, coverage per venue, the gap picture, the symbol-matching problem's size, and what a later phase must handle. Link the uncommitted full report's path.

- [ ] **Step 7: Commit**

```bash
git add scripts/qa_backfill.py tests/test_qa_backfill.py docs/phase2/backfill_coverage.md
git commit -m "feat: backfill coverage and cross-venue QA"
```

---

### Task 5: Hyperliquid per-minute market state (billed — approval gate)

This is the only task that spends money. It downloads the whole `asset_ctxs` archive: per-minute
funding, premium, open interest, mark/oracle/mid price, impact bid/ask and daily volume for every
market, 2023-05-20 → present. It is what Phase 3's point-in-time universe and Phase 7's features
are built from ([handoff §2](../../phase1/handoff.md)).

**Files:**
- Create: `scripts/backfill_hl_archive.py`
- Modify: `src/fundr/sources/hl_archive.py` (budget constant)
- Test: `tests/test_backfill_hl_archive.py`

**Measured cost: $0.91** — 1,218 files, 10.11 GB compressed, egress at $0.09/GB. The existing
guard `BUDGET_USD = 0.80` would refuse partway through.

- [ ] **Step 1: Ask the user.** Message: "The Hyperliquid per-minute archive is 1,218 files, 10.1 GB, and costs **$0.91** to download — just over the $0.80 cap the code enforces. It gives per-minute open interest, premium and prices for every market back to 2023, which Phase 3's universe and Phase 7's features need. Raise the cap to $2 and proceed?" **Wait for a yes.** Without it, stop and do the other tasks.

- [ ] **Step 2: Raise the cap** in `src/fundr/sources/hl_archive.py`: `BUDGET_USD = 2.00`, with a comment naming the measured $0.91 backfill and the date. Update `tests/test_hl_archive.py`'s budget test if it pins the old value.

- [ ] **Step 3: Write the failing test** `tests/test_backfill_hl_archive.py`

```python
import polars as pl

from scripts.backfill_hl_archive import day_completeness, parse_day


def _raw(rows_per_coin: dict[str, int]) -> pl.DataFrame:
    rows = []
    for coin, n in rows_per_coin.items():
        for i in range(n):
            rows.append({"time": f"2026-09-18T{i // 60:02d}:{i % 60:02d}:00Z", "coin": coin,
                         "funding": 0.0000125, "open_interest": 1.0, "premium": 0.0001,
                         "mark_px": 100.0, "oracle_px": 100.0, "day_ntl_vlm": 5.0})
    return pl.DataFrame(rows)


def test_parse_day_types_time_and_keeps_all_coins():
    df = parse_day(_raw({"BTC": 3, "ETH": 2}))
    assert str(df.schema["time"]).startswith("Datetime")
    assert sorted(df["coin"].unique().to_list()) == ["BTC", "ETH"]


def test_day_completeness_is_per_coin_minutes():
    df = parse_day(_raw({"BTC": 1440, "ETH": 720}))
    comp = day_completeness(df)
    assert comp.filter(pl.col("coin") == "BTC")["minutes"].item() == 1440
    assert comp.filter(pl.col("coin") == "ETH")["completeness"].item() == 0.5


def test_day_completeness_flags_a_short_day():
    df = parse_day(_raw({"BTC": 156}))
    assert day_completeness(df)["completeness"].item() < 0.15
```

- [ ] **Step 4: Run, expect failure**

Run: `uv run pytest tests/test_backfill_hl_archive.py -v`
Expected: FAIL — module not found.

- [ ] **Step 5: Implement** `scripts/backfill_hl_archive.py`

```python
"""Backfill Hyperliquid's per-minute market state (`asset_ctxs`) from the requester-pays archive.

Billed: ~$0.91 for the full range as measured 2026-09-21 (1,218 files, 10.11 GB). Resumable —
a day already converted to parquet is skipped. Phase 1 found recent days can be badly
incomplete, so every day's per-coin completeness is measured and recorded rather than assumed."""
import argparse
import time

import polars as pl

from fundr import dataset
from fundr.sources.hl_archive import ARCHIVE_BUCKET, HLArchive, parse_time, read_csv_lz4

DATASET = "hl_asset_ctxs"
SOURCE = "hl:s3:asset_ctxs"
MINUTES_PER_DAY = 1440


def parse_day(raw: pl.DataFrame) -> pl.DataFrame:
    return parse_time(raw)


def day_completeness(df: pl.DataFrame) -> pl.DataFrame:
    return (df.group_by("coin")
            .agg(pl.len().alias("minutes"))
            .with_columns((pl.col("minutes") / MINUTES_PER_DAY).alias("completeness"))
            .sort("coin"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-date", default=None, help="YYYYMMDD; default: the archive's start")
    ap.add_argument("--to-date", default=None)
    ap.add_argument("--limit", type=int, default=None, help="stop after N days (for a trial run)")
    args = ap.parse_args()

    arc = HLArchive()
    keys = sorted(k["key"] for k in arc.list_keys(ARCHIVE_BUCKET, "asset_ctxs/"))
    if args.from_date:
        keys = [k for k in keys if k.split("/")[-1][:8] >= args.from_date]
    if args.to_date:
        keys = [k for k in keys if k.split("/")[-1][:8] <= args.to_date]
    if args.limit:
        keys = keys[:args.limit]

    done = skipped = 0
    completeness_rows = []
    for i, key in enumerate(keys, 1):
        day = key.split("/")[-1][:8]
        date = f"{day[:4]}-{day[4:6]}-{day[6:]}"
        if dataset.partition_path(DATASET, date=date).exists():
            skipped += 1
            continue
        df = parse_day(read_csv_lz4(arc.download(ARCHIVE_BUCKET, key)))
        dataset.write_partition(DATASET, df, source=f"{SOURCE}:{key}", date=date)
        comp = day_completeness(df).with_columns(pl.lit(date).alias("date"))
        completeness_rows.append(comp)
        done += 1
        print(f"[{i}/{len(keys)}] {date}: {df.height} rows, {comp.height} coins, "
              f"median completeness {comp['completeness'].median():.3f}, "
              f"spend ${arc.spent_usd():.4f}")

    if completeness_rows:
        allc = pl.concat(completeness_rows)
        dataset.write_partition(f"{DATASET}_completeness", allc, source=SOURCE, kind="daily")
        worst = allc.sort("completeness").head(20)
        print("\nWorst twenty coin-days by completeness:")
        print(worst)
    dataset.update_manifest(DATASET, {"source": SOURCE, "days_written": done,
                                      "days_skipped": skipped,
                                      "spend_usd": arc.spent_usd(),
                                      "fetched_at_ms": int(time.time() * 1000)})
    print(f"\n{DATASET}: {done} days written, {skipped} already present, "
          f"total spend ${arc.spent_usd():.4f}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Run, expect pass**

Run: `uv run pytest tests/test_backfill_hl_archive.py -v`
Expected: 3 passed.

- [ ] **Step 7: Trial run of five days, then the full range**

Run: `uv run python scripts/backfill_hl_archive.py --limit 5`
Check the spend line and one day's parquet, then run the full backfill:
`uv run python scripts/backfill_hl_archive.py`
Expected: 1,218 days, total spend ≈ $0.91, no `BudgetExceeded`. Re-run once and confirm every day is skipped (idempotent).

- [ ] **Step 8: Report completeness honestly.** Add a section to `docs/phase2/backfill_coverage.md`: the days whose completeness is materially below 1.0, whether they cluster (Phase 1 found recent days do), and the resulting caveat for Phase 7.

- [ ] **Step 9: Commit**

```bash
git add scripts/backfill_hl_archive.py tests/test_backfill_hl_archive.py src/fundr/sources/hl_archive.py tests/test_hl_archive.py docs/phase2/backfill_coverage.md
git commit -m "feat: backfill Hyperliquid per-minute market state"
```

---

### Task 6: Lighter price and volume history

**Files:**
- Create: `scripts/backfill_lighter_candles.py`
- Test: `tests/test_backfill_lighter_candles.py`

**Interfaces:**
- Consumes: `LighterAPI.candles`, `dataset`, `retry.with_retries`, `discover_markets` from Task 3.
- Produces: `data/phase2/lighter_candles/market_id=<ID>/part.parquet` with `market_id, symbol, time, open, high, low, close, base_volume, quote_volume` plus provenance.
- Lighter's candles carry `t` (ms), `o/h/l/c`, `v` (base) and `V` (quote) — true per-bucket volume, unlike Hyperliquid's running daily total. Phase 1 confirmed ≥1 year at `1h`.
- Fetch in windows (the endpoint caps rows per call); page backwards from now to the market's listing.

- [ ] **Step 1: Write the failing test** `tests/test_backfill_lighter_candles.py`

```python
import polars as pl

from scripts.backfill_lighter_candles import backfill_candles, candles_frame

RAW = [{"t": 1789808400000, "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5, "v": 10.0, "V": 15.0},
       {"t": 1789812000000, "o": 1.5, "h": 2.5, "l": 1.0, "c": 2.0, "v": 20.0, "V": 30.0}]


class FakeLighter:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = 0

    def candles(self, market_id, resolution, start_s, end_s, count_back):
        self.calls += 1
        return self.pages.pop(0) if self.pages else []


def test_candles_frame_types_and_names_columns():
    df = candles_frame(RAW, market_id=1, symbol="BTC")
    assert df["market_id"].unique().to_list() == [1]
    assert df["symbol"].unique().to_list() == ["BTC"]
    assert str(df.schema["time"]).startswith("Datetime")
    assert df["base_volume"].to_list() == [10.0, 20.0]
    assert df["quote_volume"].to_list() == [15.0, 30.0]


def test_backfill_pages_until_empty_and_dedupes():
    api = FakeLighter([RAW, RAW, []])
    df = backfill_candles(api, {"market_id": 1, "symbol": "BTC", "created_at": "0"},
                          end_s=1_789_900_000, sleep=lambda s: None)
    assert api.calls >= 2
    assert df.height == 2            # identical pages de-duplicated on time
    assert df["time"].is_sorted()


def test_no_candles_returns_empty():
    df = backfill_candles(FakeLighter([[]]), {"market_id": 1, "symbol": "X", "created_at": "0"},
                          end_s=1_789_900_000, sleep=lambda s: None)
    assert df.is_empty()
```

- [ ] **Step 2: Run, expect failure**

Run: `uv run pytest tests/test_backfill_lighter_candles.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement** `scripts/backfill_lighter_candles.py`

```python
"""Backfill Lighter 1h price and volume per market. Free, no auth.

Lighter's `v`/`V` are true per-bucket base and quote volume — unlike Hyperliquid's running
daily total, which must be differenced. Phase 1 confirmed ≥1 year of 1h history."""
import argparse
import time

import polars as pl

from fundr import dataset
from fundr.analysis import epoch_ms
from fundr.retry import with_retries
from fundr.sources.lighter_api import LighterAPI

from scripts.backfill_lighter_funding import discover_markets

DATASET = "lighter_candles"
SOURCE = "lighter:/api/v1/candles"
WINDOW_S = 700 * 3600          # stay under the endpoint's per-call row cap


def candles_frame(rows: list[dict], *, market_id: int, symbol: str | None) -> pl.DataFrame:
    if not rows:
        return pl.DataFrame()
    df = pl.DataFrame(rows, schema={"t": pl.Int64, "o": pl.Float64, "h": pl.Float64,
                                    "l": pl.Float64, "c": pl.Float64, "v": pl.Float64,
                                    "V": pl.Float64})
    return df.select(
        pl.lit(market_id).alias("market_id"),
        pl.lit(symbol).alias("symbol"),
        epoch_ms("t").alias("time"),
        pl.col("o").alias("open"), pl.col("h").alias("high"),
        pl.col("l").alias("low"), pl.col("c").alias("close"),
        pl.col("v").alias("base_volume"), pl.col("V").alias("quote_volume"))


def backfill_candles(api, market: dict, *, end_s: int, start_s: int | None = None,
                     sleep=time.sleep) -> pl.DataFrame:
    begin = start_s if start_s is not None else int(market["created_at"]) // 1000
    frames = []
    t = begin
    while t < end_s:
        upper = min(t + WINDOW_S, end_s)
        rows = with_retries(
            lambda: api.candles(market["market_id"], "1h", t, upper, 700), sleep=sleep)
        if rows:
            frames.append(candles_frame(rows, market_id=market["market_id"],
                                        symbol=market.get("symbol")))
        elif not frames:
            break
        t = upper
    if not frames:
        return pl.DataFrame()
    return (pl.concat(frames).unique(subset=["time"], keep="last").sort("time"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market-ids", nargs="*", type=int, default=None)
    args = ap.parse_args()

    api = LighterAPI()
    end_s = int(time.time())
    markets = discover_markets(api)
    if args.market_ids:
        markets = [m for m in markets if m["market_id"] in set(args.market_ids)]
    total = 0
    for i, m in enumerate(markets, 1):
        mid = m["market_id"]
        existing = dataset.read_partition(DATASET, market_id=mid)
        start_s = None
        if existing is not None and existing.height:
            start_s = int(existing["time"].max().timestamp()) + 1
        fresh = backfill_candles(api, m, end_s=end_s, start_s=start_s)
        if fresh.is_empty() and existing is None:
            print(f"[{i}/{len(markets)}] {m['symbol']} ({mid}): no candles")
            continue
        merged = fresh if existing is None else pl.concat([existing.drop(
            [c for c in ("_source", "_fetched_at_ms", "_git_sha") if c in existing.columns]),
            fresh], how="diagonal")
        merged = merged.unique(subset=["time"], keep="last").sort("time")
        dataset.write_partition(DATASET, merged, source=SOURCE, market_id=mid)
        total += merged.height
        print(f"[{i}/{len(markets)}] {m['symbol']} ({mid}): {merged.height} candles "
              f"({merged['time'].min()} → {merged['time'].max()})")
    dataset.update_manifest(DATASET, {"source": SOURCE, "markets": len(markets), "rows": total,
                                      "fetched_at_ms": int(time.time() * 1000)})
    print(f"\n{DATASET}: {total} rows across {len(markets)} markets")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run, expect pass**

Run: `uv run pytest tests/test_backfill_lighter_candles.py -v`
Expected: 3 passed.

- [ ] **Step 5: Run it, three markets then all**

Run: `uv run python scripts/backfill_lighter_candles.py --market-ids 1 29 38`
then the full run. Expected: BTC ≥ 1 year of hourly candles. Note the true start per market — Phase 1 never measured it, so this is new information worth recording.

- [ ] **Step 6: Commit**

```bash
git add scripts/backfill_lighter_candles.py tests/test_backfill_lighter_candles.py
git commit -m "feat: backfill Lighter price and volume history"
```

---

### Task 7: Dataset documentation

**Files:**
- Create: `docs/phase2/datasets.md`
- Modify: `docs/phase2/dependency_map.md` (mark what is now collected)

- [ ] **Step 1: Write** `docs/phase2/datasets.md` covering, per dataset: what it is, its source and endpoint, its coverage (markets, date range, rows), its known gaps, its provenance columns, how to load it, and what it costs to refresh. Include the exact commands to re-run each backfill and note which are free and which are billed.

- [ ] **Step 2: Add a "what a later phase must handle" section**: the symbol-matching problem with its measured size; Hyperliquid's daily-cumulative volume needing differencing with a UTC-midnight reset; Lighter's percent-vs-fraction convention and the `signed_rate_fraction` column that resolves it; archive days with low completeness; and the fact that Lighter open interest and premium have no historical source at all (recorder-forward only).

- [ ] **Step 3: Update** `docs/phase2/dependency_map.md` — mark Phase 2b's rows as collected, with the dataset names, and leave the recorder-only rows as they are.

- [ ] **Step 4: Run the full suite and commit**

```bash
uv run pytest -q
git add docs/phase2/datasets.md docs/phase2/dependency_map.md
git commit -m "docs: Phase 2b dataset documentation"
```

---

## After Task 7

Phase 2b is complete when both funding datasets cover every market from its listing with a measured
coverage report, the per-minute Hyperliquid state is present (or explicitly deferred for cost), and
`docs/phase2/datasets.md` tells a later phase exactly what exists and what to watch out for. Phase 3
(universe construction) is then unblocked and needs nothing further from the recorder.
