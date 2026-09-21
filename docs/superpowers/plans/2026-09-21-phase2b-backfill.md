# Phase 2b Historical Backfill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the historical dataset Targets A and B rest on — settled hourly funding for every market on both venues, the market metadata that says what those markets *are*, plus the per-minute Hyperliquid market state and Lighter price/volume history the later phases need — with provenance on every row and a coverage report that says exactly what is missing.

**Architecture:** A small `fundr.dataset` module owns where data lands, how provenance travels, and how a re-run resumes and merges; a `fundr.markets` module owns market discovery and metadata for both venues (scripts cannot import each other — see Task 1); one backfill script per source, each resumable and each writing partitioned parquet; a QA script that measures coverage, gaps, cross-venue alignment and vendor agreement and writes a report. Existing clients (`fundr.sources.hl_api`, `lighter_api`, `hl_archive`, `oxarchive`) are reused; a retry wrapper and one Lighter endpoint method are added.

**Tech Stack:** Python 3.13, uv, polars, httpx, boto3, lz4, pytest.

**Spec:** none — this plan is written directly from the research design and Phase 1's findings, per the user's instruction (the components already exist). Authorities: `docs/funding_research_design.md` (Phase 2 "Historical Data Collection" and its core-variable list), `docs/phase1/handoff.md` §2, §5, §7 and §8, `docs/phase1/data_audit.md` §1 and §4, `docs/phase2/dependency_map.md`.

This revision folds in three independent reviews (2026-09-21): an executability review that ran the plan's probes, a data-substance review that checked its numbers against the live APIs, and a third that caught a wrong correction the second round had introduced. Every number below was re-measured; where a reviewer's figure and the measurement disagree, the measurement is what appears here — **including where a reviewer was right about the direction and wrong about the size, and where an earlier round of this plan was simply wrong.**

**Every code block and test in this plan was extracted and executed during the revision**: `uv run pytest -q` → **177 passed**, the number Task 9 expects. The files were then removed so the implementation session writes them itself, task by task, in TDD order. So a failing test in Step "run, expect failure" means the module is genuinely absent, not that the plan's code is broken.

## Global Constraints

- Python 3.13 via `uv`. Run everything as `uv run ...`.
- **Never write to `data/phase1/`** — it holds Phase 1's irreplaceable recording. Phase 2b writes only under `data/phase2/` (gitignored). This binds Task 7 in particular: `HLArchive.download` caches under `store.data_root()`, which defaults to `data/phase1`, so that task **must** run with `FUNDR_DATA=data/phase2` set.
- **Never commit data, `.env` or `auth/`.** Commit code, tests and documents only.
- Every dataset carries provenance: source name, endpoint or S3 key, fetch timestamp, and the code version that wrote it. The research design requires distinguishing native observations from vendor-supplied or reconstructed ones. Provenance is **per row, not per rewrite**: a merge preserves the stamps already on disk and stamps only the rows this run actually fetched.
- **Raw as received, then typed.** Backfills store the venues' own values; unit normalisation is applied only in a clearly-labelled derived column, never in place.
- Funding conventions established in Phase 1, to be honoured, not re-derived: Hyperliquid `fundingRate` is a **per-hour fraction**, positive = longs pay; Lighter `/api/v1/fundings` `rate` is **percent per hour**, unsigned, with `direction` giving the sign (`short` → shorts pay longs → negative); a Lighter row timestamped `T` covers the interval `[T-1h, T)`; Hyperliquid's settled row at `T` closes the hour ending at `T`. Cross-venue comparison uses **per-hour signed fraction**. HL's convention is an **inference**, not documented — Task 8 re-tests it and stops the phase if it fails.
- **Funding parameters are per market, not per venue.** Measured 2026-09-21 across Lighter's 235 perp markets: `funding_premium_multiplier` is 100 on 137, **50 on 96 and 1 on 2**; `base_interest_rate` is `0.0100` on 119, **`0.0032` on 89 and `0.0000` on 27**. Anything that assumes BTC's parameters — "the baseline is 0.00125 %/h", "off-baseline means |rate| > baseline" — is wrong on 98 of 235 markets. Task 2 persists the parameters so Phases 5, 7 and 11 can read them instead of assuming.
- Backfills are **resumable and idempotent**: re-running must not duplicate rows or refetch what is already complete. Resume cursors are computed in UTC epoch milliseconds (`dt.epoch("ms")`), never via `datetime.timestamp()` — see Task 1.
- Respect rate limits. Hyperliquid's info endpoint returned HTTP 429 during Phase 2a's validation over ~234 sequential calls; a retry with exponential backoff is mandatory for bulk work, **at page granularity** (a 429 on page 40 must not restart the coin), and only for errors a retry can fix (429, 5xx, transport/timeout). Lighter answers an unsupported parameter with a permanent HTTP 400 — retrying it six times only makes the failure slower.
- AWS: the archive is requester-pays, and **egress is priced per bucket** because the two buckets are in different regions (see Measured facts). `fundr.sources.hl_archive.BUDGET_USD` is currently `0.80`; the measured full-archive cost is **$0.922**, so Task 7 raises the cap to `1.20` deliberately and only with the user's explicit approval.

## Measured facts (2026-09-21, this revision's own live calls)

- `s3://hyperliquid-archive/asset_ctxs/`: **1,218 files, 2023-05-20 → 2026-09-19, 10.109 GB compressed** (2023 0.69, 2024 2.64, 2025 3.95, 2026 2.82 GB). The span is 1,219 days, so exactly **one date is missing: 2026-07-08** — no key exists for it, in the live listing and in Phase 1's saved listing (`data/phase1/p01/listing.json`, 1,217 keys to 2026-09-18, same single gap). That day has no per-minute HL state and never will; it is a permanent hole Phase 7 must treat as missing, not as zero.
- **Egress cost: $0.922 — and the two buckets are in different regions.** Confirmed by unauthenticated HEAD requests reading the `x-amz-bucket-region` header: **`hyperliquid-archive` is `us-east-1` ($0.09/GB)** and **`hl-mainnet-node-data` is `ap-northeast-1` ($0.114/GB)**. So 10.109 GB × $0.09 = **$0.910**, plus 1,218 × 2 requests × $0.000005 = $0.012 → **$0.922 all-in**. A middle revision of this plan priced the archive at $0.114/GB and quoted $1.165; that was wrong. The mistake is worth naming because it is easy to repeat: requesting `hyperliquid-archive` through the `ap-northeast-1` endpoint answers `301 Moved Permanently` rather than an error, and boto3 follows the redirect, so a Tokyo client reads a Virginia bucket without ever complaining — **a redirecting endpoint is not evidence of region, the header is** (that 301 itself carries `x-amz-bucket-region: us-east-1`). `hl_archive.EGRESS_USD_PER_GB` is now a per-bucket mapping with a $0.114 default for unknown buckets, so nothing under-bills. AWS's 100 GB/month free outbound allowance may still make the *invoiced* amount $0; the guard deliberately counts list price.
- **Parquet footprint, measured** by converting four real archive days already on disk (2023-05-20, 2025-02-21, 2025-12-05, 2026-09-17): parquet is **0.91–1.00× the `.lz4` size** (mean 0.95), so the full archive lands at **≈9.6 GB of parquet**, not the 15–25 GB estimated in review. With each `.lz4` deleted after its parquet is written, peak extra disk is ~10 GB plus one day's compressed file (≤14 MB). **72 GiB free on this machine today** — comfortable.
- **Hyperliquid's own settled funding starts 2023-05-12T00:00:00.048Z** — measured directly on `fundingHistory` for BTC (`-0.0006133368`) and ETH, both with the same first timestamp `1683849600048`. That is **eight days before** the S3 archive's first day and closes handoff §8 action 5, which asked for exactly this call. The previous claim of 2023-05-20 was the archive's start, not the API's.
- Hyperliquid universe: **234 markets, 56 flagged `isDelisted`**. `meta.universe` entries carry `name, szDecimals, maxLeverage, marginTableId, isDelisted, onlyIsolated, marginMode`.
- Lighter: **246 order books = 235 perp (214 active, 21 inactive) + 11 spot**. `/api/v1/orderBooks` carries `created_at, status, market_type, maker_fee, taker_fee, liquidation_fee, min_base_amount, min_quote_amount, supported_size_decimals, supported_price_decimals, is_frozen`. `/api/v1/orderBookDetails` **called with no `market_id` returns all 235 perps in one call** and is the only place the funding parameters (`funding_premium_multiplier`, `base_interest_rate`, `funding_clamp_big`, `funding_clamp_small`) appear.
- Lighter settled funding per market runs from its listing: BTC 14,644 hours from 2025-01-17. Consecutive settlement timestamps are spaced **exactly 3600 s** on markets 1 (BTC) and 138 (AMD), measured over the last 20 h; market 173 (SPACEX, `inactive`) returns **zero rows** for the same window — an inactive market is normal, not an error.
- Lighter rejects an unsupported funding resolution with **HTTP 400 `{"code":20001,"message":"invalid param "}`** (market 1, `resolution=15m`). A permanent failure, never worth retrying.
- Lighter `/api/v1/markPriceCandles` is free and native and returns `t, o, h, l, c, sc` (no volume); `/api/v1/candles` returns `t, o, h, l, c, v, V, i`. Both are wrapped in `{"code", "r", "c"}`.
- Known archive gaps from Phase 1: recent days can be badly incomplete (2026-09-15 156/1440 rows, 2026-09-16 505/1440, 2026-09-18 truncated at 09:55). The backfill must measure completeness per day, record it, and **quarantine** short days for re-fetch rather than freezing them.

## Scope decisions recorded here, deliberately

These are things the research design or the handoff asks for that Phase 2b is **not** doing. They are written down so a later phase inherits a decision, not a silence.

1. **Historical trades / aggregated flow are deferred.** The design names them a core Phase 2 variable. On HL they exist (`node_fills_by_block` from 2025-07-27, continued back by `node_fills` to 2025-05-25, whole-network files at **≈$34.4/year** — 300.7 GB/yr at `hl-mainnet-node-data`'s own `ap-northeast-1` rate of $0.114/GB, which is the bucket that really *is* in Tokyo; P3's $27.15 priced it at $0.09. The cost does not scale down with fewer coins). On Lighter **no usable historical source exists**: `/api/v1/trades` is 400 without auth and 0xArchive's fills finalize 16.7–36 h late. Collecting HL-only flow would hand H3 an asymmetric feature set — exactly the confound handoff §5 (Phase 8) warns about. Decision: collect neither now; Phase 8 decides the symmetric feature set and may commission the HL pull then, with a venue-provenance flag.
2. **Derived hourly volume is deferred to Phase 7.** The handoff asks for raw *and* derived with a provenance flag. Phase 2b stores raw only: HL `day_ntl_vlm` (a running daily notional needing differencing with a UTC-midnight reset) and Lighter candle `v`/`V` (already per bucket). The differencing rule belongs with the feature code that consumes it, and doing it here would bake a reset-handling bug into the stored data. Recorded as a decision, not an oversight.
3. **0xArchive stays free-tier.** Task 8 uses it only as a 30-day cross-check (handoff §8 action 6). No paid tier, no Lighter OI backfill; that is Phase 7's call per handoff §8 action 8.

## File map

| File | Responsibility |
|---|---|
| `pyproject.toml` | adds `pythonpath = ["."]` so `scripts/` is importable under `pytest` |
| `src/fundr/dataset.py` | dataset roots, partition paths, parquet write with per-row provenance, resume cursors, idempotent merge, manifest read/write |
| `src/fundr/retry.py` | `with_retries` — exponential backoff for 429/5xx/transport only |
| `src/fundr/markets.py` | market discovery and metadata for both venues, shared by every script |
| `src/fundr/sources/lighter_api.py` | **modify**: add `order_book_details_all()` |
| `src/fundr/sources/hl_archive.py` | `EGRESS_USD_PER_GB` **already corrected with this revision**; `BUDGET_USD` raised in Task 7 only |
| `scripts/snapshot_markets.py` | dated market-metadata snapshots, both venues |
| `scripts/backfill_hl_funding.py` | settled hourly funding, every HL market, resumable |
| `scripts/backfill_lighter_funding.py` | settled hourly funding, every Lighter perp, resumable |
| `scripts/backfill_lighter_candles.py` | 1h price/volume and mark-price candles per Lighter market |
| `scripts/backfill_hl_archive.py` | `asset_ctxs` per-minute market state (billed; approval-gated; quarantines short days) |
| `scripts/qa_backfill.py` | coverage, gaps, cross-venue alignment, survivorship, vendor cross-check; writes the report |
| `docs/phase2/datasets.md` | what exists, provenance, coverage, known gaps |
| `tests/test_dataset.py`, `test_retry.py`, `test_markets.py`, `test_backfill_*.py`, `test_qa_backfill.py` | offline tests |

## Dataset layout (all under `data/phase2/`)

```
data/phase2/
  markets/venue=hl/date=<YYYY-MM-DD>/part.parquet       # dated metadata snapshot
  markets/venue=lighter/date=<YYYY-MM-DD>/part.parquet  # incl. per-market funding parameters
  hl_funding/coin=<COIN>/part.parquet                   # settled hourly funding
  lighter_funding/market_id=<ID>/part.parquet           # settled hourly funding
  lighter_candles/market_id=<ID>/part.parquet           # 1h OHLCV
  lighter_mark_candles/market_id=<ID>/part.parquet      # 1h mark-price OHLC
  hl_asset_ctxs/date=<YYYY-MM-DD>/part.parquet          # per-minute market state, all coins
  hl_asset_ctxs_completeness/kind=daily/part.parquet    # per date × coin minutes and completeness
  aws_ledger.jsonl                                      # Phase 2b's own spend ledger
  s3/                                                   # transient .lz4 cache, emptied as it goes
  manifest.json                                         # per dataset: source, rows, coverage, fetched_at, git_sha
  qa/coverage_report.md                                 # written by scripts/qa_backfill.py
```

## Execution order

Task 1 (dataset + retry + the import fix) is the foundation — nothing else collects a byte until it lands. Task 2 (market metadata and discovery) comes next because every other script imports discovery from `fundr.markets`, and because listing dates and funding parameters are what make the later coverage numbers meaningful. Tasks 3 and 4 (the two free funding backfills) are the critical path for Targets A and B and can be done in either order. Task 5 (Lighter candles) is free and independent. Task 6 (the egress-rate correction) already shipped with this revision and is a verification step. Task 7 (the billed archive) is approval-gated but **not optional** — see "After Task 9". Task 8 (QA) wants 2–7 present but runs on whatever exists. Task 9 documents the result.

---

### Task 1: Dataset module, retry helper, and making `scripts/` importable

**Files:**
- Modify: `pyproject.toml`
- Create: `src/fundr/dataset.py`, `src/fundr/retry.py`
- Test: `tests/test_dataset.py`, `tests/test_retry.py`

**Why the `pyproject.toml` change is in this task, unconditionally:** every test in Tasks 3–8 imports its script (`from scripts.backfill_hl_funding import ...`). Today that fails at collection. Verified for this revision: with `testpaths = ["tests"]` and no `pythonpath`, `uv run pytest` raises `ModuleNotFoundError: No module named 'scripts'` — pytest's `prepend` import mode puts `tests/` on `sys.path`, not the repo root, and the `pytest` console script (unlike `python -m pytest`) does not add the working directory either. Adding `scripts/__init__.py` does **not** fix it; the problem is `sys.path`, not a package marker. Adding `pythonpath = ["."]` does fix it, with no `__init__.py` needed (PEP 420 namespace package) — verified both ways in an isolated project. The repo's two existing script tests work around this with `importlib.util.spec_from_file_location`; that workaround is no longer needed for new tests.

**Interfaces:**
- Produces:
  - `dataset.root() -> Path` — `$FUNDR_PHASE2_DATA` or `data/phase2`
  - `dataset.partition_path(name: str, **keys) -> Path` — e.g. `partition_path("hl_funding", coin="BTC")` → `<root>/hl_funding/coin=BTC/part.parquet`
  - `dataset.stamp(df: pl.DataFrame, *, source: str) -> pl.DataFrame` — fills `_source`, `_fetched_at_ms`, `_git_sha` **only where they are null or absent**
  - `dataset.write_partition(name: str, df: pl.DataFrame, *, source: str, **keys) -> Path`
  - `dataset.read_partition(name: str, **keys) -> pl.DataFrame | None`
  - `dataset.resume_cursor(existing: pl.DataFrame | None, col: str) -> int | None` — UTC epoch ms for a Datetime column, the raw maximum for an integer column, `None` when there is nothing to resume from
  - `dataset.merge_partition(existing, fresh, *, key: str | Sequence[str], source: str) -> pl.DataFrame` — diagonal concat, existing rows win, provenance preserved, sorted by key
  - `dataset.update_manifest(name: str, entry: dict) -> None`, `dataset.manifest() -> dict`
  - `retry.is_retryable(e: BaseException) -> bool` — 429, ≥500, transport/timeout; everything else `False`
  - `retry.with_retries(fn, *, attempts=6, base_delay=1.0, max_delay=20.0, retryable=is_retryable, sleep=time.sleep) -> Any` — exponential backoff with jitter; re-raises immediately on a non-retryable error and after the final attempt. **`sleep` is injectable so tests never wait.**
- The provenance columns exist so a later phase can tell native data from vendor or reconstructed data, as the research design requires. Because they are filled per row rather than per file, a row's `_fetched_at_ms` answers "when did we actually observe this?" and not "when was this file last rewritten?".

- [ ] **Step 1: Make `scripts/` importable** in `pyproject.toml`

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
# The repo root on sys.path, so tests can `from scripts.x import y`. pytest's prepend import
# mode adds tests/, not the root, and the `pytest` console script does not add the cwd.
pythonpath = ["."]
```

Run: `uv run pytest -q` — expected: **118 passed**, unchanged. This step only widens the import path.

- [ ] **Step 2: Write the failing tests** `tests/test_dataset.py`

```python
import datetime as dt
import time

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


def test_resume_cursor_is_utc_not_local_time():
    # 2026-09-21T12:00:00Z as a NAIVE Datetime, which is how every frame in this phase stores
    # time. `Series.max().timestamp()` would apply the machine's local zone here.
    df = pl.DataFrame({"time": [dt.datetime(2026, 9, 21, 12)]}).with_columns(
        pl.col("time").cast(pl.Datetime("ms")))
    assert dataset.resume_cursor(df, "time") == 1_789_992_000_000


def test_resume_cursor_survives_a_negative_utc_offset(monkeypatch):
    # The bug this guards: on a negative-offset machine `.timestamp()` returns a value AHEAD of
    # the true epoch, so the next fetch starts after hours that were never collected and the gap
    # is never revisited. Forced here so the test fails on a UTC machine too.
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    try:
        df = pl.DataFrame({"time": [dt.datetime(2026, 9, 21, 12)]}).with_columns(
            pl.col("time").cast(pl.Datetime("ms")))
        assert dataset.resume_cursor(df, "time") == 1_789_992_000_000
    finally:
        monkeypatch.delenv("TZ", raising=False)
        time.tzset()


def test_resume_cursor_on_integer_column():
    df = pl.DataFrame({"timestamp": [1, 7200, 3600]})
    assert dataset.resume_cursor(df, "timestamp") == 7200


def test_resume_cursor_none_when_nothing_to_resume():
    assert dataset.resume_cursor(None, "time") is None
    assert dataset.resume_cursor(pl.DataFrame(), "time") is None
    assert dataset.resume_cursor(pl.DataFrame({"other": [1]}), "time") is None


def test_merge_partition_dedupes_and_keeps_existing_rows_provenance():
    existing = pl.DataFrame({"k": [1, 2], "v": ["old", "old"], "_source": ["s1", "s1"],
                             "_fetched_at_ms": [111, 111], "_git_sha": ["aaa", "aaa"]})
    fresh = pl.DataFrame({"k": [2, 3], "v": ["new", "new"]})
    out = dataset.merge_partition(existing, fresh, key="k", source="s2")
    assert out["k"].to_list() == [1, 2, 3]
    assert out["v"].to_list() == ["old", "old", "new"]      # settled rows never change
    assert out["_source"].to_list() == ["s1", "s1", "s2"]   # only the new row is re-stamped
    assert out["_fetched_at_ms"].to_list()[:2] == [111, 111]
    assert out["_fetched_at_ms"][2] > 111


def test_merge_partition_without_existing_stamps_everything():
    fresh = pl.DataFrame({"k": [2, 1], "v": ["b", "a"]})
    out = dataset.merge_partition(None, fresh, key="k", source="s")
    assert out["k"].to_list() == [1, 2]
    assert out["_source"].to_list() == ["s", "s"]
    assert dataset.merge_partition(None, pl.DataFrame(), key="k", source="s").is_empty()
```

- [ ] **Step 3: Write the failing tests** `tests/test_retry.py`

```python
import httpx
import pytest

from fundr.retry import with_retries


def _status_error(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.hyperliquid.xyz/info")
    response = httpx.Response(code, request=request)
    return httpx.HTTPStatusError(str(code), request=request, response=response)


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
            raise _status_error(429)
        return "ok"

    assert with_retries(flaky, sleep=slept.append) == "ok"
    assert calls["n"] == 3
    assert len(slept) == 2 and slept[1] > slept[0]   # backoff grows


def test_reraises_after_attempts():
    calls = {"n": 0}

    def always():
        calls["n"] += 1
        raise _status_error(429)

    with pytest.raises(httpx.HTTPStatusError):
        with_retries(always, attempts=3, sleep=lambda s: None)
    assert calls["n"] == 3


def test_does_not_retry_unlisted_errors():
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise ValueError("not retryable")

    with pytest.raises(ValueError):
        with_retries(boom, sleep=lambda s: None)
    assert calls["n"] == 1


def test_does_not_retry_permanent_4xx():
    # Lighter answers an unsupported resolution with HTTP 400 {"code":20001,"message":
    # "invalid param "} (measured 2026-09-21). No number of retries makes that succeed.
    calls = {"n": 0}

    def bad_request():
        calls["n"] += 1
        raise _status_error(400)

    with pytest.raises(httpx.HTTPStatusError):
        with_retries(bad_request, sleep=lambda s: None)
    assert calls["n"] == 1


def test_retries_server_errors():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise _status_error(503)
        return "ok"

    assert with_retries(flaky, sleep=lambda s: None) == "ok"
    assert calls["n"] == 2


def test_retries_transport_errors():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise httpx.ConnectError("dns")
        return "ok"

    assert with_retries(flaky, sleep=lambda s: None) == "ok"
    assert calls["n"] == 2
```

- [ ] **Step 4: Run, expect failure**

Run: `uv run pytest tests/test_dataset.py tests/test_retry.py -v`
Expected: FAIL — `ModuleNotFoundError: fundr.dataset`.

- [ ] **Step 5: Implement** `src/fundr/retry.py`

```python
"""Exponential backoff for bulk historical fetches.

Hyperliquid's info endpoint returned HTTP 429 during Phase 2a over ~234 sequential calls; a
backfill makes tens of thousands of calls. Retry only what a retry can fix: 429, 5xx, and
transport/timeout errors. A permanent 4xx must surface at once — Lighter answers an unsupported
funding resolution with HTTP 400 `{"code":20001,"message":"invalid param "}` (measured
2026-09-21, market 1, resolution 15m), and burning six backoffs on that hides the real fault."""
import random
import time
from collections.abc import Callable
from typing import Any

import httpx

TRANSPORT_ERRORS = (httpx.TransportError, TimeoutError)  # TimeoutException subclasses the former


def is_retryable(e: BaseException) -> bool:
    if isinstance(e, httpx.HTTPStatusError):
        response = getattr(e, "response", None)
        if response is None:
            return False
        return response.status_code == 429 or response.status_code >= 500
    return isinstance(e, TRANSPORT_ERRORS)


def with_retries(fn: Callable[[], Any], *, attempts: int = 6, base_delay: float = 1.0,
                 max_delay: float = 20.0,
                 retryable: Callable[[BaseException], bool] = is_retryable,
                 sleep: Callable[[float], None] = time.sleep) -> Any:
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:
            if i == attempts - 1 or not retryable(e):
                raise
            sleep(min(base_delay * (2 ** i), max_delay) + random.uniform(0, 0.25))
    raise AssertionError("unreachable")
```

- [ ] **Step 6: Implement** `src/fundr/dataset.py`

```python
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
```

- [ ] **Step 7: Run, expect pass**

Run: `uv run pytest tests/test_dataset.py tests/test_retry.py -v`
Expected: 17 passed.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml src/fundr/dataset.py src/fundr/retry.py tests/test_dataset.py tests/test_retry.py
git commit -m "feat: phase 2b dataset layout, provenance, resume/merge and retry helper"
```

---

### Task 2: Market discovery and metadata

**Files:**
- Create: `src/fundr/markets.py`, `scripts/snapshot_markets.py`
- Modify: `src/fundr/sources/lighter_api.py` (add `order_book_details_all`)
- Test: `tests/test_markets.py`, `tests/test_lighter_api.py` (one added test)

**Why discovery lives in the library, not in a script.** Three backfills and the QA all need the market list. A script cannot import another script: running `python scripts/backfill_lighter_candles.py` puts `scripts/` on `sys.path`, not the repo root, so `from scripts.backfill_lighter_funding import discover_markets` raises `ModuleNotFoundError` at runtime even though `pytest` (after Task 1) imports it happily. Putting it in `fundr.markets` makes the same code work in both contexts.

**Why the metadata is collected at all.** `docs/funding_research_design.md` names "Market metadata" a core Phase 2 variable. Phase 3 needs listing dates to build the point-in-time universe and to date entries; Phase 9 needs fees and size limits to simulate; Phases 5, 7 and 11 need the **per-market funding parameters**, because they are not uniform (Global Constraints, measured). Discovery that reads this metadata and throws it away is a free dataset discarded.

**Interfaces:**
- Consumes: `HLInfo.meta_and_asset_ctxs`, `LighterAPI.order_books`, `LighterAPI.order_book_details_all`, `dataset`.
- Produces:
  - `markets.discover_hl_markets(hl) -> list[str]` — every coin name, delisted included
  - `markets.hl_market_metadata(hl) -> pl.DataFrame`
  - `markets.discover_lighter_markets(api) -> list[dict]` — every perp book, active and inactive, sorted by `market_id`
  - `markets.lighter_market_metadata(api) -> pl.DataFrame` — books joined to details, with `effective_multiplier` and the off-default flags
  - `markets.funding_period_s(timestamps) -> int | None` — the modal gap between settlements
  - `LighterAPI.order_book_details_all() -> list[dict]`
  - dated snapshots at `data/phase2/markets/venue=<v>/date=<YYYY-MM-DD>/part.parquet`
- A snapshot is dated because the metadata changes: markets list, delist, freeze, and change fees. A later phase reading "the" metadata must be able to ask "as of when?".

- [ ] **Step 1: Write the failing tests** `tests/test_markets.py`

```python
import polars as pl

from fundr import markets

META = {"universe": [
    {"name": "BTC", "szDecimals": 5, "maxLeverage": 40, "marginTableId": 56},
    {"name": "OLD", "szDecimals": 2, "maxLeverage": 3, "marginTableId": 5, "isDelisted": True,
     "marginMode": "isolated"},
    {"name": "ETH", "szDecimals": 4, "maxLeverage": 25, "marginTableId": 55},
]}
CTXS = [{"funding": "0.0000125"}, {"funding": "0.0"}, {"funding": "0.0000125"}]

BOOKS = [
    {"symbol": "BTC", "market_id": 1, "status": "active", "market_type": "perp",
     "created_at": "1737098461107", "maker_fee": "0.0000", "taker_fee": "0.0000",
     "liquidation_fee": "1.0000", "min_base_amount": "0.00007", "min_quote_amount": "10.000000",
     "supported_size_decimals": 5, "supported_price_decimals": 1, "is_frozen": False,
     "start_timestamp": 0, "end_timestamp": 0, "settlement_type": 0, "settlement_price": 0,
     "settlement_cap": 0},
    {"symbol": "AMD", "market_id": 138, "status": "active", "market_type": "perp",
     "created_at": "1770669512153", "maker_fee": "0.0000", "taker_fee": "0.0000",
     "liquidation_fee": "1.0000", "min_base_amount": "0.0100", "min_quote_amount": "10.000000",
     "supported_size_decimals": 4, "supported_price_decimals": 2, "is_frozen": False},
    {"symbol": "DEAD", "market_id": 9, "status": "inactive", "market_type": "perp",
     "created_at": "1700000000000"},
    {"symbol": "SPOTX", "market_id": 50, "status": "active", "market_type": "spot",
     "created_at": "1700000000000"},
]
DETAILS = [
    {"market_id": 1, "symbol": "BTC", "funding_premium_multiplier": 100,
     "base_interest_rate": "0.0100", "funding_clamp_big": "4.0000",
     "funding_clamp_small": "0.0500"},
    {"market_id": 138, "symbol": "AMD", "funding_premium_multiplier": 50,
     "base_interest_rate": "0.0032", "funding_clamp_big": "4.0000",
     "funding_clamp_small": "0.0500"},
    {"market_id": 9, "symbol": "DEAD", "funding_premium_multiplier": 100,
     "base_interest_rate": "0.0000", "funding_clamp_big": "4.0000",
     "funding_clamp_small": "0.0500"},
]


class FakeHL:
    def meta_and_asset_ctxs(self):
        return [META, CTXS]


class FakeLighter:
    def order_books(self):
        return BOOKS

    def order_book_details_all(self):
        return DETAILS


def test_discover_hl_markets_includes_delisted():
    # A delisted market still has history worth collecting; dropping it makes the universe
    # survivorship-biased, which is exactly what Phase 3 must not inherit.
    assert markets.discover_hl_markets(FakeHL()) == ["BTC", "ETH", "OLD"]


def test_hl_market_metadata_carries_listing_fields():
    df = markets.hl_market_metadata(FakeHL())
    row = df.filter(pl.col("coin") == "OLD").row(0, named=True)
    assert row["is_delisted"] is True
    assert row["sz_decimals"] == 2 and row["max_leverage"] == 3 and row["margin_table_id"] == 5
    assert row["margin_mode"] == "isolated"
    assert df.filter(pl.col("coin") == "BTC")["is_delisted"].item() is False


def test_discover_lighter_markets_keeps_perps_including_inactive_and_skips_spot():
    got = markets.discover_lighter_markets(FakeLighter())
    assert [m["market_id"] for m in got] == [1, 9, 138]


def test_lighter_market_metadata_joins_funding_parameters():
    df = markets.lighter_market_metadata(FakeLighter())
    btc = df.filter(pl.col("market_id") == 1).row(0, named=True)
    assert btc["funding_premium_multiplier"] == 100
    assert btc["effective_multiplier"] == 1.0          # the API reports hundredths (P7)
    assert btc["base_interest_rate_pct"] == 0.01
    assert btc["taker_fee"] == 0.0 and btc["min_base_amount"] == 0.00007
    assert str(df.schema["listed_at"]).startswith("Datetime")
    # Lighter dates no delisting, so the settlement block is the only exit information there is.
    assert {"start_timestamp", "end_timestamp", "settlement_type", "settlement_price",
            "settlement_cap"} <= set(df.columns)


def test_lighter_market_metadata_flags_off_default_funding_parameters():
    df = markets.lighter_market_metadata(FakeLighter())
    flagged = df.filter(pl.col("off_default_multiplier"))["symbol"].to_list()
    assert flagged == ["AMD"]                          # multiplier 50, not 100
    off_rate = df.filter(pl.col("off_default_interest"))["symbol"].to_list()
    assert sorted(off_rate) == ["AMD", "DEAD"]         # 0.0032 and 0.0000, not 0.0100


def test_funding_period_s_measures_the_modal_gap():
    hourly = [3600 * i for i in range(10)]
    assert markets.funding_period_s(hourly) == 3600
    four_hourly = [14400 * i for i in range(10)]
    assert markets.funding_period_s(four_hourly) == 14400
    assert markets.funding_period_s([3600]) is None    # not enough to measure
```

- [ ] **Step 2: Write the failing test** — add to `tests/test_lighter_api.py`

```python
def test_order_book_details_all_needs_no_market_id():
    # Measured 2026-09-21: /api/v1/orderBookDetails with no market_id returns every perp market
    # (235 of them) in one call, and is the only route carrying the funding parameters.
    seen = {}

    def handler(request):
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"code": 200, "order_book_details": [
            {"market_id": 1, "symbol": "BTC", "funding_premium_multiplier": 100}]})

    details = _client(handler).order_book_details_all()
    assert seen["params"] == {}
    assert details[0]["market_id"] == 1
```

- [ ] **Step 3: Run, expect failure**

Run: `uv run pytest tests/test_markets.py tests/test_lighter_api.py -v`
Expected: FAIL — `ModuleNotFoundError: fundr.markets`, plus `AttributeError: order_book_details_all`.

- [ ] **Step 4: Implement** — add to `src/fundr/sources/lighter_api.py`, next to `order_book_details`

```python
    def order_book_details_all(self) -> list[dict]:
        """Every market's details in one call — measured 2026-09-21: 235 perp markets returned
        with no `market_id` parameter. This is the only route carrying `funding_premium_multiplier`,
        `base_interest_rate` and the two clamps, which vary by market."""
        return self.get("/api/v1/orderBookDetails")["order_book_details"]
```

- [ ] **Step 5: Implement** `src/fundr/markets.py`

```python
"""Which markets exist on each venue, and what the venue says about them.

Discovery lives here rather than in a script because three backfills and the QA all need it and
a script cannot import another script: `python scripts/x.py` puts `scripts/` on sys.path, not
the repo root.

The metadata is a research variable in its own right — the design names "Market metadata" a core
Phase 2 variable, Phase 3 dates listings from it and Phase 9 prices fees from it. It also carries
the per-market funding parameters, which are NOT uniform: measured 2026-09-21 over Lighter's 235
perp markets, `funding_premium_multiplier` is 100 on 137 markets, 50 on 96 and 1 on 2 (OPENAI,
ANTHROPIC); `base_interest_rate` is 0.0100 on 119, 0.0032 on 89 and 0.0000 on 27. Any
baseline-vs-off-baseline split that assumes BTC's parameters is wrong on 98 of 235 markets."""
import polars as pl

HL_SOURCE = "hl:metaAndAssetCtxs"
LIGHTER_SOURCE = "lighter:/api/v1/orderBooks+orderBookDetails"
DEFAULT_MULTIPLIER = 100          # reported in hundredths; 100 == an effective 1.0 (P7)
DEFAULT_INTEREST_PCT = 0.01       # 0.0100 %/h, the crypto baseline


def _f(x) -> float | None:
    return None if x is None else float(x)


def discover_hl_markets(hl) -> list[str]:
    """Every market name, delisted included — a delisted market's history is still history."""
    meta, _ = hl.meta_and_asset_ctxs()
    return sorted(m["name"] for m in meta["universe"])


def hl_market_metadata(hl) -> pl.DataFrame:
    meta, _ = hl.meta_and_asset_ctxs()
    return pl.DataFrame([{
        "coin": m["name"],
        "is_delisted": bool(m.get("isDelisted", False)),
        "sz_decimals": m.get("szDecimals"),
        "max_leverage": m.get("maxLeverage"),
        "margin_table_id": m.get("marginTableId"),
        "margin_mode": m.get("marginMode"),
        "only_isolated": bool(m.get("onlyIsolated", False)),
    } for m in meta["universe"]]).sort("coin")


def discover_lighter_markets(api) -> list[dict]:
    """Every perp order book, active and inactive (measured 2026-09-21: 246 books = 235 perp,
    214 active and 21 inactive, plus 11 spot). Spot markets have no funding."""
    perps = [b for b in api.order_books() if b.get("market_type", "perp") == "perp"]
    return sorted(perps, key=lambda b: b["market_id"])


def lighter_market_metadata(api) -> pl.DataFrame:
    details = {d["market_id"]: d for d in api.order_book_details_all()}
    rows = []
    for b in discover_lighter_markets(api):
        d = details.get(b["market_id"], {})
        mult = d.get("funding_premium_multiplier")
        rows.append({
            "market_id": b["market_id"],
            "symbol": b.get("symbol"),
            "status": b.get("status"),
            "market_type": b.get("market_type"),
            "created_at_ms": int(b["created_at"]) if b.get("created_at") else None,
            "funding_premium_multiplier": mult,
            "effective_multiplier": None if mult is None else mult / 100,
            "base_interest_rate_pct": _f(d.get("base_interest_rate")),
            "funding_clamp_big_pct": _f(d.get("funding_clamp_big")),
            "funding_clamp_small_pct": _f(d.get("funding_clamp_small")),
            "maker_fee": _f(b.get("maker_fee")),
            "taker_fee": _f(b.get("taker_fee")),
            "liquidation_fee": _f(b.get("liquidation_fee")),
            "min_base_amount": _f(b.get("min_base_amount")),
            "min_quote_amount": _f(b.get("min_quote_amount")),
            "supported_size_decimals": b.get("supported_size_decimals"),
            "supported_price_decimals": b.get("supported_price_decimals"),
            "is_frozen": bool(b.get("is_frozen", False)),
            # The settlement block: Lighter dates no delisting, so for an expiring or settled
            # market these four fields are the only forward-looking exit information that
            # exists. Cheap to keep, impossible to reconstruct later.
            "start_timestamp": b.get("start_timestamp"),
            "end_timestamp": b.get("end_timestamp"),
            "settlement_type": b.get("settlement_type"),
            "settlement_price": b.get("settlement_price"),
            "settlement_cap": b.get("settlement_cap"),
        })
    return (pl.DataFrame(rows)
            .with_columns(
                pl.from_epoch("created_at_ms", time_unit="ms").dt.cast_time_unit("ms")
                  .alias("listed_at"),
                (pl.col("funding_premium_multiplier") != DEFAULT_MULTIPLIER)
                  .alias("off_default_multiplier"),
                (pl.col("base_interest_rate_pct") != DEFAULT_INTEREST_PCT)
                  .alias("off_default_interest"))
            .sort("market_id"))


def funding_period_s(timestamps) -> int | None:
    """The modal gap between consecutive settlements, in seconds.

    Lighter's funding period is a per-market configuration (P5): measure it per market rather
    than assuming 1h. Measured 2026-09-21: exactly 3600 s on markets 1 and 138."""
    ts = sorted({int(t) for t in timestamps})
    if len(ts) < 3:
        return None
    gaps = [b - a for a, b in zip(ts, ts[1:])]
    return max(set(gaps), key=gaps.count)
```

- [ ] **Step 6: Implement** `scripts/snapshot_markets.py`

```python
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
```

- [ ] **Step 7: Run, expect pass**

Run: `uv run pytest tests/test_markets.py tests/test_lighter_api.py -v`
Expected: 11 passed (6 new + 5 in `test_lighter_api.py`).

- [ ] **Step 8: Run it for real**

Run: `uv run python scripts/snapshot_markets.py`
Expected, against 2026-09-21's measurements: HL **234 markets, 56 delisted**; Lighter **235 perp markets, 214 active**, with the multiplier histogram `{100: 137, 50: 96, 1: 2}` and the interest histogram `{0.0100: 119, 0.0032: 89, 0.0000: 27}`. **Read the two histograms.** If the counts have moved, that is real news about the venue, not a bug — record the new values in `docs/phase2/datasets.md`.

- [ ] **Step 9: Commit**

```bash
git add src/fundr/markets.py scripts/snapshot_markets.py src/fundr/sources/lighter_api.py \
        tests/test_markets.py tests/test_lighter_api.py
git commit -m "feat: market discovery and dated metadata snapshots for both venues"
```

---

### Task 3: Hyperliquid settled funding backfill

**Files:**
- Create: `scripts/backfill_hl_funding.py`
- Test: `tests/test_backfill_hl_funding.py`

**Interfaces:**
- Consumes: `HLInfo.post`, `funding_history_frame`, `markets.discover_hl_markets`, `dataset`, `retry.with_retries`.
- Produces: `data/phase2/hl_funding/coin=<COIN>/part.parquet` with columns `coin, time, settle_time, funding_rate, premium, funding_rate_str, signed_rate_fraction` plus provenance; a manifest entry per run.
- Produces the importable functions `paged_funding_history(hl, coin, *, start_ms, end_ms, sleep=time.sleep)` and `backfill_coin(hl, coin, *, start_ms, end_ms, sleep=time.sleep) -> pl.DataFrame` so the tests can drive them without network.
- `signed_rate_fraction` is the cross-venue column: for HL it equals `funding_rate` (already a per-hour signed fraction). It exists so the two venues' tables share one comparable column name.

**Paging and retry granularity.** The script does **not** call `HLInfo.funding_history`, which pages internally: wrapping that in `with_retries` means a 429 on page 40 discards 39 pages and restarts the coin, and BTC alone is ~29,000 hours. Instead it re-implements the page loop around `HLInfo.post` so each page is retried on its own, and adds a small proactive throttle between pages — the same shape `scripts/validate_day1.py` already uses for its per-coin backoff.

**Resumability:** if a partition exists, resume from the last row's `time` (via `dataset.resume_cursor`, UTC-safe) rather than refetching; merge and de-duplicate on `settle_time`.

- [ ] **Step 1: Write the failing test** `tests/test_backfill_hl_funding.py`

```python
import httpx
import pytest

from scripts.backfill_hl_funding import backfill_coin


def _row(t, rate="0.0000125", premium="0.0001"):
    return {"coin": "BTC", "fundingRate": rate, "premium": premium, "time": t}


class FakeHL:
    """Pages like the real endpoint: at most `page_size` rows at or after startTime."""

    def __init__(self, rows=None, page_size=2, fail_on_call=()):
        self.rows = sorted(rows or [], key=lambda r: r["time"])
        self.page_size = page_size
        self.fail_on_call = set(fail_on_call)
        self.calls = 0

    def post(self, payload):
        self.calls += 1
        if self.calls in self.fail_on_call:
            request = httpx.Request("POST", "https://api.hyperliquid.xyz/info")
            raise httpx.HTTPStatusError("429", request=request,
                                        response=httpx.Response(429, request=request))
        lo, hi = payload["startTime"], payload["endTime"]
        return [r for r in self.rows if lo <= r["time"] <= hi][:self.page_size]


def test_backfill_coin_returns_typed_frame_with_signed_fraction():
    hl = FakeHL([_row(3_600_000), _row(7_200_000, rate="-0.00002")])
    df = backfill_coin(hl, "BTC", start_ms=0, end_ms=10_000_000, sleep=lambda s: None)
    assert df.height == 2
    assert df["signed_rate_fraction"].to_list() == [0.0000125, -0.00002]
    assert str(df.schema["settle_time"]).startswith("Datetime")


def test_backfill_coin_pages_and_concatenates():
    rows = [_row(3_600_000 * i) for i in range(1, 6)]
    hl = FakeHL(rows, page_size=2)
    df = backfill_coin(hl, "BTC", start_ms=0, end_ms=10_000_000_000, sleep=lambda s: None)
    assert df.height == 5
    assert hl.calls >= 3                      # 2 + 2 + 1, then one empty page to stop
    assert df["time"].is_sorted()


def test_retry_is_per_page_not_per_coin():
    # A 429 on the second page must resume AT the second page. Five rows at page_size 2 take
    # exactly 3 content pages + 1 terminating empty page; with one injected 429 that is 5 calls.
    # Had the retry wrapped the whole paging loop, it would have restarted from startTime=0 and
    # re-fetched page 1, costing a sixth call.
    rows = [_row(3_600_000 * i) for i in range(1, 6)]
    hl = FakeHL(rows, page_size=2, fail_on_call=[2])
    df = backfill_coin(hl, "BTC", start_ms=0, end_ms=10_000_000_000, sleep=lambda s: None)
    assert df.height == 5
    assert hl.calls == 5


def test_backfill_coin_is_empty_not_error_when_no_history():
    df = backfill_coin(FakeHL([]), "BTC", start_ms=0, end_ms=10_000_000,
                       sleep=lambda s: None)
    assert df.is_empty()
```

- [ ] **Step 2: Run, expect failure**

Run: `uv run pytest tests/test_backfill_hl_funding.py -v`
Expected: FAIL — `ModuleNotFoundError: scripts.backfill_hl_funding`. (Not a `sys.path` failure: Task 1 fixed that.)

- [ ] **Step 3: Implement** `scripts/backfill_hl_funding.py`

```python
"""Backfill Hyperliquid settled hourly funding for every market, from each market's listing.

Free, unauthenticated. Resumable: an existing partition is extended, not refetched.

Paging is done here rather than through `HLInfo.funding_history` so each page can be retried on
its own: that method pages internally, so a 429 on page 40 of BTC's ~29,000 hours would discard
39 pages and start the coin again."""
import argparse
import time

import polars as pl

from fundr import dataset, markets
from fundr.retry import with_retries
from fundr.sources.hl_api import HLInfo, funding_history_frame

DATASET = "hl_funding"
SOURCE = "hl:fundingHistory"
# Measured 2026-09-21: HL's own settled funding starts 2023-05-12T00:00:00.048Z for BTC and ETH
# (fundingHistory from startTime=0) -- eight days BEFORE the S3 archive's first day, 2023-05-20.
# Starting earlier than the true first hour costs one empty page; starting later would silently
# truncate the oldest markets.
DEFAULT_START_MS = 1_672_531_200_000  # 2023-01-01
PAGE_THROTTLE_S = 0.1                 # proactive, on top of the reactive backoff


def paged_funding_history(hl, coin: str, *, start_ms: int, end_ms: int,
                          sleep=time.sleep, throttle_s: float = PAGE_THROTTLE_S) -> list[dict]:
    rows: list[dict] = []
    cursor = start_ms
    while cursor < end_ms:
        page = with_retries(
            lambda c=cursor: hl.post({"type": "fundingHistory", "coin": coin,
                                      "startTime": c, "endTime": end_ms}), sleep=sleep)
        if not page:
            break
        rows += page
        cursor = page[-1]["time"] + 1
        sleep(throttle_s)
    return rows


def backfill_coin(hl, coin: str, *, start_ms: int, end_ms: int, sleep=time.sleep) -> pl.DataFrame:
    rows = paged_funding_history(hl, coin, start_ms=start_ms, end_ms=end_ms, sleep=sleep)
    if not rows:
        return pl.DataFrame()
    df = funding_history_frame(rows).unique(subset=["time"], keep="first").sort("time")
    # HL's fundingRate is already a per-hour signed fraction (Phase 1, P4): positive = longs pay.
    return df.with_columns(pl.col("funding_rate").alias("signed_rate_fraction"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start-ms", type=int, default=DEFAULT_START_MS)
    ap.add_argument("--coins", nargs="*", default=None, help="default: every market")
    args = ap.parse_args()

    hl = HLInfo()
    end_ms = int(time.time() * 1000)
    coins = args.coins or markets.discover_hl_markets(hl)
    total = 0
    for i, coin in enumerate(coins, 1):
        existing = dataset.read_partition(DATASET, coin=coin)
        cursor = dataset.resume_cursor(existing, "time")
        start = args.start_ms if cursor is None else cursor + 1
        fresh = backfill_coin(hl, coin, start_ms=start, end_ms=end_ms)
        if fresh.is_empty() and existing is None:
            print(f"[{i}/{len(coins)}] {coin}: no history")
            continue
        merged = dataset.merge_partition(existing, fresh, key="settle_time", source=SOURCE)
        dataset.write_partition(DATASET, merged, source=SOURCE, coin=coin)
        total += merged.height
        print(f"[{i}/{len(coins)}] {coin}: {merged.height} hours (+{fresh.height} new) "
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
Expected: three partitions with plausible hour counts. **BTC's first row must be 2023-05-12T00:00:00.048** — the measured start. Check: `uv run python -c "from fundr import dataset; d=dataset.read_partition('hl_funding', coin='BTC'); print(d.height, d['time'].min(), d['settle_time'].max())"`

- [ ] **Step 6: Run the full backfill**

Run: `uv run python scripts/backfill_hl_funding.py`
Expected: every market (234 today), no unhandled 429s. Record the wall time and the total row count. Re-run it once and confirm it is idempotent: **row counts unchanged, and the `+N new` counter reads 0 or 1 per coin** (1 when the hour rolled over between runs).

- [ ] **Step 7: Commit**

```bash
git add scripts/backfill_hl_funding.py tests/test_backfill_hl_funding.py
git commit -m "feat: backfill Hyperliquid settled funding"
```

---

### Task 4: Lighter settled funding backfill

**Files:**
- Create: `scripts/backfill_lighter_funding.py`
- Test: `tests/test_backfill_lighter_funding.py`

**Interfaces:**
- Consumes: `LighterAPI.fundings`, `fundings_frame`, `lighter_signed_rate`, `markets.discover_lighter_markets`, `markets.funding_period_s`, `dataset`, `retry.with_retries`.
- Produces: `data/phase2/lighter_funding/market_id=<ID>/part.parquet` with `market_id, symbol, timestamp, settle_time, rate_str, rate, direction, signed_rate, value, signed_rate_fraction` plus provenance.
- Produces `paged_fundings(api, market_id, *, start_s, end_s, sleep=time.sleep)` and `backfill_market(api, market, *, end_s, start_s=None, sleep=time.sleep) -> pl.DataFrame`.
- **`signed_rate_fraction` = `signed_rate / 100`** — Lighter reports percent per hour (Phase 1, P5/P7), so dividing by 100 puts it on Hyperliquid's per-hour-fraction basis. This is the one normalisation Phase 2b performs, and it is an added column, never a replacement.

**Paging and retry granularity**, as in Task 3: `LighterAPI.fundings_all` pages internally, so the script pages by 700-hour window around `LighterAPI.fundings` and retries each window on its own. `fundings_all` is left untouched for its existing callers.

**The funding period is measured, not assumed.** Phase 1 established that the period is a per-market configuration and said to assert `1h` per market. After each market is merged, `markets.funding_period_s` measures the modal gap; anything other than 3600 s is collected into a list printed at the end and written to the manifest, and flagged by the QA. Measured 2026-09-21: exactly 3600 s on markets 1, 138 and 173.

**An inactive market usually has history; it just stopped.** Market 173 (SPACEX, `inactive`) returns **985 settlements, 2026-05-08 20:00Z → 2026-06-18 20:00Z, with no gaps** — a complete life, measured over its full range. It returns zero rows only for a *recent* window, because nothing has settled since June, and an earlier draft of this plan generalised a 20-hour probe into "this market has no history". So: an empty frame is normal and reported, never an error; but the expectation for an inactive market is a complete series that **ends**, and the QA anchors its coverage to its own last row rather than to now (Task 8).

- [ ] **Step 1: Write the failing test** `tests/test_backfill_lighter_funding.py`

```python
import httpx
import pytest

from scripts.backfill_lighter_funding import backfill_market

MARKET = {"market_id": 1, "symbol": "BTC", "created_at": "0"}


def _row(ts, rate="0.0012", direction="long"):
    return {"timestamp": ts, "value": "1.0", "rate": rate, "direction": direction}


class FakeLighter:
    def __init__(self, rows=None, fail_on_call=()):
        self.rows = rows or []
        self.fail_on_call = set(fail_on_call)
        self.calls = 0
        self.windows = []

    def fundings(self, market_id, resolution, start_s, end_s, count_back):
        self.calls += 1
        self.windows.append((start_s, end_s))
        if self.calls in self.fail_on_call:
            request = httpx.Request("GET", "https://mainnet.zklighter.elliot.ai/api/v1/fundings")
            raise httpx.HTTPStatusError("429", request=request,
                                        response=httpx.Response(429, request=request))
        return [r for r in self.rows if start_s <= r["timestamp"] < end_s]


def test_signed_fraction_is_percent_divided_by_100():
    api = FakeLighter([_row(3600, "0.0012", "long"), _row(7200, "0.0478", "short")])
    df = backfill_market(api, MARKET, end_s=10_000, sleep=lambda s: None)
    assert df["signed_rate"].to_list() == [0.0012, -0.0478]
    assert df["signed_rate_fraction"].to_list() == pytest.approx([0.000012, -0.000478])


def test_settle_time_is_datetime_and_sorted():
    api = FakeLighter([_row(7200), _row(3600)])
    df = backfill_market(api, MARKET, end_s=10_000, sleep=lambda s: None)
    assert str(df.schema["settle_time"]).startswith("Datetime")
    assert df["timestamp"].to_list() == [3600, 7200]


def test_empty_history_returns_empty_frame():
    # A market that has stopped settling returns zero rows for any recent window -- market 173
    # (SPACEX) last settled 2026-06-18, though its 985-hour history is complete and intact. An
    # empty frame is something to report, never something to raise on.
    df = backfill_market(FakeLighter([]), MARKET, end_s=10_000, sleep=lambda s: None)
    assert df.is_empty()


def test_retry_is_per_window_not_per_market():
    rows = [_row(3600 * i) for i in range(1, 4)]   # 3600, 7200, 10800 -- all inside end_s
    api = FakeLighter(rows, fail_on_call=[1])
    df = backfill_market(api, MARKET, end_s=20_000, sleep=lambda s: None)
    assert df.height == 3
    assert api.calls == 2                    # the failed window, then the same window again
    assert api.windows[0] == api.windows[1]  # it resumed AT the failure, not before it
```

- [ ] **Step 2: Run, expect failure**

Run: `uv run pytest tests/test_backfill_lighter_funding.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement** `scripts/backfill_lighter_funding.py`

```python
"""Backfill Lighter settled hourly funding for every perp market, from its listing.

Free, no auth. Lighter reports percent per hour with an unsigned rate plus a direction field
(Phase 1, P5); `signed_rate_fraction` converts to Hyperliquid's per-hour-fraction basis.

Paged here rather than through `LighterAPI.fundings_all` so each 700-hour window is retried on
its own instead of restarting a 14,000-hour market on one 429."""
import argparse
import time

import polars as pl

from fundr import dataset, markets
from fundr.retry import with_retries
from fundr.sources.lighter_api import LighterAPI, fundings_frame

DATASET = "lighter_funding"
SOURCE = "lighter:/api/v1/fundings"
RESOLUTION = "1h"
WINDOW_S = 700 * 3600          # the documented 750-row cap, with headroom
PAGE_THROTTLE_S = 0.05
EXPECTED_PERIOD_S = 3600


def paged_fundings(api, market_id: int, *, start_s: int, end_s: int, sleep=time.sleep,
                   throttle_s: float = PAGE_THROTTLE_S) -> list[dict]:
    seen: dict[int, dict] = {}
    t = start_s
    while t < end_s:
        upper = min(t + WINDOW_S, end_s)
        rows = with_retries(
            lambda a=t, b=upper: api.fundings(market_id, RESOLUTION, a, b, 750), sleep=sleep)
        for row in rows:
            seen[row["timestamp"]] = row
        t = upper
        sleep(throttle_s)
    return [seen[k] for k in sorted(seen)]


def backfill_market(api, market: dict, *, end_s: int, start_s: int | None = None,
                    sleep=time.sleep) -> pl.DataFrame:
    begin = start_s if start_s is not None else int(market["created_at"]) // 1000
    rows = paged_fundings(api, market["market_id"], start_s=begin, end_s=end_s, sleep=sleep)
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
    market_list = markets.discover_lighter_markets(api)
    if args.market_ids:
        market_list = [m for m in market_list if m["market_id"] in set(args.market_ids)]
    total, empty, odd_period = 0, [], []
    for i, m in enumerate(market_list, 1):
        mid = m["market_id"]
        existing = dataset.read_partition(DATASET, market_id=mid)
        cursor = dataset.resume_cursor(existing, "timestamp")
        fresh = backfill_market(api, m, end_s=end_s,
                                start_s=None if cursor is None else cursor + 1)
        if fresh.is_empty() and existing is None:
            empty.append((mid, m.get("symbol")))
            print(f"[{i}/{len(market_list)}] {m['symbol']} ({mid}): no history")
            continue
        merged = dataset.merge_partition(existing, fresh, key="timestamp", source=SOURCE)
        dataset.write_partition(DATASET, merged, source=SOURCE, market_id=mid)
        total += merged.height
        # Phase 1: the funding period is a per-market CONFIGURATION. Assert it, do not assume it.
        period = markets.funding_period_s(merged["timestamp"].to_list())
        if period not in (None, EXPECTED_PERIOD_S):
            odd_period.append((mid, m.get("symbol"), period))
        print(f"[{i}/{len(market_list)}] {m['symbol']} ({mid}): {merged.height} hours "
              f"(+{fresh.height} new, period {period}s) "
              f"({merged['settle_time'].min()} → {merged['settle_time'].max()})")
    dataset.update_manifest(DATASET, {"source": SOURCE, "markets": len(market_list),
                                      "rows": total, "markets_without_history": empty,
                                      "markets_off_hourly_period": odd_period,
                                      "fetched_at_ms": int(time.time() * 1000)})
    print(f"\n{DATASET}: {total} rows across {len(market_list)} markets; "
          f"{len(empty)} with no history; {len(odd_period)} not on a 1h period")
    if odd_period:
        print("NOT HOURLY — every phase that assumes an hourly grid must special-case these:")
        for mid, symbol, period in odd_period:
            print(f"  {symbol} ({mid}): {period}s")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run, expect pass**

Run: `uv run pytest tests/test_backfill_lighter_funding.py -v`
Expected: 4 passed.

- [ ] **Step 5: Run it for real, three markets first, then all**

Run: `uv run python scripts/backfill_lighter_funding.py --market-ids 1 138 173`
then: `uv run python scripts/backfill_lighter_funding.py`
Expected: BTC (market 1) ≈ 14,600+ hours from 2025-01-17 with period 3600 s; AMD (138) present with period 3600 s; **SPACEX (173) ≈ 985 hours, 2026-05-08 20:00Z → 2026-06-18 20:00Z, period 3600 s** — an inactive market with a complete history that ends, not an empty one. Every perp market attempted (235 today). Re-run once to confirm idempotence; on the re-run SPACEX must add **0** new rows, which is the cleanest idempotence check available because nothing about it can change any more.

- [ ] **Step 6: Commit**

```bash
git add scripts/backfill_lighter_funding.py tests/test_backfill_lighter_funding.py
git commit -m "feat: backfill Lighter settled funding"
```

---

### Task 5: Lighter price and volume history

**Files:**
- Create: `scripts/backfill_lighter_candles.py`
- Test: `tests/test_backfill_lighter_candles.py`

**Interfaces:**
- Consumes: `LighterAPI.candles`, `LighterAPI.get` (for `markPriceCandles`), `markets.discover_lighter_markets`, `dataset`, `retry.with_retries`.
- Produces: `data/phase2/lighter_candles/market_id=<ID>/part.parquet` with `market_id, symbol, time, open, high, low, close, base_volume, quote_volume` and `data/phase2/lighter_mark_candles/market_id=<ID>/part.parquet` with `market_id, symbol, time, open, high, low, close, sc`, both plus provenance.
- Lighter's candles carry `t` (ms), `o/h/l/c`, `v` (base) and `V` (quote) — true per-bucket volume, unlike Hyperliquid's running daily total. Phase 1 confirmed ≥1 year at `1h`.
- **Mark-price candles are collected too.** `/api/v1/markPriceCandles` is free and native, measured 2026-09-21 returning `t, o, h, l, c, sc` (no volume). It is the venue's own mark series, which is what funding is computed against; Lighter's *index* price remains recorder-only and is listed as a hazard in Task 9.
- Fetch in windows (the endpoint caps rows per call); page forward from the market's listing to now.

**Two paging bugs this task must not have.** (a) Candles exist only where trades happened, so a market can be silent for its first weeks: the loop must page **to `end_s` regardless**, stopping early only after `MAX_EMPTY_WINDOWS` consecutive empty windows, never on the first empty one. (b) The most recent bucket is the hour **in progress** — it must be dropped before writing, or a partial bar is frozen into the dataset by the merge's existing-wins rule.

- [ ] **Step 1: Write the failing test** `tests/test_backfill_lighter_candles.py`

```python
from scripts.backfill_lighter_candles import (
    backfill_candles, candles_frame, mark_candles_frame)

MARKET = {"market_id": 1, "symbol": "BTC", "created_at": "0"}
RAW = [{"t": 1_789_808_400_000, "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5, "v": 10.0, "V": 15.0,
        "i": 31_369_348_944},
       {"t": 1_789_812_000_000, "o": 1.5, "h": 2.5, "l": 1.0, "c": 2.0, "v": 20.0, "V": 30.0,
        "i": 31_379_915_246}]
MARK_RAW = [{"t": 1_789_808_400_000, "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5, "sc": 924_848}]


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


def test_mark_price_candles_frame_has_no_volume_columns():
    # Measured 2026-09-21: markPriceCandles returns t,o,h,l,c,sc -- no v/V.
    df = mark_candles_frame(MARK_RAW, market_id=1, symbol="BTC")
    assert "base_volume" not in df.columns
    assert df["sc"].to_list() == [924_848]
    assert str(df.schema["time"]).startswith("Datetime")


def test_backfill_pages_past_an_empty_first_window():
    # A market can be silent for its first windows -- candles exist only where trades happened.
    # Breaking on the first empty window drops the market entirely.
    api = FakeLighter([[], [], RAW, []])
    df = backfill_candles(api, MARKET, end_s=1_789_900_000, sleep=lambda s: None)
    assert df.height == 2
    assert api.calls >= 3


def test_backfill_stops_after_max_empty_windows():
    api = FakeLighter([[] for _ in range(50)])
    df = backfill_candles(api, MARKET, end_s=1_789_900_000, max_empty_windows=3,
                          sleep=lambda s: None)
    assert df.is_empty()
    assert api.calls == 3


def test_backfill_dedupes_overlapping_pages():
    api = FakeLighter([RAW, RAW, []])
    df = backfill_candles(api, MARKET, end_s=1_789_900_000, sleep=lambda s: None)
    assert df.height == 2
    assert df["time"].is_sorted()


def test_backfill_drops_the_in_progress_bucket():
    # end_s lands inside the bucket stamped 1_789_812_000_000: that hour has not closed, so the
    # bar is partial and must not be written -- the merge keeps the first version it sees.
    api = FakeLighter([RAW, []])
    df = backfill_candles(api, MARKET, end_s=1_789_812_000 + 1800, sleep=lambda s: None)
    assert df["time"].dt.epoch("ms").to_list() == [1_789_808_400_000]


def test_no_candles_returns_empty():
    df = backfill_candles(FakeLighter([[]]), MARKET, end_s=1_789_900_000, max_empty_windows=1,
                          sleep=lambda s: None)
    assert df.is_empty()
```

- [ ] **Step 2: Run, expect failure**

Run: `uv run pytest tests/test_backfill_lighter_candles.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement** `scripts/backfill_lighter_candles.py`

```python
"""Backfill Lighter 1h price and volume per market, plus the native mark-price candles.

Free, no auth. Lighter's `v`/`V` are true per-bucket base and quote volume -- unlike
Hyperliquid's running daily total, which must be differenced. Phase 1 confirmed >= 1 year of 1h
history. `markPriceCandles` is the venue's own mark series (measured 2026-09-21: t,o,h,l,c,sc,
no volume); Lighter's INDEX price still has no historical source at all and stays recorder-only.

Discovery is imported from `fundr.markets`, not from a sibling script: `python scripts/x.py`
puts `scripts/` on sys.path, not the repo root, so a `from scripts.y import ...` here would
raise ModuleNotFoundError at run time even though pytest imports it fine."""
import argparse
import time

import polars as pl

from fundr import dataset, markets
from fundr.analysis import epoch_ms
from fundr.retry import with_retries
from fundr.sources.lighter_api import LighterAPI

DATASET = "lighter_candles"
MARK_DATASET = "lighter_mark_candles"
SOURCE = "lighter:/api/v1/candles"
MARK_SOURCE = "lighter:/api/v1/markPriceCandles"
WINDOW_S = 700 * 3600          # stay under the endpoint's per-call row cap
HOUR_MS = 3_600_000
MAX_EMPTY_WINDOWS = 24         # ~2 years of silence before giving up on a market
PAGE_THROTTLE_S = 0.05


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


def mark_candles_frame(rows: list[dict], *, market_id: int, symbol: str | None) -> pl.DataFrame:
    if not rows:
        return pl.DataFrame()
    df = pl.DataFrame(rows, schema={"t": pl.Int64, "o": pl.Float64, "h": pl.Float64,
                                    "l": pl.Float64, "c": pl.Float64, "sc": pl.Int64})
    return df.select(
        pl.lit(market_id).alias("market_id"),
        pl.lit(symbol).alias("symbol"),
        epoch_ms("t").alias("time"),
        pl.col("o").alias("open"), pl.col("h").alias("high"),
        pl.col("l").alias("low"), pl.col("c").alias("close"),
        "sc")  # undocumented counter, kept raw as received


def _page(api, market_id: int, start_s: int, end_s: int, *, mark: bool, sleep) -> list[dict]:
    if mark:
        return with_retries(
            lambda: api.get("/api/v1/markPriceCandles", market_id=market_id, resolution="1h",
                            start_timestamp=start_s, end_timestamp=end_s,
                            count_back=700)["c"], sleep=sleep)
    return with_retries(lambda: api.candles(market_id, "1h", start_s, end_s, 700), sleep=sleep)


def backfill_candles(api, market: dict, *, end_s: int, start_s: int | None = None,
                     mark: bool = False, max_empty_windows: int = MAX_EMPTY_WINDOWS,
                     sleep=time.sleep) -> pl.DataFrame:
    """Page forward to `end_s`, tolerating silent windows.

    Candles exist only where trades happened, so a quiet first window says nothing about the
    rest of a market's life; stopping on the first empty page would drop the market. Only
    `max_empty_windows` CONSECUTIVE empties end the scan."""
    begin = start_s if start_s is not None else int(market["created_at"]) // 1000
    frames, empties = [], 0
    t = begin
    while t < end_s and empties < max_empty_windows:
        upper = min(t + WINDOW_S, end_s)
        rows = _page(api, market["market_id"], t, upper, mark=mark, sleep=sleep)
        if rows:
            builder = mark_candles_frame if mark else candles_frame
            frames.append(builder(rows, market_id=market["market_id"],
                                  symbol=market.get("symbol")))
            empties = 0
        else:
            empties += 1
        t = upper
        sleep(PAGE_THROTTLE_S)
    if not frames:
        return pl.DataFrame()
    # The bucket containing `end_s` is the hour still in progress: a partial bar the merge would
    # then keep forever, because existing rows win on a key collision.
    open_bucket_ms = (end_s * 1000 // HOUR_MS) * HOUR_MS
    return (pl.concat(frames)
            .filter(pl.col("time").dt.epoch("ms") < open_bucket_ms)
            .unique(subset=["time"], keep="last").sort("time"))


def _run(api, market_list, *, name: str, source: str, mark: bool, end_s: int) -> int:
    total = 0
    for i, m in enumerate(market_list, 1):
        mid = m["market_id"]
        existing = dataset.read_partition(name, market_id=mid)
        cursor = dataset.resume_cursor(existing, "time")
        fresh = backfill_candles(api, m, end_s=end_s, mark=mark,
                                 start_s=None if cursor is None else cursor // 1000 + 1)
        if fresh.is_empty() and existing is None:
            print(f"[{i}/{len(market_list)}] {m['symbol']} ({mid}): no {name}")
            continue
        merged = dataset.merge_partition(existing, fresh, key="time", source=source)
        dataset.write_partition(name, merged, source=source, market_id=mid)
        total += merged.height
        print(f"[{i}/{len(market_list)}] {m['symbol']} ({mid}): {merged.height} bars "
              f"(+{fresh.height} new) ({merged['time'].min()} → {merged['time'].max()})")
    dataset.update_manifest(name, {"source": source, "markets": len(market_list),
                                   "rows": total, "fetched_at_ms": int(time.time() * 1000)})
    return total


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market-ids", nargs="*", type=int, default=None)
    ap.add_argument("--skip-mark", action="store_true")
    args = ap.parse_args()

    api = LighterAPI()
    end_s = int(time.time())
    market_list = markets.discover_lighter_markets(api)
    if args.market_ids:
        market_list = [m for m in market_list if m["market_id"] in set(args.market_ids)]
    total = _run(api, market_list, name=DATASET, source=SOURCE, mark=False, end_s=end_s)
    print(f"\n{DATASET}: {total} rows across {len(market_list)} markets")
    if not args.skip_mark:
        mark_total = _run(api, market_list, name=MARK_DATASET, source=MARK_SOURCE,
                          mark=True, end_s=end_s)
        print(f"{MARK_DATASET}: {mark_total} rows across {len(market_list)} markets")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run, expect pass**

Run: `uv run pytest tests/test_backfill_lighter_candles.py -v`
Expected: 7 passed.

- [ ] **Step 5: Run it, three markets then all**

Run: `uv run python scripts/backfill_lighter_candles.py --market-ids 1 138 173`
then the full run. Expected: BTC ≥ 1 year of hourly candles; a market with no trade history reported rather than silently dropped. **Note the true first candle per market** — Phase 1 never measured it, so this is new information worth recording in `docs/phase2/datasets.md`.

- [ ] **Step 6: Commit**

```bash
git add scripts/backfill_lighter_candles.py tests/test_backfill_lighter_candles.py
git commit -m "feat: backfill Lighter price, volume and mark-price history"
```

---

### Task 6: Per-bucket egress pricing — **already applied with this revision**

**Files:** `src/fundr/sources/hl_archive.py`, `tests/test_hl_archive.py`, `docs/phase1/data_audit.md`

These changes make every cost figure in this plan true, so they shipped with the plan revision
itself rather than waiting for an implementation session. This task is a verification step, kept
in place so the sequence stays legible.

- `EGRESS_USD_PER_GB` is now a **per-bucket mapping** — `hyperliquid-archive` → `0.09`
  (`us-east-1`), `hl-mainnet-node-data` → `0.114` (`ap-northeast-1`) — with a `0.114` default for
  any unknown bucket, so an unrecognised bucket can only ever be over-billed, never under. `_charge`
  takes the bucket it is charging for and records it in each ledger line. The module docstring
  records how the regions were established (`x-amz-bucket-region`) and warns that a redirecting
  endpoint is not evidence of region — the trap a middle revision of this plan fell into.
- `tests/test_hl_archive.py` gains two tests (`test_egress_is_priced_per_bucket`,
  `test_ledger_prices_each_bucket_at_its_own_rate`) and its over-budget fixture is now priced
  against the real archive bucket. The repo baseline is therefore **118 tests**, not 116.
- `docs/phase1/data_audit.md`'s Done check carries a correction line: repricing Phase 1's ledger
  **per bucket** (archive 0.0875 GB over 61 calls at $0.09, node data 0.0392 GB over 28 at $0.114)
  gives **$0.01278** against the recorded $0.01184 — a $0.0009 difference, almost all of it from
  the node-data bucket. The line also states plainly that the earlier $0.0149 correction was wrong
  and why. Phase 1's evidence notes are deliberately **not** rewritten — they record what was
  measured at the time, and a correction line is the honest form of the fix.

- [ ] **Step 1: Verify**

Run: `uv run pytest -q`
Expected: **118 passed**. `tests/test_hl_archive.py::test_download_refuses_over_budget` now prices its fixture against the real archive bucket at $0.09/GB: 40 GB ≈ $3.60, three times the cap Task 7 asks for. That bump is load-bearing rather than cosmetic — at the true rate the old 20 GB fixture is **$1.80**, which a $2.00 cap (what a middle revision of this plan proposed) would have swallowed silently, leaving the test asserting nothing at all.

---

### Task 7: Hyperliquid per-minute market state (billed — approval gate)

This is the only task that spends money. It downloads the whole `asset_ctxs` archive: per-minute
funding, premium, open interest, mark/oracle/mid price, impact bid/ask and daily volume for every
market, 2023-05-20 → present. It is what Phase 3's point-in-time universe and Phase 7's features
are built from ([handoff §2](../../phase1/handoff.md)).

**Files:**
- Create: `scripts/backfill_hl_archive.py`
- Modify: `src/fundr/sources/hl_archive.py` (budget constant), `tests/test_hl_archive.py` (budget fixture)
- Test: `tests/test_backfill_hl_archive.py`

**Measured cost: $0.922** — 1,218 files, 10.109 GB compressed, egress at `hyperliquid-archive`'s own rate of **$0.09/GB (`us-east-1`, confirmed by `x-amz-bucket-region`)** = $0.910, plus $0.012 of request charges. The existing guard `BUDGET_USD = 0.80` would refuse with about 90% of the archive downloaded, which is the worst possible place to stop. (A middle revision of this plan quoted $1.165 by pricing this bucket as Tokyo; only `hl-mainnet-node-data` is. Task 6 fixed the pricing.)

**Where it writes, and what that does to the spend guard.** `HLArchive.download` caches under `store.data_root()`, which defaults to `data/phase1` — the directory this plan forbids writing to, and where ~10 GB would land. So every command in this task runs with **`FUNDR_DATA=data/phase2`** and constructs `HLArchive(ledger=Path("data/phase2/aws_ledger.jsonl"))` explicitly. The consequence, stated plainly: the guard then counts **Phase 2b's spend only**, starting from zero, and Phase 1's ledger stays untouched as Phase 1's record. That is the intent — the raised cap is a Phase 2b budget, not a lifetime one — but it means the cap no longer protects against total historical spend, and anyone reading `data/phase2/aws_ledger.jsonl` is reading this phase's bill, not the project's.

**Disk.** Compressed downloads total 10.109 GB, but each `.lz4` is **deleted as soon as its parquet is written**, so the cache never accumulates: peak extra disk is one day (≤14 MB) plus the parquet. Parquet was measured at 0.91–1.00× the `.lz4` size on four real days → **≈9.6 GB**. Against **72 GiB free** today, that is comfortable. A re-fetched quarantined day re-downloads and therefore re-pays its egress (~$0.001/day).

**Incomplete days are quarantined, not frozen.** The handoff is explicit: validate every day against 1,440 rows per coin, quarantine short days, and re-download later to pick up the venue's backfill. Skipping any date whose parquet exists would freeze 2026-09-15 at 156/1440 forever. So the skip is gated on the **recorded completeness**, a quarantine list is kept, and a later run re-fetches the quarantined days.

**The completeness record is itself resumable.** It is the record Step 9 reports from, so a run that writes only its own days would erase every earlier day's measurement. It is read, has the dates this run re-fetched removed, is concatenated with the fresh rows and de-duplicated on `(date, coin)`.

- [ ] **Step 1: Ask the user.** Message: "The Hyperliquid per-minute archive is 1,218 files, 10.11 GB, and costs **$0.92** — $0.91 of egress at $0.09/GB (the bucket is in `us-east-1`, not Tokyo as an earlier estimate assumed) plus ~$0.01 of request charges. The code's cap is $0.80, so it would refuse about 90% of the way through. AWS's 100 GB/month free outbound allowance may make the actual invoice $0, but the guard counts list price. The archive gives per-minute open interest, premium and prices for every market back to 2023, which Phase 3's point-in-time universe and Phase 7's features need, and there is no free substitute. Raise the cap to $1.20 and proceed?" **Wait for a yes.** Without it, stop and do the other tasks — but see "After Task 9": this task is not optional, it is deferred, and the phase's conclusion changes if it is skipped.

- [ ] **Step 2: Raise the cap** in `src/fundr/sources/hl_archive.py`: `BUDGET_USD = 1.20`, with a comment naming the measured $0.922 Phase 2b backfill and the date. $1.20 is deliberately close to the measured cost: it covers the full pull plus roughly 300 quarantine re-fetches and still stops a runaway well short of a surprise. A cap of $2.00 would be worse than useless here — see the fixture note below.

  The over-budget fixture in `tests/test_hl_archive.py::test_download_refuses_over_budget` is already 40 GB (raised with this revision, priced against the real archive bucket): ≈**$3.60** at $0.09/GB, three times the new cap. Confirm it still fails as intended after the cap change. It matters that it is not 20 GB: at the true rate 20 GB is **$1.80**, which a $2.00 cap would have swallowed silently — the test would have passed by doing nothing.

- [ ] **Step 3: Write the failing test** `tests/test_backfill_hl_archive.py`

```python
import lz4.frame
import polars as pl
import pytest

from scripts.backfill_hl_archive import (
    check_data_root, convert_day, day_completeness, merge_completeness, needs_fetch, parse_day)


def _raw(rows_per_coin: dict[str, int]) -> pl.DataFrame:
    rows = []
    for coin, n in rows_per_coin.items():
        for i in range(n):
            rows.append({"time": f"2026-09-18T{i // 60:02d}:{i % 60:02d}:00Z", "coin": coin,
                         "funding": 0.0000125, "open_interest": 1.0, "premium": 0.0001,
                         "mark_px": 100.0, "oracle_px": 100.0, "day_ntl_vlm": 5.0})
    return pl.DataFrame(rows)


def _completeness(date: str, coin: str, completeness: float) -> pl.DataFrame:
    return pl.DataFrame({"date": [date], "coin": [coin],
                         "minutes": [int(1440 * completeness)],
                         "completeness": [completeness]})


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


def test_completeness_record_merges_across_runs():
    # The record Step 9 reports from must survive a run that only touched later days -- and a
    # re-fetched day must REPLACE its old row, not lose to it.
    existing = pl.concat([_completeness("2026-09-15", "BTC", 0.108),
                          _completeness("2026-09-17", "BTC", 1.0)])
    fresh = _completeness("2026-09-15", "BTC", 1.0)
    out = merge_completeness(existing, fresh)
    assert out.height == 2
    assert out.filter(pl.col("date") == "2026-09-15")["completeness"].item() == 1.0
    assert out.filter(pl.col("date") == "2026-09-17")["completeness"].item() == 1.0


def test_needs_fetch_is_false_for_a_recorded_complete_day(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNDR_PHASE2_DATA", str(tmp_path))
    path = tmp_path / "hl_asset_ctxs" / "date=2026-09-17" / "part.parquet"
    path.parent.mkdir(parents=True)
    pl.DataFrame({"x": [1]}).write_parquet(path)
    assert needs_fetch("2026-09-17", _completeness("2026-09-17", "BTC", 1.0)) is False


def test_needs_fetch_is_true_for_a_quarantined_short_day(tmp_path, monkeypatch):
    # 2026-09-15 really did hold 156 of 1440 rows. The handoff says quarantine and re-download
    # later to pick up the venue's backfill; skipping on file existence freezes it forever.
    monkeypatch.setenv("FUNDR_PHASE2_DATA", str(tmp_path))
    path = tmp_path / "hl_asset_ctxs" / "date=2026-09-15" / "part.parquet"
    path.parent.mkdir(parents=True)
    pl.DataFrame({"x": [1]}).write_parquet(path)
    assert needs_fetch("2026-09-15", _completeness("2026-09-15", "BTC", 0.108)) is True
    assert needs_fetch("2026-09-15", None) is True          # no record == unmeasured == fetch


def test_a_permanently_short_day_is_not_re_fetched(tmp_path, monkeypatch):
    # 2023-05-20 is the archive's first file and starts at 02:50:04Z: all 21 coins hold exactly
    # 1,270 of 1,440 minutes (0.8819, measured on the real file). No re-download will ever fill
    # it, so quarantining it means re-paying its egress on every run forever.
    monkeypatch.setenv("FUNDR_PHASE2_DATA", str(tmp_path))
    path = tmp_path / "hl_asset_ctxs" / "date=2023-05-20" / "part.parquet"
    path.parent.mkdir(parents=True)
    pl.DataFrame({"x": [1]}).write_parquet(path)
    assert needs_fetch("2023-05-20", _completeness("2023-05-20", "BTC", 0.8819)) is False
    # ... but it is still fetched the first time, when no parquet exists.
    other = tmp_path / "elsewhere"
    monkeypatch.setenv("FUNDR_PHASE2_DATA", str(other))
    assert needs_fetch("2023-05-20", None) is True


def test_check_data_root_refuses_a_split_tree(tmp_path, monkeypatch):
    # The guard that stops ~10 GB of .lz4 landing in data/phase1 because someone forgot an env
    # var: HLArchive caches under $FUNDR_DATA, everything else writes under $FUNDR_PHASE2_DATA.
    monkeypatch.setenv("FUNDR_PHASE2_DATA", str(tmp_path / "phase2"))
    monkeypatch.setenv("FUNDR_DATA", str(tmp_path / "phase1"))
    with pytest.raises(SystemExit, match="refusing to run"):
        check_data_root()
    monkeypatch.setenv("FUNDR_DATA", str(tmp_path / "phase2"))
    check_data_root()


def test_convert_day_deletes_the_lz4(tmp_path, monkeypatch):
    # Global constraint: the whole archive is 10.1 GB compressed. Keeping every .lz4 alongside
    # its parquet doubles the footprint for no benefit -- the parquet is the artefact.
    monkeypatch.setenv("FUNDR_PHASE2_DATA", str(tmp_path))
    csv = b"time,coin,funding\n2026-09-18T00:00:00Z,BTC,0.0000125\n"
    lz4_path = tmp_path / "20260918.csv.lz4"
    lz4_path.write_bytes(lz4.frame.compress(csv))
    df = convert_day(lz4_path, date="2026-09-18", source="test")
    assert df.height == 1
    assert not lz4_path.exists()
```

- [ ] **Step 4: Run, expect failure**

Run: `uv run pytest tests/test_backfill_hl_archive.py -v`
Expected: FAIL — module not found.

- [ ] **Step 5: Implement** `scripts/backfill_hl_archive.py`

```python
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
              f"spend ${arc.spent_usd():.4f}")

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
    dataset.update_manifest(DATASET, {"source": SOURCE, "days_written": done,
                                      "days_skipped": skipped,
                                      "days_requarantined": requarantined,
                                      "missing_from_archive": ["2026-07-08"],
                                      "spend_usd": arc.spent_usd(),
                                      "ledger": str(ledger),
                                      "permanently_short": sorted(PERMANENT_SHORT),
                                      "fetched_at_ms": int(time.time() * 1000)})
    print(f"\n{DATASET}: {done} days written, {skipped} already complete, "
          f"total Phase 2b spend ${arc.spent_usd():.4f}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Run, expect pass**

Run: `uv run pytest tests/test_backfill_hl_archive.py tests/test_hl_archive.py -v`
Expected: 14 passed (9 new + 5 existing).

- [ ] **Step 7: Confirm the guard fires before spending anything**

Run: `uv run python scripts/backfill_hl_archive.py --limit 1` **without** `FUNDR_DATA`.
Expected: an immediate `refusing to run: downloads would cache under .../data/phase1 while datasets go to .../data/phase2`, exit code 1, **no S3 call and no ledger line**. This is the guard that stops ~10 GB landing in Phase 1's directory; see it work once before trusting it.

- [ ] **Step 8: Trial run of five days, then the full range**

Run: `FUNDR_DATA=data/phase2 uv run python scripts/backfill_hl_archive.py --limit 5`
Check the spend line, that `data/phase2/s3/` is empty afterwards (the `.lz4` files were deleted), that `data/phase2/aws_ledger.jsonl` exists (and that `data/phase1/aws_ledger.jsonl` is **untouched** — compare its line count before and after), and one day's parquet. Then:
`FUNDR_DATA=data/phase2 uv run python scripts/backfill_hl_archive.py`
Expected: 1,213 days fetched (1,218 keys minus the 5 already done, and **no key for 2026-07-08**), total spend ≈ **$0.93**, no `BudgetExceeded`. Re-run once: every complete day is skipped, 2023-05-20 is skipped as permanently short rather than re-fetched, and only genuinely quarantined days come back — the log's `N already complete / M to fetch (K re-fetched from quarantine)` line says which.

- [ ] **Step 9: Re-run the quarantine a day later.** The venue backfills late files. Run the script again after 24 h and confirm that the days it re-fetched came back more complete. Record the before/after in the coverage doc. If a day stays short for a week it is permanent: add it to `PERMANENT_SHORT` with the measured reason, exactly as 2023-05-20 is, so it stops costing egress — and Phase 7 must treat its missing minutes as missing.

- [ ] **Step 10: Report completeness honestly.** Add a section to `docs/phase2/backfill_coverage.md`: the days whose median completeness is materially below 1.0, whether they cluster (Phase 1 found recent days do), **the permanently missing 2026-07-08**, and the resulting caveat for Phase 7.

- [ ] **Step 11: Commit**

```bash
git add scripts/backfill_hl_archive.py tests/test_backfill_hl_archive.py \
        src/fundr/sources/hl_archive.py tests/test_hl_archive.py docs/phase2/backfill_coverage.md
git commit -m "feat: backfill Hyperliquid per-minute market state"
```

---

### Task 8: Coverage, gaps, alignment and vendor QA

**Files:**
- Create: `scripts/qa_backfill.py`
- Test: `tests/test_qa_backfill.py`
- Create (after run): `data/phase2/qa/coverage_report.md` (not committed) and `docs/phase2/backfill_coverage.md` (committed summary)

**Interfaces:**
- Consumes: `dataset`, `fundr.analysis.gap_scan`, `fundr.markets`, `fundr.sources.oxarchive.OXArchive`.
- Produces:
  - `coverage_row(key, df, *, time_col, expected_start=None, expected_end=None, start_source="data") -> dict`
  - `coverage(frames: dict[str, pl.DataFrame], *, time_col, expected_starts=None, expected_ends=None, expected_end=None) -> pl.DataFrame` — the per-dataset table, built from `coverage_row`; **implemented, and its signature is the one the code actually has**. It takes the already-loaded frames (what `main` has in hand), not a dataset name and key — the declaration here and the definition below were written against each other and must stay that way
  - `match_symbols(hl_symbols, lighter_symbols) -> dict`
  - `cross_venue(hl_by_symbol, li_by_symbol) -> pl.DataFrame` — per symbol pair: overlapping hours, hours present on both, correlation of `signed_rate_fraction`
  - `lag_scan(hl_by_symbol, li_by_symbol, lags=(-1, 0, 1)) -> pl.DataFrame` and `alignment_verdict(lag_table) -> str`
  - `roster_diff(archive_coins, meta_coins) -> dict`
  - `vendor_crosscheck(ox, venue, symbol, ours, *, days=30) -> dict`
  - a written report, and a **non-zero exit code** if the alignment verdict fails
- **Five datasets, not two.** `hl_funding`, `lighter_funding`, `lighter_candles`, `lighter_mark_candles` and `hl_asset_ctxs` are all reported. A dataset that is absent (the archive, if Task 7 was declined) is reported as absent rather than skipped silently. Mark candles are not a formality: measured on market 138 (AMD), the trade candles start **2026-02-09 21:00Z** — 22 minutes after listing — while the mark candles start **2026-02-18 05:00Z**, an **8-day head gap** in a series Phase 7 would otherwise join straight onto the other one. Collecting a dataset the QA never measures is how that ships unnoticed.
- **A market that stopped settling is not a market with a coverage hole.** Lighter market 173 (SPACEX, `inactive`) has a complete 985-hour history from 2026-05-08 20:00Z to 2026-06-18 20:00Z and nothing after — measured. Scored against "now" it would read ~30% covered and head the worst-ten table forever. So the tail anchor is applied per market: `active` markets are measured to the last complete hour, `inactive` ones to their own last row, with `last` reported so a reader sees when they stopped. Lighter publishes **no delisting timestamp at all** (Phase 1), so that last row is the only exit date this phase can offer Phase 3.
- **The denominator is not self-referential.** Expected hours run from the market's **listing** (Lighter `created_at` from the Task 2 snapshot; HL from the coin's first appearance in the archive's completeness record, falling back to the coin's own first settled hour with `expected_start_source` recording which) to the **last complete hour now** — not from the data's own min and max, under which a fetch truncated at both ends scores a perfect 1.0. Two caveats to read the numbers with: a listing-derived start is a **lower bound**, so `coverage_row` takes whichever of proxy and first row is earlier (HL's funding history predates the archive by eight days, and a literal proxy would score BTC above 1.0); and the HL proxy is accurate only **to the day**, so up to 23 hours of `missing_head_hours` is expected noise — only heads materially longer than a day mean anything.
- **Symbol matching is a known hazard** (Phase 1): Hyperliquid uses `kPEPE`-style prefixes where Lighter uses `1000PEPE`. The QA must report unmatched symbols on both sides explicitly rather than silently intersecting — Phase 3 owns the alias table, but Phase 2b must surface how many markets are affected.
- **Hyperliquid's settlement-stamp convention is re-tested here** — it is an inference, and this is the cheapest place to check it. If the lag-0 cross-venue correlation is not the strongest of {−1, 0, +1}, the alignment Target B rests on is wrong and the phase stops.
- **Survivorship is checked** by diffing the coin roster in `asset_ctxs` against today's `meta.universe`: if HL ever drops delisted entries from `meta`, coins present only in the archive are exactly the ones a `meta`-built universe would silently lose.
- **The free 0xArchive cross-check** (handoff §8 action 6) compares the last 30 days of both venues against the vendor. Units are already comparable: 0xArchive's HL `funding_rate` is identical to HL's native fraction, and its Lighter `funding_rate` is the native percent ÷ 100 (P8, median ratio exactly 0.01 over 466 rows) — the same basis as `signed_rate_fraction`. The vendor's Lighter rows carry the *previous* settlement within an hour (P8), so the check reports the match rate at lag 0 **and** at −1 h and says which wins, rather than asserting an alignment it has not measured.

- [ ] **Step 1: Write the failing test** `tests/test_qa_backfill.py`

```python
from datetime import datetime, timedelta

import polars as pl
import pytest

from scripts.qa_backfill import (
    alignment_verdict, coverage, coverage_row, cross_venue, lag_scan, match_symbols, roster_diff,
    vendor_crosscheck, worst_by_coverage)


def _funding(symbol, hours, rate=0.00001, t0=datetime(2026, 1, 1), vary=False):
    times = pl.datetime_range(t0, t0 + timedelta(hours=hours - 1), interval="1h",
                              eager=True).cast(pl.Datetime("ms"))
    # A varying series must also be NON-LINEAR: a straight ramp correlates 1.0 with itself at
    # every lag, so the lag scan below could not tell an aligned series from a shifted one.
    # This permutation cycle gives corr 1.0 at lag 0 and ~-0.40 at lag +-1 (measured).
    rates = [rate * (1 + (i * 37) % 11) for i in range(hours)] if vary else [rate] * hours
    return pl.DataFrame({"symbol": [symbol] * hours, "settle_time": times,
                         "signed_rate_fraction": rates})


def test_coverage_row_counts_hours_and_gaps():
    df = _funding("BTC", 10)
    df = df.filter(pl.col("settle_time") != df["settle_time"][5])   # punch one hole
    row = coverage_row("BTC", df, time_col="settle_time")
    assert row["rows"] == 9
    assert row["expected_hours"] == 10
    assert row["gaps"] == 1
    assert 0.89 < row["coverage"] < 0.91


def test_coverage_row_expected_hours_come_from_the_listing_not_the_data():
    # The market listed 5 hours before our first row: a fetch that started late must not score
    # 1.0 just because it is internally consistent.
    df = _funding("BTC", 10)
    row = coverage_row("BTC", df, time_col="settle_time",
                       expected_start=datetime(2025, 12, 31, 19), start_source="listing")
    assert row["expected_hours"] == 15
    assert row["coverage"] == pytest.approx(10 / 15)
    assert row["missing_head_hours"] == 5
    assert row["expected_start_source"] == "listing"


def test_coverage_row_counts_a_truncated_tail():
    df = _funding("BTC", 10)
    row = coverage_row("BTC", df, time_col="settle_time",
                       expected_end=datetime(2026, 1, 1, 12))
    assert row["missing_tail_hours"] == 3
    assert row["coverage"] < 1.0


def test_coverage_anchors_an_inactive_market_to_its_own_last_row():
    # Lighter 173 (SPACEX) settled 985 complete hours and stopped on 2026-06-18. Measured to
    # "now" it would read ~30% and head the worst-ten table forever; measured to its own end it
    # is what it actually is -- complete, and over.
    dead = {"SPACEX": _funding("SPACEX", 10)}
    now = datetime(2026, 6, 1)
    scored_to_now = coverage(dead, time_col="settle_time", expected_end=now)
    scored_to_its_end = coverage(dead, time_col="settle_time",
                                 expected_ends={"SPACEX": None}, expected_end=now)
    assert scored_to_now["coverage"].item() < 0.01
    assert scored_to_its_end["coverage"].item() == 1.0


def test_match_symbols_reports_unmatched_both_ways():
    m = match_symbols(["BTC", "kPEPE", "ETH"], ["BTC", "1000PEPE", "SOL"])
    assert m["matched"] == ["BTC"]
    assert "kPEPE" in m["hl_only"] and "ETH" in m["hl_only"]
    assert "1000PEPE" in m["lighter_only"] and "SOL" in m["lighter_only"]


def test_cross_venue_reports_overlap_and_correlation():
    # Rates must VARY or both standard deviations are zero, the correlation is undefined, and
    # the test proves nothing about the code path it claims to cover.
    hl = _funding("BTC", 24, rate=0.00001, vary=True)
    li = _funding("BTC", 24, rate=0.00002, vary=True)
    out = cross_venue({"BTC": hl}, {"BTC": li})
    row = out.row(0, named=True)
    assert row["symbol"] == "BTC"
    assert row["overlap_hours"] == 24
    assert row["corr_signed_rate"] == pytest.approx(1.0)


def test_lag_scan_prefers_lag_zero_on_aligned_series():
    hl = _funding("BTC", 48, vary=True)
    li = _funding("BTC", 48, vary=True)
    table = lag_scan({"BTC": hl}, {"BTC": li})
    best = table.sort("mean_corr", descending=True, nulls_last=True).row(0, named=True)
    assert best["lag_hours"] == 0
    assert alignment_verdict(table) == "ALIGNED"


def test_lag_scan_detects_a_one_hour_shift():
    hl = _funding("BTC", 48, vary=True)
    li = _funding("BTC", 48, vary=True, t0=datetime(2026, 1, 1, 1))
    table = lag_scan({"BTC": hl}, {"BTC": li})
    assert alignment_verdict(table).startswith("MISALIGNED")


def test_roster_diff_flags_coins_missing_from_meta():
    # Survivorship: if HL ever drops delisted entries from `meta`, the coins that exist only in
    # the archive are exactly the ones a meta-built universe silently loses.
    d = roster_diff(archive_coins=["BTC", "ETH", "GONE"], meta_coins=["BTC", "ETH", "NEW"])
    assert d["archive_only"] == ["GONE"]
    assert d["meta_only"] == ["NEW"]


def test_worst_by_coverage_puts_nulls_last():
    cov = pl.DataFrame({"key": ["a", "b", "c"], "coverage": [0.5, None, 0.9]})
    assert worst_by_coverage(cov, n=3)["key"].to_list() == ["a", "c", "b"]


def test_vendor_crosscheck_counts_matches_at_both_lags():
    ours = _funding("BTC", 5, rate=0.00001, vary=True)

    class FakeOX:
        def get_all(self, path, **params):
            return [{"timestamp": t, "funding_rate": r}
                    for t, r in zip(ours["settle_time"].dt.epoch("ms").to_list(),
                                    ours["signed_rate_fraction"].to_list())]

    out = vendor_crosscheck(FakeOX(), "hyperliquid", "BTC", ours, days=30)
    assert out["rows_vendor"] == 5
    assert out["matched_lag0"] == 5
    assert out["matched_lag0"] >= out["matched_lag_minus_1h"]
```

- [ ] **Step 2: Run, expect failure**

Run: `uv run pytest tests/test_qa_backfill.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement** `scripts/qa_backfill.py`

`_md_table` is defined **at the top of the module, immediately after the imports and constants**, before any function that calls it, and every table in the report goes through it.

```python
"""Measure what the backfill actually got: coverage per market against the market's LISTING,
gaps, how far the two venues overlap, whether the hour grids really line up, whether the coin
roster is survivorship-safe, and whether a third party agrees with us.

A dataset nobody has measured is a dataset nobody should model on -- and a coverage number
computed from the data's own first and last row measures nothing at all."""
import argparse
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl

from fundr import dataset
from fundr.analysis import gap_scan

HOUR = timedelta(hours=1)
LAGS = (-1, 0, 1)
VENDOR_TOL = 1e-9


def _md_table(df: pl.DataFrame) -> str:
    """Minimal markdown table -- avoids pulling pandas/tabulate in for a report."""
    if df.is_empty():
        return "_(empty)_"
    cols = df.columns
    head = "| " + " | ".join(cols) + " |"
    rule = "|" + "|".join("---" for _ in cols) + "|"
    rows = ["| " + " | ".join("" if v is None else str(v) for v in row) + " |"
            for row in df.rows()]
    return "\n".join([head, rule, *rows])


def last_complete_hour(now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC).replace(tzinfo=None)
    return now.replace(minute=0, second=0, microsecond=0) - HOUR


def coverage_row(key: str, df: pl.DataFrame, *, time_col: str,
                 expected_start: datetime | None = None, expected_end: datetime | None = None,
                 start_source: str = "data") -> dict:
    if df.is_empty():
        return {"key": key, "rows": 0, "first": None, "last": None, "expected_hours": 0,
                "coverage": None, "gaps": 0, "missing_head_hours": None,
                "missing_tail_hours": None, "expected_start_source": start_source}
    first, last = df[time_col].min(), df[time_col].max()
    # A listing-derived start is a LOWER bound on history, never an upper one: HL's own settled
    # funding begins 2023-05-12 while the archive (which dates HL listings) begins 2023-05-20, so
    # taking the proxy literally would make BTC score above 1.0. Whichever is earlier wins.
    start = min(expected_start, first) if expected_start else first
    end = max(expected_end, last) if expected_end else last
    expected = int((end - start).total_seconds() // 3600) + 1
    gaps = gap_scan(df.with_columns(pl.lit(key).alias("_k")), time_col, "_k", HOUR)
    return {"key": key, "rows": df.height, "first": first, "last": last,
            "expected_hours": expected,
            "coverage": df.height / expected if expected > 0 else None,
            "gaps": gaps.height,
            "missing_head_hours": max(0, int((first - start).total_seconds() // 3600)),
            "missing_tail_hours": max(0, int((end - last).total_seconds() // 3600)),
            "expected_start_source": start_source}


def coverage(frames: dict[str, pl.DataFrame], *, time_col: str,
             expected_starts: dict[str, datetime] | None = None,
             expected_ends: dict[str, datetime | None] | None = None,
             expected_end: datetime | None = None) -> pl.DataFrame:
    """The per-dataset coverage table.

    `expected_starts` maps key -> listing time; a key absent from it falls back to its own first
    row and says so in `expected_start_source`. `expected_ends` overrides the tail anchor per
    key, with an explicit None meaning "measure this market to its own last row" -- which is the
    right answer for a market that has stopped settling (Lighter 173 ended 2026-06-18 and would
    otherwise score ~30% forever). `expected_end` is the default for keys it does not name."""
    expected_starts = expected_starts or {}
    expected_ends = expected_ends or {}
    rows = []
    for key, df in frames.items():
        start = expected_starts.get(key)
        end = expected_ends.get(key, expected_end) if key in expected_ends else expected_end
        rows.append(coverage_row(key, df, time_col=time_col, expected_start=start,
                                 expected_end=end,
                                 start_source="listing" if start else "data"))
    return pl.DataFrame(rows) if rows else pl.DataFrame()


def worst_by_coverage(cov: pl.DataFrame, n: int = 10) -> pl.DataFrame:
    """Worst first -- with nulls LAST. A null coverage means "not measurable", not "worst"."""
    return cov.sort("coverage", nulls_last=True).head(n)


def match_symbols(hl_symbols: list[str], lighter_symbols: list[str]) -> dict:
    hl, li = set(hl_symbols), set(lighter_symbols)
    return {"matched": sorted(hl & li), "hl_only": sorted(hl - li),
            "lighter_only": sorted(li - hl)}


def _paired(hl_by_symbol, li_by_symbol, symbol, lag_hours=0):
    h = hl_by_symbol[symbol].select("settle_time", pl.col("signed_rate_fraction").alias("hl"))
    li = li_by_symbol[symbol].select(
        (pl.col("settle_time") + pl.duration(hours=lag_hours)).alias("settle_time"),
        pl.col("signed_rate_fraction").alias("li"))
    return h.join(li, on="settle_time", how="inner")


def _corr(j: pl.DataFrame) -> float | None:
    if j.height > 2 and j["hl"].std() and j["li"].std():
        return float(j.select(pl.corr("hl", "li")).item())
    return None


def cross_venue(hl_by_symbol: dict[str, pl.DataFrame],
                li_by_symbol: dict[str, pl.DataFrame]) -> pl.DataFrame:
    rows = []
    for symbol in sorted(set(hl_by_symbol) & set(li_by_symbol)):
        j = _paired(hl_by_symbol, li_by_symbol, symbol)
        rows.append({"symbol": symbol, "overlap_hours": j.height,
                     "hl_hours": hl_by_symbol[symbol].height,
                     "lighter_hours": li_by_symbol[symbol].height,
                     "corr_signed_rate": _corr(j),
                     "overlap_start": j["settle_time"].min() if j.height else None,
                     "overlap_end": j["settle_time"].max() if j.height else None})
    return pl.DataFrame(rows)


def lag_scan(hl_by_symbol: dict[str, pl.DataFrame], li_by_symbol: dict[str, pl.DataFrame],
             lags=LAGS) -> pl.DataFrame:
    """Re-test Hyperliquid's settlement-stamp convention, which Phase 1 could only INFER.

    Target B is the HL-minus-Lighter spread at lag 0. If some other lag correlates better, the
    inference is wrong and every spread in the phase is an hour out of place."""
    rows = []
    for lag in lags:
        corrs = [c for symbol in sorted(set(hl_by_symbol) & set(li_by_symbol))
                 if (c := _corr(_paired(hl_by_symbol, li_by_symbol, symbol, lag))) is not None]
        rows.append({"lag_hours": lag, "pairs": len(corrs),
                     "mean_corr": sum(corrs) / len(corrs) if corrs else None,
                     "median_corr": float(pl.Series(corrs).median()) if corrs else None})
    return pl.DataFrame(rows)


def alignment_verdict(lag_table: pl.DataFrame) -> str:
    usable = lag_table.filter(pl.col("mean_corr").is_not_null())
    if usable.is_empty():
        return "NO DATA"
    best = usable.sort("mean_corr", descending=True).row(0, named=True)
    if best["lag_hours"] == 0:
        return "ALIGNED"
    return (f"MISALIGNED: lag {best['lag_hours']}h correlates best "
            f"(mean {best['mean_corr']:.4f}) -- HL's settlement-stamp inference is wrong")


def roster_diff(archive_coins: list[str], meta_coins: list[str]) -> dict:
    a, m = set(archive_coins), set(meta_coins)
    return {"archive_only": sorted(a - m), "meta_only": sorted(m - a),
            "both": len(a & m)}


def vendor_crosscheck(ox, venue: str, symbol: str, ours: pl.DataFrame, *, days: int = 30,
                      tol: float = VENDOR_TOL) -> dict:
    """Free-tier 0xArchive cross-check (handoff §8 action 6; the free tier reaches ~30 days).

    Units already agree: the vendor's HL `funding_rate` is HL's own fraction, and its Lighter
    `funding_rate` is Lighter's native percent / 100 (P8) -- both on our `signed_rate_fraction`
    basis. The vendor's Lighter rows carry the PREVIOUS settlement within an hour, so the match
    rate is reported at lag 0 and at -1h and the caller reads which one wins."""
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 86_400_000
    rows = ox.get_all(f"/v1/{venue}/funding/{symbol}", max_pages=30,
                      start=start_ms, end=end_ms, limit=1000)
    if not rows:
        return {"symbol": symbol, "venue": venue, "rows_vendor": 0, "rows_ours": ours.height,
                "matched_lag0": 0, "matched_lag_minus_1h": 0, "max_abs_diff": None}
    vendor = (pl.DataFrame({"timestamp": [int(r["timestamp"]) for r in rows],
                            "vendor": [float(r["funding_rate"]) for r in rows]})
              .with_columns(pl.from_epoch("timestamp", time_unit="ms")
                            .dt.cast_time_unit("ms").dt.truncate("1h").alias("settle_time"))
              .unique(subset=["settle_time"], keep="last", maintain_order=True))
    out = {"symbol": symbol, "venue": venue, "rows_vendor": vendor.height,
           "rows_ours": ours.height, "max_abs_diff": None}
    for lag, label in ((0, "matched_lag0"), (-1, "matched_lag_minus_1h")):
        shifted = vendor.select(
            (pl.col("settle_time") + pl.duration(hours=lag)).alias("settle_time"), "vendor")
        j = ours.select("settle_time", "signed_rate_fraction").join(
            shifted, on="settle_time", how="inner")
        diff = (j["signed_rate_fraction"] - j["vendor"]).abs() if j.height else None
        out[label] = int((diff <= tol).sum()) if j.height else 0
        if lag == 0 and j.height:
            out["max_abs_diff"] = float(diff.max())
    return out


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


def _lighter_metadata() -> pl.DataFrame | None:
    """The newest Task 2 snapshot, or None if Task 2 has not run."""
    base = dataset.root() / "markets" / "venue=lighter"
    parts = sorted(base.glob("date=*/part.parquet")) if base.exists() else []
    return pl.read_parquet(parts[-1]) if parts else None


def _lighter_listings(meta: pl.DataFrame | None) -> dict[str, datetime]:
    """Listing times, keyed by the symbol `_load` keys on."""
    if meta is None:
        return {}
    return {row["symbol"]: row["listed_at"] for row in meta.iter_rows(named=True)
            if row["symbol"] and row["listed_at"]}


def _lighter_tail_anchors(meta: pl.DataFrame | None, end: datetime) -> dict[str, datetime | None]:
    """`end` for markets still trading, None (= the market's own last row) for inactive ones.

    Lighter publishes no delisting timestamp, so an inactive market's last settled hour is the
    only exit date that exists. Measuring it against "now" would manufacture a coverage hole out
    of a market that simply ended -- 173 (SPACEX) settled 985 complete hours and stopped."""
    if meta is None:
        return {}
    return {row["symbol"]: (end if row["status"] == "active" else None)
            for row in meta.iter_rows(named=True) if row["symbol"]}


def _hl_listings(completeness: pl.DataFrame | None) -> dict[str, datetime]:
    """First appearance per HL coin, from the archive's own completeness record.

    HL publishes no listing date, but `asset_ctxs` names the coins present on every day, so the
    earliest date a coin appears is its listing to within a day. Free: the record is already a
    per-(date, coin) table. Without the archive this returns {} and the HL coverage denominators
    fall back to each coin's own first settled hour, marked `expected_start_source = data`."""
    if completeness is None or completeness.is_empty():
        return {}
    first = completeness.group_by("coin").agg(pl.col("date").min().alias("first_date"))
    return {row["coin"]: datetime.strptime(row["first_date"], "%Y-%m-%d")
            for row in first.iter_rows(named=True)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    ap.add_argument("--vendor-symbols", nargs="*", default=[],
                    help="symbols to cross-check against 0xArchive (needs OXARCHIVE_API_KEY)")
    args = ap.parse_args()

    end = last_complete_hour()
    arch = dataset.read_partition("hl_asset_ctxs_completeness", kind="daily")
    meta = _lighter_metadata()
    listings, tails = _lighter_listings(meta), _lighter_tail_anchors(meta, end)
    hl = _load("hl_funding", "coin")
    li = _load("lighter_funding", "market_id")
    candles = _load("lighter_candles", "market_id")
    mark = _load("lighter_mark_candles", "market_id")
    hl_cov = coverage(hl, time_col="settle_time", expected_starts=_hl_listings(arch),
                      expected_end=end)
    li_cov = coverage(li, time_col="settle_time", expected_starts=listings,
                      expected_ends=tails, expected_end=end)
    candle_cov = coverage(candles, time_col="time", expected_starts=listings,
                          expected_ends=tails, expected_end=end)
    mark_cov = coverage(mark, time_col="time", expected_starts=listings,
                        expected_ends=tails, expected_end=end)
    symbols = match_symbols(list(hl), list(li))
    xv = cross_venue(hl, li)
    lags = lag_scan(hl, li)
    verdict = alignment_verdict(lags)

    lines = ["# Phase 2b backfill coverage", "",
             f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M}Z; expected coverage runs to "
             f"{end} (the last complete hour).", ""]
    for name, cov, unit in (("Hyperliquid funding", hl_cov, "hours"),
                            ("Lighter funding", li_cov, "hours"),
                            ("Lighter candles", candle_cov, "bars"),
                            ("Lighter mark-price candles", mark_cov, "bars")):
        if cov.is_empty():
            lines += [f"## {name}", "", "**no data** — this dataset was not collected.", ""]
            continue
        lines += [f"## {name}", "",
                  f"- markets: {cov.height}",
                  f"- rows: {int(cov['rows'].sum())} {unit}",
                  f"- earliest: {cov['first'].min()}  latest: {cov['last'].max()}",
                  f"- markets with gaps: {int((cov['gaps'] > 0).sum())}",
                  f"- markets short at the head: {int((cov['missing_head_hours'] > 0).sum())}",
                  f"- markets short at the tail: {int((cov['missing_tail_hours'] > 0).sum())}",
                  f"- median coverage: {cov['coverage'].median():.4f}",
                  f"- coverage denominators from the venue's listing: "
                  f"{int((cov['expected_start_source'] == 'listing').sum())} of {cov.height}", "",
                  "Worst ten by coverage (nulls last):", "",
                  _md_table(worst_by_coverage(cov)), ""]

    if candle_cov.height and mark_cov.height:
        gaps = (candle_cov.select("key", pl.col("first").alias("candle_first"))
                .join(mark_cov.select("key", pl.col("first").alias("mark_first")),
                      on="key", how="inner")
                .with_columns((pl.col("mark_first") - pl.col("candle_first"))
                              .dt.total_hours().alias("mark_starts_later_hours"))
                .filter(pl.col("mark_starts_later_hours").abs() > 1)
                .sort("mark_starts_later_hours", descending=True))
        lines += ["## Mark-price candles vs trade candles: head gaps", "",
                  "The two price series do not necessarily start together. Measured on market "
                  "138 (AMD) while planning: trade candles from 2026-02-09 21:00Z, mark candles "
                  "from 2026-02-18 05:00Z — eight days apart. Any market below starts its two "
                  "series on different days, and a feature that joins them is silently short at "
                  "the head unless it says so.", "",
                  f"- markets whose series start more than an hour apart: {gaps.height}", "",
                  _md_table(gaps.head(20)), ""]

    if arch is None or arch.is_empty():
        lines += ["## Hyperliquid per-minute state (`hl_asset_ctxs`)", "",
                  "**not collected** — Task 7 was declined or has not run. Phase 3's "
                  "point-in-time universe and Phase 7's HL-side features have no source "
                  "without it.", ""]
    else:
        by_day = (arch.group_by("date")
                  .agg(pl.col("completeness").median().alias("median_completeness"),
                       pl.len().alias("coins")).sort("date"))
        lines += ["## Hyperliquid per-minute state (`hl_asset_ctxs`)", "",
                  f"- days measured: {by_day.height}",
                  f"- days below 0.99 median completeness: "
                  f"{int((by_day['median_completeness'] < 0.99).sum())}",
                  "- **2026-07-08 has no file in the archive at all** and never will; treat it "
                  "as missing, never as zero.", "",
                  "Worst ten days:", "",
                  _md_table(by_day.sort("median_completeness").head(10)), ""]

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
                  f"- median overlap per pair: {xv['overlap_hours'].median()}",
                  f"- median correlation: {xv['corr_signed_rate'].median()}", "",
                  _md_table(xv.sort("overlap_hours", descending=True).head(20)), ""]
    lines += ["## Settlement alignment (re-test of an inference)", "",
              "Phase 1 could only INFER that Hyperliquid's row stamped `T` closes the hour "
              "ending at `T`; Lighter's convention was verified directly. Target B is the "
              "lag-0 spread, so if another lag correlates better the whole target is an hour "
              "out of place.", "", _md_table(lags), "",
              f"**Verdict: {verdict}**", ""]

    if args.vendor_symbols:
        from fundr.sources.oxarchive import OXArchive
        ox = OXArchive()
        vendor_rows = []
        for symbol in args.vendor_symbols:
            if symbol in hl:
                vendor_rows.append(vendor_crosscheck(ox, "hyperliquid", symbol, hl[symbol]))
            if symbol in li:
                vendor_rows.append(vendor_crosscheck(ox, "lighter", symbol, li[symbol]))
        lines += ["## Independent cross-check (0xArchive free tier, last 30 days)", "",
                  "Handoff §8 action 6. Units already agree (P8): the vendor's HL rate is HL's "
                  "own fraction, its Lighter rate is Lighter's percent ÷ 100.", "",
                  _md_table(pl.DataFrame(vendor_rows)), "",
                  f"credits logged this run: {len(ox.calls)} calls", ""]

    out = Path(args.out or (dataset.root() / "qa" / "coverage_report.md"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines))
    print("\n".join(lines[:60]))
    print(f"\nreport written to {out}")
    if verdict.startswith("MISALIGNED"):
        print("\nSTOP: " + verdict)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run, expect pass**

Run: `uv run pytest tests/test_qa_backfill.py -v`
Expected: 11 passed.

- [ ] **Step 5: Run it against the real backfill**

Run: `uv run python scripts/qa_backfill.py`
then, once, with the vendor check: `uv run python scripts/qa_backfill.py --vendor-symbols BTC ENA`
Expected: a report naming per-venue coverage against listing-derived denominators, the archive's completeness picture, the gap counts, the matched/unmatched symbol counts, the cross-venue overlap and the alignment verdict. **Read it.** Flag anything surprising — any market far below 1.0 coverage, how many symbols fail to match, and above all the alignment verdict.

- [ ] **Step 6: If the alignment verdict is MISALIGNED, stop the phase.** Do not "fix" it by shifting the data. Re-run the test on a larger symbol set, then re-open `docs/phase1/evidence/P4-hl-api-crosscheck.md`: the inference recorded there would be wrong, and Targets A and B, the spread definition and every downstream phase depend on it. Report it to the user before writing anything else.

- [ ] **Step 7: Survivorship check.** With the archive present, compare its coin roster against today's `meta.universe`:

```bash
uv run python -c "
import polars as pl
from fundr import dataset, markets
from fundr.sources.hl_api import HLInfo
from scripts.qa_backfill import roster_diff
arch = sorted({c for p in (dataset.root()/'hl_asset_ctxs').glob('date=*/part.parquet')
               for c in pl.read_parquet(p, columns=['coin'])['coin'].unique().to_list()})
print(roster_diff(arch, markets.discover_hl_markets(HLInfo())))"
```

Expected: `archive_only` is empty or small. Every coin it names is a coin a `meta`-built universe would lose — record them in the coverage doc as the survivorship-risk list.

- [ ] **Step 8: Write** `docs/phase2/backfill_coverage.md` — a committed summary of the report: what was collected, coverage per venue against listing-derived denominators, the gap picture, the archive completeness and the permanently-missing 2026-07-08, the symbol-matching problem's size, the alignment verdict, the survivorship list, the vendor cross-check result, and what a later phase must handle. Link the uncommitted full report's path.

- [ ] **Step 9: Commit**

```bash
git add scripts/qa_backfill.py tests/test_qa_backfill.py docs/phase2/backfill_coverage.md
git commit -m "feat: backfill coverage, alignment, survivorship and vendor QA"
```

---

### Task 9: Dataset documentation

**Files:**
- Create: `docs/phase2/datasets.md`
- Modify: `docs/phase2/dependency_map.md` (mark what is now collected)

- [ ] **Step 1: Write** `docs/phase2/datasets.md` covering, per dataset: what it is, its source and endpoint, its coverage (markets, date range, rows), its known gaps, its provenance columns, how to load it, and what it costs to refresh. Include the exact commands to re-run each backfill and note which are free and which are billed. State that Task 7's commands need `FUNDR_DATA=data/phase2` and that `data/phase2/aws_ledger.jsonl` records Phase 2b's spend only.

- [ ] **Step 2: Add a "what a later phase must handle" section**, covering at least:
  - the symbol-matching problem with its measured size;
  - Hyperliquid's daily-cumulative `day_ntl_vlm` needing differencing with a UTC-midnight reset — **deferred to Phase 7 deliberately** (Scope decision 2), with raw stored and no derived column;
  - Lighter's percent-vs-fraction convention and the `signed_rate_fraction` column that resolves it;
  - **per-market funding parameters**: the multiplier and base interest rate histograms from Task 2, and the explicit warning that a baseline test written against BTC's parameters is wrong on ~98 of 235 Lighter markets;
  - archive days with low completeness, the quarantine mechanism, and **2026-07-08, which has no file at all**;
  - the recorder-only hazard list: Lighter open interest, Lighter premium, **Lighter index price**, Lighter delisting timestamps, and an exact-to-settlement HL running funding series;
  - **historical trades / aggregated flow are not collected** (Scope decision 1) — HL's node fills exist at ≈$34.4/yr (Tokyo egress; P3's $27.15 used the US rate), Lighter has no usable historical counterpart, and Phase 8 must therefore decide H3's symmetric feature set before anyone builds an HL-only flow feature;
  - Hyperliquid's settled funding starts **2023-05-12**, eight days before the archive, which is why the two datasets have different first days.

- [ ] **Step 3: Update** `docs/phase2/dependency_map.md` — mark Phase 2b's rows as collected, with the dataset names; add the market-metadata row; leave the recorder-only rows as they are, and add Lighter index price to them.

- [ ] **Step 4: Run the full suite and commit**

```bash
uv run pytest -q
git add docs/phase2/datasets.md docs/phase2/dependency_map.md
git commit -m "docs: Phase 2b dataset documentation"
```

Expected: **177 passed** — 118 before Phase 2b (the repo's 116 plus the two per-bucket egress tests Task 6 shipped) plus 59 new: 17 dataset/retry, 7 markets and the Lighter client, 4 HL funding, 4 Lighter funding, 7 Lighter candles, 9 archive, 11 QA. If the number differs, reconcile it before committing rather than editing the expectation.

---

## After Task 9

Phase 2b is complete when both funding datasets cover every market from its listing with a measured
coverage report, the market metadata is snapshotted for both venues, the per-minute Hyperliquid
state is present, and `docs/phase2/datasets.md` tells a later phase exactly what exists and what to
watch out for.

**Task 7 is not optional, and declining it does not merely defer a nice-to-have.** Phase 3's
point-in-time universe is defined by Hyperliquid open interest (handoff §5), and per-minute OI
exists **only** in `asset_ctxs` — Lighter has no native OI history at all, and the vendor's starts
2025-08-25 behind a $49/month tier. Declining Task 7 therefore removes the point-in-time universe
*and* HL's historical price, premium, oracle and volume series, which are Phase 7's entire HL-side
feature set. A reviewer costed the narrower alternative: a 2025-01-17→now subset (the date Lighter's
oldest market lists, so the both-venue window) is ~6.8 GB and **~$0.61 at the archive's true $0.09/GB**,
which would serve Target B's overlap window but **not Target A** (HL-only, back to 2023-05-12) and
**not the point-in-time universe** (which needs the pre-overlap history to rank markets as of any
date). The full pull at $0.92 is the correct call, and at the archive's true rate the subset saves
only **$0.31** — a third of a dollar to give up Target A's first twenty months and the universe
definition. The subset is the fallback if the budget is refused, and it must be recorded as a scope
reduction, not a saving.

With Task 7 done, Phase 3 (universe construction) is unblocked and needs nothing further from the
recorder. Without it, Phase 3 is blocked on its defining input and this plan's completion claim does
not hold.
