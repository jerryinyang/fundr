# Phase 1 Data Probe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build small tested source clients for Hyperliquid, Lighter and 0xArchive, run probes P0–P10, and produce the Phase 1 data audit, Target C decision and decision map.

**Architecture:** `src/fundr/` holds reusable clients (`sources/`), shared analysis helpers (`analysis.py`), sample selection (`sample.py`) and funding formulas (`funding/`). `probes/` holds one thin script per probe; each writes raw output to `data/phase1/<probe>/` (gitignored). Findings go into hand-written evidence notes under `docs/phase1/evidence/`, then into `docs/phase1/data_audit.md` and `docs/phase1/decision_map.md`.

**Tech Stack:** Python 3.13, uv, httpx, websockets, boto3, lz4, polars, pytest.

**Spec:** `docs/superpowers/specs/2026-09-19-phase1-data-probe-design.md` (parent: `docs/funding_research_design.md`). Executors read both.

## Global Constraints

- Python 3.13, managed with `uv`. Run everything as `uv run ...`.
- Dependencies: `httpx`, `websockets`, `boto3`, `lz4`, `polars`, `pytest` only. (`websockets` added to the spec's list: Lighter current funding is only exposed on its websocket — see pre-plan findings.)
- Raw downloads and probe output go to `data/phase1/` (gitignored). Never commit raw data.
- Test fixtures live in `tests/fixtures/` and are committed (gitignore negation added in Task 1).
- `OXARCHIVE_API_KEY` from environment only; never committed or printed.
- AWS: existing default profile; every S3 call uses requester-pays. Hard budget guard at $0.80 estimated; pause and ask the user before exceeding ~$1.
- 0xArchive: free tier only (50k credits, last 30 days of data, 15 req/s, 3 concurrent).
- No modelling, no large-scale extraction, no freezing of target design.
- A claim is "verified" only with a real file or response behind it. Docs/third-party claims stay "unverified".
- No funding formula is encoded until P7 confirms it (both venues).
- Stop and ask the user if: AWS spend heads past ~$1; 0xArchive free credits look insufficient; a finding changes Targets A/B (e.g. Lighter history too short, funding not comparable across venues).
- Messages to the user: plain, short, no jargon (see `CLAUDE.md` §5).

## Pre-plan findings (live checks on 2026-09-19, unverified until the probes record them)

- HL `POST https://api.hyperliquid.xyz/info` `{"type":"metaAndAssetCtxs"}` returns `[meta, ctxs]`; ctx fields: `funding, openInterest, prevDayPx, dayNtlVlm, premium, oraclePx, markPx, midPx, impactPxs, dayBaseVlm`. 234 universe entries (includes delisted).
- HL `fundingHistory` rows: `{coin, fundingRate, premium, time(ms, ~50ms after the hour)}`. Baseline hourly rate `0.0000125`.
- HL `predictedFundings` returns `[[coin, [[venue, {fundingRate, nextFundingTime, fundingIntervalHours}], ...]], ...]`, venues incl. `HlPerp`, `BinPerp`, `BybitPerp`.
- Lighter REST base `https://mainnet.zklighter.elliot.ai`. `/api/v1/fundings` rows: `{timestamp(s), value, rate:"0.0012", direction:"long"}` — rate unsigned with a direction field; units look like percent. `/api/v1/candles` works (`/candlesticks` returns empty). `/api/v1/orderBookDetails` includes `open_interest, mark_price, index_price`, volumes, `funding_premium_multiplier:100, funding_clamp_small:"0.0500", funding_clamp_big:"4.0000", base_interest_rate:"0.0100"` but **no current funding**. `/api/v1/orderBooks` has `status` (active/inactive) and `created_at` (ms).
- Lighter websocket `wss://mainnet.zklighter.elliot.ai/stream`, subscribe `{"type":"subscribe","channel":"market_stats/<id>"}` → `market_stats` with `current_funding_rate, funding_rate, funding_timestamp(ms), premium, open_interest, index_price, mark_price`, volumes.
- 0xArchive REST base `https://api.0xarchive.io`, header `X-API-Key`. Routes: `/v1/symbols`, `/v1/lighter/instruments`, `/v1/{hyperliquid|lighter}/funding/{symbol}` (raw updates when `interval` omitted; fields `funding_rate, premium, timestamp`), `/v1/{venue}/openinterest/{symbol}`, `/v1/{venue}/trades/{symbol}`, `/v1/data-quality/coverage/{exchange}/{symbol}` (earliest, latest, gaps, cadence per data type). Params `start`/`end` (ms), `limit` ≤1000, `cursor` from `meta.next_cursor`. Lighter replay exists via WebSocket (`op: "replay"`); Lighter has no live 0xArchive WS.
- Plan dry run (scratch copy, 2026-09-19): all library tests pass (25); P0, a 2-minute P9, P9 trade check, P9 analysis, P5 and P6 ran end to end against live APIs. Seen: 95 markets on both venues; sample ENA/PONS/ONDO/ETHFI/STRK/KAITO + BTC. PONS was listed ~17 days ago, so expect a swap at Task 9 Step 5. Lighter `/fundings` ignores `count_back` when a start/end window is given, caps at 750 rows, rejects sub-hour resolutions (HTTP 400). Lighter `/api/v1/trades` needs auth; `/recentTrades` does not. Lighter candles return data from 1 year ago (1h) and 180 days ago (1m). These are hints only — the real runs record the evidence.
- **Free tier serves only the most recent 30 days.** Consequence: P8 reads full-history coverage from the coverage/symbols routes (metadata), and pulls data only inside the last 30 days. If Lighter rebuild inputs (P10) come from 0xArchive, both P10 windows must fall inside the last 30 days instead of near the P1 old/recent dates. Record this deviation in the P10 evidence note.

## File map

| File | Responsibility |
|---|---|
| `pyproject.toml` | deps, pytest config, src layout |
| `src/fundr/store.py` | where probes write raw output |
| `src/fundr/analysis.py` | hourly profile, gap scan, match stats, tolerance, rebuild verdict, settled pairing |
| `src/fundr/sample.py` | P0 market-sample rule |
| `src/fundr/sources/hl_api.py` | HL info endpoint client + frames |
| `src/fundr/sources/lighter_api.py` | Lighter REST + websocket client + frames |
| `src/fundr/sources/hl_archive.py` | requester-pays S3 with spend ledger + lz4 decode |
| `src/fundr/sources/oxarchive.py` | 0xArchive REST client with call log |
| `src/fundr/funding/hl_formula.py` | HL hourly funding from premium (after P7) |
| `src/fundr/funding/lighter_formula.py` | Lighter hourly funding from premium (after P7) |
| `probes/p00_sample.py` … `probes/p10_rebuild.py` | one script per probe |
| `docs/phase1/evidence/_TEMPLATE.md` | evidence note shape |
| `docs/phase1/data_audit.md`, `docs/phase1/decision_map.md` | deliverables |

## Execution order

Tasks 1–6 build the library. Then: Task 7 (P0) → Task 8 (start P9 in background) → Tasks 9–11 (P1, P4, P5) → Task 12 (P9 analysis, once ≥4h collected) → Tasks 13–14 (P6, P8) → Task 15 (P7) → Task 16 (formula code) → Task 17 (P10) → Tasks 18–19 (P2, P3) → Task 20 (reports).

---

### Task 1: Project scaffold and raw-output store

**Files:**
- Modify: `pyproject.toml`
- Modify: `.gitignore` (append only)
- Create: `src/fundr/__init__.py`, `src/fundr/sources/__init__.py`, `src/fundr/funding/__init__.py`, `src/fundr/store.py`
- Create: `docs/phase1/evidence/_TEMPLATE.md`
- Test: `tests/test_store.py`

**Interfaces:**
- Produces: `fundr.store.data_root() -> Path`, `probe_dir(probe: str) -> Path`, `save_json(probe: str, name: str, obj) -> Path`, `append_jsonl(probe: str, name: str, obj) -> Path`, `load_json(probe: str, name: str) -> Any`. Root is `$FUNDR_DATA` or `data/phase1`.

- [ ] **Step 1: Replace `pyproject.toml`**

```toml
[project]
name = "fundr"
version = "0.1.0"
description = "Cross-venue funding dynamics research"
requires-python = ">=3.13"
dependencies = [
    "boto3",
    "httpx",
    "lz4",
    "polars",
    "websockets",
]

[dependency-groups]
dev = ["pytest"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/fundr"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: Append fixture negation to `.gitignore`**

```bash
cat >> .gitignore <<'EOF'

# fundr test fixtures are small trimmed samples and are committed.
!tests/fixtures/
!tests/fixtures/**
EOF
```

- [ ] **Step 3: Create package files and install**

```bash
mkdir -p src/fundr/sources src/fundr/funding probes tests/fixtures docs/phase1/evidence
touch src/fundr/__init__.py src/fundr/sources/__init__.py src/fundr/funding/__init__.py
uv sync
```

Expected: `uv sync` resolves and installs without error.

- [ ] **Step 4: Write the failing test** `tests/test_store.py`

```python
import json

from fundr import store


def test_save_and_load_json(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNDR_DATA", str(tmp_path))
    path = store.save_json("p00", "x.json", {"a": 1})
    assert path == tmp_path / "p00" / "x.json"
    assert store.load_json("p00", "x.json") == {"a": 1}


def test_append_jsonl(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNDR_DATA", str(tmp_path))
    store.append_jsonl("p09", "live.jsonl", {"n": 1})
    path = store.append_jsonl("p09", "live.jsonl", {"n": 2})
    lines = path.read_text().splitlines()
    assert [json.loads(line)["n"] for line in lines] == [1, 2]
```

- [ ] **Step 5: Run it, expect failure**

Run: `uv run pytest tests/test_store.py -v`
Expected: FAIL — `ImportError: cannot import name 'store'`.

- [ ] **Step 6: Implement** `src/fundr/store.py`

```python
"""Where Phase 1 probes put raw output. Everything under data_root() is gitignored."""
import json
import os
from pathlib import Path
from typing import Any


def data_root() -> Path:
    return Path(os.environ.get("FUNDR_DATA", "data/phase1"))


def probe_dir(probe: str) -> Path:
    path = data_root() / probe
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_json(probe: str, name: str, obj: Any) -> Path:
    path = probe_dir(probe) / name
    path.write_text(json.dumps(obj, indent=2, default=str))
    return path


def append_jsonl(probe: str, name: str, obj: Any) -> Path:
    path = probe_dir(probe) / name
    with path.open("a") as f:
        f.write(json.dumps(obj, default=str) + "\n")
    return path


def load_json(probe: str, name: str) -> Any:
    return json.loads((data_root() / probe / name).read_text())
```

- [ ] **Step 7: Run tests, expect pass**

Run: `uv run pytest tests/test_store.py -v`
Expected: 2 passed.

- [ ] **Step 8: Create evidence template** `docs/phase1/evidence/_TEMPLATE.md`

```markdown
# P<n> — <name>

Status: verified | unverified | unavailable

## What ran
- Command:
- Run date (UTC):
- Markets:
- Dates / windows sampled:

## What came back
- Row counts / sizes:
- Schema:
- Excerpt (≤10 lines):

## Findings
- (facts only; each tied to a file or response above)

## Open issues
-
```

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml uv.lock .gitignore .python-version src tests docs/phase1/evidence/_TEMPLATE.md
git commit -m "chore: scaffold fundr package and raw-output store"
```

---

### Task 2: Shared analysis helpers

**Files:**
- Create: `src/fundr/analysis.py`
- Test: `tests/test_analysis.py`

**Interfaces:**
- Produces:
  - `hourly_profile(df: pl.DataFrame, time_col: str, key_col: str, value_cols: list[str]) -> pl.DataFrame` — columns `key_col, hour, n_rows, <v>_n_distinct, <v>_last` per value column. `time_col` must be `pl.Datetime`.
  - `gap_scan(df, time_col: str, key_col: str, max_gap: timedelta) -> pl.DataFrame` — columns `key_col, gap_start, gap_end, gap`.
  - `reported_tolerance(values: Iterable[str]) -> float` — one unit in the last decimal place.
  - `match_stats(pred: pl.Series, actual: pl.Series, tol: float) -> dict` — keys `n, n_match, rate, mean_signed_error`.
  - `rebuild_verdict(rebuilt: pl.Series, settled: pl.Series, baseline: pl.Series, tol: float, min_off_baseline: int = 100) -> dict` — keys `verdict` (`pass|near_miss|fail|insufficient_sample`), `all`, `off_baseline`, `tol`.
  - `attach_settled(profile: pl.DataFrame, settled: pl.DataFrame, key_col: str) -> pl.DataFrame` — adds `settle_time = hour + 1h` and left-joins `settled` on `[key_col, "settle_time"]`.
  - `epoch_ms(col: str) -> pl.Expr`, `epoch_s(col: str) -> pl.Expr` — both give `pl.Datetime("ms")`.

- [ ] **Step 1: Write the failing tests** `tests/test_analysis.py`

```python
from datetime import datetime, timedelta

import polars as pl
import pytest

from fundr import analysis as a


def _df():
    t = [datetime(2026, 1, 1, 0, m) for m in (0, 20, 40)] + [datetime(2026, 1, 1, 1, 0)]
    return pl.DataFrame(
        {"t": t, "coin": ["X"] * 4, "funding": [1.0, 1.0, 2.0, 3.0]}
    ).with_columns(pl.col("t").cast(pl.Datetime("ms")))


def test_hourly_profile_counts_distinct_and_last():
    out = a.hourly_profile(_df(), "t", "coin", ["funding"])
    first = out.row(0, named=True)
    assert first["n_rows"] == 3
    assert first["funding_n_distinct"] == 2
    assert first["funding_last"] == 2.0
    assert out.height == 2


def test_gap_scan_finds_long_gap():
    df = _df()
    gaps = a.gap_scan(df, "t", "coin", timedelta(minutes=20))
    assert gaps.height == 0
    gaps = a.gap_scan(df, "t", "coin", timedelta(minutes=10))
    assert gaps.height == 3


def test_reported_tolerance_uses_most_decimals():
    assert a.reported_tolerance(["0.0000125", "0.001"]) == pytest.approx(1e-7)
    assert a.reported_tolerance(["0.0012"]) == pytest.approx(1e-4)


def test_match_stats_counts_one_unit_as_match():
    pred = pl.Series([0.0000125, 0.0000124, 0.0000200])
    act = pl.Series([0.0000125, 0.0000125, 0.0000125])
    s = a.match_stats(pred, act, 1e-7)
    assert s["n"] == 3 and s["n_match"] == 2


def test_rebuild_verdict_pass_and_insufficient():
    n = 200
    settled = pl.Series([0.00002] * n)
    baseline = pl.Series([False] * n)
    ok = a.rebuild_verdict(settled, settled, baseline, 1e-7)
    assert ok["verdict"] == "pass"
    few = a.rebuild_verdict(settled, settled, pl.Series([True] * n), 1e-7)
    assert few["verdict"] == "insufficient_sample"


def test_rebuild_verdict_near_miss_and_fail():
    settled = pl.Series([0.00002] * 200)
    baseline = pl.Series([False] * 200)
    near = pl.Series([0.00002] * 194 + [0.0001] * 6)  # 97% match
    assert a.rebuild_verdict(near, settled, baseline, 1e-7)["verdict"] == "near_miss"
    bad = pl.Series([0.00002] * 100 + [0.0001] * 100)  # 50%
    assert a.rebuild_verdict(bad, settled, baseline, 1e-7)["verdict"] == "fail"


def test_attach_settled_pairs_hour_with_next_settlement():
    prof = a.hourly_profile(_df(), "t", "coin", ["funding"])
    settled = pl.DataFrame(
        {"coin": ["X"], "settle_time": [datetime(2026, 1, 1, 1, 0)], "settled": [9.0]}
    ).with_columns(pl.col("settle_time").cast(pl.Datetime("ms")))
    out = a.attach_settled(prof, settled, "coin")
    assert out.row(0, named=True)["settled"] == 9.0
```

- [ ] **Step 2: Run, expect failure**

Run: `uv run pytest tests/test_analysis.py -v`
Expected: FAIL — `ImportError: cannot import name 'analysis'`.

- [ ] **Step 3: Implement** `src/fundr/analysis.py`

```python
"""Helpers shared by probes: intra-hour profiles, gaps, and settled-value matching."""
from collections.abc import Iterable
from datetime import timedelta

import polars as pl


def epoch_ms(col: str) -> pl.Expr:
    return pl.from_epoch(pl.col(col), time_unit="ms")


def epoch_s(col: str) -> pl.Expr:
    return pl.from_epoch(pl.col(col) * 1000, time_unit="ms")


def hourly_profile(df: pl.DataFrame, time_col: str, key_col: str, value_cols: list[str]) -> pl.DataFrame:
    """Per key and UTC hour: row count, distinct-value count and last value of each column."""
    aggs = [pl.len().alias("n_rows")]
    for c in value_cols:
        aggs += [pl.col(c).n_unique().alias(f"{c}_n_distinct"), pl.col(c).last().alias(f"{c}_last")]
    return (
        df.sort(time_col)
        .with_columns(pl.col(time_col).dt.truncate("1h").alias("hour"))
        .group_by([key_col, "hour"], maintain_order=True)
        .agg(aggs)
        .sort([key_col, "hour"])
    )


def gap_scan(df: pl.DataFrame, time_col: str, key_col: str, max_gap: timedelta) -> pl.DataFrame:
    return (
        df.sort([key_col, time_col])
        .with_columns(pl.col(time_col).shift(1).over(key_col).alias("gap_start"))
        .with_columns((pl.col(time_col) - pl.col("gap_start")).alias("gap"))
        .filter(pl.col("gap") > max_gap)
        .select(key_col, "gap_start", pl.col(time_col).alias("gap_end"), "gap")
    )


def reported_tolerance(values: Iterable[str]) -> float:
    """One unit in the last decimal place, from values exactly as the venue reported them."""
    places = max(len(v.split(".")[1]) if "." in v else 0 for v in values)
    return 10.0 ** -places


def match_stats(pred: pl.Series, actual: pl.Series, tol: float) -> dict:
    err = pred - actual
    n = len(err)
    # Tiny slack so a difference of exactly one reported unit survives float rounding.
    n_match = int((err.abs() <= tol * (1 + 1e-9)).sum())
    return {
        "n": n,
        "n_match": n_match,
        "rate": n_match / n if n else float("nan"),
        "mean_signed_error": float(err.mean()) if n else float("nan"),
    }


def rebuild_verdict(
    rebuilt: pl.Series, settled: pl.Series, baseline: pl.Series, tol: float, min_off_baseline: int = 100
) -> dict:
    """Spec P10 rule. `baseline` marks hours where settled funding sits at the interest baseline or a clamp."""
    all_ = match_stats(rebuilt, settled, tol)
    off = match_stats(rebuilt.filter(~baseline), settled.filter(~baseline), tol)
    if off["n"] < min_off_baseline:
        verdict = "insufficient_sample"
    elif all_["rate"] >= 0.99 and off["rate"] >= 0.99 and abs(all_["mean_signed_error"]) <= tol / 10:
        verdict = "pass"
    elif min(all_["rate"], off["rate"]) >= 0.95:
        verdict = "near_miss"
    else:
        verdict = "fail"
    return {"verdict": verdict, "all": all_, "off_baseline": off, "tol": tol}


def attach_settled(profile: pl.DataFrame, settled: pl.DataFrame, key_col: str) -> pl.DataFrame:
    """Pair each hour with the settlement that closes it (settle_time = hour + 1h).
    `settled` needs columns key_col and settle_time (Datetime ms)."""
    return profile.with_columns((pl.col("hour") + pl.duration(hours=1)).alias("settle_time")).join(
        settled, on=[key_col, "settle_time"], how="left"
    )
```

- [ ] **Step 4: Run, expect pass**

Run: `uv run pytest tests/test_analysis.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/fundr/analysis.py tests/test_analysis.py
git commit -m "feat: shared analysis helpers for probes"
```

---

### Task 3: Hyperliquid info API client

**Files:**
- Create: `src/fundr/sources/hl_api.py`
- Test: `tests/test_hl_api.py`

**Interfaces:**
- Consumes: `fundr.analysis.epoch_ms`.
- Produces:
  - `INFO_URL = "https://api.hyperliquid.xyz/info"`
  - `class HLInfo(client: httpx.Client | None = None)` with `post(payload) -> Any`, `meta_and_asset_ctxs() -> list`, `predicted_fundings() -> list`, `candle_snapshot(coin, interval, start_ms, end_ms) -> list`, `funding_history(coin, start_ms, end_ms) -> list[dict]` (pages until exhausted).
  - `asset_ctxs_frame(raw: list) -> pl.DataFrame` — columns `coin, is_delisted, funding, premium, open_interest, mark_px, oracle_px, day_ntl_vlm, oi_notional_usd`.
  - `funding_history_frame(rows: list[dict]) -> pl.DataFrame` — columns `coin, time (Datetime ms), settle_time (time truncated to hour), funding_rate, premium, funding_rate_str`.

- [ ] **Step 1: Write the failing tests** `tests/test_hl_api.py`

```python
import json

import httpx

from fundr.sources import hl_api

META = {"universe": [{"name": "BTC", "szDecimals": 5}, {"name": "OLD", "szDecimals": 0, "isDelisted": True}]}
CTXS = [
    {"funding": "0.0000125", "openInterest": "10", "prevDayPx": "1", "dayNtlVlm": "5", "premium": "0.0005",
     "oraclePx": "100", "markPx": "101", "midPx": "101", "impactPxs": ["100", "102"], "dayBaseVlm": "1"},
    {"funding": "0.0", "openInterest": "0", "prevDayPx": "1", "dayNtlVlm": "0", "premium": None,
     "oraclePx": "1", "markPx": "1", "midPx": None, "impactPxs": None, "dayBaseVlm": "0"},
]


def test_asset_ctxs_frame():
    df = hl_api.asset_ctxs_frame([META, CTXS])
    btc = df.row(0, named=True)
    assert btc["coin"] == "BTC" and btc["oi_notional_usd"] == 1010.0 and not btc["is_delisted"]
    assert df.row(1, named=True)["is_delisted"]


def test_funding_history_pages_until_empty():
    pages = [
        [{"coin": "ETH", "fundingRate": "0.0000125", "premium": "-0.0003", "time": 1000},
         {"coin": "ETH", "fundingRate": "0.0000125", "premium": "0.0001", "time": 3_601_000}],
        [],
    ]
    seen = []

    def handler(request):
        seen.append(json.loads(request.content)["startTime"])
        return httpx.Response(200, json=pages[len(seen) - 1])

    client = hl_api.HLInfo(httpx.Client(transport=httpx.MockTransport(handler)))
    rows = client.funding_history("ETH", 0, 10_000_000)
    assert len(rows) == 2
    assert seen == [0, 3_601_001]


def test_funding_history_frame_truncates_settle_time():
    df = hl_api.funding_history_frame(
        [{"coin": "ETH", "fundingRate": "0.0000125", "premium": "0.0001", "time": 1735689600054}]
    )
    row = df.row(0, named=True)
    assert row["funding_rate"] == 0.0000125
    assert row["funding_rate_str"] == "0.0000125"
    assert row["settle_time"].minute == 0 and row["settle_time"].second == 0
```

- [ ] **Step 2: Run, expect failure**

Run: `uv run pytest tests/test_hl_api.py -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Implement** `src/fundr/sources/hl_api.py`

```python
"""Hyperliquid public info endpoint."""
from typing import Any

import httpx
import polars as pl

from fundr.analysis import epoch_ms

INFO_URL = "https://api.hyperliquid.xyz/info"


class HLInfo:
    def __init__(self, client: httpx.Client | None = None):
        self._client = client or httpx.Client(timeout=30)

    def post(self, payload: dict) -> Any:
        r = self._client.post(INFO_URL, json=payload)
        r.raise_for_status()
        return r.json()

    def meta_and_asset_ctxs(self) -> list:
        return self.post({"type": "metaAndAssetCtxs"})

    def predicted_fundings(self) -> list:
        return self.post({"type": "predictedFundings"})

    def candle_snapshot(self, coin: str, interval: str, start_ms: int, end_ms: int) -> list:
        req = {"coin": coin, "interval": interval, "startTime": start_ms, "endTime": end_ms}
        return self.post({"type": "candleSnapshot", "req": req})

    def funding_history(self, coin: str, start_ms: int, end_ms: int) -> list[dict]:
        rows, cursor = [], start_ms
        while cursor < end_ms:
            page = self.post({"type": "fundingHistory", "coin": coin, "startTime": cursor, "endTime": end_ms})
            if not page:
                break
            rows += page
            cursor = page[-1]["time"] + 1
        return rows


def _f(x) -> float | None:
    return None if x is None else float(x)


def asset_ctxs_frame(raw: list) -> pl.DataFrame:
    meta, ctxs = raw
    records = [
        {
            "coin": u["name"],
            "is_delisted": bool(u.get("isDelisted", False)),
            "funding": _f(c.get("funding")),
            "premium": _f(c.get("premium")),
            "open_interest": _f(c.get("openInterest")),
            "mark_px": _f(c.get("markPx")),
            "oracle_px": _f(c.get("oraclePx")),
            "day_ntl_vlm": _f(c.get("dayNtlVlm")),
        }
        for u, c in zip(meta["universe"], ctxs)
    ]
    return pl.DataFrame(records).with_columns(
        (pl.col("open_interest") * pl.col("mark_px")).alias("oi_notional_usd")
    )


def funding_history_frame(rows: list[dict]) -> pl.DataFrame:
    df = pl.DataFrame(
        rows, schema={"coin": pl.String, "fundingRate": pl.String, "premium": pl.String, "time": pl.Int64}
    )
    return df.select(
        "coin",
        epoch_ms("time").alias("time"),
        epoch_ms("time").dt.truncate("1h").alias("settle_time"),
        pl.col("fundingRate").cast(pl.Float64).alias("funding_rate"),
        pl.col("premium").cast(pl.Float64),
        pl.col("fundingRate").alias("funding_rate_str"),
    )
```

- [ ] **Step 4: Run, expect pass**

Run: `uv run pytest tests/test_hl_api.py -v`
Expected: 3 passed.

- [ ] **Step 5: Live smoke check** (free, public)

Run: `uv run python -c "from fundr.sources.hl_api import *; print(asset_ctxs_frame(HLInfo().meta_and_asset_ctxs()).sort('oi_notional_usd', descending=True).head(3))"`
Expected: BTC/ETH-like rows with non-null `oi_notional_usd`.

- [ ] **Step 6: Commit**

```bash
git add src/fundr/sources/hl_api.py tests/test_hl_api.py
git commit -m "feat: Hyperliquid info API client"
```

---

### Task 4: Lighter REST + websocket client

**Files:**
- Create: `src/fundr/sources/lighter_api.py`
- Test: `tests/test_lighter_api.py`

**Interfaces:**
- Consumes: `fundr.analysis.epoch_s`.
- Produces:
  - `BASE_URL = "https://mainnet.zklighter.elliot.ai"`, `WS_URL = "wss://mainnet.zklighter.elliot.ai/stream"`
  - `class LighterAPI(client: httpx.Client | None = None)` with `get(path, **params) -> dict` (raises on `code != 200`), `order_books() -> list[dict]`, `order_book_details(market_id) -> dict`, `funding_rates() -> list[dict]`, `fundings(market_id, resolution, start_s, end_s, count_back) -> list[dict]`, `fundings_all(market_id, resolution, start_s, end_s) -> list[dict]` (700-period chunks, deduped by timestamp, sorted), `candles(market_id, resolution, start_s, end_s, count_back) -> list[dict]`.
  - `async market_stats_stream(market_ids: list[int], on_stats: Callable[[dict, int | None], None], stop: asyncio.Event) -> None`
  - `lighter_signed_rate(rate: float, direction: str) -> float` — **assumption until P5**: `direction == "long"` means longs pay (positive).
  - `fundings_frame(rows: list[dict], market_id: int) -> pl.DataFrame` — columns `market_id, timestamp, settle_time (Datetime ms), rate_str, rate, direction, signed_rate, value`.

- [ ] **Step 1: Write the failing tests** `tests/test_lighter_api.py`

```python
import httpx
import pytest

from fundr.sources import lighter_api


def _client(handler):
    return lighter_api.LighterAPI(
        httpx.Client(transport=httpx.MockTransport(handler), base_url=lighter_api.BASE_URL)
    )


def test_get_raises_on_error_code():
    api = _client(lambda r: httpx.Response(200, json={"code": 400, "message": "bad"}))
    with pytest.raises(RuntimeError):
        api.get("/api/v1/fundings")


def test_fundings_all_chunks_and_dedupes():
    calls = []

    def handler(request):
        p = request.url.params
        calls.append((int(p["start_timestamp"]), int(p["end_timestamp"])))
        start = int(p["start_timestamp"])
        rows = [{"timestamp": start, "value": "1", "rate": "0.0012", "direction": "long"},
                {"timestamp": 0, "value": "1", "rate": "0.0012", "direction": "long"}]
        return httpx.Response(200, json={"code": 200, "resolution": "1h", "fundings": rows})

    rows = _client(handler).fundings_all(1, "1h", 0, 3600 * 1500)
    assert calls[0] == (0, 3600 * 700)
    assert len(calls) == 3
    assert [r["timestamp"] for r in rows] == sorted({0, 3600 * 700, 3600 * 1400})


def test_signed_rate_assumption():
    assert lighter_api.lighter_signed_rate(0.0012, "long") == 0.0012
    assert lighter_api.lighter_signed_rate(0.0012, "short") == -0.0012


def test_fundings_frame():
    df = lighter_api.fundings_frame(
        [{"timestamp": 1789804800, "value": "0.97", "rate": "0.0012", "direction": "short"}], 1
    )
    row = df.row(0, named=True)
    assert row["market_id"] == 1 and row["signed_rate"] == -0.0012 and row["rate_str"] == "0.0012"
    assert row["settle_time"].minute == 0
```

- [ ] **Step 2: Run, expect failure**

Run: `uv run pytest tests/test_lighter_api.py -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Implement** `src/fundr/sources/lighter_api.py`

```python
"""Lighter public REST API and market_stats websocket."""
import asyncio
import json
from collections.abc import Callable

import httpx
import polars as pl
import websockets

from fundr.analysis import epoch_s

BASE_URL = "https://mainnet.zklighter.elliot.ai"
WS_URL = "wss://mainnet.zklighter.elliot.ai/stream"
_PERIOD_S = {"1h": 3600, "1d": 86400}
_CHUNK_PERIODS = 700  # stays under the documented 750-row cap per call


class LighterAPI:
    def __init__(self, client: httpx.Client | None = None):
        self._client = client or httpx.Client(base_url=BASE_URL, timeout=30)

    def get(self, path: str, **params) -> dict:
        r = self._client.get(path, params=params)
        r.raise_for_status()
        body = r.json()
        if body.get("code") != 200:
            raise RuntimeError(f"{path} {params}: {body}")
        return body

    def order_books(self) -> list[dict]:
        return self.get("/api/v1/orderBooks")["order_books"]

    def order_book_details(self, market_id: int) -> dict:
        return self.get("/api/v1/orderBookDetails", market_id=market_id)["order_book_details"][0]

    def funding_rates(self) -> list[dict]:
        return self.get("/api/v1/funding-rates")["funding_rates"]

    def fundings(self, market_id: int, resolution: str, start_s: int, end_s: int, count_back: int) -> list[dict]:
        return self.get(
            "/api/v1/fundings", market_id=market_id, resolution=resolution,
            start_timestamp=start_s, end_timestamp=end_s, count_back=count_back,
        )["fundings"]

    def fundings_all(self, market_id: int, resolution: str, start_s: int, end_s: int) -> list[dict]:
        step = _PERIOD_S[resolution] * _CHUNK_PERIODS
        seen: dict[int, dict] = {}
        t = start_s
        while t < end_s:
            for row in self.fundings(market_id, resolution, t, min(t + step, end_s), 750):
                seen[row["timestamp"]] = row
            t += step
        return [seen[k] for k in sorted(seen)]

    def candles(self, market_id: int, resolution: str, start_s: int, end_s: int, count_back: int) -> list[dict]:
        return self.get(
            "/api/v1/candles", market_id=market_id, resolution=resolution,
            start_timestamp=start_s, end_timestamp=end_s, count_back=count_back,
        )["c"]


async def market_stats_stream(
    market_ids: list[int], on_stats: Callable[[dict, int | None], None], stop: asyncio.Event
) -> None:
    async with websockets.connect(WS_URL) as ws:
        for m in market_ids:
            await ws.send(json.dumps({"type": "subscribe", "channel": f"market_stats/{m}"}))
        while not stop.is_set():
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), 5))
            except TimeoutError:
                continue
            if "market_stats" in msg:
                on_stats(msg["market_stats"], msg.get("timestamp"))


def lighter_signed_rate(rate: float, direction: str) -> float:
    """ASSUMPTION until P5 verifies it: direction 'long' means longs pay (positive rate)."""
    return rate if direction == "long" else -rate


def fundings_frame(rows: list[dict], market_id: int) -> pl.DataFrame:
    df = pl.DataFrame(
        rows, schema={"timestamp": pl.Int64, "value": pl.String, "rate": pl.String, "direction": pl.String}
    )
    return df.select(
        pl.lit(market_id).alias("market_id"),
        "timestamp",
        epoch_s("timestamp").alias("settle_time"),
        pl.col("rate").alias("rate_str"),
        pl.col("rate").cast(pl.Float64).alias("rate"),
        "direction",
        pl.struct("rate", "direction")
        .map_elements(lambda s: lighter_signed_rate(float(s["rate"]), s["direction"]), return_dtype=pl.Float64)
        .alias("signed_rate"),
        "value",
    )
```

- [ ] **Step 4: Run, expect pass**

Run: `uv run pytest tests/test_lighter_api.py -v`
Expected: 4 passed.

- [ ] **Step 5: Live smoke check** (free, public)

Run: `uv run python -c "from fundr.sources.lighter_api import *; import time; n=int(time.time()); print(fundings_frame(LighterAPI().fundings(1,'1h',n-10800,n,3),1))"`
Expected: 3 rows for BTC (market 1).

- [ ] **Step 6: Commit**

```bash
git add src/fundr/sources/lighter_api.py tests/test_lighter_api.py
git commit -m "feat: Lighter REST and websocket client"
```

---

### Task 5: Hyperliquid S3 archive client with spend guard

**Files:**
- Create: `src/fundr/sources/hl_archive.py`
- Test: `tests/test_hl_archive.py`

**Interfaces:**
- Consumes: `fundr.store.data_root`, `fundr.store.probe_dir`.
- Produces:
  - `ARCHIVE_BUCKET = "hyperliquid-archive"`, `NODE_BUCKET = "hl-mainnet-node-data"`, `BUDGET_USD = 0.80`
  - `class BudgetExceeded(RuntimeError)`
  - `class HLArchive(s3=None, ledger: Path | None = None, region: str = "ap-northeast-1")` with `spent_usd() -> float`, `list_prefixes(bucket, prefix) -> list[str]`, `list_keys(bucket, prefix, max_keys: int = 1000) -> list[dict]` (`key, size, last_modified`), `download(bucket, key) -> Path` (cached under `data/phase1/s3/<bucket>/<key>`).
  - `decode_lz4(path) -> bytes`, `read_csv_lz4(path) -> pl.DataFrame`.
- Cost model (upper-bound estimate): each request $0.000005, egress $0.09/GB. The ledger (`data/phase1/aws_ledger.jsonl`) records every charged call. A call that would push the estimate above `BUDGET_USD` raises `BudgetExceeded` **before** it is made.

- [ ] **Step 1: Write the failing tests** `tests/test_hl_archive.py`

```python
import io

import boto3
import lz4.frame
import pytest
from botocore.response import StreamingBody
from botocore.stub import Stubber

from fundr.sources import hl_archive


def _archive(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNDR_DATA", str(tmp_path))
    s3 = boto3.client("s3", region_name="ap-northeast-1", aws_access_key_id="x", aws_secret_access_key="x")
    return hl_archive.HLArchive(s3=s3), Stubber(s3)


def test_download_writes_file_and_ledger(tmp_path, monkeypatch):
    arc, stub = _archive(tmp_path, monkeypatch)
    data = lz4.frame.compress(b"time,coin,funding\n2023-01-01T00:00:00,BTC,0.0000125\n")
    params = {"Bucket": "hyperliquid-archive", "Key": "asset_ctxs/20230101.csv.lz4", "RequestPayer": "requester"}
    stub.add_response("head_object", {"ContentLength": len(data)}, params)
    stub.add_response("get_object", {"Body": StreamingBody(io.BytesIO(data), len(data))}, params)
    with stub:
        path = arc.download("hyperliquid-archive", "asset_ctxs/20230101.csv.lz4")
    df = hl_archive.read_csv_lz4(path)
    assert df.columns == ["time", "coin", "funding"]
    assert arc.spent_usd() > 0


def test_download_refuses_over_budget(tmp_path, monkeypatch):
    arc, stub = _archive(tmp_path, monkeypatch)
    params = {"Bucket": "b", "Key": "k", "RequestPayer": "requester"}
    stub.add_response("head_object", {"ContentLength": 20 * 10**9}, params)  # 20 GB ≈ $1.80
    with stub, pytest.raises(hl_archive.BudgetExceeded):
        arc.download("b", "k")


def test_list_keys_uses_requester_pays(tmp_path, monkeypatch):
    arc, stub = _archive(tmp_path, monkeypatch)
    stub.add_response(
        "list_objects_v2",
        {"Contents": [{"Key": "asset_ctxs/20230101.csv.lz4", "Size": 10}], "IsTruncated": False},
        {"Bucket": "hyperliquid-archive", "Prefix": "asset_ctxs/", "RequestPayer": "requester", "MaxKeys": 1000},
    )
    with stub:
        keys = arc.list_keys("hyperliquid-archive", "asset_ctxs/")
    assert keys[0]["key"] == "asset_ctxs/20230101.csv.lz4"
```

- [ ] **Step 2: Run, expect failure**

Run: `uv run pytest tests/test_hl_archive.py -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Implement** `src/fundr/sources/hl_archive.py`

```python
"""Hyperliquid requester-pays S3 archives. Every call is charged to the user's AWS account,
so each one is estimated and logged first, and refused if it would pass BUDGET_USD."""
import io
import json
from pathlib import Path

import boto3
import lz4.frame
import polars as pl

from fundr.store import data_root, probe_dir

ARCHIVE_BUCKET = "hyperliquid-archive"
NODE_BUCKET = "hl-mainnet-node-data"
BUDGET_USD = 0.80
REQUEST_USD = 0.000005  # upper bound per LIST/HEAD/GET request
EGRESS_USD_PER_GB = 0.09


class BudgetExceeded(RuntimeError):
    pass


class HLArchive:
    def __init__(self, s3=None, ledger: Path | None = None, region: str = "ap-northeast-1"):
        self._s3 = s3 or boto3.client("s3", region_name=region)
        self._ledger = ledger or data_root() / "aws_ledger.jsonl"

    def spent_usd(self) -> float:
        if not self._ledger.exists():
            return 0.0
        return sum(json.loads(line)["usd"] for line in self._ledger.read_text().splitlines())

    def _charge(self, op: str, target: str, nbytes: int = 0) -> None:
        usd = REQUEST_USD + nbytes / 1e9 * EGRESS_USD_PER_GB
        if self.spent_usd() + usd > BUDGET_USD:
            raise BudgetExceeded(f"{op} {target}: would reach ${self.spent_usd() + usd:.3f} > ${BUDGET_USD}")
        self._ledger.parent.mkdir(parents=True, exist_ok=True)
        with self._ledger.open("a") as f:
            f.write(json.dumps({"op": op, "target": target, "bytes": nbytes, "usd": usd}) + "\n")

    def list_prefixes(self, bucket: str, prefix: str) -> list[str]:
        out, token = [], None
        while True:
            self._charge("list", f"{bucket}/{prefix}")
            kw = {"Bucket": bucket, "Prefix": prefix, "Delimiter": "/", "RequestPayer": "requester"}
            if token:
                kw["ContinuationToken"] = token
            resp = self._s3.list_objects_v2(**kw)
            out += [p["Prefix"] for p in resp.get("CommonPrefixes", [])]
            if not resp.get("IsTruncated"):
                return out
            token = resp["NextContinuationToken"]

    def list_keys(self, bucket: str, prefix: str, max_keys: int = 1000) -> list[dict]:
        out, token = [], None
        while True:
            self._charge("list", f"{bucket}/{prefix}")
            kw = {"Bucket": bucket, "Prefix": prefix, "RequestPayer": "requester", "MaxKeys": max_keys}
            if token:
                kw["ContinuationToken"] = token
            resp = self._s3.list_objects_v2(**kw)
            out += [
                {"key": o["Key"], "size": o["Size"], "last_modified": o.get("LastModified")}
                for o in resp.get("Contents", [])
            ]
            if not resp.get("IsTruncated"):
                return out
            token = resp["NextContinuationToken"]

    def download(self, bucket: str, key: str) -> Path:
        dest = probe_dir("s3") / bucket / key
        if dest.exists():
            return dest
        self._charge("head", f"{bucket}/{key}")
        size = self._s3.head_object(Bucket=bucket, Key=key, RequestPayer="requester")["ContentLength"]
        self._charge("get", f"{bucket}/{key}", size)
        body = self._s3.get_object(Bucket=bucket, Key=key, RequestPayer="requester")["Body"].read()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
        return dest


def decode_lz4(path: Path) -> bytes:
    return lz4.frame.decompress(Path(path).read_bytes())


def read_csv_lz4(path: Path) -> pl.DataFrame:
    return pl.read_csv(io.BytesIO(decode_lz4(path)), infer_schema_length=10000)
```

- [ ] **Step 4: Run, expect pass**

Run: `uv run pytest tests/test_hl_archive.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/fundr/sources/hl_archive.py tests/test_hl_archive.py
git commit -m "feat: requester-pays S3 client with spend guard"
```

---

### Task 6: 0xArchive client with call log

**Files:**
- Create: `src/fundr/sources/oxarchive.py`
- Test: `tests/test_oxarchive.py`

**Interfaces:**
- Produces:
  - `BASE_URL = "https://api.0xarchive.io"`
  - `class OXArchive(client: httpx.Client | None = None, key: str | None = None)` — key from `OXARCHIVE_API_KEY` if not given. Methods: `get(path, **params) -> dict` (logs every call), `get_all(path, max_pages: int = 20, **params) -> list` (follows `meta.next_cursor` via `cursor`), attribute `calls: list[dict]` — each `{path, params, status, request_id, headers}` where `headers` keeps only credit/rate-limit/request-id headers.

- [ ] **Step 1: Write the failing tests** `tests/test_oxarchive.py`

```python
import httpx

from fundr.sources import oxarchive


def _client(handler):
    http = httpx.Client(transport=httpx.MockTransport(handler), base_url=oxarchive.BASE_URL)
    return oxarchive.OXArchive(client=http, key="test")


def test_get_logs_call_with_credit_headers():
    def handler(request):
        return httpx.Response(
            200, json={"success": True, "data": [], "meta": {"request_id": "r1"}},
            headers={"x-credits-remaining": "49990", "content-type": "application/json"},
        )

    api = _client(handler)
    api.get("/v1/lighter/funding/BTC", start=1, end=2)
    call = api.calls[0]
    assert call["path"] == "/v1/lighter/funding/BTC" and call["request_id"] == "r1"
    assert call["headers"] == {"x-credits-remaining": "49990"}


def test_get_all_follows_cursor():
    pages = {None: (["a"], "c1"), "c1": (["b"], None)}

    def handler(request):
        data, nxt = pages[request.url.params.get("cursor")]
        return httpx.Response(200, json={"success": True, "data": data, "meta": {"next_cursor": nxt}})

    assert _client(handler).get_all("/v1/x") == ["a", "b"]
```

- [ ] **Step 2: Run, expect failure**

Run: `uv run pytest tests/test_oxarchive.py -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Implement** `src/fundr/sources/oxarchive.py`

```python
"""0xArchive REST client. Free tier: last 30 days of data, 50k credits/month.
Every call is logged so P8 can report credit use."""
import os

import httpx

BASE_URL = "https://api.0xarchive.io"
_KEEP_HEADERS = ("credit", "ratelimit", "rate-limit", "request-id")


class OXArchive:
    def __init__(self, client: httpx.Client | None = None, key: str | None = None):
        key = key or os.environ["OXARCHIVE_API_KEY"]
        self._client = client or httpx.Client(base_url=BASE_URL, timeout=60)
        self._client.headers["X-API-Key"] = key
        self.calls: list[dict] = []

    def get(self, path: str, **params) -> dict:
        r = self._client.get(path, params=params)
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        self.calls.append({
            "path": path,
            "params": params,
            "status": r.status_code,
            "request_id": (body.get("meta") or {}).get("request_id") or r.headers.get("x-request-id"),
            "headers": {k: v for k, v in r.headers.items() if any(s in k.lower() for s in _KEEP_HEADERS)},
        })
        r.raise_for_status()
        return body

    def get_all(self, path: str, max_pages: int = 20, **params) -> list:
        out, cursor = [], None
        for _ in range(max_pages):
            body = self.get(path, **params, **({"cursor": cursor} if cursor else {}))
            out += body.get("data") or []
            cursor = (body.get("meta") or {}).get("next_cursor")
            if not cursor:
                break
        return out
```

- [ ] **Step 4: Run, expect pass**

Run: `uv run pytest tests/test_oxarchive.py -v`
Expected: 2 passed.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -v`
Expected: all tests pass (store 2, analysis 7, hl_api 3, lighter_api 4, hl_archive 3, oxarchive 2 = 21).

- [ ] **Step 6: Commit**

```bash
git add src/fundr/sources/oxarchive.py tests/test_oxarchive.py
git commit -m "feat: 0xArchive client with call log"
```

---

### Task 7: P0 — pick the market sample

**Files:**
- Create: `src/fundr/sample.py`, `probes/p00_sample.py`
- Test: `tests/test_sample.py`
- Create (after run): `docs/phase1/evidence/P0-sample.md`

**Interfaces:**
- Consumes: `HLInfo`, `asset_ctxs_frame`, `LighterAPI.order_books`, `store.save_json`.
- Produces:
  - `MID_RANKS = (11, 18, 25, 33, 40)`, `SMALL_RANGE = (60, 100)`, `SMALL_TARGET = 80`, `CONTROL = "BTC"`
  - `pick_sample(hl: pl.DataFrame, lighter_symbols: set[str], exclude: frozenset[str] = frozenset()) -> list[dict]` — each `{coin, rank, role, oi_notional_usd}`; roles `mid`, `small`, `control`. Ranks computed on the full candidate set; an excluded pick is replaced by the nearest higher-ranked (bigger) eligible market.
  - Output file `data/phase1/p00/sample.json`: list of `{coin, rank, role, oi_notional_usd, lighter_market_id}`. **Every later probe reads this file.**

- [ ] **Step 1: Write the failing tests** `tests/test_sample.py`

```python
import polars as pl

from fundr.sample import pick_sample


def _hl(n):
    coins = ["BTC"] + [f"C{i}" for i in range(2, n + 1)]
    return pl.DataFrame({
        "coin": coins,
        "is_delisted": [False] * n,
        "oi_notional_usd": [float(n - i) for i in range(n)],
    })


def test_picks_ranks_and_control():
    hl = _hl(120)
    sample = pick_sample(hl, set(hl["coin"]))
    by_role = {}
    for s in sample:
        by_role.setdefault(s["role"], []).append(s["rank"])
    assert by_role["mid"] == [11, 18, 25, 33, 40]
    assert by_role["small"] == [80]
    assert by_role["control"] == [1]


def test_only_markets_on_both_venues_and_not_delisted():
    hl = _hl(120).with_columns(pl.when(pl.col("coin") == "C3").then(True).otherwise(False).alias("is_delisted"))
    lighter = set(hl["coin"]) - {"C2"}
    sample = pick_sample(hl, lighter)
    assert all(s["coin"] not in {"C2", "C3"} for s in sample)


def test_short_candidate_list_takes_lowest_available():
    hl = _hl(50)
    small = [s for s in pick_sample(hl, set(hl["coin"])) if s["role"] == "small"]
    assert small[0]["rank"] == 50


def test_exclude_moves_to_next_bigger_market():
    hl = _hl(120)
    first = pick_sample(hl, set(hl["coin"]))
    rank11 = next(s["coin"] for s in first if s["rank"] == 11)
    again = pick_sample(hl, set(hl["coin"]), exclude=frozenset({rank11}))
    mids = [s["rank"] for s in again if s["role"] == "mid"]
    assert mids == [10, 18, 25, 33, 40]
```

- [ ] **Step 2: Run, expect failure**

Run: `uv run pytest tests/test_sample.py -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Implement** `src/fundr/sample.py`

```python
"""Spec P0: the fixed probe market sample, chosen by rule."""
import polars as pl

MID_RANKS = (11, 18, 25, 33, 40)
SMALL_RANGE = (60, 100)
SMALL_TARGET = 80
CONTROL = "BTC"


def pick_sample(hl: pl.DataFrame, lighter_symbols: set[str], exclude: frozenset[str] = frozenset()) -> list[dict]:
    cand = (
        hl.filter(~pl.col("is_delisted") & pl.col("coin").is_in(list(lighter_symbols)))
        .sort("oi_notional_usd", descending=True)
        .with_row_index("rank", offset=1)
    )
    rows = cand.to_dicts()
    n = len(rows)
    taken: set[str] = set()

    def take(target: int, role: str) -> dict:
        # Walk from the target rank toward bigger markets until one is eligible.
        for r in range(min(target, n), 0, -1):
            row = rows[r - 1]
            if row["coin"] not in exclude and row["coin"] not in taken and row["coin"] != CONTROL:
                taken.add(row["coin"])
                return {"coin": row["coin"], "rank": r, "role": role, "oi_notional_usd": row["oi_notional_usd"]}
        raise ValueError(f"no eligible market at or above rank {target}")

    picks = [take(r, "mid") for r in MID_RANKS]
    small_target = SMALL_TARGET if n >= SMALL_TARGET else n  # n < 60 is recorded in the P0 note
    picks.append(take(small_target, "small"))
    control = next((r for r in rows if r["coin"] == CONTROL), None)
    picks.append({
        "coin": CONTROL,
        "rank": control["rank"] if control else None,
        "role": "control",
        "oi_notional_usd": control["oi_notional_usd"] if control else None,
    })
    return picks
```

- [ ] **Step 4: Run, expect pass**

Run: `uv run pytest tests/test_sample.py -v`
Expected: 4 passed.

- [ ] **Step 5: Write the probe** `probes/p00_sample.py`

```python
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
```

- [ ] **Step 6: Run it**

Run: `uv run python probes/p00_sample.py`
Expected: 7 rows printed, each with a non-null `lighter_market_id`. If a pick looks like a naming mismatch (e.g. HL `kPEPE` vs Lighter `1000PEPE` left out of the candidate set), record it as an open issue in the P0 note; do not add alias logic.

- [ ] **Step 7: Write** `docs/phase1/evidence/P0-sample.md` using the template: run time, candidate-set size, the 7 picks with ranks and OI, any swaps, visible naming mismatches. Status: verified.

- [ ] **Step 8: Commit**

```bash
git add src/fundr/sample.py tests/test_sample.py probes/p00_sample.py docs/phase1/evidence/P0-sample.md
git commit -m "feat: P0 market sample rule and evidence"
```

---

### Task 8: P9 — start the live watch (runs ~4h in background)

**Files:**
- Create: `probes/p09_live.py`, `probes/p09_trades_check.py`

**Interfaces:**
- Consumes: `store.load_json("p00", "sample.json")`, `HLInfo`, `asset_ctxs_frame`, `LighterAPI`, `market_stats_stream`, `store.append_jsonl`, `store.save_json`.
- Produces: `data/phase1/p09/live.jsonl`, one JSON object per line, always with keys `t_ms, venue, source`, plus:
  - `venue="hl", source="metaAndAssetCtxs"`: `coin` + all `asset_ctxs_frame` columns.
  - `venue="hl", source="predictedFundings"`: `coin, venues` (raw list).
  - `venue="lighter", source="ws_market_stats"`: `market_id, n_msgs, cfr_values` (distinct `current_funding_rate` strings seen during the minute), `ws_ts`, plus every `market_stats` field of the latest message.
  - `venue="lighter", source="orderBookDetails"`: `market_id` + raw details.
  - `venue="lighter", source="funding-rates"`: `rows` (raw rows for sample market ids).
  - `venue="lighter", source="ws_reconnect"`: `error`.
- Produces: `data/phase1/p09/trades_hl.json`, `data/phase1/p09/trades_lighter.json`.

- [ ] **Step 1: Write** `probes/p09_live.py`

```python
"""P9: poll both venues about once a minute to see how 'current funding' behaves within the hour."""
import argparse
import asyncio
import time

import websockets

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
        except (OSError, websockets.ConnectionClosed) as e:
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
        except Exception as e:  # one failed poll must not end a 4-hour run; it is logged and visible in the data
            write(venue="probe", source="poll_error", error=repr(e))
        await asyncio.sleep(max(0.0, 60 - (time.time() - started)))
    stop.set()
    await ws_task


asyncio.run(main())
```

- [ ] **Step 2: Smoke-run for 2 minutes**

Run: `FUNDR_DATA=/tmp/fundr-smoke uv run python probes/p09_live.py --minutes 2` (copy `data/phase1/p00/sample.json` to `/tmp/fundr-smoke/p00/` first)
Expected: `/tmp/fundr-smoke/p09/live.jsonl` has lines for all five `source` values; `ws_market_stats` lines have `n_msgs > 0`. No `poll_error` lines.

- [ ] **Step 3: Start the real run in the background**

Run (background): `uv run python probes/p09_live.py --minutes 250`
It must span at least 3 hourly settlements. Note the start time (UTC) for the P9 note. Continue with Tasks 9–11 while it runs.

- [ ] **Step 4: Write** `probes/p09_trades_check.py` — confirms a live source of signed trades exists on each venue (needed by a Phase 2 recorder)

```python
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
```

- [ ] **Step 5: Run it**

Run: `uv run python probes/p09_trades_check.py`
Expected: both venues return trade messages carrying a side/aggressor field (HL `side`; Lighter field names recorded as seen). If the chosen mid-cap is quiet for 30s, rerun with the rank-11 market or BTC and note it.

- [ ] **Step 6: Commit**

```bash
git add probes/p09_live.py probes/p09_trades_check.py
git commit -m "feat: P9 live watch and trade-flow check"
```

---

### Task 9: P1 — Hyperliquid `asset_ctxs` archive

**Files:**
- Create: `probes/p01_hl_asset_ctxs.py`
- Create: `tests/fixtures/hl_asset_ctxs_sample.csv.lz4`, `tests/test_fixture_asset_ctxs.py`
- Create (after run): `docs/phase1/evidence/P1-hl-asset-ctxs.md`

**Interfaces:**
- Consumes: `HLArchive`, `ARCHIVE_BUCKET`, `read_csv_lz4`, `hourly_profile`, `gap_scan`, `store`.
- Produces: `data/phase1/p01/listing.json` (all `asset_ctxs/` keys), `data/phase1/p01/dates.json` (`{"old","mid","recent"}` → key), `data/phase1/p01/asset_ctxs_<YYYYMMDD>.parquet` (sample coins only, `time` parsed to `Datetime("ms")`), `data/phase1/p01/profile_<YYYYMMDD>.parquet`. Task 10 and Task 17 read these.

- [ ] **Step 1: Ask the user before the first billed call.** Message: "Next step downloads 3 sample files from Hyperliquid's archive, billed to your AWS account. Expected cost: a few cents, hard cap $0.80. OK to go ahead?" Wait for yes.

- [ ] **Step 2: Confirm the bucket region**

Run: `aws s3api get-bucket-location --bucket hyperliquid-archive --request-payer requester`
Expected: a `LocationConstraint`. If it is not `ap-northeast-1`, pass `region=<value>` to `HLArchive(...)` in the probes (Tasks 9, 18, 19) and record it in the P1 note. If access is denied, stop and ask the user (credentials/profile problem).

- [ ] **Step 3a: Add `parse_time` to** `src/fundr/sources/hl_archive.py` (shared by P1 and P10)

```python
def parse_time(df: pl.DataFrame) -> pl.DataFrame:
    """asset_ctxs `time` column → Datetime(ms), naive UTC. Extend here if P1 finds another format."""
    dtype = df.schema["time"]
    if dtype == pl.String:
        return df.with_columns(pl.col("time").str.to_datetime(time_unit="ms"))
    if dtype.is_integer():
        return df.with_columns(pl.from_epoch("time", time_unit="ms"))
    raise ValueError(f"unexpected time dtype {dtype}; inspect and extend parse_time")
```

- [ ] **Step 3: Write** `probes/p01_hl_asset_ctxs.py`

```python
"""P1: what the Hyperliquid asset_ctxs archive really holds, and how often it changes within an hour."""
from datetime import UTC, datetime, timedelta

import polars as pl

from fundr import store
from fundr.analysis import gap_scan, hourly_profile
from fundr.sources.hl_archive import ARCHIVE_BUCKET, HLArchive, parse_time, read_csv_lz4

CANDIDATE_FIELDS = ["funding", "premium", "open_interest", "mark_px", "oracle_px", "mid_px",
                    "impact_bid_px", "impact_ask_px", "day_ntl_vlm"]

arc = HLArchive()
sample = [s["coin"] for s in store.load_json("p00", "sample.json")]
keys = sorted(k["key"] for k in arc.list_keys(ARCHIVE_BUCKET, "asset_ctxs/"))
store.save_json("p01", "listing.json", keys)
dates = {"old": keys[0], "mid": keys[len(keys) // 2], "recent": keys[-1]}
store.save_json("p01", "dates.json", dates)
latest_day = datetime.strptime(keys[-1].split("/")[-1][:8], "%Y%m%d").replace(tzinfo=UTC)
print(f"{len(keys)} files, first {keys[0]}, last {keys[-1]}, upload lag {(datetime.now(UTC) - latest_day).days} days")



for label, key in dates.items():
    raw = read_csv_lz4(arc.download(ARCHIVE_BUCKET, key))
    day = key.split("/")[-1][:8]
    print(f"\n=== {label} {key}: {raw.height} rows, {raw['coin'].n_unique()} coins")
    print(raw.schema)
    print(raw.head(5))
    df = parse_time(raw).filter(pl.col("coin").is_in(sample))
    missing = sorted(set(sample) - set(df["coin"].unique()))
    print(f"sample coins missing on this date: {missing}")
    fields = [f for f in CANDIDATE_FIELDS if f in df.columns]
    prof = hourly_profile(df, "time", "coin", fields)
    df.write_parquet(store.probe_dir("p01") / f"asset_ctxs_{day}.parquet")
    prof.write_parquet(store.probe_dir("p01") / f"profile_{day}.parquet")
    interval = df.sort(["coin", "time"]).group_by("coin").agg(pl.col("time").diff().median().alias("median_interval"))
    print(interval)
    print("rows per hour (min/median/max):", prof["n_rows"].min(), prof["n_rows"].median(), prof["n_rows"].max())
    for f in fields:
        share = (prof[f"{f}_n_distinct"] > 1).mean()
        print(f"share of coin-hours where {f} changes within the hour: {share:.3f}")
    print("gaps > 5 min:", gap_scan(df, "time", "coin", timedelta(minutes=5)).height)
    print("null counts:", df.null_count())
```

- [ ] **Step 4: Run it**

Run: `uv run python probes/p01_hl_asset_ctxs.py`
Expected: three date blocks with schema, row frequency, change shares, gaps; spend stays under the cap (`uv run python -c "from fundr.sources.hl_archive import HLArchive; print(HLArchive().spent_usd())"`). If `parse_time` raises, inspect the raw schema, extend `parse_time` in `hl_archive.py` for the real format, rerun, and record the format.

- [ ] **Step 5: Sample existence check (spec P0 step 5).** If any sample coin is missing on any sampled date, rerun `uv run python probes/p00_sample.py --exclude <coin> ...`, rerun this probe, and record the swap in the P0 and P1 notes. The P9 live run keeps its original coins; note that P9 is about funding semantics, so a swapped coin's P9 data stays valid.

- [ ] **Step 6: Cut a real fixture** (≤ 200 rows, 2 coins, from the recent file)

```bash
uv run python - <<'EOF'
import io, json, lz4.frame, polars as pl
from fundr.sources.hl_archive import read_csv_lz4
from fundr import store
key = store.load_json("p01", "dates.json")["recent"]
raw = read_csv_lz4(store.data_root() / "s3" / "hyperliquid-archive" / key)
coins = raw["coin"].unique().sort()[:2].to_list()
buf = io.BytesIO(); raw.filter(pl.col("coin").is_in(coins)).head(200).write_csv(buf)
open("tests/fixtures/hl_asset_ctxs_sample.csv.lz4", "wb").write(lz4.frame.compress(buf.getvalue()))
EOF
```

- [ ] **Step 7: Write** `tests/test_fixture_asset_ctxs.py` — pins the real archive format so parser changes are caught

```python
from pathlib import Path

from fundr.sources.hl_archive import parse_time, read_csv_lz4

FIXTURE = Path(__file__).parent / "fixtures" / "hl_asset_ctxs_sample.csv.lz4"


def test_real_asset_ctxs_fixture_parses():
    df = read_csv_lz4(FIXTURE)
    assert {"time", "coin", "funding"} <= set(df.columns)
    assert df.height > 0


def test_real_asset_ctxs_time_parses():
    df = parse_time(read_csv_lz4(FIXTURE))
    assert str(df.schema["time"]).startswith("Datetime")
```

Run: `uv run pytest tests/test_fixture_asset_ctxs.py -v`
Expected: 2 passed. If the real columns differ from `time/coin/funding`, change the assertion to the real names and record them in the P1 note.

- [ ] **Step 8: Write** `docs/phase1/evidence/P1-hl-asset-ctxs.md`. Findings must answer every spec P1 question: exact row frequency; schema and dtypes; timestamp meaning; whether `funding` changes within the hour (share printed above); running vs settled (settle this in Task 10); whether `premium` is intra-hour; OI and impact bid/ask frequency; gaps; stability across the 3 dates and coins; archive coverage start; upload lag. Include total AWS spend so far.

- [ ] **Step 9: Commit**

```bash
git add src/fundr/sources/hl_archive.py probes/p01_hl_asset_ctxs.py tests/fixtures/hl_asset_ctxs_sample.csv.lz4 tests/test_fixture_asset_ctxs.py docs/phase1/evidence/P1-hl-asset-ctxs.md
git commit -m "feat: P1 asset_ctxs probe, fixture and evidence"
```

---

### Task 10: P4 — Hyperliquid API cross-check

**Files:**
- Create: `probes/p04_hl_api_crosscheck.py`
- Create (after run): `docs/phase1/evidence/P4-hl-api-crosscheck.md`

**Interfaces:**
- Consumes: P1 parquet files, `HLInfo`, `funding_history_frame`, `attach_settled`, `match_stats`, `reported_tolerance`.
- Produces: `data/phase1/p04/settled_<YYYYMMDD>.parquet` (HL settled funding per sample coin for each P1 date), `data/phase1/p04/predicted_fundings.json`, `data/phase1/p04/candles_*.json`. Task 17 reads the settled files.

- [ ] **Step 1: Write** `probes/p04_hl_api_crosscheck.py`

```python
"""P4: is archived `funding` the settled rate, a running estimate, or something else?
Also: predictedFundings shape, candle lookback limit, funding units and precision."""
import time
from datetime import datetime, timedelta

import polars as pl

from fundr import store
from fundr.analysis import attach_settled, match_stats, reported_tolerance
from fundr.sources.hl_api import HLInfo, funding_history_frame

hl = HLInfo()
sample = [s["coin"] for s in store.load_json("p00", "sample.json")]
dates = store.load_json("p01", "dates.json")

for label, key in dates.items():
    day = key.split("/")[-1][:8]
    start = datetime.strptime(day, "%Y%m%d")
    start_ms = int((start - datetime(1970, 1, 1)).total_seconds() * 1000)
    end_ms = start_ms + int(timedelta(hours=26).total_seconds() * 1000)
    rows = [r for c in sample for r in hl.funding_history(c, start_ms, end_ms)]
    settled = funding_history_frame(rows)
    settled.write_parquet(store.probe_dir("p04") / f"settled_{day}.parquet")
    tol = reported_tolerance(settled["funding_rate_str"].to_list())
    prof = pl.read_parquet(store.probe_dir("p01") / f"profile_{day}.parquet")
    s = settled.select("coin", "settle_time", pl.col("funding_rate").alias("settled"))
    print(f"\n=== {label} {day}: {settled.height} settled rows, tolerance {tol:g}")
    # Alignment A: last archived value in hour H vs the settlement that closes H (H+1h).
    a = attach_settled(prof, s, "coin").drop_nulls("settled")
    print("last-in-hour vs closing settlement:", match_stats(a["funding_last"], a["settled"], tol))
    # Alignment B: archived value in hour H vs the settlement at the start of H (archive lags settled).
    b = prof.join(s.rename({"settle_time": "hour"}), on=["coin", "hour"], how="inner")
    print("last-in-hour vs opening settlement:", match_stats(b["funding_last"], b["settled"], tol))
    print("settled premium sample:", settled.select("coin", "time", "premium").head(3))

store.save_json("p04", "predicted_fundings.json", hl.predicted_fundings())

now = int(time.time() * 1000)
day_ms = 86_400_000
recent = hl.candle_snapshot("BTC", "1m", now - 5 * day_ms, now)
old = hl.candle_snapshot("BTC", "1m", now - 30 * day_ms, now - 29 * day_ms)
store.save_json("p04", "candles_recent_1m.json", recent)
store.save_json("p04", "candles_old_1m.json", old)
print(f"\ncandles 1m last 5 days: {len(recent)} (5000 cap => ~3.5 days); 30 days ago window: {len(old)}")
```

- [ ] **Step 2: Run it**

Run: `uv run python probes/p04_hl_api_crosscheck.py`
Expected: match rates for both alignments on each date; a candle count near 5000 for the recent request and 0 (or much fewer than 1440) for the old window if the limit is real.

- [ ] **Step 3: Units, sign and interval.** From the HL funding docs (https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding) and the data: confirm that `fundingRate` is a per-hour fraction (baseline `0.0000125` = 0.01% per 8h ÷ 8), paid every hour, and that positive means longs pay. Record the reported precision (tolerance printed above).

- [ ] **Step 4: Write** `docs/phase1/evidence/P4-hl-api-crosscheck.md`. Findings: which alignment matches (tells whether archived `funding` is running, closing-settled or lagged-settled); `predictedFundings` shape and what it offers (current HL predicted rate + other venues, next funding time, interval); candle limit evidence; units, sign, interval, precision.

- [ ] **Step 5: Target C checkpoint (HL).** If archived `funding` changes within the hour (P1) **and** the last in-hour value matches the closing settlement on ≥99% of coin-hours (Alignment A), HL is an Outcome A candidate; P9 (Task 12) confirms the live value behaves the same way. Write this interim reading in the note.

- [ ] **Step 6: Commit**

```bash
git add probes/p04_hl_api_crosscheck.py docs/phase1/evidence/P4-hl-api-crosscheck.md
git commit -m "feat: P4 HL API cross-check and evidence"
```

---

### Task 11: P5 — Lighter `/fundings` history

**Files:**
- Create: `probes/p05_lighter_fundings.py`
- Create (after run): `docs/phase1/evidence/P5-lighter-fundings.md`

**Interfaces:**
- Consumes: `LighterAPI`, `fundings_frame`, `gap_scan`, `reported_tolerance`, `store`.
- Produces: `data/phase1/p05/fundings_<market_id>.parquet` (full 1h history per sample market; columns from `fundings_frame`). Tasks 12 and 17 read these.

- [ ] **Step 1: Write** `probes/p05_lighter_fundings.py`

```python
"""P5: Lighter settled funding history — coverage, resolutions, paging, units, gaps."""
import time
from datetime import timedelta

import httpx
import polars as pl

from fundr import store
from fundr.analysis import gap_scan, reported_tolerance
from fundr.sources.lighter_api import LighterAPI, fundings_frame

api = LighterAPI()
sample = store.load_json("p00", "sample.json")
created = {b["market_id"]: int(b["created_at"]) // 1000 for b in api.order_books()}
now = int(time.time())

for s in sample:
    m = s["lighter_market_id"]
    rows = api.fundings_all(m, "1h", created[m], now)
    df = fundings_frame(rows, m)
    df.write_parquet(store.probe_dir("p05") / f"fundings_{m}.parquet")
    expected = (now - created[m]) // 3600
    gaps = gap_scan(df.with_columns(pl.lit(s["coin"]).alias("coin")), "settle_time", "coin", timedelta(hours=1))
    print(f"{s['coin']} (market {m}): {df.height} rows vs ~{expected} hours since listing; "
          f"first {df['settle_time'].min()}; gaps>1h {gaps.height}; "
          f"directions {df['direction'].value_counts().to_dicts()}; "
          f"tolerance {reported_tolerance(df['rate_str'].to_list()):g}")
    print(df.head(3))


def attempt(label, **kw):
    try:
        rows = api.fundings(**kw)
        ts = [r["timestamp"] for r in rows]
        print(f"{label}: {len(rows)} rows, first {min(ts) if ts else None}, last {max(ts) if ts else None}")
        return rows
    except (RuntimeError, httpx.HTTPStatusError) as e:
        print(f"{label}: error {e}")


day = 86400
attempt("1d, last 30 days", market_id=1, resolution="1d", start_s=now - 30 * day, end_s=now, count_back=30)
attempt("count_back=5 over 10 days (earliest or latest 5?)", market_id=1, resolution="1h",
        start_s=now - 10 * day, end_s=now, count_back=5)
attempt("count_back=1000 over 40 days (cap?)", market_id=1, resolution="1h",
        start_s=now - 40 * day, end_s=now, count_back=1000)
for res in ("1m", "5m", "15m"):
    attempt(f"resolution {res} (any sub-hour state?)", market_id=1, resolution=res,
            start_s=now - 3600 * 3, end_s=now, count_back=100)
print("All calls above were made without any auth header.")
```

- [ ] **Step 2: Run it**

Run: `uv run python probes/p05_lighter_fundings.py`
Expected: per-market history with start date, gap count, precision; answers to the 1d / count_back / cap / sub-hour questions.

- [ ] **Step 3: Write** `docs/phase1/evidence/P5-lighter-fundings.md`. Findings: coverage start per market; row count vs hours since listing; gaps; `1h`/`1d` behaviour; which rows `count_back` returns; cap behaviour; no auth needed (or not); timestamp convention (seconds, on the hour; which hour it closes is settled in Task 12); meaning of `value` if determinable; units (compare a baseline hour: `0.0012` vs `base_interest_rate 0.0100` ÷ 8 = `0.00125` → percent per hour, truncated to 4 dp?); whether any sub-hour/intermediate state is preserved (spec formula item 5). Sign convention: leave "pending Task 12" and fill it in there.

- [ ] **Step 4: Stop check.** If the shortest sample-market history is under ~6 months, or the rank-11 market has no usable history, stop and tell the user: this limits the Target B study window.

- [ ] **Step 5: Commit**

```bash
git add probes/p05_lighter_fundings.py docs/phase1/evidence/P5-lighter-fundings.md
git commit -m "feat: P5 Lighter funding history probe and evidence"
```

---

### Task 12: P9 — analyse the live watch

Run only after `probes/p09_live.py` has finished (≥ 4h, ≥ 3 settlements).

**Files:**
- Create: `probes/p09_analyze.py`
- Create (after run): `docs/phase1/evidence/P9-live-probe.md`
- Modify: `docs/phase1/evidence/P5-lighter-fundings.md` (sign convention)

**Interfaces:**
- Consumes: `data/phase1/p09/live.jsonl`, `HLInfo`, `funding_history_frame`, `LighterAPI`, `fundings_frame`, `hourly_profile`, `attach_settled`, `match_stats`, `reported_tolerance`, `epoch_ms`.
- Produces: `data/phase1/p09/hl_profile.parquet`, `data/phase1/p09/lighter_profile.parquet`.

- [ ] **Step 1: Write** `probes/p09_analyze.py`

```python
"""P9 analysis: does each venue's current funding move within the hour, and does its
last in-hour value equal the rate that then settles?"""
import json

import polars as pl

from fundr import store
from fundr.analysis import attach_settled, epoch_ms, hourly_profile, match_stats, reported_tolerance
from fundr.sources.hl_api import HLInfo, funding_history_frame
from fundr.sources.lighter_api import LighterAPI, fundings_frame

lines = [json.loads(x) for x in (store.probe_dir("p09") / "live.jsonl").read_text().splitlines()]
by_source: dict[str, list] = {}
for rec in lines:
    by_source.setdefault(rec["source"], []).append(rec)
print({k: len(v) for k, v in by_source.items()})
t0, t1 = min(r["t_ms"] for r in lines), max(r["t_ms"] for r in lines)

# --- Hyperliquid ---
hl = pl.DataFrame(by_source["metaAndAssetCtxs"]).with_columns(epoch_ms("t_ms").alias("time"))
hl_prof = hourly_profile(hl, "time", "coin", ["funding", "premium", "open_interest"])
hl_prof.write_parquet(store.probe_dir("p09") / "hl_profile.parquet")
api = HLInfo()
settled = funding_history_frame([r for c in hl["coin"].unique() for r in api.funding_history(c, t0 - 3_600_000, t1 + 7_200_000)])
tol = reported_tolerance(settled["funding_rate_str"].to_list())
s = settled.select("coin", "settle_time", pl.col("funding_rate").alias("settled"))
full = hl_prof.filter(pl.col("n_rows") >= 50)  # only hours fully observed
print("\nHL share of hours where funding moves within the hour:", (full["funding_n_distinct"] > 1).mean())
a = attach_settled(full, s, "coin").drop_nulls("settled")
print("HL last-in-hour vs closing settlement:", match_stats(a["funding_last"], a["settled"], tol))
b = full.join(s.rename({"settle_time": "hour"}), on=["coin", "hour"], how="inner")
print("HL last-in-hour vs opening settlement:", match_stats(b["funding_last"], b["settled"], tol))
pred = [(r["coin"], v[1]["fundingRate"]) for r in by_source["predictedFundings"] for v in r["venues"] if v[0] == "HlPerp"]
print("predictedFundings HlPerp sample:", pred[:5])

# --- Lighter ---
li = pl.DataFrame(by_source["ws_market_stats"]).with_columns(
    epoch_ms("t_ms").alias("time"),
    *[pl.col(c).cast(pl.Float64) for c in ("current_funding_rate", "funding_rate", "premium")],
)
li_prof = hourly_profile(li, "time", "market_id", ["current_funding_rate", "funding_rate", "premium", "funding_timestamp"])
li_prof.write_parquet(store.probe_dir("p09") / "lighter_profile.parquet")
print("\nLighter max distinct current_funding_rate values inside one minute:",
      li.select(pl.col("cfr_values").list.len().max()).item())
lapi = LighterAPI()
lset = pl.concat([
    fundings_frame(lapi.fundings(m, "1h", t0 // 1000 - 3600, t1 // 1000 + 7200, 20), m)
    for m in li["market_id"].unique()
])
ltol = reported_tolerance(lset["rate_str"].to_list())
lfull = li_prof.filter(pl.col("n_rows") >= 50)
print("Lighter share of hours where current_funding_rate moves:", (lfull["current_funding_rate_n_distinct"] > 1).mean())
for col in ("signed_rate", "rate"):
    ls = lset.select("market_id", "settle_time", pl.col(col).alias("settled"))
    a = attach_settled(lfull, ls, "market_id").drop_nulls("settled")
    print(f"Lighter current_funding_rate last-in-hour vs closing settlement ({col}):",
          match_stats(a["current_funding_rate_last"], a["settled"], ltol))
    b = lfull.join(ls.rename({"settle_time": "hour"}), on=["market_id", "hour"], how="inner")
    print(f"Lighter funding_rate field vs opening settlement ({col}):",
          match_stats(b["funding_rate_last"], b["settled"], ltol))
neg = li.filter(pl.col("current_funding_rate") < 0)
print("minutes with negative current_funding_rate:", neg.height, "(sign check: compare with /fundings direction)")
print(lset.select("market_id", "settle_time", "rate", "direction").tail(10))

fr = [row for r in by_source["funding-rates"] for row in r["rows"]]
print("\n/funding-rates exchanges seen:", sorted({row["exchange"] for row in fr}))
print("recorder fields available live:",
      {src: sorted(by_source[src][0].keys()) for src in ("metaAndAssetCtxs", "ws_market_stats", "orderBookDetails")})
print("poll errors:", len(by_source.get("poll_error", [])), "ws reconnects:", len(by_source.get("ws_reconnect", [])))
```

- [ ] **Step 2: Run it**

Run: `uv run python probes/p09_analyze.py`
Expected: for each venue, the share of hours where current funding moves, and match rates for both alignments. For Lighter, the `signed_rate` vs `rate` comparisons show which sign rule is right (if every sampled hour was positive, say the sign is still unconfirmed and look for a negative-funding market in the live Lighter WS for one settlement before closing this task).

- [ ] **Step 3: Write** `docs/phase1/evidence/P9-live-probe.md`. Findings per venue: does current funding move within the hour and how often; does its last in-hour value equal the closing settlement (→ it is a running estimate of the next settlement) or the opening one (→ it is the last settled rate); what `funding_rate`, `funding_timestamp`, `predictedFundings`, `/funding-rates` each mean (NautilusTrader: `current_funding_rate` = upcoming estimate, `funding_rate` = completed); trade-flow check result (Task 8 Step 5); the list of live fields a Phase 2 recorder can capture (funding, premium, OI, mark/index/oracle, volume, trades) and which are missing; poll errors/reconnects.

- [ ] **Step 4: Update P5 note** with the verified Lighter sign convention and units, and HL vs Lighter normalisation: both to per-hour fraction, positive = longs pay. If the two venues cannot be put on the same basis, stop and tell the user (changes Target B).

- [ ] **Step 5: Commit**

```bash
git add probes/p09_analyze.py docs/phase1/evidence/P9-live-probe.md docs/phase1/evidence/P5-lighter-fundings.md
git commit -m "feat: P9 live-watch analysis and evidence"
```

---

### Task 13: P6 — Lighter market-state endpoints

**Files:**
- Create: `probes/p06_lighter_market_state.py`
- Create (after run): `docs/phase1/evidence/P6-lighter-market-state.md`

**Interfaces:**
- Consumes: `lighter_api.BASE_URL`, `store`.
- Produces: `data/phase1/p06/<slug>.json` per endpoint call (status + body).

- [ ] **Step 1: List every public market-data endpoint** in the official SDK

Run: `curl -sL https://raw.githubusercontent.com/elliottech/lighter-python/main/README.md | grep -E "api/v1" `
Expected: an endpoint table. Any public GET endpoint about markets, prices, OI, volume, funding or trades that is not already in the `CHECKS` list below gets added to it (same tuple shape) before running.

- [ ] **Step 2: Write** `probes/p06_lighter_market_state.py`

```python
"""P6: which Lighter market-state series are historical, and which are live snapshots only."""
import re
import time

import httpx

from fundr import store
from fundr.sources.lighter_api import BASE_URL

now, day = int(time.time()), 86400
sample = store.load_json("p00", "sample.json")
mid = next(s for s in sample if s["role"] == "mid")["lighter_market_id"]

CHECKS = [
    ("candles 1h one year ago", "/api/v1/candles",
     dict(market_id=1, resolution="1h", start_timestamp=now - 365 * day, end_timestamp=now - 364 * day, count_back=24)),
    ("candles 1m 180 days ago", "/api/v1/candles",
     dict(market_id=1, resolution="1m", start_timestamp=now - 180 * day, end_timestamp=now - 180 * day + 3600, count_back=60)),
    ("candles 1h mid-cap 90 days ago", "/api/v1/candles",
     dict(market_id=mid, resolution="1h", start_timestamp=now - 90 * day, end_timestamp=now - 89 * day, count_back=24)),
    ("orderBookDetails", "/api/v1/orderBookDetails", dict(market_id=mid)),
    ("exchangeStats", "/api/v1/exchangeStats", {}),
    ("funding-rates", "/api/v1/funding-rates", {}),
    ("funding-rates with a past timestamp", "/api/v1/funding-rates", dict(timestamp=now - day)),
    ("recentTrades", "/api/v1/recentTrades", dict(market_id=mid, limit=10)),
    ("trades", "/api/v1/trades", dict(market_id=mid, sort_by="timestamp", limit=10)),
    ("orderBooks (listing metadata)", "/api/v1/orderBooks", {}),
]

client = httpx.Client(base_url=BASE_URL, timeout=30)
for label, path, params in CHECKS:
    r = client.get(path, params=params)
    try:
        body = r.json()
    except ValueError:
        body = r.text[:500]
    slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    store.save_json("p06", f"{slug}.json", {"path": path, "params": params, "status": r.status_code, "body": body})
    print(f"\n{label}: HTTP {r.status_code}")
    print(str(body)[:400])
```

- [ ] **Step 3: Run it**

Run: `uv run python probes/p06_lighter_market_state.py`
Expected: one block per endpoint. Old candle windows returning rows = historical price/volume exists.

- [ ] **Step 4: Write** `docs/phase1/evidence/P6-lighter-market-state.md`. A table: endpoint → historical or live-only → fields → resolution → earliest data seen. Must answer: is there any native historical OI series; historical price and volume (candles) and how far back; premium history (none expected natively); `/funding-rates` is live-only (show the past-timestamp call returned the same current rows, or an error); listing/delisting metadata (`orderBooks` `status`, `created_at`; is a delisting time exposed?); the "representative Lighter market-state response" required by the deliverable (link the saved `orderBookDetails` file).

- [ ] **Step 5: Commit**

```bash
git add probes/p06_lighter_market_state.py docs/phase1/evidence/P6-lighter-market-state.md
git commit -m "feat: P6 Lighter market-state probe and evidence"
```

---

### Task 14: P8 — 0xArchive coverage (free tier)

**Prerequisite:** the user has created a free 0xArchive account and exported `OXARCHIVE_API_KEY`. If not set, ask: "0xArchive check needs a free API key (no card). Please sign up at https://0xarchive.io/dashboard, create a key, and run `! export OXARCHIVE_API_KEY=...`." Wait.

**Files:**
- Create: `probes/p08_oxarchive.py`
- Create (after run): `docs/phase1/evidence/P8-oxarchive.md`

**Interfaces:**
- Consumes: `OXArchive`, `data/phase1/p09/live.jsonl`, `store`, `hourly_profile`, `epoch_ms`.
- Produces: `data/phase1/p08/*.json` (symbols, instruments, coverage per venue/symbol, sample pages), `data/phase1/p08/calls.json` (every call with credit headers), `data/phase1/p08/lighter_funding_raw.parquet`, `data/phase1/p08/hl_funding_raw.parquet`. Task 17 may read the Lighter raw file as rebuild input.

- [ ] **Step 1: Write** `probes/p08_oxarchive.py`

```python
"""P8: what 0xArchive holds for our sample markets, at what cadence, and at what credit cost.
Free tier: data calls must stay inside the last 30 days; coverage routes give full-history dates."""
import json

import polars as pl

from fundr import store
from fundr.analysis import hourly_profile
from fundr.sources.oxarchive import OXArchive

ox = OXArchive()
sample = store.load_json("p00", "sample.json")
live = [json.loads(x) for x in (store.probe_dir("p09") / "live.jsonl").read_text().splitlines()]
t0, t1 = min(r["t_ms"] for r in live), max(r["t_ms"] for r in live)


def save(name, obj):
    store.save_json("p08", name, obj)


save("symbols.json", ox.get("/v1/symbols"))
save("lighter_instruments.json", ox.get("/v1/lighter/instruments"))

for s in sample:
    for venue in ("lighter", "hyperliquid"):
        try:
            cov = ox.get(f"/v1/data-quality/coverage/{venue}/{s['coin']}")
        except Exception as e:  # record route/plan refusals as findings, keep going
            cov = {"error": repr(e)}
        save(f"coverage_{venue}_{s['coin']}.json", cov)
        for dtype, info in (cov.get("data_types") or {}).items():
            print(f"{venue} {s['coin']} {dtype}: {info.get('earliest')} → {info.get('latest')}, "
                  f"cadence {info.get('cadence')}, completeness {info.get('completeness')}, gaps {len(info.get('gaps') or [])}")


def funding_raw(venue: str, coin: str) -> pl.DataFrame:
    rows = ox.get_all(f"/v1/{venue}/funding/{coin}", max_pages=30, start=t0, end=t1, limit=1000)
    df = pl.DataFrame(rows).with_columns(
        pl.col("timestamp").str.to_datetime(time_unit="ms").dt.replace_time_zone(None).alias("time"),  # naive UTC
        pl.col("funding_rate").cast(pl.Float64),
        pl.col("premium").cast(pl.Float64),
        pl.lit(coin).alias("coin"),
    )
    return df


# Raw funding over the P9 window: does it change within the hour, and does it equal what P9 saw live?
probe_coins = [sample[0]["coin"], "BTC"]
for venue in ("lighter", "hyperliquid"):
    df = pl.concat([funding_raw(venue, c) for c in probe_coins])
    df.write_parquet(store.probe_dir("p08") / f"{'hl' if venue == 'hyperliquid' else venue}_funding_raw.parquet")
    prof = hourly_profile(df, "time", "coin", ["funding_rate", "premium"])
    print(f"\n{venue} raw funding rows {df.height}; median spacing {df.sort('time')['time'].diff().median()}")
    print(f"{venue} share of hours where funding_rate moves:", (prof["funding_rate_n_distinct"] > 1).mean())

save("lighter_oi_page.json", ox.get(f"/v1/lighter/openinterest/{sample[0]['coin']}", start=t1 - 86_400_000, end=t1, limit=100))
save("hl_oi_page.json", ox.get(f"/v1/hyperliquid/openinterest/{sample[0]['coin']}", start=t1 - 86_400_000, end=t1, limit=100))
save("lighter_trades_page.json", ox.get(f"/v1/lighter/trades/{sample[0]['coin']}", start=t1 - 3_600_000, end=t1, limit=100))
save("hl_trades_page.json", ox.get(f"/v1/hyperliquid/trades/{sample[0]['coin']}", start=t1 - 3_600_000, end=t1, limit=100))
save("lighter_l3_current.json", ox.get(f"/v1/lighter/l3orderbook/{sample[0]['coin']}"))

save("calls.json", ox.calls)
print(f"\n{len(ox.calls)} calls; credit headers on last call: {ox.calls[-1]['headers']}")
```

- [ ] **Step 2: Run it**

Run: `uv run python probes/p08_oxarchive.py`
Expected: coverage lines per venue/coin/data type; raw funding spacing and intra-hour movement for both venues; saved pages; credit headers. If any call returns HTTP 402/403 (plan restriction), record it as a finding. If credits remaining drop below 10,000, stop and ask the user.

- [ ] **Step 3: Compare 0xArchive raw funding with the P9 live log** (Outcome A test for the 0xArchive source)

```bash
uv run python - <<'EOF'
import json, polars as pl
from fundr import store
from fundr.analysis import epoch_ms
ox = pl.read_parquet(store.probe_dir("p08") / "lighter_funding_raw.parquet").sort("time")
live = [json.loads(x) for x in (store.probe_dir("p09") / "live.jsonl").read_text().splitlines()]
sample = {s["lighter_market_id"]: s["coin"] for s in store.load_json("p00", "sample.json")}
lv = (pl.DataFrame([r for r in live if r["source"] == "ws_market_stats"])
      .with_columns(epoch_ms("t_ms").alias("time"), pl.col("market_id").replace_strict(sample).alias("coin"),
                    pl.col("current_funding_rate").cast(pl.Float64))
      .select("time", "coin", "current_funding_rate").sort("time"))
j = lv.join_asof(ox.select("time", "coin", "funding_rate"), on="time", by="coin", strategy="backward")
j = j.drop_nulls("funding_rate")
print("rows compared:", j.height)
print("ratio funding_rate / current_funding_rate (units check):", (j["funding_rate"] / j["current_funding_rate"]).median())
print(j.head(10))
EOF
```

Expected: a stable ratio (e.g. `0.01` if 0xArchive stores fractions and Lighter WS shows percent). Record whether 0xArchive raw funding tracks the live current funding minute by minute.

- [ ] **Step 4: Replay check.** Read https://docs.0xarchive.io/websocket/replay.md. Using the documented WebSocket URL and `op: "replay"` command, request a 10-minute replay of the Lighter `funding` channel for one sample market inside the last 30 days (one short Python snippet with `websockets`, saved to `data/phase1/p08/replay.json`). Record: accepted or rejected on free tier, message shape, cadence.

- [ ] **Step 5: Cost estimate.** From `calls.json`, work out credits per call per route (difference in the remaining-credits header between calls, or the documented cost if no header). Estimate credits for research scale: 50 markets × 365 days of Lighter raw funding + OI + trades. Convert to a plan: Build ($49/mo, 80M credits), Pro ($199/mo, 400M), and note Build+ is needed for any history older than 30 days.

- [ ] **Step 6: Write** `docs/phase1/evidence/P8-oxarchive.md`. Per sample market and venue: funding/OI/trades/L3 earliest date, cadence, completeness, gaps; whether raw funding is intra-hour and matches live current funding (Outcome A candidate for Lighter?); units of `funding_rate`; replay result; HL fallback coverage; credits used; $/month estimate; free-tier 30-day limit.

- [ ] **Step 7: Commit**

```bash
git add probes/p08_oxarchive.py docs/phase1/evidence/P8-oxarchive.md
git commit -m "feat: P8 0xArchive coverage probe and evidence"
```

---

### Task 15: P7 — confirm both funding formulas

No formula enters `src/` until this task's note says "confirmed".

**Files:**
- Create: `probes/p07_hl_formula_check.py`, `probes/p07_lighter_formula_check.py`
- Create: `tests/fixtures/hl_formula_cases.json`, `tests/fixtures/lighter_formula_cases.json`
- Create (after run): `docs/phase1/evidence/P7-funding-formulas.md`

**Interfaces:**
- Consumes: P4 `settled_*.parquet`; P9 `live.jsonl`; P5 `fundings_*.parquet`; P8 `lighter_funding_raw.parquet`.
- Produces: fixture files, each a JSON list of `{"premium": float, "settled": float, "settled_str": str}`, 20 rows, at least 15 of them off-baseline (settled ≠ baseline). Task 16 tests read these.

- [ ] **Step 1: Read the sources and write down each formula exactly as stated**
  - HL: https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding — expected: 8h rate `F = P + clamp(I − P, −0.0005, 0.0005)` with `I = 0.0001` (0.01% per 8h), `P` = average of premium index samples (every 5 s) over the hour, paid hourly at `F / 8`, capped at 4% per hour.
  - Lighter: https://docs.lighter.xyz (funding page), `lighter-python` source/docs, https://nautilustrader.io/docs/nightly/integrations/lighter/, and the live parameters from `orderBookDetails` (`funding_premium_multiplier`, `funding_clamp_small`, `funding_clamp_big`, `base_interest_rate`). Note where the multiplier and each clamp sit, sample count per hour, and units (percent vs fraction).

- [ ] **Step 2: Write** `probes/p07_hl_formula_check.py` — tests the documented HL formula against HL's own settled `premium` → `fundingRate` pairs

```python
"""P7 (HL): does the documented formula turn fundingHistory's hourly premium into its fundingRate?"""
import json
from pathlib import Path

import polars as pl

from fundr import store
from fundr.analysis import match_stats, reported_tolerance

INTEREST_8H, CLAMP, CAP_HOURLY, BASELINE = 0.0001, 0.0005, 0.04, 0.0000125


def candidate(p: pl.Expr) -> pl.Expr:
    f8 = p + (INTEREST_8H - p).clip(-CLAMP, CLAMP)
    return (f8 / 8).clip(-CAP_HOURLY, CAP_HOURLY)


df = pl.concat([pl.read_parquet(p) for p in sorted(store.probe_dir("p04").glob("settled_*.parquet"))])
df = df.with_columns(candidate(pl.col("premium")).alias("rebuilt"))
tol = reported_tolerance(df["funding_rate_str"].to_list())
off = df.filter(pl.col("funding_rate") != BASELINE)
print("all hours:", match_stats(df["rebuilt"], df["funding_rate"], tol))
print("off-baseline hours:", match_stats(off["rebuilt"], off["funding_rate"], tol))
print(off.filter((pl.col("rebuilt") - pl.col("funding_rate")).abs() > tol).head(10))

cases = pl.concat([off.head(15), df.filter(pl.col("funding_rate") == BASELINE).head(5)])
Path("tests/fixtures/hl_formula_cases.json").write_text(json.dumps([
    {"premium": r["premium"], "settled": r["funding_rate"], "settled_str": r["funding_rate_str"]}
    for r in cases.to_dicts()
], indent=1))
```

Run: `uv run python probes/p07_hl_formula_check.py`
Expected: ≥99% match on off-baseline hours → HL formula confirmed (given the hourly average premium). If it fails, inspect the mismatches, fix `candidate` to match the docs more closely, rerun, and record what changed. If P4 had fewer than 15 off-baseline hours, widen the P4 date range for one coin and rerun P4 first.

- [ ] **Step 3: Write** `probes/p07_lighter_formula_check.py` — tests candidate Lighter formulas against settled funding, using minute premium from 0xArchive raw rows (P8) averaged per hour

```python
"""P7 (Lighter): which formula placement reproduces settled funding from the hourly average premium?
Candidates come from Step 1; keep only those the sources actually support."""
import json
from pathlib import Path

import polars as pl

from fundr import store
from fundr.analysis import attach_settled, match_stats, reported_tolerance

details = store.load_json("p06", "orderbookdetails.json")["body"]["order_book_details"][0]
MULT = float(details["funding_premium_multiplier"])
SMALL = float(details["funding_clamp_small"])
BIG = float(details["funding_clamp_big"])
INTEREST = float(details["base_interest_rate"])
PREMIUM_SCALE = 1.0  # set from P8 Step 3 so premium is in the same units as the settled rate (percent)

CANDIDATES = {
    # rate_pct = clamp((P + clamp(I - P, ±small)) / 8, ±big)
    "no_multiplier": lambda p: ((p + (INTEREST - p).clip(-SMALL, SMALL)) / 8).clip(-BIG, BIG),
    # same with premium divided by the multiplier before the formula
    "premium_div_mult": lambda p: ((p / MULT + (INTEREST - p / MULT).clip(-SMALL, SMALL)) / 8).clip(-BIG, BIG),
    # same with premium multiplied by the multiplier before the formula
    "premium_x_mult": lambda p: ((p * MULT + (INTEREST - p * MULT).clip(-SMALL, SMALL)) / 8).clip(-BIG, BIG),
}

sample = {s["coin"]: s["lighter_market_id"] for s in store.load_json("p00", "sample.json")}
raw = (pl.read_parquet(store.probe_dir("p08") / "lighter_funding_raw.parquet")
       .with_columns((pl.col("premium") * PREMIUM_SCALE).alias("premium"),
                     pl.col("coin").replace_strict(sample).alias("market_id")))
prof = raw.group_by("market_id", pl.col("time").dt.truncate("1h").alias("hour")).agg(
    pl.col("premium").mean().alias("p_avg"), pl.len().alias("n_rows")).sort(["market_id", "hour"])
settled = pl.concat([pl.read_parquet(store.probe_dir("p05") / f"fundings_{m}.parquet") for m in raw["market_id"].unique()])
j = attach_settled(prof, settled.select("market_id", "settle_time", "signed_rate", "rate_str"), "market_id").drop_nulls("signed_rate")
tol = reported_tolerance(j["rate_str"].to_list())
baseline = round(INTEREST / 8, 4)
for name, fn in CANDIDATES.items():
    rebuilt = j.select(fn(pl.col("p_avg")).alias("r"))["r"]
    off = j["signed_rate"] != baseline
    print(name, "all:", match_stats(rebuilt, j["signed_rate"], tol),
          "off-baseline:", match_stats(rebuilt.filter(off), j["signed_rate"].filter(off), tol))

best = "no_multiplier"  # replace with the best-matching candidate from the printout above
j = j.with_columns(CANDIDATES[best](pl.col("p_avg")).alias("rebuilt"))
cases = pl.concat([j.filter(pl.col("signed_rate") != baseline).head(15), j.filter(pl.col("signed_rate") == baseline).head(5)])
Path("tests/fixtures/lighter_formula_cases.json").write_text(json.dumps([
    {"premium": r["p_avg"], "settled": r["signed_rate"], "settled_str": r["rate_str"]} for r in cases.to_dicts()
], indent=1))
```

Run: `uv run python probes/p07_lighter_formula_check.py` (after setting `PREMIUM_SCALE` from P8 Step 3 and, once the printout is in, `best`).
Expected: one candidate clearly best. Rounding to 4 dp may be needed (`rate` is reported to 4 dp) — if the best candidate misses only by rounding, round `rebuilt` to 4 dp and record that the venue truncates/rounds.

- [ ] **Step 4: Cross-check with P9.** In the P9 live data, does Lighter's `current_funding_rate` equal the candidate formula applied to the running average of `premium` so far in the hour? Record yes/no — this tells whether the displayed value is a running estimate built the same way.

- [ ] **Step 5: Write** `docs/phase1/evidence/P7-funding-formulas.md` with, per venue: formula as documented; formula as confirmed (or "not confirmed"); parameter meanings and values; premium sampling rate; units; what is public before settlement; match rates. Mark each venue "confirmed" or "not confirmed".

- [ ] **Step 6: Commit**

```bash
git add probes/p07_hl_formula_check.py probes/p07_lighter_formula_check.py tests/fixtures/hl_formula_cases.json tests/fixtures/lighter_formula_cases.json docs/phase1/evidence/P7-funding-formulas.md
git commit -m "feat: P7 funding formula checks and evidence"
```

---

### Task 16: Funding formula code (only for venues P7 marked "confirmed")

**Files:**
- Create: `src/fundr/funding/hl_formula.py`, `src/fundr/funding/lighter_formula.py`
- Test: `tests/test_formulas.py`

**Interfaces:**
- Consumes: `tests/fixtures/hl_formula_cases.json`, `tests/fixtures/lighter_formula_cases.json` (Task 15).
- Produces:
  - `hl_formula.hourly_rate(p: pl.Expr) -> pl.Expr` (per-hour fraction), constants `INTEREST_8H, CLAMP_8H, CAP_HOURLY, BASELINE_HOURLY`.
  - `lighter_formula.hourly_rate(p: pl.Expr, interest: float, clamp_small: float, clamp_big: float, multiplier: float) -> pl.Expr` (percent per hour, same units as `/fundings` `rate`).
- If P7 marked a venue "not confirmed", skip that venue's file and test, and Task 17 marks that venue "not attemptable".

- [ ] **Step 1: Write the failing tests** `tests/test_formulas.py`

```python
import json
from pathlib import Path

import polars as pl
import pytest

from fundr.analysis import match_stats, reported_tolerance
from fundr.funding import hl_formula, lighter_formula

FIX = Path(__file__).parent / "fixtures"


def _cases(name):
    return pl.DataFrame(json.loads((FIX / name).read_text()))


def test_hl_baseline_and_clamp():
    out = pl.DataFrame({"p": [0.0, 0.001]}).select(hl_formula.hourly_rate(pl.col("p")))["p"]
    assert out[0] == pytest.approx(0.0000125)
    assert out[1] == pytest.approx(0.0000625)  # 0.001 + clamp(0.0001 - 0.001 → -0.0005) = 0.0005; /8


def test_hl_matches_real_settled_hours():
    df = _cases("hl_formula_cases.json")
    rebuilt = df.select(hl_formula.hourly_rate(pl.col("premium")))["premium"]
    stats = match_stats(rebuilt, df["settled"], reported_tolerance(df["settled_str"].to_list()))
    assert stats["n_match"] == stats["n"]


LIGHTER_PARAMS = dict(interest=0.0100, clamp_small=0.0500, clamp_big=4.0, multiplier=100.0)


def test_lighter_baseline():
    out = pl.DataFrame({"p": [0.0]}).select(lighter_formula.hourly_rate(pl.col("p"), **LIGHTER_PARAMS))["p"]
    assert out[0] == pytest.approx(0.00125, abs=1e-4)  # 0.01% / 8, reported to 4 dp


def test_lighter_matches_real_settled_hours():
    df = _cases("lighter_formula_cases.json")
    rebuilt = df.select(lighter_formula.hourly_rate(pl.col("premium"), **LIGHTER_PARAMS))["premium"]
    stats = match_stats(rebuilt, df["settled"], reported_tolerance(df["settled_str"].to_list()))
    assert stats["n_match"] == stats["n"]
```

`LIGHTER_PARAMS` must equal the values recorded in the P7 note (from `orderBookDetails`); update them if they differ.

- [ ] **Step 2: Run, expect failure**

Run: `uv run pytest tests/test_formulas.py -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Implement** `src/fundr/funding/hl_formula.py`

```python
"""Hyperliquid hourly funding from the hourly average premium. Confirmed in P7
(docs/phase1/evidence/P7-funding-formulas.md)."""
import polars as pl

INTEREST_8H = 0.0001
CLAMP_8H = 0.0005
CAP_HOURLY = 0.04
BASELINE_HOURLY = INTEREST_8H / 8


def hourly_rate(p: pl.Expr) -> pl.Expr:
    f8 = p + (INTEREST_8H - p).clip(-CLAMP_8H, CLAMP_8H)
    return (f8 / 8).clip(-CAP_HOURLY, CAP_HOURLY)
```

- [ ] **Step 4: Implement** `src/fundr/funding/lighter_formula.py` using the candidate P7 confirmed. The code below is the `no_multiplier` candidate; if P7 confirmed `premium_div_mult` or `premium_x_mult`, replace `p` on the first line of the body with `p / multiplier` or `p * multiplier` respectively, and add 4-dp rounding if P7 found the venue rounds.

```python
"""Lighter hourly funding (percent per hour) from the hourly average premium. Confirmed in P7
(docs/phase1/evidence/P7-funding-formulas.md); parameters come from orderBookDetails."""
import polars as pl


def hourly_rate(p: pl.Expr, interest: float, clamp_small: float, clamp_big: float, multiplier: float) -> pl.Expr:
    prem = p  # P7 placement of `multiplier` goes here
    return ((prem + (interest - prem).clip(-clamp_small, clamp_small)) / 8).clip(-clamp_big, clamp_big)
```

- [ ] **Step 5: Run, expect pass**

Run: `uv run pytest tests/test_formulas.py -v`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add src/fundr/funding tests/test_formulas.py
git commit -m "feat: confirmed funding formulas for HL and Lighter"
```

---

### Task 17: P10 — rebuild test

**Files:**
- Create: `probes/p10_rebuild.py`
- Create (after run): `docs/phase1/evidence/P10-rebuild.md`

**Interfaces:**
- Consumes: `HLArchive`, `read_csv_lz4`, `parse_time` (Task 9), `HLInfo`, `funding_history_frame`, `OXArchive`, P5 `fundings_*.parquet`, `hl_formula`, `lighter_formula`, `rebuild_verdict`, `reported_tolerance`, `attach_settled`.
- Produces: `data/phase1/p10/verdict_hl.json`, `data/phase1/p10/verdict_lighter.json` — `{verdict, attemptable, input_cadence, all, off_baseline, tol, windows}`.

Run per venue only where P1/P6/P8 show premium inputs exist. Mid-cap sample markets only (5).

- [ ] **Step 1: Ask the user before HL downloads.** "The rebuild test needs 6 more Hyperliquid archive days (two 4-day windows). Expected cost: a few cents; cap still $0.80 total. OK?" Wait for yes.

- [ ] **Step 2: Estimate Lighter credits.** From P8's cost per raw-funding page: pages ≈ 5 markets × 2 windows × 4 days × (rows per day ÷ 1000). If that exceeds half the remaining free credits, stop and ask the user.

- [ ] **Step 3: Write** `probes/p10_rebuild.py`

```python
"""P10: can each venue's hourly funding be rebuilt from archived premium inputs? Spec pass rule."""
import argparse
from datetime import datetime, timedelta

import polars as pl

from fundr import store
from fundr.analysis import attach_settled, rebuild_verdict, reported_tolerance
from fundr.funding import hl_formula, lighter_formula
from fundr.sources.hl_api import HLInfo, funding_history_frame
from fundr.sources.hl_archive import ARCHIVE_BUCKET, HLArchive, parse_time, read_csv_lz4
from fundr.sources.oxarchive import OXArchive

HL_SAMPLE_SECONDS = 5  # HL averages premium samples taken every 5 s (P7)
LIGHTER_SAMPLE_SECONDS = 60  # set from the P7 note
PREMIUM_SCALE = 1.0  # set from P8 Step 3: 0xArchive premium → Lighter percent units

ap = argparse.ArgumentParser()
ap.add_argument("--venue", choices=["hl", "lighter"], required=True)
args = ap.parse_args()

sample = [s for s in store.load_json("p00", "sample.json") if s["role"] == "mid"]
day_ms = 86_400_000


def to_ms(d: datetime) -> int:
    return int((d - datetime(1970, 1, 1)).total_seconds() * 1000)


def hourly_avg(df: pl.DataFrame, key: str) -> pl.DataFrame:
    return df.group_by(key, pl.col("time").dt.truncate("1h").alias("hour")).agg(
        pl.col("premium").mean().alias("p_avg"), pl.len().alias("n_rows")).sort([key, "hour"])


if args.venue == "hl":
    arc, api = HLArchive(), HLInfo()
    listing = set(store.load_json("p01", "listing.json"))
    dates = store.load_json("p01", "dates.json")
    coins = [s["coin"] for s in sample]
    frames, settled, windows = [], [], []
    for label in ("old", "recent"):
        start = datetime.strptime(dates[label].split("/")[-1][:8], "%Y%m%d")
        if label == "recent":
            start -= timedelta(days=3)  # keep the window inside the archive
        days = [start + timedelta(days=i) for i in range(4)]
        windows.append([d.date().isoformat() for d in days])
        for d in days:
            key = f"asset_ctxs/{d:%Y%m%d}.csv.lz4"
            if key not in listing:
                print(f"missing archive day {key}; recorded as a gap")
                continue
            frames.append(parse_time(read_csv_lz4(arc.download(ARCHIVE_BUCKET, key))).filter(pl.col("coin").is_in(coins)))
        rows = [r for c in coins for r in api.funding_history(c, to_ms(days[0]), to_ms(days[-1]) + day_ms + 3_600_000)]
        settled.append(funding_history_frame(rows))
    inputs = pl.concat(frames)
    st = pl.concat(settled)
    tol = reported_tolerance(st["funding_rate_str"].to_list())
    s = st.select("coin", "settle_time", pl.col("funding_rate").alias("settled"))
    key, rebuilt_expr, sample_s = "coin", hl_formula.hourly_rate(pl.col("p_avg")), HL_SAMPLE_SECONDS
    baseline_expr = (pl.col("settled") == hl_formula.BASELINE_HOURLY) | (pl.col("settled").abs() == hl_formula.CAP_HOURLY)
else:
    ox = OXArchive()
    details = store.load_json("p06", "orderbookdetails.json")["body"]["order_book_details"][0]
    params = dict(interest=float(details["base_interest_rate"]), clamp_small=float(details["funding_clamp_small"]),
                  clamp_big=float(details["funding_clamp_big"]), multiplier=float(details["funding_premium_multiplier"]))
    now = to_ms(datetime.utcnow())
    win = [(now - 29 * day_ms, now - 25 * day_ms), (now - 6 * day_ms, now - 2 * day_ms)]  # free tier: last 30 days
    windows = [[datetime.utcfromtimestamp(a / 1000).isoformat(), datetime.utcfromtimestamp(b / 1000).isoformat()] for a, b in win]
    frames = []
    for s_ in sample:
        for a, b in win:
            rows = ox.get_all(f"/v1/lighter/funding/{s_['coin']}", max_pages=200, start=a, end=b, limit=1000)
            frames.append(pl.DataFrame(rows).select(
                pl.col("timestamp").str.to_datetime(time_unit="ms").dt.replace_time_zone(None).alias("time"),
                (pl.col("premium").cast(pl.Float64) * PREMIUM_SCALE).alias("premium"),
                pl.lit(s_["lighter_market_id"]).alias("market_id")))
    store.save_json("p10", "oxarchive_calls.json", ox.calls)
    inputs = pl.concat(frames)
    st = pl.concat([pl.read_parquet(store.probe_dir("p05") / f"fundings_{s_['lighter_market_id']}.parquet") for s_ in sample])
    tol = reported_tolerance(st["rate_str"].to_list())
    s = st.select("market_id", "settle_time", pl.col("signed_rate").alias("settled"))
    key, rebuilt_expr, sample_s = "market_id", lighter_formula.hourly_rate(pl.col("p_avg"), **params), LIGHTER_SAMPLE_SECONDS
    baseline = round(params["interest"] / 8, 4)
    baseline_expr = (pl.col("settled") == baseline) | (pl.col("settled").abs() == params["clamp_big"])

cadence = inputs.sort([key, "time"]).group_by(key).agg(pl.col("time").diff().median().alias("c"))["c"].max()
attemptable = cadence.total_seconds() <= sample_s
j = attach_settled(hourly_avg(inputs, key).filter(pl.col("n_rows") >= 0.9 * 3600 / max(cadence.total_seconds(), 1)),
                   s, key).drop_nulls("settled").with_columns(rebuilt_expr.alias("rebuilt"), baseline_expr.alias("baseline"))
v = rebuild_verdict(j["rebuilt"], j["settled"], j["baseline"], tol)
out = {**v, "verdict": v["verdict"] if attemptable else "not_attemptable", "raw_verdict": v["verdict"],
       "attemptable": attemptable, "input_cadence": str(cadence), "windows": windows}
store.save_json("p10", f"verdict_{args.venue}.json", out)
print(out)
```

- [ ] **Step 4: Run it per venue**

Run: `uv run python probes/p10_rebuild.py --venue hl` then `uv run python probes/p10_rebuild.py --venue lighter`
Expected: a verdict JSON per venue. `not_attemptable` means the archived inputs are sampled more coarsely than the formula uses; `raw_verdict` still shows how close a coarse rebuild gets. If `raw_verdict` is `pass` while `attemptable` is false, flag it to the user — that would be a practical (not exact) rebuild, and the spec calls it not validated.

- [ ] **Step 5: Write** `docs/phase1/evidence/P10-rebuild.md`: inputs used, cadence, windows (note the Lighter 30-day free-tier deviation from the spec), tolerance, match rates (all / off-baseline), mean error, verdict per venue, largest mismatches and likely cause.

- [ ] **Step 6: Commit**

```bash
git add probes/p10_rebuild.py docs/phase1/evidence/P10-rebuild.md
git commit -m "feat: P10 rebuild test and evidence"
```

---

### Task 18: P2 — Hyperliquid L2 archive

**Files:**
- Create: `probes/p02_hl_l2.py`
- Create (after run): `docs/phase1/evidence/P2-hl-l2.md`

**Interfaces:**
- Consumes: `HLArchive`, `ARCHIVE_BUCKET`, `decode_lz4`, `store`.
- Produces: `data/phase1/p02/summary.json`.

- [ ] **Step 1: Write** `probes/p02_hl_l2.py`

```python
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
```

- [ ] **Step 2: Run it**

Run: `uv run python probes/p02_hl_l2.py`
Expected: date range of the L2 archive; per coin/hour: present or not, size, record count, spacing, depth; one record excerpt. If record structure differs from `raw.data.{time,levels}`, adjust the two extraction lines to the real keys and rerun.

- [ ] **Step 3: Write** `docs/phase1/evidence/P2-hl-l2.md`: timestamp resolution; snapshot vs delta (full `levels` each record = snapshots); depth; coin coverage; file sizes and extrapolated cost for all sample coins × 1 year (bytes × $0.09/GB); gaps (missing hours/coins). Conclusion: usable later as an optional feature set, or not.

- [ ] **Step 4: Commit**

```bash
git add probes/p02_hl_l2.py docs/phase1/evidence/P2-hl-l2.md
git commit -m "feat: P2 HL L2 archive probe and evidence"
```

---

### Task 19: P3 — Hyperliquid node fills and trades

**Files:**
- Create: `probes/p03_hl_node_fills.py`
- Create (after run): `docs/phase1/evidence/P3-hl-node-fills.md`

**Interfaces:**
- Consumes: `HLArchive`, `NODE_BUCKET`, `decode_lz4`, `store`.
- Produces: `data/phase1/p03/summary.json`.

- [ ] **Step 1: Write** `probes/p03_hl_node_fills.py`

```python
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
```

- [ ] **Step 2: Run it**

Run: `uv run python probes/p03_hl_node_fills.py`
Expected: per dataset: present or not, first/last folder (date range), file sizes, one record. If a file is lz4 in a different container (decode fails), record that and the file extension; do not add new decoders.

- [ ] **Step 3: Write** `docs/phase1/evidence/P3-hl-node-fills.md`: per dataset — date range, file granularity (per hour/block), sizes and extrapolated cost for 1 year, fields (is there a side/aggressor field → signed flow?), coin identification, gaps. Conclusion: usable historical signed-flow source for HL, and from when.

- [ ] **Step 4: Commit**

```bash
git add probes/p03_hl_node_fills.py docs/phase1/evidence/P3-hl-node-fills.md
git commit -m "feat: P3 HL node fills probe and evidence"
```

---

### Task 20: Data audit report, Target C decision, decision map

**Files:**
- Create: `docs/phase1/data_audit.md`, `docs/phase1/decision_map.md`

**Interfaces:**
- Consumes: every evidence note P0–P10.

- [ ] **Step 1: Write** `docs/phase1/data_audit.md` with exactly these sections, every claim linking to an evidence note:

```markdown
# Phase 1 Data Audit

Run window: <first probe date> – <last probe date> (UTC). Sample: see [P0](evidence/P0-sample.md).

## 1. Venue/source matrix

| Venue | Dataset | Endpoint/path | Coverage start | Native resolution | Key fields | Timestamp convention | Missingness | Access/cost | Status | Evidence |
|---|---|---|---|---|---|---|---|---|---|---|
| HL | asset_ctxs | s3://hyperliquid-archive/asset_ctxs/[date].csv.lz4 | | | | | | requester-pays | | [P1](evidence/P1-hl-asset-ctxs.md) |
| HL | L2 book | s3://hyperliquid-archive/market_data/[date]/[hour]/l2Book/[coin].lz4 | | | | | | requester-pays | | [P2](evidence/P2-hl-l2.md) |
| HL | node_fills_by_block / node_fills / node_trades | s3://hl-mainnet-node-data/... | | | | | | requester-pays | | [P3](evidence/P3-hl-node-fills.md) |
| HL | fundingHistory | info API | | 1h | | | | free | | [P4](evidence/P4-hl-api-crosscheck.md) |
| HL | predictedFundings | info API | live only | | | | | free | | [P4](evidence/P4-hl-api-crosscheck.md) |
| HL | candleSnapshot | info API | last 5,000 candles | | | | | free | | [P4](evidence/P4-hl-api-crosscheck.md) |
| HL | metaAndAssetCtxs | info API | live only | | | | | free | | [P9](evidence/P9-live-probe.md) |
| Lighter | /api/v1/fundings | REST | | 1h, 1d | | | | free, no auth | | [P5](evidence/P5-lighter-fundings.md) |
| Lighter | /api/v1/funding-rates | REST | live only | | | | | free | | [P6](evidence/P6-lighter-market-state.md) |
| Lighter | candles, orderBookDetails, orderBooks, trades | REST | | | | | | free | | [P6](evidence/P6-lighter-market-state.md) |
| Lighter | market_stats websocket | WS | live only | | | | | free | | [P9](evidence/P9-live-probe.md) |
| 0xArchive | Lighter funding / OI / trades / L3 | REST, replay | | | | | | free tier 30 days; paid for history | | [P8](evidence/P8-oxarchive.md) |
| 0xArchive | HL funding / OI / trades | REST | | | | | | as above | | [P8](evidence/P8-oxarchive.md) |

Status is one of: verified / unverified / unavailable. Add a row for any other source a probe found.

## 2. Funding semantics

Per venue: settled vs current/running value; settlement timestamp (which hour a settled row closes); units; sign convention; interval; reported precision; formula (confirmed or not, [P7](evidence/P7-funding-formulas.md)); raw inputs; whether intra-hour state is observable live and historically. Then: how to put both venues on one basis (per-hour fraction, positive = longs pay).

## 3. Sample-file evidence

- HL asset_ctxs file: [P1](evidence/P1-hl-asset-ctxs.md)
- HL L2 file: [P2](evidence/P2-hl-l2.md)
- Lighter historical funding response: [P5](evidence/P5-lighter-fundings.md)
- Lighter market-state response: [P6](evidence/P6-lighter-market-state.md)
- 0xArchive coverage for the sample markets: [P8](evidence/P8-oxarchive.md)

## 4. Final source hierarchy

| Feature | Primary source | Why | Fallback-only | Must be recorded going forward? |
|---|---|---|---|---|
| Funding (settled) | | | | |
| Current/running funding | | | | |
| Premium | | | | |
| Open interest | | | | |
| Perp price | | | | |
| Spot/index price | | | | |
| Volume | | | | |
| Signed trade flow | | | | |
| Market metadata / listings | | | | |
| Settlement timestamps | | | | |

## 5. Target C decision

| Venue | Outcome | Rule applied | Evidence |
|---|---|---|---|
| Hyperliquid | Historical (A) / Reconstructed (C) / Prospective only (B) | | |
| Lighter | | | |

Spread version historical over: <period where both qualify, or "none">.
```

Fill every cell. Apply the spec's Target C rule in order: A if an archived source holds intra-hour current funding that moves within the hour like P9's live value **and** its last in-hour value equals the closing settlement on ≥99% of market-hours; else C if P10 `verdict == "pass"`; else B.

- [ ] **Step 2: Write** `docs/phase1/decision_map.md` — one table, every row from the spec filled with the actual finding and its consequence:

```markdown
# Phase 1 → Phases 2–11 Decision Map

| Phase 1 finding | Actual result | Affects | Consequence |
|---|---|---|---|
| Target C outcome per venue | | Phase 2 recorder scope; Phase 4; Phase 11 | |
| Lighter funding history start | | Target B study window | |
| Funding units, sign, interval per venue | | Phase 4 normalisation of F_t | |
| Settlement timing and alignment across venues | | Pairing of S_t (Phase 4) | |
| Intra-hour OI/price availability per venue | | Phase 7 features: historical vs prospective | |
| Volume source per venue | | Phases 2, 3, 7 | |
| Premium availability per venue | | Premium feature; Target C inputs | |
| Settlement schedule known historically | | Time since/until settlement (Phases 2, 7) | |
| Signed trade flow source | | Trade imbalance; 0xArchive paid tier? | |
| Historical OI per venue | | Point-in-time universe (Phase 3); survivorship flag | |
| Listing/delisting metadata | | Point-in-time universe (Phase 3) | |
| Spot/index price source | | Basis feature | |
| HL archive upload lag | | Recent data from recorder vs archive | |
| Live endpoints for each recorder field | | Phase 2 recorder field list | |
| 0xArchive $/month at research scale | | Paid tier in Phase 2? | |
| L2 archive usability | | Optional future feature set | |
```

- [ ] **Step 3: Done check** — confirm each spec "Done when" item and tick it at the bottom of `data_audit.md`:
  - every matrix row has a status and an evidence link;
  - all five sample-file evidence items exist;
  - funding semantics written for both venues, incl. cross-venue normalisation;
  - source hierarchy complete (a source per feature, fallbacks, prospective-only list);
  - Target C decided per venue;
  - decision map filled;
  - total AWS spend and 0xArchive credits used recorded.

- [ ] **Step 4: Run the full test suite**

Run: `uv run pytest -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add docs/phase1/data_audit.md docs/phase1/decision_map.md
git commit -m "docs: Phase 1 data audit, Target C decision and decision map"
```

- [ ] **Step 6: Report to the user** in ≤ 15 plain lines: the Target C outcome per venue in one sentence each, how far back usable history goes, what must be recorded going forward, total spend, and the one decision needed next (e.g. "build the always-on recorder now?" with a recommendation).
