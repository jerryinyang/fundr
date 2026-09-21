# Phase 2a Recorder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An always-on service that records funding-related market state from Hyperliquid and Lighter, continuously and durably, so Target C (realized-minus-published funding) becomes studiable from the day it starts.

**Architecture:** One Python asyncio service with three independent tasks — Hyperliquid REST market state, Lighter `market_stats` websocket, and a universe/parameters sweep — each owning its own timer, connection and output file so no call can block another. All I/O is async and watchdog-bounded. Records are the venues' raw payloads inside a provenance envelope, written to hourly gzip parts and uploaded to S3.

**Tech Stack:** Python 3.13, uv, httpx (async), websockets, boto3, pytest. No polars in the recorder import path.

**Spec:** `docs/superpowers/specs/2026-09-20-phase2-recorder-design.md` (predecessor context: `docs/phase1/handoff.md`). Executors read both.

## Global Constraints

- Python 3.13, managed with `uv`. Run everything as `uv run ...`.
- The recorder imports **no polars** — keep `fundr.sources` (which imports polars) out of `fundr.recorder`'s import path. Analysis and validation scripts may use polars.
- **All network I/O is async** (`httpx.AsyncClient`, `websockets`). A synchronous client anywhere in a feed task is a defect: awaiting one blocks the event loop and every other task, which is Phase 1's exact failure.
- **Every network call runs under `asyncio.wait_for`** with the configured watchdog timeout. A client-level timeout is not sufficient: a `timeout=30` client failed to bound an 84-minute read in Phase 1.
- Carve-out: `src/fundr/recorder/upload.py` is intentionally synchronous. The "all I/O async" constraint exists so a slow call cannot starve a feed task sharing the recorder daemon's event loop; the uploader runs as its own systemd `oneshot` unit on a timer, sharing no event loop with the daemon, so a slow or hung upload blocks only itself.
- **No feed's recording may depend on another feed's success.** Each task owns its timer, connection and writer.
- Records are raw as received. No normalisation, signing, unit conversion or derived funding in the recorder.
- Every record carries: `t_ms` (wall clock), `mono_ns` (monotonic), `feed`, `venue`, `seq` (monotonic per feed, including gap records), `run_id` (uuid4 minted once per process), `rec_ver`, `git_sha`. `seq` restarts at 1 on every process start, so gap detection across a restart is only possible by grouping on `run_id` first.
- Output filenames carry the instance id (`FUNDR_INSTANCE_ID`, default the hostname), so two instances writing into the same bucket can never collide on one S3 key.
- Recorder data root is `$FUNDR_RECORDER_DATA` (default `/var/lib/fundr`); never the Phase 1 `data/phase1` tree.
- Never commit recorded data, `.env`, or anything under `auth/`.
- Commit messages end with a blank line then `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- Health thresholds (spec, verbatim): `ok` = every active market ≥ 95% of expected snapshots in the last hour, no open gap older than 5 minutes, both feeds reconnected < 5 times in the hour. `degraded` = any market between 50% and 95%, or an open gap under 30 minutes. `broken` = any feed below 50%, an open gap over 30 minutes, or no write in 5 minutes.
- **"In the last hour" is a trailing 60-minute window, not the calendar hour**, and expectations inside it are prorated by how much of the window has actually elapsed for that market. A calendar-hour reading would score every feed `broken` for the first ~30 minutes of every hour (60 expected vs 1 received) and the phase's "seven consecutive days at `ok`" criterion could never pass.
- Coverage counts **venue** events, never our own timer: a market whose subscription dies silently must lose coverage, and a market the universe feed lists but we never hear from must appear with zero received. Expectations are therefore set from the universe feed's active market lists, not from the records we happen to write.
- Cadences (spec): HL poll 60 s; Lighter heartbeat 60 s; Lighter forced boundary snapshots at `HH:59:30` and `HH:59:52` (`boundary_offsets_s=(30, 8)`); universe 5 min; watchdog 45 s; upload 10 min; local retention 7 days. The offsets were moved out from `(15, 3)`: 3 s leaves no margin for clock skew, and a fast clock would file the hour's single most important record into the next hour's partition.

## Live payload shapes (verified 2026-09-20)

- `POST https://api.hyperliquid.xyz/info {"type":"metaAndAssetCtxs"}` → `[meta, ctxs]`; `meta.universe[i]` = `{name, szDecimals, maxLeverage, marginTableId}` plus `isDelisted: true` on delisted entries only (234 entries, 56 delisted); `ctxs[i]` = `{funding, openInterest, prevDayPx, dayNtlVlm, premium, oraclePx, markPx, midPx, impactPxs, dayBaseVlm}`.
- `{"type":"predictedFundings"}` → `[[coin, [[venue, {fundingRate, nextFundingTime, fundingIntervalHours}], ...]], ...]`.
- `{"type":"meta"}` → `{universe: [...]}` (same universe shape).
- `GET https://mainnet.zklighter.elliot.ai/api/v1/orderBooks` → `{code, order_books:[{symbol, market_id, status, created_at, ...}]}`.
- `GET /api/v1/orderBookDetails` → `{code, order_book_details:[{symbol, market_id, status, funding_premium_multiplier, funding_clamp_small, funding_clamp_big, base_interest_rate, open_interest, index_price, mark_price, ...}]}` (235 markets).
- `wss://mainnet.zklighter.elliot.ai/stream`, subscribe `{"type":"subscribe","channel":"market_stats/<id>"}` → messages `{channel, market_stats:{market_id, symbol, current_funding_rate, funding_rate, funding_timestamp, premium, mark_price, index_price, mid_price, best_bid_price, best_ask_price, open_interest, daily_*}, timestamp, type}`.

## File map

| File | Responsibility |
|---|---|
| `src/fundr/recorder/config.py` | URLs, cadences, paths, `Config.from_env()`, `Clock` |
| `src/fundr/recorder/records.py` | envelope + gap record builders, per-feed sequence counter |
| `src/fundr/recorder/writer.py` | hourly rotating gzip parts, member close + fsync, closed-part listing |
| `src/fundr/recorder/health.py` | per-feed/per-market coverage over a trailing 60-minute window, status thresholds, `health.json` |
| `src/fundr/recorder/clients.py` | async HL info + Lighter REST clients |
| `src/fundr/recorder/feeds/hl_state.py` | HL poll loop: watchdog, gap detection, records |
| `src/fundr/recorder/feeds/lighter_state.py` | Lighter socket: change/heartbeat/boundary snapshots, stall detection, dynamic subscribe |
| `src/fundr/recorder/feeds/universe.py` | HL meta + Lighter orderBooks/orderBookDetails sweep; publishes market set |
| `src/fundr/recorder/supervisor.py` | starts/restarts tasks, owns health writing and shutdown |
| `src/fundr/recorder/upload.py` | S3 sync of closed parts, retention cleanup |
| `deploy/` | systemd units, `bootstrap.sh` |
| `scripts/validate_day1.py` | day-one correctness check, both venues |
| `tests/recorder/` | offline tests with fake feeds and fake clocks |

## Execution order

Tasks 1–3 (config/records/writer, health, async clients) are foundations. Tasks 4–6 are the feeds. Task 7 is the supervisor and the Phase-1-bug test. Task 8 is upload. Task 9 is deploy artefacts plus the one-hour local run. Task 10 provisions AWS and deploys. Task 11 is day-one validation.

---

### Task 1: Config, records, and the hourly writer

**Files:**
- Create: `src/fundr/recorder/__init__.py`, `config.py`, `records.py`, `writer.py`
- Create: `tests/recorder/__init__.py`, `tests/recorder/test_writer.py`, `tests/recorder/test_records.py`

**Interfaces:**
- Produces:
  - `config.HL_INFO_URL`, `config.LIGHTER_BASE_URL`, `config.LIGHTER_WS_URL`, `config.REC_VER = "2a.1"`, `config.INSTANCE_ID` (`FUNDR_INSTANCE_ID`, default the hostname)
  - `config.Config` (frozen dataclass): `root: Path`, `bucket: str | None`, `hl_poll_s=60`, `lighter_heartbeat_s=60`, `universe_s=300`, `boundary_offsets_s=(30, 8)`, `watchdog_s=45`, `upload_s=600`, `local_retention_days=7`; classmethod `from_env() -> Config`.
  - `config.Clock` (dataclass): `now_ms: Callable[[], int]`, `mono_ns: Callable[[], int]`, `sleep: Callable[[float], Awaitable[None]]`; default `Clock()` uses the real ones.
  - `records.RUN_ID` (uuid4, minted once per process), `records.Seq(feed: str)` with `.next() -> int`.
  - `records.envelope(feed, venue, seq, payload, *, t_ms, mono_ns, **extra) -> dict`
  - `records.gap(feed, venue, seq, *, t_ms, mono_ns, from_ms, to_ms, reason) -> dict`
  - `writer.HourlyWriter(root: Path, feed: str)` with `.write(rec: dict) -> None`, `.flush() -> list[Path]` (closes the current gzip member, fsyncs the file and its parent directory, returns paths closed since the last flush), `.close() -> list[Path]`, `.path_for(t_ms: int) -> Path`. Filenames include the instance id.

- [ ] **Step 1: Write the failing tests** `tests/recorder/test_records.py`

```python
from fundr.recorder import records


def test_envelope_carries_provenance_and_extras():
    rec = records.envelope("lighter_state", "lighter", 7, {"a": 1}, t_ms=1000, mono_ns=5,
                           n_msgs=47, trigger="boundary")
    assert rec["feed"] == "lighter_state" and rec["venue"] == "lighter"
    assert rec["seq"] == 7 and rec["t_ms"] == 1000 and rec["mono_ns"] == 5
    assert rec["payload"] == {"a": 1}
    assert rec["n_msgs"] == 47 and rec["trigger"] == "boundary"
    assert rec["rec_ver"] and "git_sha" in rec
    assert rec["run_id"] == records.RUN_ID


def test_gap_record_has_seq_and_reason():
    rec = records.gap("hl_state", "hl", 3, t_ms=2000, mono_ns=9, from_ms=1000, to_ms=2000,
                      reason="watchdog_timeout")
    assert rec["type"] == "gap" and rec["seq"] == 3
    assert rec["from_ms"] == 1000 and rec["to_ms"] == 2000
    assert rec["reason"] == "watchdog_timeout"
    assert rec["run_id"] == records.RUN_ID
    assert "payload" not in rec


def test_seq_is_monotonic_per_feed():
    s = records.Seq("hl_state")
    assert [s.next(), s.next(), s.next()] == [1, 2, 3]


def test_seq_is_continuous_across_gap_records():
    """A gap record consumes a sequence number like any other, so an analyst reading a run
    can tell 'a record is missing' from 'the recorder itself declared a hole here'."""
    s = records.Seq("lighter_state")
    out = [
        records.envelope("lighter_state", "lighter", s.next(), {"i": 0}, t_ms=1, mono_ns=1),
        records.gap("lighter_state", "lighter", s.next(), t_ms=2, mono_ns=2, from_ms=1,
                    to_ms=2, reason="ws_error:ConnectionClosed"),
        records.envelope("lighter_state", "lighter", s.next(), {"i": 1}, t_ms=3, mono_ns=3),
    ]
    assert [r["seq"] for r in out] == [1, 2, 3]
    assert {r["run_id"] for r in out} == {records.RUN_ID}
```

- [ ] **Step 2: Write the failing tests** `tests/recorder/test_writer.py`

```python
import gzip
import json

from fundr.recorder.writer import HourlyWriter

HOUR = 3_600_000


def _rec(t_ms, i):
    return {"t_ms": t_ms, "seq": i, "feed": "f", "payload": {"i": i}}


def test_writes_and_rotates_by_hour(tmp_path):
    w = HourlyWriter(tmp_path, "hl_state")
    w.write(_rec(HOUR * 10, 1))
    w.write(_rec(HOUR * 10 + 5, 2))
    w.write(_rec(HOUR * 11, 3))
    closed = w.close()
    paths = sorted(p for p in (tmp_path / "hl_state").rglob("*.jsonl.gz"))
    assert len(paths) == 2
    assert set(closed) == set(paths)
    first = [json.loads(x) for x in gzip.open(paths[0], "rt").read().splitlines()]
    assert [r["seq"] for r in first] == [1, 2]


def test_flush_closes_a_readable_member_and_writing_continues(tmp_path):
    w = HourlyWriter(tmp_path, "lighter_state")
    w.write(_rec(HOUR * 10, 1))
    flushed = w.flush()
    assert len(flushed) == 1
    # The part is complete and readable while the hour is still open.
    assert [json.loads(x)["seq"] for x in gzip.open(flushed[0], "rt").read().splitlines()] == [1]
    w.write(_rec(HOUR * 10 + 1, 2))
    w.close()
    rows = [json.loads(x) for x in gzip.open(flushed[0], "rt").read().splitlines()]
    assert [r["seq"] for r in rows] == [1, 2]


def test_path_layout_is_date_hour_partitioned_and_instance_scoped(tmp_path):
    from fundr.recorder.config import INSTANCE_ID
    w = HourlyWriter(tmp_path, "universe")
    p = w.path_for(1789812743255)
    assert "date=" in str(p) and "hour=" in str(p) and p.name.endswith(".jsonl.gz")
    # Without the instance id two recorders would overwrite each other's S3 key.
    assert INSTANCE_ID in p.name
```

- [ ] **Step 3: Run them, expect failure**

Run: `uv run pytest tests/recorder -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fundr.recorder'`.

- [ ] **Step 4: Implement** `src/fundr/recorder/config.py`

```python
"""Recorder configuration. No polars, no fundr.sources imports — this module and everything
under fundr.recorder must stay importable in a 1 GB instance without pulling analysis deps."""
import asyncio
import os
import socket
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

HL_INFO_URL = "https://api.hyperliquid.xyz/info"
LIGHTER_BASE_URL = "https://mainnet.zklighter.elliot.ai"
LIGHTER_WS_URL = "wss://mainnet.zklighter.elliot.ai/stream"
REC_VER = "2a.1"

# Part of every output filename: two recorders sharing a bucket must not collide on one key.
INSTANCE_ID = os.environ.get("FUNDR_INSTANCE_ID") or socket.gethostname().split(".")[0]


@dataclass(frozen=True)
class Config:
    root: Path
    bucket: str | None = None
    hl_poll_s: int = 60
    lighter_heartbeat_s: int = 60
    universe_s: int = 300
    # Seconds before the hour boundary. (30, 8) and not (15, 3): 3 s leaves no margin for
    # clock skew, and a fast clock would file the hour's critical record into the next hour.
    boundary_offsets_s: tuple[int, ...] = (30, 8)
    watchdog_s: int = 45
    upload_s: int = 600
    local_retention_days: int = 7

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            root=Path(os.environ.get("FUNDR_RECORDER_DATA", "/var/lib/fundr")),
            bucket=os.environ.get("FUNDR_BUCKET"),
        )


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class Clock:
    """Injectable time. Tests replace these; production uses the defaults.
    mono_ns is what distinguishes host suspension from an NTP step."""
    now_ms: Callable[[], int] = field(default=_now_ms)
    mono_ns: Callable[[], int] = field(default=time.monotonic_ns)
    sleep: Callable[[float], Awaitable[None]] = field(default=asyncio.sleep)
```

- [ ] **Step 5: Implement** `src/fundr/recorder/records.py`

```python
"""Record envelope. Every record — including gap records — carries provenance and a sequence
number, so a missing record is always detectable."""
import os
import uuid
from typing import Any

from fundr.recorder.config import REC_VER

GIT_SHA = os.environ.get("FUNDR_GIT_SHA", "unknown")

# seq restarts at 1 on every process start, so seq alone cannot detect a gap across a
# restart. run_id partitions the sequence: group on run_id, then look for holes in seq.
RUN_ID = str(uuid.uuid4())


class Seq:
    """Monotonic sequence per feed."""

    def __init__(self, feed: str):
        self.feed = feed
        self._n = 0

    def next(self) -> int:
        self._n += 1
        return self._n


def _base(feed: str, venue: str, seq: int, t_ms: int, mono_ns: int) -> dict:
    return {"t_ms": t_ms, "mono_ns": mono_ns, "feed": feed, "venue": venue, "seq": seq,
            "run_id": RUN_ID, "rec_ver": REC_VER, "git_sha": GIT_SHA}


def envelope(feed: str, venue: str, seq: int, payload: Any, *, t_ms: int, mono_ns: int,
             **extra) -> dict:
    return {**_base(feed, venue, seq, t_ms, mono_ns), **extra, "payload": payload}


def gap(feed: str, venue: str, seq: int, *, t_ms: int, mono_ns: int, from_ms: int, to_ms: int,
        reason: str) -> dict:
    return {**_base(feed, venue, seq, t_ms, mono_ns), "type": "gap",
            "from_ms": from_ms, "to_ms": to_ms, "reason": reason}
```

- [ ] **Step 6: Implement** `src/fundr/recorder/writer.py`

```python
"""Hourly gzip parts. A part is closed and fsynced on every flush, so whatever the uploader
sends to S3 is always a complete, readable file — gzip members concatenate, so writing
continues into the same path afterwards."""
import gzip
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from fundr.recorder.config import INSTANCE_ID

HOUR_MS = 3_600_000


class HourlyWriter:
    def __init__(self, root: Path, feed: str):
        self._root = Path(root)
        self._feed = feed
        self._hour: int | None = None
        self._fh = None
        self._path: Path | None = None
        self._closed: list[Path] = []

    def path_for(self, t_ms: int) -> Path:
        dt = datetime.fromtimestamp(t_ms / 1000, UTC)
        return (self._root / self._feed / f"date={dt:%Y-%m-%d}" / f"hour={dt:%H}"
                / f"{self._feed}-{INSTANCE_ID}-{dt:%Y%m%dT%H}.jsonl.gz")

    def write(self, rec: dict) -> None:
        hour = rec["t_ms"] // HOUR_MS
        if self._fh is not None and hour != self._hour:
            self._close_member()
        if self._fh is None:
            self._hour = hour
            self._path = self.path_for(rec["t_ms"])
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = gzip.open(self._path, "at")
        self._fh.write(json.dumps(rec, separators=(",", ":")) + "\n")

    def _close_member(self) -> None:
        """Order matters: close() is what writes the gzip trailer, so fsyncing before it
        durably stores a truncated member. Close first, then fsync the file, then fsync the
        parent directory — without the second fsync the *name* can still be lost on a crash
        even though the bytes survived."""
        if self._fh is None:
            return
        self._fh.close()
        self._fh = None
        path, self._path = self._path, None
        if path is None:
            return
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        self._closed.append(path)

    def flush(self) -> list[Path]:
        """Close the current member so the file on disk is complete; return paths closed
        since the previous flush. Writing afterwards appends a new gzip member."""
        self._close_member()
        closed, self._closed = self._closed, []
        return closed

    def close(self) -> list[Path]:
        return self.flush()
```

- [ ] **Step 7: Run tests, expect pass**

Run: `uv run pytest tests/recorder -v`
Expected: 7 passed (4 in `test_records.py`, 3 in `test_writer.py`).

- [ ] **Step 8: Commit**

```bash
git add src/fundr/recorder tests/recorder
git commit -m "feat: recorder config, record envelope and hourly writer"
```

---

### Task 2: Health counters and status thresholds

**Files:**
- Create: `src/fundr/recorder/health.py`
- Test: `tests/recorder/test_health.py`

**The window is trailing, and expectations are prorated.** The spec says "in the last hour".
Read literally as the *calendar* hour, a feed that expects 60 snapshots per hour scores 1/60 =
1.7% one minute past `HH:00` and stays `broken` until roughly `HH:30` — every hour, forever, so
the phase's "seven consecutive days at `status: ok`" criterion could never pass. This task
implements the spec's literal wording instead: **a trailing 60-minute window**, with each
market's expectation scaled by how much of that window has elapsed since the market was first
seen. `expect(feed, market, n)` keeps meaning "expected per *full* hour"; the judgement is made
over the trailing window.

**One time source, never rolling backwards.** Three feeds share one `Health`. The earlier design
rolled its counters on an hour change, and rolled on `t_ms` in `record()` but on
`clock.now_ms()` in `expect()` — so a record arriving just after a boundary with a `t_ms` just
before it cleared the window twice and wiped live counters. There is no roll here: a trailing
window needs none, and every entry point advances one clamped, monotonically non-decreasing
clock reading.

**Interfaces:**
- Consumes: nothing from earlier tasks (pure bookkeeping).
- Produces: `health.Health(clock: Clock)` with:
  - `.expect(feed: str, market: str, n: int) -> None` — expected events per **full hour** for that market
  - `.expect_markets(feed: str, markets: list[str], n: int) -> None` — set the feed's whole active market set at once, dropping markets no longer listed. This is how the universe feed publishes expectations; a market that is listed but never heard from must still have an entry, at zero.
  - `.record(feed: str, market: str, t_ms: int) -> None` — one **venue** event
  - `.reconnect(feed: str) -> None`
  - `.gap_open(feed: str, from_ms: int) -> None`, `.gap_close(feed: str, to_ms: int) -> None`
  - `.status(now_ms: int) -> str` — `"ok" | "degraded" | "broken"`
  - `.snapshot(now_ms: int) -> dict` — the `health.json` body: `{status, generated_ms, window_ms, feeds: {feed: {received_window, expected_window, coverage, last_write_ms, reconnects_window, open_gap_from_ms, markets: {market: {received_window, expected_window, coverage, first_seen_ms}}}}}`
  - `.write(path: Path, now_ms: int) -> None` — atomic write of `snapshot()`

- [ ] **Step 1: Write the failing test** `tests/recorder/test_health.py`

Every test here advances a fake wall clock as the records arrive. The previous draft fed 60
records while the clock stood still one minute into the hour — a physically impossible state, and
the reason the calendar-hour defect above could not be detected by any of them.

```python
import json
from pathlib import Path

from fundr.recorder.config import Clock
from fundr.recorder.health import Health

HOUR = 3_600_000
MIN = 60_000
START = HOUR * 100


def _health(start_ms: int = START):
    """A clock the test moves forward by hand. `state["wall"]` is the only time source, and
    no test ever moves it backwards — `Health` clamps it anyway, but a test that rewound it
    would be measuring the clamp instead of the window."""
    state = {"wall": start_ms}
    return Health(Clock(now_ms=lambda: state["wall"], mono_ns=lambda: 0)), state


def _minutely(h, state, feed, counts: dict[str, int], n_minutes: int, *,
              per_hour: int = 60, every_min: int = 1):
    """Feed `counts[market]` records, one every `every_min` minutes, moving the clock as
    reality would: sixty records a minute apart take fifty-nine minutes, not zero."""
    for m in counts:
        h.expect(feed, m, per_hour)
    for i in range(n_minutes):
        if i:
            state["wall"] += every_min * MIN
        for m, n in counts.items():
            if i < n:
                h.record(feed, m, state["wall"])


def test_a_full_trailing_hour_of_full_coverage_is_ok():
    h, state = _health()
    _minutely(h, state, "hl_state", {"BTC": 60, "ETH": 60}, 60)   # 60 records over 59 min
    state["wall"] += 1_000
    assert h.status(state["wall"]) == "ok"


def test_ten_minutes_into_the_hour_with_ten_records_is_ok():
    """The regression test for the calendar-hour bug: 10 of 60 expected is 100% of the
    10 minutes that have actually elapsed, not 17% of an hour that has not."""
    h, state = _health()
    _minutely(h, state, "hl_state", {"BTC": 10}, 10)              # minutes 0..9
    state["wall"] = START + 10 * MIN
    assert h.status(state["wall"]) == "ok"


def test_a_market_seen_seconds_ago_is_not_judged_yet():
    h, state = _health()
    h.expect("lighter_state", "NEWCOIN", 60)          # just listed, nothing received yet
    state["wall"] = START + 30_000
    h.expect("lighter_state", "BTC", 60)
    h.record("lighter_state", "BTC", state["wall"])   # another market keeps the feed alive
    assert h.status(state["wall"]) == "ok"            # 30 s earns 0.5 expected: too young


def test_one_market_below_95_percent_is_degraded():
    h, state = _health()
    _minutely(h, state, "hl_state", {"BTC": 60, "ETH": 40}, 60)   # ETH silent after min 39
    state["wall"] += 1_000
    assert h.status(state["wall"]) == "degraded"


def test_feed_below_50_percent_is_broken():
    h, state = _health()
    _minutely(h, state, "lighter_state", {"BTC": 10}, 10, every_min=4)  # 10 over 36 min
    state["wall"] = START + 40 * MIN
    assert h.status(state["wall"]) == "broken"


def test_long_open_gap_is_broken_and_short_one_degraded():
    h, state = _health()
    _minutely(h, state, "hl_state", {"BTC": 60}, 60)
    state["wall"] += 1_000
    now = state["wall"]
    h.gap_open("hl_state", now - 10 * MIN)
    assert h.status(now) == "degraded"
    h.gap_open("hl_state", now - 40 * MIN)
    assert h.status(now) == "broken"


def test_no_write_for_five_minutes_is_broken():
    h, state = _health()
    _minutely(h, state, "hl_state", {"BTC": 30}, 30)
    state["wall"] += 6 * MIN
    assert h.status(state["wall"]) == "broken"


def test_many_reconnects_drop_out_of_ok():
    h, state = _health()
    _minutely(h, state, "lighter_state", {"BTC": 60}, 60)
    state["wall"] += 1_000
    for _ in range(5):
        h.reconnect("lighter_state")
    assert h.status(state["wall"]) != "ok"


def test_counters_survive_an_hour_boundary_and_a_late_record():
    """Three feeds share one Health. The old calendar-hour roll cleared every counter when
    the clock crossed HH:00, and a record whose t_ms sat just before the boundary rolled it
    a second time. A trailing window must do neither."""
    h, state = _health(START + 50 * MIN)                     # 50 minutes into hour 100
    _minutely(h, state, "lighter_state", {"BTC": 20}, 20)    # crosses into hour 101
    state["wall"] += 1_000
    now = state["wall"]
    assert h.status(now) == "ok"
    before = h.snapshot(now)["feeds"]["lighter_state"]["markets"]["BTC"]["received_window"]
    h.record("lighter_state", "BTC", now - 2 * MIN)   # late arrival, timestamped in the past
    after = h.snapshot(now)["feeds"]["lighter_state"]["markets"]["BTC"]
    assert after["received_window"] == before + 1
    assert h.status(now) == "ok"


def test_expect_markets_replaces_the_active_set():
    h, state = _health()
    h.expect_markets("lighter_state", ["BTC", "ETH"], 60)
    assert set(h.snapshot(START)["feeds"]["lighter_state"]["markets"]) == {"BTC", "ETH"}
    h.expect_markets("lighter_state", ["BTC", "SOL"], 60)   # ETH delisted, SOL listed
    assert set(h.snapshot(START)["feeds"]["lighter_state"]["markets"]) == {"BTC", "SOL"}


def test_snapshot_written_atomically(tmp_path: Path):
    h, state = _health()
    h.expect("hl_state", "BTC", 60)
    h.record("hl_state", "BTC", state["wall"])
    out = tmp_path / "health.json"
    h.write(out, state["wall"])
    body = json.loads(out.read_text())
    assert body["status"] in {"ok", "degraded", "broken"}
    assert body["feeds"]["hl_state"]["markets"]["BTC"]["received_window"] == 1
    assert body["window_ms"] == HOUR
    assert not list(tmp_path.glob("*.tmp"))
```

- [ ] **Step 2: Run, expect failure**

Run: `uv run pytest tests/recorder/test_health.py -v`
Expected: FAIL — `ModuleNotFoundError: fundr.recorder.health`.

- [ ] **Step 3: Implement** `src/fundr/recorder/health.py`

```python
"""Coverage-based health. Phase 1's lost hour was a 0.4% difference in record count, invisible
to any count-based check — so status is judged on coverage per market, open gaps and write
recency.

The window is a TRAILING 60 minutes, not the calendar hour, and each market's expectation is
prorated by how much of that window has elapsed for it. Judged against the calendar hour, a
feed expecting 60 snapshots an hour reads 1/60 = 1.7% one minute past HH:00 and stays `broken`
until roughly HH:30 — every single hour — so "seven consecutive days at ok" could never pass.

Everything here advances ONE clamped, never-decreasing clock reading. Three feeds share one
Health instance; the earlier design rolled counters on the calendar hour, and rolled on the
record's own t_ms in record() but on clock.now_ms() in expect(), so a record arriving just
after a boundary with a timestamp just before it wiped the live window twice."""
import json
import os
from collections import defaultdict, deque
from pathlib import Path

from fundr.recorder.config import Clock

WINDOW_MS = 3_600_000          # the trailing window the spec's "in the last hour" means
OK_COVERAGE = 0.95
BROKEN_COVERAGE = 0.50
OK_MAX_RECONNECTS = 5
DEGRADED_GAP_MS = 30 * 60_000
STALE_WRITE_MS = 5 * 60_000
# Below one prorated expected event the ratio is dominated by rounding: a market listed
# seconds ago is not evidence of anything, so it is reported but not judged.
MIN_JUDGEABLE = 1.0


class Health:
    def __init__(self, clock: Clock | None = None):
        self._clock = clock or Clock()
        self._now = 0
        self._expected: dict[tuple[str, str], int] = {}
        self._events: dict[tuple[str, str], deque[int]] = defaultdict(deque)
        self._first_seen: dict[tuple[str, str], int] = {}
        self._reconnects: dict[str, deque[int]] = defaultdict(deque)
        self._last_write: dict[str, int] = {}
        self._last_event_ms: dict[str, int] = {}
        self._open_gap: dict[str, int] = {}

    # --- the single time source -------------------------------------------
    def _advance(self, now_ms: int | None = None) -> int:
        """Never moves backwards. A late record, a stepped clock or a caller passing an old
        timestamp must not rewind the window — the old code's two roll sites did exactly
        that and cleared every counter."""
        seen = self._clock.now_ms()
        if now_ms is not None:
            seen = max(seen, now_ms)
        self._now = max(self._now, seen)
        return self._now

    @staticmethod
    def _trim(dq: deque[int], now: int) -> None:
        while dq and dq[0] < now - WINDOW_MS:
            dq.popleft()

    # --- bookkeeping -------------------------------------------------------
    def expect(self, feed: str, market: str, n: int) -> None:
        """`n` is the expected count per FULL hour; the window prorates it."""
        now = self._advance()
        self._expected[(feed, market)] = n
        self._first_seen.setdefault((feed, market), now)

    def expect_markets(self, feed: str, markets: list[str], n: int) -> None:
        """Set the feed's whole active market set, from the universe sweep. Markets that have
        gone away stop being judged; markets that have just appeared start at zero received,
        which is the point — a subscribed market we never hear from must have an entry."""
        self._advance()
        wanted = set(markets)
        for key in [k for k in self._expected if k[0] == feed and k[1] not in wanted]:
            self._expected.pop(key, None)
            self._events.pop(key, None)
            self._first_seen.pop(key, None)
        for market in sorted(wanted):
            self.expect(feed, market, n)

    def record(self, feed: str, market: str, t_ms: int) -> None:
        """One VENUE event for this market. `t_ms` is the event's own wall clock, reported as
        `last_event_ms`; the window itself uses the clamped clock so a skewed or late
        timestamp cannot rewind it."""
        now = self._advance()
        key = (feed, market)
        self._first_seen.setdefault(key, now)
        dq = self._events[key]
        dq.append(now)
        self._trim(dq, now)
        self._last_write[feed] = now
        self._last_event_ms[feed] = max(self._last_event_ms.get(feed, 0), t_ms)

    def reconnect(self, feed: str) -> None:
        now = self._advance()
        dq = self._reconnects[feed]
        dq.append(now)
        self._trim(dq, now)

    def gap_open(self, feed: str, from_ms: int) -> None:
        self._advance()
        self._open_gap[feed] = from_ms

    def gap_close(self, feed: str, to_ms: int) -> None:
        self._advance(to_ms)
        self._open_gap.pop(feed, None)

    # --- judgement ---------------------------------------------------------
    def _market_stats(self, key: tuple[str, str], now: int) -> dict:
        n = self._expected.get(key, 0)
        first = self._first_seen.get(key, now)
        elapsed = min(WINDOW_MS, max(0, now - first))
        expected = n * elapsed / WINDOW_MS
        dq = self._events.get(key)
        if dq is not None:
            self._trim(dq, now)
        received = len(dq) if dq is not None else 0
        coverage = received / expected if expected >= MIN_JUDGEABLE else None
        return {"received_window": received, "expected_window": round(expected, 2),
                "expected_hour": n, "coverage": coverage, "first_seen_ms": first}

    def _coverages(self, now: int) -> list[float]:
        out = [self._market_stats(k, now)["coverage"] for k in self._expected]
        return [c for c in out if c is not None]

    def status(self, now_ms: int) -> str:
        now = self._advance(now_ms)
        coverages = self._coverages(now)
        gaps = [now - t for t in self._open_gap.values()]
        stale = [now - t for t in self._last_write.values()]
        for dq in self._reconnects.values():
            self._trim(dq, now)
        if (coverages and min(coverages) < BROKEN_COVERAGE) or \
           any(g > DEGRADED_GAP_MS for g in gaps) or any(s > STALE_WRITE_MS for s in stale):
            return "broken"
        if (coverages and min(coverages) < OK_COVERAGE) or gaps or \
           any(len(dq) >= OK_MAX_RECONNECTS for dq in self._reconnects.values()):
            return "degraded"
        return "ok"

    def snapshot(self, now_ms: int) -> dict:
        status = self.status(now_ms)          # advances the clock first
        now = self._now
        feeds: dict[str, dict] = {}
        for feed, market in self._expected:
            f = feeds.setdefault(feed, {"received_window": 0, "expected_window": 0.0,
                                        "markets": {}})
            stats = self._market_stats((feed, market), now)
            f["markets"][market] = stats
            f["received_window"] += stats["received_window"]
            f["expected_window"] += stats["expected_window"]
        for feed, f in feeds.items():
            exp = f["expected_window"]
            f["expected_window"] = round(exp, 2)
            f["coverage"] = f["received_window"] / exp if exp else None
            f["last_write_ms"] = self._last_write.get(feed)
            f["last_event_ms"] = self._last_event_ms.get(feed)
            f["reconnects_window"] = len(self._reconnects.get(feed, ()))
            f["open_gap_from_ms"] = self._open_gap.get(feed)
        return {"status": status, "generated_ms": now, "window_ms": WINDOW_MS, "feeds": feeds}

    def write(self, path: Path, now_ms: int) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.snapshot(now_ms), indent=1))
        os.replace(tmp, path)
```

- [ ] **Step 4: Run, expect pass**

Run: `uv run pytest tests/recorder/test_health.py -v`
Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add src/fundr/recorder/health.py tests/recorder/test_health.py
git commit -m "feat: recorder coverage health and status thresholds"
```

---

### Task 3: Async venue clients

**Files:**
- Create: `src/fundr/recorder/clients.py`
- Test: `tests/recorder/test_clients.py`

**Interfaces:**
- Consumes: `config.HL_INFO_URL`, `config.LIGHTER_BASE_URL`.
- Produces:
  - `clients.AsyncHLInfo(client: httpx.AsyncClient | None = None)` with `async post(payload: dict) -> Any`, `async meta_and_asset_ctxs() -> list`, `async predicted_fundings() -> list`, `async meta() -> dict`, `async aclose() -> None`.
  - `clients.AsyncLighterREST(client: httpx.AsyncClient | None = None)` with `async get(path: str, **params) -> dict` (raises `RuntimeError` when the body's `code != 200`), `async order_books() -> list[dict]`, `async order_book_details() -> list[dict]`, `async aclose() -> None`.
- These are async on purpose: `fundr.sources.hl_api` / `lighter_api` are synchronous and must not be used inside a feed task (Global Constraints).

- [ ] **Step 1: Write the failing test** `tests/recorder/test_clients.py`

```python
import httpx
import pytest

from fundr.recorder.clients import AsyncHLInfo, AsyncLighterREST


@pytest.mark.asyncio
async def test_hl_info_posts_and_returns_json():
    seen = {}

    async def handler(request):
        import json
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=[{"universe": []}, []])

    api = AsyncHLInfo(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    out = await api.meta_and_asset_ctxs()
    assert seen["body"] == {"type": "metaAndAssetCtxs"}
    assert out[0] == {"universe": []}
    await api.aclose()


@pytest.mark.asyncio
async def test_lighter_raises_on_error_code():
    async def handler(request):
        return httpx.Response(200, json={"code": 400, "message": "bad"})

    api = AsyncLighterREST(httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                             base_url="https://x"))
    with pytest.raises(RuntimeError):
        await api.order_books()
    await api.aclose()


@pytest.mark.asyncio
async def test_lighter_order_book_details_unwraps_list():
    async def handler(request):
        return httpx.Response(200, json={"code": 200, "order_book_details": [{"market_id": 1}]})

    api = AsyncLighterREST(httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                             base_url="https://x"))
    assert await api.order_book_details() == [{"market_id": 1}]
    await api.aclose()
```

- [ ] **Step 2: Add the async test plugin**

The repo has no async test support yet. Add `pytest-asyncio` to the dev group and enable auto mode:

```bash
uv add --dev pytest-asyncio
```

Then append to `pyproject.toml`'s `[tool.pytest.ini_options]`:

```toml
asyncio_mode = "auto"
```

With auto mode the `@pytest.mark.asyncio` decorators above are redundant but harmless; keep them for clarity.

- [ ] **Step 3: Run, expect failure**

Run: `uv run pytest tests/recorder/test_clients.py -v`
Expected: FAIL — `ModuleNotFoundError: fundr.recorder.clients`.

- [ ] **Step 4: Implement** `src/fundr/recorder/clients.py`

```python
"""Async venue clients. fundr.sources' clients are synchronous: awaiting one from the event
loop blocks every feed task, which is exactly how Phase 1 lost 95 minutes."""
from typing import Any

import httpx

from fundr.recorder.config import HL_INFO_URL, LIGHTER_BASE_URL


class AsyncHLInfo:
    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client or httpx.AsyncClient(timeout=30)

    async def post(self, payload: dict) -> Any:
        r = await self._client.post(HL_INFO_URL, json=payload)
        r.raise_for_status()
        return r.json()

    async def meta_and_asset_ctxs(self) -> list:
        return await self.post({"type": "metaAndAssetCtxs"})

    async def predicted_fundings(self) -> list:
        return await self.post({"type": "predictedFundings"})

    async def meta(self) -> dict:
        return await self.post({"type": "meta"})

    async def aclose(self) -> None:
        await self._client.aclose()


class AsyncLighterREST:
    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client or httpx.AsyncClient(base_url=LIGHTER_BASE_URL, timeout=30)

    async def get(self, path: str, **params) -> dict:
        r = await self._client.get(path, params=params)
        r.raise_for_status()
        body = r.json()
        if body.get("code") != 200:
            raise RuntimeError(f"{path} {params}: {body}")
        return body

    async def order_books(self) -> list[dict]:
        return (await self.get("/api/v1/orderBooks"))["order_books"]

    async def order_book_details(self) -> list[dict]:
        return (await self.get("/api/v1/orderBookDetails"))["order_book_details"]

    async def aclose(self) -> None:
        await self._client.aclose()
```

- [ ] **Step 5: Run, expect pass**

Run: `uv run pytest tests/recorder/test_clients.py -v`
Expected: 3 passed.

- [ ] **Step 6: Live smoke check** (free, public)

Run: `uv run python -c "
import asyncio
from fundr.recorder.clients import AsyncHLInfo, AsyncLighterREST
async def main():
    hl, li = AsyncHLInfo(), AsyncLighterREST()
    m = await hl.meta_and_asset_ctxs(); d = await li.order_book_details()
    print('hl universe', len(m[0]['universe']), 'lighter markets', len(d))
    await hl.aclose(); await li.aclose()
asyncio.run(main())"`
Expected: about 234 HL universe entries and about 235 Lighter markets.

- [ ] **Step 7: Commit**

```bash
git add src/fundr/recorder/clients.py tests/recorder/test_clients.py pyproject.toml uv.lock
git commit -m "feat: async venue clients for the recorder"
```

---

### Task 4: Hyperliquid market-state feed

**Files:**
- Create: `src/fundr/recorder/feeds/__init__.py`, `src/fundr/recorder/feeds/hl_state.py`
- Test: `tests/recorder/test_hl_state.py`

**Interfaces:**
- Consumes: `Config`, `Clock`, `HourlyWriter`, `Health`, `records.Seq/envelope/gap`, `AsyncHLInfo`.
- Produces: `hl_state.HLStateFeed(cfg, writer, health, clock, client)` with `async run(stop: asyncio.Event) -> None` and `async cycle() -> None` (one poll, exposed for tests).
- Writes one record per market per cycle: `envelope("hl_state", "hl", seq, payload={"coin":…, "ctx":…, "meta":…, "predicted":…}, t_ms, mono_ns, trigger="poll")`, plus gap records on timeout or clock jump.
- Gap reasons produced: `"watchdog_timeout"`, `"suspend_or_hang"`, `"clock_step"`, `"poll_error"`.

- [ ] **Step 1: Write the failing test** `tests/recorder/test_hl_state.py`

```python
import asyncio

import pytest

from fundr.recorder.config import Clock, Config
from fundr.recorder.feeds.hl_state import HLStateFeed
from fundr.recorder.health import Health
from fundr.recorder.writer import HourlyWriter

META = {"universe": [{"name": "BTC"}, {"name": "OLD", "isDelisted": True}]}
CTXS = [{"funding": "0.0000125", "openInterest": "10", "markPx": "100", "premium": "0.0005"},
        {"funding": "0.0", "openInterest": "0", "markPx": "1", "premium": None}]
PRED = [["BTC", [["HlPerp", {"fundingRate": "0.0000125", "nextFundingTime": 1}]]]]


class FakeHL:
    def __init__(self, hang: bool = False, fail: bool = False):
        self.hang, self.fail, self.calls = hang, fail, 0

    async def meta_and_asset_ctxs(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("boom")
        if self.hang:
            await asyncio.sleep(3600)
        return [META, CTXS]

    async def predicted_fundings(self):
        return PRED


def _rows(path_root, feed="hl_state"):
    import gzip, json
    out = []
    for p in sorted((path_root / feed).rglob("*.jsonl.gz")):
        out += [json.loads(x) for x in gzip.open(p, "rt").read().splitlines()]
    return out


def _feed(tmp_path, client, now_ms=3_600_000 * 100, mono_ns=0, watchdog_s=1):
    cfg = Config(root=tmp_path, watchdog_s=watchdog_s)
    clock = Clock(now_ms=lambda: now_ms, mono_ns=lambda: mono_ns, sleep=asyncio.sleep)
    w = HourlyWriter(tmp_path, "hl_state")
    return HLStateFeed(cfg, w, Health(clock), clock, client), w


async def test_cycle_writes_one_record_per_live_market(tmp_path):
    feed, w = _feed(tmp_path, FakeHL())
    await feed.cycle()
    w.close()
    rows = _rows(tmp_path)
    assert [r["payload"]["coin"] for r in rows] == ["BTC", "OLD"]
    btc = rows[0]
    assert btc["payload"]["ctx"]["funding"] == "0.0000125"
    assert btc["payload"]["predicted"][0][0] == "HlPerp"
    assert btc["payload"]["meta"].get("isDelisted") in (None, False)
    assert rows[1]["payload"]["meta"]["isDelisted"] is True
    assert [r["seq"] for r in rows] == [1, 2]


async def test_hung_call_is_bounded_and_writes_a_gap(tmp_path):
    feed, w = _feed(tmp_path, FakeHL(hang=True), watchdog_s=1)
    await asyncio.wait_for(feed.cycle(), timeout=5)  # must return, not hang
    w.close()
    rows = _rows(tmp_path)
    assert [r["type"] for r in rows] == ["gap"]
    assert rows[0]["reason"] == "watchdog_timeout"
    assert rows[0]["seq"] == 1


async def test_failed_call_writes_a_gap_and_seq_stays_continuous(tmp_path):
    client = FakeHL(fail=True)
    feed, w = _feed(tmp_path, client)
    await feed.cycle()
    client.fail = False
    await feed.cycle()
    w.close()
    rows = _rows(tmp_path)
    assert rows[0]["type"] == "gap" and rows[0]["reason"] == "poll_error"
    # seq is continuous across the gap record and the error envelope, so a reader can tell a
    # declared hole from a lost record. run_id is constant within a process.
    assert [r["seq"] for r in rows] == list(range(1, len(rows) + 1))
    assert len({r["run_id"] for r in rows}) == 1


async def test_monotonic_jump_is_recorded_as_suspension(tmp_path):
    cfg = Config(root=tmp_path, hl_poll_s=60)
    state = {"mono": 0, "wall": 3_600_000 * 100}
    clock = Clock(now_ms=lambda: state["wall"], mono_ns=lambda: state["mono"],
                  sleep=asyncio.sleep)
    w = HourlyWriter(tmp_path, "hl_state")
    feed = HLStateFeed(cfg, w, Health(clock), clock, FakeHL())
    await feed.cycle()
    state["mono"] += 600 * 1_000_000_000   # 10 minutes of monotonic time
    state["wall"] += 600_000
    await feed.cycle()
    w.close()
    gaps = [r for r in _rows(tmp_path) if r.get("type") == "gap"]
    assert gaps and gaps[0]["reason"] == "suspend_or_hang"


async def test_wall_clock_only_jump_is_a_clock_step(tmp_path):
    cfg = Config(root=tmp_path, hl_poll_s=60)
    state = {"mono": 0, "wall": 3_600_000 * 100}
    clock = Clock(now_ms=lambda: state["wall"], mono_ns=lambda: state["mono"],
                  sleep=asyncio.sleep)
    w = HourlyWriter(tmp_path, "hl_state")
    feed = HLStateFeed(cfg, w, Health(clock), clock, FakeHL())
    await feed.cycle()
    state["mono"] += 60 * 1_000_000_000    # one normal interval
    state["wall"] += 3_600_000             # but an hour of wall clock
    await feed.cycle()
    w.close()
    gaps = [r for r in _rows(tmp_path) if r.get("type") == "gap"]
    assert gaps and gaps[0]["reason"] == "clock_step"
```

- [ ] **Step 2: Run, expect failure**

Run: `uv run pytest tests/recorder/test_hl_state.py -v`
Expected: FAIL — `ModuleNotFoundError: fundr.recorder.feeds`.

- [ ] **Step 3: Implement** `src/fundr/recorder/feeds/hl_state.py`

```python
"""Hyperliquid market state, polled. Owns its own timer, client and writer: nothing here can
be blocked by, or block, another feed."""
import asyncio

from fundr.recorder import records
from fundr.recorder.clients import AsyncHLInfo
from fundr.recorder.config import Clock, Config
from fundr.recorder.health import Health
from fundr.recorder.writer import HourlyWriter

FEED = "hl_state"
VENUE = "hl"


class HLStateFeed:
    def __init__(self, cfg: Config, writer: HourlyWriter, health: Health, clock: Clock,
                 client: AsyncHLInfo | None = None):
        self._cfg = cfg
        self._w = writer
        self._health = health
        self._clock = clock
        self._client = client or AsyncHLInfo()
        self._seq = records.Seq(FEED)
        self._last_mono_ns: int | None = None
        self._last_wall_ms: int | None = None

    def _check_clocks(self, t_ms: int, mono_ns: int) -> None:
        """A monotonic gap means we were suspended or hung; a wall-only jump means the clock
        was stepped. Phase 1 could not tell these apart."""
        if self._last_mono_ns is None:
            return
        mono_delta_ms = (mono_ns - self._last_mono_ns) / 1_000_000
        wall_delta_ms = t_ms - self._last_wall_ms
        limit = 2 * self._cfg.hl_poll_s * 1000
        if mono_delta_ms > limit:
            self._gap(t_ms, mono_ns, self._last_wall_ms, t_ms, "suspend_or_hang")
        elif abs(wall_delta_ms - mono_delta_ms) > limit:
            self._gap(t_ms, mono_ns, self._last_wall_ms, t_ms, "clock_step")

    def _gap(self, t_ms: int, mono_ns: int, from_ms: int, to_ms: int, reason: str) -> None:
        self._w.write(records.gap(FEED, VENUE, self._seq.next(), t_ms=t_ms, mono_ns=mono_ns,
                                  from_ms=from_ms, to_ms=to_ms, reason=reason))
        self._health.gap_open(FEED, from_ms)

    async def cycle(self) -> None:
        t_ms, mono_ns = self._clock.now_ms(), self._clock.mono_ns()
        self._check_clocks(t_ms, mono_ns)
        self._last_mono_ns, self._last_wall_ms = mono_ns, t_ms
        try:
            raw = await asyncio.wait_for(self._client.meta_and_asset_ctxs(),
                                         timeout=self._cfg.watchdog_s)
            predicted = await asyncio.wait_for(self._client.predicted_fundings(),
                                               timeout=self._cfg.watchdog_s)
        except TimeoutError:
            self._gap(t_ms, mono_ns, t_ms, self._clock.now_ms(), "watchdog_timeout")
            return
        except Exception as e:  # a bad response must not end the feed; it is recorded instead
            self._gap(t_ms, mono_ns, t_ms, self._clock.now_ms(), "poll_error")
            self._w.write(records.envelope(FEED, VENUE, self._seq.next(), {"error": repr(e)},
                                           t_ms=t_ms, mono_ns=mono_ns, trigger="error"))
            return
        self._health.gap_close(FEED, t_ms)
        meta, ctxs = raw[0], raw[1]
        pred = {coin: venues for coin, venues in predicted}
        for m, ctx in zip(meta["universe"], ctxs):
            coin = m["name"]
            payload = {"coin": coin, "meta": m, "ctx": ctx, "predicted": pred.get(coin)}
            self._w.write(records.envelope(FEED, VENUE, self._seq.next(), payload,
                                           t_ms=t_ms, mono_ns=mono_ns, trigger="poll"))
            # Coverage counts what the VENUE returned. Expectations are NOT set here: they
            # come from the universe sweep (Task 6), so a coin that drops out of the
            # response shows as missing coverage instead of quietly losing its expectation.
            self._health.record(FEED, coin, t_ms)

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            started = self._clock.mono_ns()
            await self.cycle()
            elapsed_s = (self._clock.mono_ns() - started) / 1e9
            await self._clock.sleep(max(0.0, self._cfg.hl_poll_s - elapsed_s))
```

- [ ] **Step 4: Run, expect pass**

Run: `uv run pytest tests/recorder/test_hl_state.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/fundr/recorder/feeds tests/recorder/test_hl_state.py
git commit -m "feat: Hyperliquid market-state feed with watchdog and clock-jump detection"
```

---

### Task 5: Lighter market-state feed

This is the task the whole recorder exists for: Lighter's running funding converges exactly on the
settled rate, and only the hour's **last** premium reading reproduces it
(`docs/phase1/evidence/P7-funding-formulas.md`). A fixed 60-second timer can alias against the
~1-minute field refresh and drop that value permanently.

**Files:**
- Create: `src/fundr/recorder/feeds/lighter_state.py`
- Test: `tests/recorder/test_lighter_state.py`

**Interfaces:**
- Consumes: `Config`, `Clock`, `HourlyWriter`, `Health`, `records`.
- Produces: `lighter_state.LighterStateFeed(cfg, writer, health, clock, connect=None)` with:
  - `async run(stop: asyncio.Event) -> None`
  - `on_message(msg: dict) -> None` — called for each websocket message (event-driven writes happen here)
  - `snapshot(trigger: str) -> None` — writes every subscribed market's latest values
  - `set_markets(market_ids: list[int]) -> None` — dynamic subscribe/unsubscribe; called by the universe feed
  - `next_boundary_sleep_s() -> float` — seconds until the next forced snapshot instant
- `connect` is an injectable async factory returning an object with `async send(str)`, `async recv() -> str`, `async close()`; production passes a `websockets` connection.
- Triggers written: `"change"` (premium or current_funding_rate differs), `"heartbeat"`, `"boundary"`. Records carry `n_msgs` and `n_funding_changes` for the interval, `age_ms` (how old the captured values are), `ws_ts` / `ws_type` (the venue's own message timestamp and type) and `stale: bool`.

**Five structural rules this task must respect** — each one is a way the straightforward version
silently loses exactly the data the recorder exists for:

1. **The trigger label is decided from the instant the timer slept *to*, not by re-querying the
   clock.** After sleeping to `HH:59:30`, `next_boundary_sleep_s()` already answers with the
   *next* offset (~22 s), so a `<= 1.0` re-check labels the hour's critical record `heartbeat`.
   Task 9's soak gate and Task 11's diagnostic both key on that label.
2. **The heartbeat/boundary timer lives OUTSIDE the connection loop.** If the timers only run
   inside `async with self._connect()`, a reconnect at `HH:59:4x` costs that hour its boundary
   snapshot entirely.
3. **The reader and the timers run in an `asyncio.TaskGroup`** (Python 3.13). A bare
   `asyncio.gather` propagates one task's failure without cancelling the other, leaving an
   orphaned reader writing through a stale connection into the shared writer.
4. **Freshness is per market.** `last_msg_ms` is tracked per market and `age_ms` is on every
   record; one busy market must not make 245 silent ones look alive. `_latest` is never written
   as if fresh — a value older than two heartbeat intervals is `stale: true` with its real age.
   It is deliberately *not* discarded on reconnect: a stale-but-labelled pre-boundary premium is
   worth far more than no record at all, which is what clearing it would produce.
5. **`ws.send` and `ws.recv` run under `asyncio.wait_for`**, like every other network call
   (Global Constraints). `_sync_subscriptions` issues ~214 sends on a reconnect; an unbounded
   one stalls the whole feed.

- [ ] **Step 1: Write the failing test** `tests/recorder/test_lighter_state.py`

```python
import asyncio
import gzip
import json

from fundr.recorder.config import Clock, Config
from fundr.recorder.feeds.lighter_state import LighterStateFeed
from fundr.recorder.health import Health
from fundr.recorder.writer import HourlyWriter

HOUR = 3_600_000


def _stats(market_id=1, premium="0.0010", cfr="0.0012", symbol="BTC", ws_ts=1789812743):
    return {"channel": f"market_stats:{market_id}",
            "market_stats": {"market_id": market_id, "symbol": symbol, "premium": premium,
                             "current_funding_rate": cfr, "funding_rate": "0.0011",
                             "funding_timestamp": HOUR * 100, "open_interest": "5"},
            "timestamp": ws_ts, "type": "update/market_stats"}


def _rows(root):
    out = []
    for p in sorted((root / "lighter_state").rglob("*.jsonl.gz")):
        out += [json.loads(x) for x in gzip.open(p, "rt").read().splitlines()]
    return out


def _feed(tmp_path, now_ms=HOUR * 100, markets=(1,)):
    """The fake clock's sleep advances the fake wall clock, so a test that drives the timers
    experiences real elapsed time. The fixture also calls mark_subscribed: without it the
    feed's pending lists never settle and every subscription assertion is meaningless."""
    state = {"wall": now_ms}

    async def sleep(seconds: float) -> None:
        state["wall"] += int(seconds * 1000)
        await asyncio.sleep(0)

    clock = Clock(now_ms=lambda: state["wall"], mono_ns=lambda: 0, sleep=sleep)
    w = HourlyWriter(tmp_path, "lighter_state")
    feed = LighterStateFeed(Config(root=tmp_path), w, Health(clock), clock)
    feed.set_markets(list(markets))
    feed.mark_subscribed(list(markets))
    return feed, w, state


async def test_value_change_writes_immediately(tmp_path):
    feed, w, _ = _feed(tmp_path)
    feed.on_message(_stats(premium="0.0010"))
    feed.on_message(_stats(premium="0.0010"))   # unchanged: no new record
    feed.on_message(_stats(premium="0.0020"))   # changed: record
    w.close()
    rows = [r for r in _rows(tmp_path) if r.get("trigger") == "change"]
    assert [r["payload"]["premium"] for r in rows] == ["0.0010", "0.0020"]
    assert rows[-1]["n_funding_changes"] >= 1


async def test_boundary_snapshot_captures_the_last_pre_settlement_value(tmp_path):
    feed, w, state = _feed(tmp_path, now_ms=HOUR * 100)
    feed.on_message(_stats(premium="0.0010"))
    state["wall"] = HOUR * 101 - 30_000        # HH:59:30
    feed.on_message(_stats(premium="0.0099"))  # the value that will settle
    feed.snapshot("boundary")
    w.close()
    boundary = [r for r in _rows(tmp_path) if r["trigger"] == "boundary"]
    assert boundary and boundary[-1]["payload"]["premium"] == "0.0099"
    assert boundary[-1]["stale"] is False


async def test_records_carry_age_and_the_venue_message_timestamp(tmp_path):
    """The venue stamps its own message; keeping only market_stats threw that away for good,
    and capture time is not the same thing."""
    feed, w, state = _feed(tmp_path)
    feed.on_message(_stats(ws_ts=1789812743))
    state["wall"] += 9_000
    feed.snapshot("heartbeat")
    w.close()
    hb = [r for r in _rows(tmp_path) if r["trigger"] == "heartbeat"][-1]
    assert hb["ws_ts"] == 1789812743
    assert hb["ws_type"] == "update/market_stats"
    assert hb["age_ms"] == 9_000


async def test_two_silent_heartbeat_intervals_go_stale_and_force_a_reconnect(tmp_path):
    feed, w, state = _feed(tmp_path)
    feed.on_message(_stats())
    feed.snapshot("heartbeat")                  # interval had messages
    state["wall"] += 60_000
    feed.snapshot("heartbeat")                  # silent interval 1
    state["wall"] += 60_000
    feed.snapshot("heartbeat")                  # silent interval 2 -> stale + reconnect
    w.close()
    hb = [r for r in _rows(tmp_path) if r["trigger"] == "heartbeat"]
    assert hb[0]["n_msgs"] >= 1 and hb[0]["stale"] is False
    assert hb[-1]["stale"] is True and hb[-1]["age_ms"] == 120_000
    assert feed.needs_reconnect is True


async def test_boundary_snapshots_do_not_trip_the_silence_rule(tmp_path):
    """The two boundary snapshots are 22 s apart, which is not a heartbeat interval. Counting
    them as silent intervals would force a reconnect at every hour boundary."""
    feed, w, state = _feed(tmp_path, now_ms=HOUR * 101 - 30_000)
    feed.on_message(_stats())
    feed.snapshot("boundary")                   # HH:59:30
    state["wall"] += 22_000
    feed.snapshot("boundary")                   # HH:59:52, no messages in between
    assert feed.needs_reconnect is False


async def test_one_active_market_does_not_mask_a_silent_one(tmp_path):
    """Staleness is scoped per market: a single chatty market must not make a dead
    subscription look alive."""
    feed, w, state = _feed(tmp_path, markets=(1, 2))
    feed.on_message(_stats(market_id=1, symbol="BTC"))
    feed.on_message(_stats(market_id=2, symbol="ETH"))
    for i in range(3):                          # market 1 keeps talking, market 2 goes quiet
        state["wall"] += 60_000
        feed.on_message(_stats(market_id=1, symbol="BTC", premium=f"0.002{i}"))
        feed.snapshot("heartbeat")
    w.close()
    last = {r["payload"]["market_id"]: r
            for r in _rows(tmp_path) if r["trigger"] == "heartbeat"}
    assert last[1]["stale"] is False and last[1]["age_ms"] == 0
    assert last[2]["stale"] is True and last[2]["age_ms"] == 180_000
    assert feed.needs_reconnect is False        # the socket itself is alive


async def test_set_markets_adds_and_removes(tmp_path):
    feed, w, _ = _feed(tmp_path)                # fixture has already subscribed market 1
    feed.set_markets([1, 2])
    assert feed.pending_subscribe == [2] and feed.pending_unsubscribe == []
    feed.mark_subscribed([2])
    feed.set_markets([2])
    assert feed.pending_unsubscribe == [1] and feed.pending_subscribe == []


async def test_next_boundary_sleep_is_before_the_hour_ends(tmp_path):
    feed, _, state = _feed(tmp_path, now_ms=HOUR * 100)
    state["wall"] = HOUR * 100 + 1000           # 1s into the hour
    assert 3500 < feed.next_boundary_sleep_s() < 3600
    state["wall"] = HOUR * 101 - 20_000         # HH:59:40, past the first offset (30 s)
    assert 11 < feed.next_boundary_sleep_s() <= 12      # the second offset is at HH:59:52


async def test_timers_label_the_pre_boundary_snapshots_boundary(tmp_path):
    """Drives _timers, not snapshot(), across an hour boundary. Deciding the trigger by
    re-querying next_boundary_sleep_s() after the sleep labels the hour's critical record
    'heartbeat' — and Task 9's soak gate and Task 11's diagnostic both look for 'boundary'."""
    state = {"wall": HOUR * 101 - 150_000}      # HH:57:30
    stop = asyncio.Event()

    async def sleep(seconds: float) -> None:
        state["wall"] += int(seconds * 1000)
        if state["wall"] >= HOUR * 101 + 30_000:
            stop.set()
        await asyncio.sleep(0)

    clock = Clock(now_ms=lambda: state["wall"], mono_ns=lambda: 0, sleep=sleep)
    w = HourlyWriter(tmp_path, "lighter_state")
    feed = LighterStateFeed(Config(root=tmp_path), w, Health(clock), clock)
    feed.set_markets([1])
    feed.mark_subscribed([1])
    feed.on_message(_stats(premium="0.0099"))

    await asyncio.wait_for(feed._timers(stop), timeout=5)
    w.close()
    rows = _rows(tmp_path)
    triggers = [r["trigger"] for r in rows]
    assert triggers.count("boundary") == 2, triggers    # HH:59:30 and HH:59:52
    boundary = [r for r in rows if r["trigger"] == "boundary"]
    assert boundary[-1]["payload"]["premium"] == "0.0099"
    # Both land in the closing hour's partition, not the next one.
    assert all(r["t_ms"] < HOUR * 101 for r in boundary)
```

- [ ] **Step 2: Run, expect failure**

Run: `uv run pytest tests/recorder/test_lighter_state.py -v`
Expected: FAIL — `ModuleNotFoundError: fundr.recorder.feeds.lighter_state`.

- [ ] **Step 3: Implement** `src/fundr/recorder/feeds/lighter_state.py`

```python
"""Lighter market state over websocket.

Capture rule (docs/superpowers/specs/2026-09-20-phase2-recorder-design.md): write on every
change of premium / current_funding_rate, on a heartbeat, and — the part that matters — twice
in the final seconds of each hour. Lighter's premium is a running mean that resets at the
boundary and only its last in-hour value reproduces the settled rate.

Structure, and why it is this shape:
- the timer task runs OUTSIDE the connection loop, so a reconnect at HH:59:4x cannot cost the
  hour its boundary snapshot;
- the trigger label comes from the instant we slept TO, never from re-querying the clock after
  the sleep (which would answer with the *next* offset and mislabel the critical record);
- reader and timers live in a TaskGroup, so neither can be orphaned writing through a dead
  socket into the shared writer;
- freshness is per market, with age_ms on every record;
- every send and recv is bounded by asyncio.wait_for."""
import asyncio
import json

import websockets

from fundr.recorder import records
from fundr.recorder.config import Clock, Config, LIGHTER_WS_URL
from fundr.recorder.health import Health
from fundr.recorder.writer import HourlyWriter

FEED = "lighter_state"
VENUE = "lighter"
HOUR_MS = 3_600_000
WATCHED = ("premium", "current_funding_rate")


class LighterStateFeed:
    def __init__(self, cfg: Config, writer: HourlyWriter, health: Health, clock: Clock,
                 connect=None):
        self._cfg = cfg
        self._w = writer
        self._health = health
        self._clock = clock
        self._connect = connect or (lambda: websockets.connect(LIGHTER_WS_URL))
        self._seq = records.Seq(FEED)
        self._latest: dict[int, dict] = {}
        self._last_msg_ms: dict[int, int] = {}   # per market: one busy market must not
        self._ws_ts: dict[int, object] = {}      # make 245 silent ones look alive
        self._ws_type: dict[int, object] = {}
        self._watched: dict[int, tuple] = {}
        self._n_msgs: dict[int, int] = {}
        self._n_changes: dict[int, int] = {}
        self._silent_intervals = 0               # whole-socket silence, heartbeats only
        self.needs_reconnect = False
        self._subscribed: set[int] = set()
        self._wanted: set[int] = set()
        self.pending_subscribe: list[int] = []
        self.pending_unsubscribe: list[int] = []

    # --- market set -------------------------------------------------------
    def set_markets(self, market_ids: list[int]) -> None:
        self._wanted = set(market_ids)
        self.pending_subscribe = sorted(self._wanted - self._subscribed)
        self.pending_unsubscribe = sorted(self._subscribed - self._wanted)

    def mark_subscribed(self, market_ids: list[int]) -> None:
        self._subscribed |= set(market_ids)
        self.set_markets(sorted(self._wanted))

    def mark_unsubscribed(self, market_ids: list[int]) -> None:
        self._subscribed -= set(market_ids)
        self.set_markets(sorted(self._wanted))

    # --- capture ----------------------------------------------------------
    def on_message(self, msg: dict) -> None:
        stats = msg.get("market_stats")
        if not stats:
            return
        mid = stats["market_id"]
        t_ms = self._clock.now_ms()
        self._n_msgs[mid] = self._n_msgs.get(mid, 0) + 1
        self._latest[mid] = stats
        self._last_msg_ms[mid] = t_ms
        # The venue stamps its own message; keeping only market_stats discards it for good,
        # and capture time is not the same quantity.
        self._ws_ts[mid] = msg.get("timestamp")
        self._ws_type[mid] = msg.get("type")
        # Coverage counts VENUE messages, never our own timer. Counting our writes would give
        # a market whose subscription died silently a perfect score forever.
        self._health.record(FEED, stats.get("symbol") or str(mid), t_ms)
        key = tuple(stats.get(f) for f in WATCHED)
        if self._watched.get(mid) != key:
            self._watched[mid] = key
            self._n_changes[mid] = self._n_changes.get(mid, 0) + 1
            self._write(mid, "change")

    def snapshot(self, trigger: str) -> None:
        if trigger == "heartbeat":
            # Only heartbeats are a fixed 60 s apart. The two boundary snapshots are 22 s
            # apart, and counting them as intervals would force a reconnect every hour.
            if sum(self._n_msgs.values()) == 0:
                self._silent_intervals += 1
                if self._silent_intervals >= 2:
                    self.needs_reconnect = True
            else:
                self._silent_intervals = 0
        for mid in sorted(self._latest):
            self._write(mid, trigger)
        self._n_msgs.clear()
        self._n_changes.clear()

    def _age_ms(self, mid: int, t_ms: int) -> int | None:
        last = self._last_msg_ms.get(mid)
        return None if last is None else t_ms - last

    def _is_stale(self, mid: int, t_ms: int) -> bool:
        """Per market, and age-based rather than interval-counted: it stays correct across a
        reconnect and across the irregular spacing of the boundary snapshots."""
        age = self._age_ms(mid, t_ms)
        return age is None or age >= 2 * self._cfg.lighter_heartbeat_s * 1000

    def _write(self, mid: int, trigger: str) -> None:
        stats = self._latest.get(mid)
        if stats is None:
            return      # never heard from: write nothing, and let health report the zero
        t_ms = self._clock.now_ms()
        self._w.write(records.envelope(
            FEED, VENUE, self._seq.next(), stats, t_ms=t_ms, mono_ns=self._clock.mono_ns(),
            trigger=trigger, n_msgs=self._n_msgs.get(mid, 0),
            n_funding_changes=self._n_changes.get(mid, 0),
            ws_ts=self._ws_ts.get(mid), ws_type=self._ws_type.get(mid),
            age_ms=self._age_ms(mid, t_ms), stale=self._is_stale(mid, t_ms)))

    # --- timing -----------------------------------------------------------
    def next_boundary_sleep_s(self) -> float:
        """Seconds until the next forced pre-boundary snapshot."""
        now_ms = self._clock.now_ms()
        into_hour = now_ms % HOUR_MS
        for offset in sorted(self._cfg.boundary_offsets_s, reverse=True):
            at = HOUR_MS - offset * 1000
            if into_hour < at:
                return (at - into_hour) / 1000
        return (HOUR_MS - into_hour + (HOUR_MS - max(self._cfg.boundary_offsets_s) * 1000)) / 1000

    # --- run --------------------------------------------------------------
    async def run(self, stop: asyncio.Event) -> None:
        """The timer task is deliberately OUTSIDE the connection loop: a reconnect at
        HH:59:4x must not cost the hour its boundary snapshot, the one record this feed
        exists to produce. TaskGroup and not gather(): gather propagates one child's failure
        without cancelling the other, which would leave an orphaned reader writing through a
        dead connection into the shared writer."""
        async with asyncio.TaskGroup() as tg:
            tg.create_task(self._timers(stop))
            tg.create_task(self._connections(stop))

    async def _connections(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                async with self._connect() as ws:
                    self._subscribed.clear()
                    self.set_markets(sorted(self._wanted))
                    await self._sync_subscriptions(ws)
                    self.needs_reconnect = False
                    self._silent_intervals = 0
                    await self._reader(ws, stop)
                if self.needs_reconnect and not stop.is_set():
                    t_ms = self._clock.now_ms()
                    self._w.write(records.gap(FEED, VENUE, self._seq.next(), t_ms=t_ms,
                                              mono_ns=self._clock.mono_ns(), from_ms=t_ms,
                                              to_ms=t_ms, reason="stall_reconnect"))
                    self._health.reconnect(FEED)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # deliberate: a 24/7 feed must survive every ws error,
                # and the error is recorded in the data rather than lost to a crash.
                t_ms = self._clock.now_ms()
                self._w.write(records.gap(FEED, VENUE, self._seq.next(), t_ms=t_ms,
                                          mono_ns=self._clock.mono_ns(), from_ms=t_ms,
                                          to_ms=t_ms, reason=f"ws_error:{type(e).__name__}"))
                self._health.reconnect(FEED)
                await self._clock.sleep(5)

    async def _send(self, ws, obj: dict) -> None:
        # Global Constraint: every network call is bounded out-of-band. A reconnect issues
        # ~214 of these, and one unbounded send stalls the whole feed.
        await asyncio.wait_for(ws.send(json.dumps(obj)), timeout=self._cfg.watchdog_s)

    async def _sync_subscriptions(self, ws) -> None:
        for mid in self.pending_subscribe:
            await self._send(ws, {"type": "subscribe", "channel": f"market_stats/{mid}"})
        self.mark_subscribed(self.pending_subscribe)
        for mid in self.pending_unsubscribe:
            await self._send(ws, {"type": "unsubscribe", "channel": f"market_stats/{mid}"})
        self.mark_unsubscribed(self.pending_unsubscribe)

    async def _reader(self, ws, stop: asyncio.Event) -> None:
        while not stop.is_set() and not self.needs_reconnect:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=self._cfg.watchdog_s)
            except TimeoutError:
                continue    # re-check stop / needs_reconnect; the timers judge silence
            self.on_message(json.loads(raw))
            if self.pending_subscribe or self.pending_unsubscribe:
                await self._sync_subscriptions(ws)   # new listings, without dropping the socket

    async def _timers(self, stop: asyncio.Event) -> None:
        """Heartbeat and forced pre-boundary snapshots. Runs whatever the socket is doing.

        The trigger is decided from the instant we are sleeping TO, not by re-querying
        next_boundary_sleep_s() afterwards: standing at HH:59:30 that call already answers
        with the *next* offset (~22 s), so a `<= 1.0` re-check labels the hour's critical
        record `heartbeat` and the soak gate and day-one diagnostic never see a boundary."""
        while not stop.is_set():
            boundary_s = self.next_boundary_sleep_s()
            if boundary_s <= self._cfg.lighter_heartbeat_s:
                wait_s, trigger = boundary_s, "boundary"
            else:
                wait_s, trigger = float(self._cfg.lighter_heartbeat_s), "heartbeat"
            await self._clock.sleep(max(0.0, wait_s))
            if stop.is_set():
                return
            self.snapshot(trigger)
```

- [ ] **Step 4: Run, expect pass**

Run: `uv run pytest tests/recorder/test_lighter_state.py -v`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add src/fundr/recorder/feeds/lighter_state.py tests/recorder/test_lighter_state.py
git commit -m "feat: Lighter feed with change, heartbeat and pre-boundary capture"
```

---

### Task 6: Universe and funding-parameter sweep

**Files:**
- Create: `src/fundr/recorder/feeds/universe.py`
- Test: `tests/recorder/test_universe.py`

**Interfaces:**
- Consumes: `AsyncHLInfo.meta`, `AsyncLighterREST.order_books`, `AsyncLighterREST.order_book_details`, `Config`, `Clock`, `HourlyWriter`, `Health`, `records`.
- Produces: `universe.UniverseFeed(cfg, writer, health, clock, hl=None, lighter=None, on_markets=None)` with `async run(stop)` and `async cycle() -> list[int]` (returns the active Lighter market ids and calls `on_markets(ids)` when set).
- Writes one record per sweep: `payload = {"hl_universe": [...], "lighter_order_books": [...], "lighter_details": [...]}`, `trigger="sweep"`.
- **Also publishes the coverage expectations** for both feeds: `health.expect_markets("hl_state", live_coins, 3600 // hl_poll_s)` and `health.expect_markets("lighter_state", active_symbols, 3600 // lighter_heartbeat_s)`. This is the spec's Done-when, literally — "expected counts derived from the universe feed, so markets listed or delisted mid-window are handled". Without it a subscribed-but-silent market has no health entry at all, and a market whose subscription dies scores full coverage because the only thing counted was our own timer.
- Why `orderBookDetails` is mandatory here: it is the only source of `funding_premium_multiplier`, `funding_clamp_small/big` and `base_interest_rate` (`docs/phase1/evidence/P6-lighter-market-state.md`), which Phase 1 could not test and the spec's Done-when requires.

**`funding_premium_multiplier` is in hundredths, and the recorder stores it raw.** Live values:
`100` on 137 markets, `50` on 96, `1` on 2. P7 resolved the units on data — `100` denotes a
multiplier of **1.0**, taking it literally scores 0/16 off-baseline hours while treating it as 1.0
scores 16/16 ([P7 §"Resolution of part A's three open points", item 2](../../phase1/evidence/P7-funding-formulas.md)).
So `50` is 0.5 (the documented RWA value) and `1` is 0.01 (Pre-IPO/Pre-Market). The recorder does
**not** divide: it captures raw as received, per the spec's "capture only" scope. Phase 4 divides
by 100 exactly once — this note exists so it does not scale twice, and so the 96+2 non-1.0 markets
are recognised as the answer to Phase 1's open question about markets with multiplier ≠ 1.

- [ ] **Step 1: Write the failing test** `tests/recorder/test_universe.py`

```python
import asyncio
import gzip
import json

from fundr.recorder.config import Clock, Config
from fundr.recorder.feeds.universe import UniverseFeed
from fundr.recorder.health import Health
from fundr.recorder.writer import HourlyWriter

HOUR = 3_600_000


class FakeHL:
    async def meta(self):
        return {"universe": [{"name": "BTC"}, {"name": "OLD", "isDelisted": True}]}


class FakeLighter:
    async def order_books(self):
        return [{"symbol": "BTC", "market_id": 1, "status": "active", "created_at": "1737098461107"},
                {"symbol": "DEAD", "market_id": 9, "status": "inactive", "created_at": "1"}]

    async def order_book_details(self):
        return [{"symbol": "BTC", "market_id": 1, "status": "active",
                 "funding_premium_multiplier": 100, "funding_clamp_small": "0.0500",
                 "funding_clamp_big": "4.0000", "base_interest_rate": "0.0100"}]


def _feed(tmp_path, on_markets=None):
    clock = Clock(now_ms=lambda: HOUR * 100, mono_ns=lambda: 0, sleep=asyncio.sleep)
    w = HourlyWriter(tmp_path, "universe")
    feed = UniverseFeed(Config(root=tmp_path), w, Health(clock), clock,
                        FakeHL(), FakeLighter(), on_markets)
    return feed, w


async def test_sweep_records_all_three_sources_including_funding_parameters(tmp_path):
    feed, w = _feed(tmp_path)
    await feed.cycle()
    w.close()
    p = next((tmp_path / "universe").rglob("*.jsonl.gz"))
    rec = json.loads(gzip.open(p, "rt").read().splitlines()[0])
    pay = rec["payload"]
    assert [m["name"] for m in pay["hl_universe"]] == ["BTC", "OLD"]
    assert {b["symbol"] for b in pay["lighter_order_books"]} == {"BTC", "DEAD"}
    # Stored raw: 100 means a multiplier of 1.0 (P7), and Phase 4 does the division, once.
    assert pay["lighter_details"][0]["funding_premium_multiplier"] == 100
    assert rec["trigger"] == "sweep"


async def test_active_markets_are_published_to_the_feed(tmp_path):
    seen = []
    feed, w = _feed(tmp_path, on_markets=seen.append)
    ids = await feed.cycle()
    w.close()
    assert ids == [1]          # only the active market
    assert seen == [[1]]


async def test_coverage_expectations_come_from_the_venue_market_lists(tmp_path):
    """The spec's Done-when: expected counts derive from the universe feed. A market that is
    listed but silent must show up at zero received, not vanish from health entirely."""
    clock = Clock(now_ms=lambda: HOUR * 100, mono_ns=lambda: 0, sleep=asyncio.sleep)
    w = HourlyWriter(tmp_path, "universe")
    health = Health(clock)
    feed = UniverseFeed(Config(root=tmp_path), w, health, clock, FakeHL(), FakeLighter())
    await feed.cycle()
    w.close()
    feeds = health.snapshot(HOUR * 100)["feeds"]
    assert set(feeds["hl_state"]["markets"]) == {"BTC"}          # OLD is delisted
    assert set(feeds["lighter_state"]["markets"]) == {"BTC"}     # DEAD is inactive
    assert feeds["lighter_state"]["markets"]["BTC"]["expected_hour"] == 60
    assert feeds["lighter_state"]["markets"]["BTC"]["received_window"] == 0
```

- [ ] **Step 2: Run, expect failure**

Run: `uv run pytest tests/recorder/test_universe.py -v`
Expected: FAIL — `ModuleNotFoundError: fundr.recorder.feeds.universe`.

- [ ] **Step 3: Implement** `src/fundr/recorder/feeds/universe.py`

```python
"""Universe sweep: which markets exist, their status and listing dates, and Lighter's
per-market funding parameters. Runs every 5 minutes because it is the only source future
Lighter delisting timestamps will ever have — its cadence is their resolution."""
import asyncio
from collections.abc import Callable

from fundr.recorder import records
from fundr.recorder.clients import AsyncHLInfo, AsyncLighterREST
from fundr.recorder.config import Clock, Config
from fundr.recorder.health import Health
from fundr.recorder.writer import HourlyWriter

FEED = "universe"


class UniverseFeed:
    def __init__(self, cfg: Config, writer: HourlyWriter, health: Health, clock: Clock,
                 hl: AsyncHLInfo | None = None, lighter: AsyncLighterREST | None = None,
                 on_markets: Callable[[list[int]], None] | None = None):
        self._cfg = cfg
        self._w = writer
        self._health = health
        self._clock = clock
        self._hl = hl or AsyncHLInfo()
        self._lighter = lighter or AsyncLighterREST()
        self._on_markets = on_markets
        self._seq = records.Seq(FEED)

    async def cycle(self) -> list[int]:
        t_ms, mono_ns = self._clock.now_ms(), self._clock.mono_ns()
        w = self._cfg.watchdog_s
        try:
            hl_meta = await asyncio.wait_for(self._hl.meta(), timeout=w)
            books = await asyncio.wait_for(self._lighter.order_books(), timeout=w)
            details = await asyncio.wait_for(self._lighter.order_book_details(), timeout=w)
        except (TimeoutError, Exception) as e:
            self._w.write(records.gap(FEED, "both", self._seq.next(), t_ms=t_ms,
                                      mono_ns=mono_ns, from_ms=t_ms, to_ms=self._clock.now_ms(),
                                      reason=f"sweep_error:{type(e).__name__}"))
            return []
        payload = {"hl_universe": hl_meta["universe"], "lighter_order_books": books,
                   "lighter_details": details}
        self._w.write(records.envelope(FEED, "both", self._seq.next(), payload, t_ms=t_ms,
                                       mono_ns=mono_ns, trigger="sweep"))
        self._health.record(FEED, "sweep", t_ms)
        self._health.expect(FEED, "sweep", 3600 // self._cfg.universe_s)

        live = [b for b in books
                if b.get("status") == "active" and b.get("market_type", "perp") == "perp"]
        active = sorted(b["market_id"] for b in live)
        # Coverage expectations come from the VENUES' market lists, not from the records we
        # write. This is the spec's Done-when verbatim, and it is what makes a silently dead
        # subscription visible: the market keeps its expectation and its received count falls.
        self._health.expect_markets(
            "hl_state", [m["name"] for m in hl_meta["universe"] if not m.get("isDelisted")],
            3600 // self._cfg.hl_poll_s)
        self._health.expect_markets(
            "lighter_state", [b.get("symbol") or str(b["market_id"]) for b in live],
            3600 // self._cfg.lighter_heartbeat_s)

        if self._on_markets is not None:
            self._on_markets(active)
        return active

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            started = self._clock.mono_ns()
            await self.cycle()
            elapsed_s = (self._clock.mono_ns() - started) / 1e9
            await self._clock.sleep(max(0.0, self._cfg.universe_s - elapsed_s))
```

- [ ] **Step 4: Run, expect pass**

Run: `uv run pytest tests/recorder/test_universe.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/fundr/recorder/feeds/universe.py tests/recorder/test_universe.py
git commit -m "feat: universe sweep with Lighter funding parameters"
```

---

### Task 7: Supervisor — and the test for Phase 1's actual bug

**Files:**
- Create: `src/fundr/recorder/supervisor.py`, `src/fundr/recorder/__main__.py`
- Test: `tests/recorder/test_supervisor.py`

**Interfaces:**
- Consumes: every feed class, `Config`, `Clock`, `HourlyWriter`, `Health`.
- Produces:
  - `supervisor.Supervisor(cfg, clock=None, feeds=None)` with `async run(stop: asyncio.Event) -> None`, `.health: Health`, `.writers: dict[str, HourlyWriter]`.
  - `supervisor.supervise(name: str, factory: Callable[[], Awaitable[None]], stop, clock, on_restart=None)` — runs a coroutine, restarts it after `restart_delay_s` if it raises, until `stop` is set.
  - `__main__.main()` — builds the real feeds from `Config.from_env()`, installs SIGTERM/SIGINT handling, runs until stopped, flushes writers.
- The supervisor owns: starting the three feeds, wiring `UniverseFeed.on_markets` → `LighterStateFeed.set_markets`, writing `health.json` every 60 s, and flushing writers every `cfg.upload_s`.

- [ ] **Step 1: Write the failing test** `tests/recorder/test_supervisor.py`

```python
import asyncio
import gzip
import json

from fundr.recorder.config import Clock, Config
from fundr.recorder.feeds.hl_state import HLStateFeed
from fundr.recorder.feeds.lighter_state import LighterStateFeed
from fundr.recorder.health import Health
from fundr.recorder.supervisor import Supervisor, supervise
from fundr.recorder.writer import HourlyWriter


def _rows(root, feed):
    out = []
    for p in sorted((root / feed).rglob("*.jsonl.gz")):
        out += [json.loads(x) for x in gzip.open(p, "rt").read().splitlines()]
    return out


async def test_supervise_restarts_a_failing_task():
    calls = {"n": 0}
    stop = asyncio.Event()

    async def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("boom")
        stop.set()

    clock = Clock(sleep=lambda s: asyncio.sleep(0))
    await asyncio.wait_for(supervise("flaky", flaky, stop, clock), timeout=5)
    assert calls["n"] == 3


class HangingHL:
    """A REST client whose call never returns — Phase 1's 84-minute read, in miniature."""

    def __init__(self):
        self.started = 0
        self.finished = 0

    async def meta_and_asset_ctxs(self):
        self.started += 1
        await asyncio.sleep(3600)
        self.finished += 1

    async def predicted_fundings(self):
        return []


class FakeSocket:
    """A Lighter socket that keeps delivering changing premia."""

    def __init__(self):
        self.n = 0

    async def send(self, _payload):
        return None

    async def recv(self):
        await asyncio.sleep(0.02)
        self.n += 1
        return json.dumps({"channel": "market_stats:1", "timestamp": self.n,
                           "type": "update/market_stats",
                           "market_stats": {"market_id": 1, "symbol": "BTC",
                                            "premium": f"0.{self.n:06d}",
                                            "current_funding_rate": "0.0012"}})

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


async def test_a_hung_rest_feed_never_stops_the_websocket_feed(tmp_path):
    """Phase 1's actual failure, against the real feed classes rather than two synthetic
    coroutines: one hung REST call silently ended websocket recording. Lighter records must
    keep appearing throughout, AND the hung poll must be cancelled by its own watchdog."""
    cfg = Config(root=tmp_path, hl_poll_s=1, lighter_heartbeat_s=1, watchdog_s=1)
    clock = Clock()                                   # real time: this test is about timing
    health = Health(clock)
    writers = {n: HourlyWriter(tmp_path, n) for n in ("hl_state", "lighter_state")}

    hl_client = HangingHL()
    hl = HLStateFeed(cfg, writers["hl_state"], health, clock, hl_client)
    socket = FakeSocket()
    lighter = LighterStateFeed(cfg, writers["lighter_state"], health, clock,
                               connect=lambda: socket)
    lighter.set_markets([1])
    lighter.mark_subscribed([1])

    stop = asyncio.Event()
    sup = Supervisor(cfg, clock, writers=writers, health=health,
                     feeds={"hl_state": lambda: hl.run(stop),
                            "lighter_state": lambda: lighter.run(stop)})
    task = asyncio.create_task(sup.run(stop))
    await asyncio.sleep(2.5)
    stop.set()
    await asyncio.wait_for(task, timeout=10)

    lighter_rows = _rows(tmp_path, "lighter_state")
    assert len(lighter_rows) >= 5, "the websocket feed must keep recording while REST is hung"
    assert any(r["payload"]["premium"] != lighter_rows[0]["payload"]["premium"]
               for r in lighter_rows), "the records must be fresh, not one value repeated"

    hl_rows = _rows(tmp_path, "hl_state")
    assert hl_client.started >= 2, "the poll must be retried, not left hanging forever"
    assert hl_client.finished == 0
    assert any(r.get("reason") == "watchdog_timeout" for r in hl_rows), \
        "the hung poll must be cancelled by its watchdog and recorded as a gap"


async def test_health_file_is_written(tmp_path):
    stop = asyncio.Event()

    async def idle():
        await asyncio.sleep(3600)

    sup = Supervisor(Config(root=tmp_path), feeds={"hl_state": idle})
    task = asyncio.create_task(sup.run(stop))
    await asyncio.sleep(0.2)
    stop.set()
    await asyncio.wait_for(task, timeout=5)
    body = json.loads((tmp_path / "health.json").read_text())
    assert body["status"] in {"ok", "degraded", "broken"}
```

- [ ] **Step 2: Run, expect failure**

Run: `uv run pytest tests/recorder/test_supervisor.py -v`
Expected: FAIL — `ModuleNotFoundError: fundr.recorder.supervisor`.

- [ ] **Step 3: Implement** `src/fundr/recorder/supervisor.py`

```python
"""Task supervision. Each feed runs as its own task: one dying, hanging or erroring must never
stop another — that coupling is what cost Phase 1 95 minutes of Lighter data."""
import asyncio
import contextlib
from collections.abc import Awaitable, Callable

from fundr.recorder.config import Clock, Config
from fundr.recorder.health import Health
from fundr.recorder.writer import HourlyWriter

RESTART_DELAY_S = 5
HEALTH_INTERVAL_S = 60


async def supervise(name: str, factory: Callable[[], Awaitable[None]], stop: asyncio.Event,
                    clock: Clock, on_restart: Callable[[str], None] | None = None) -> None:
    while not stop.is_set():
        try:
            await factory()
            return
        except asyncio.CancelledError:
            raise
        except Exception:  # deliberate: a 24/7 service restarts its parts rather than dying
            if on_restart is not None:
                on_restart(name)
            await clock.sleep(RESTART_DELAY_S)


class Supervisor:
    def __init__(self, cfg: Config, clock: Clock | None = None,
                 feeds: dict[str, Callable[[], Awaitable[None]]] | None = None,
                 writers: dict[str, HourlyWriter] | None = None,
                 health: Health | None = None):
        self._cfg = cfg
        self._clock = clock or Clock()
        self.health = health or Health(self._clock)
        self.writers = writers or {
            name: HourlyWriter(cfg.root, name) for name in ("hl_state", "lighter_state", "universe")
        }
        self._feeds = feeds or {}

    def set_feeds(self, feeds: dict[str, Callable[[], Awaitable[None]]]) -> None:
        self._feeds = feeds

    async def _health_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            self.health.write(self._cfg.root / "health.json", self._clock.now_ms())
            await self._clock.sleep(HEALTH_INTERVAL_S)

    async def _flush_loop(self, stop: asyncio.Event) -> None:
        """Flush FIRST, then sleep. Sleeping first means nothing on disk is complete — and so
        nothing is uploadable — for the first `upload_s`, which turns the spec's "instance
        loss bounded to minutes" into an hour-plus on a freshly started recorder."""
        while not stop.is_set():
            for w in self.writers.values():
                w.flush()
            await self._clock.sleep(self._cfg.upload_s)

    async def run(self, stop: asyncio.Event) -> None:
        tasks = [asyncio.create_task(
            supervise(name, factory, stop, self._clock, self.health.reconnect))
            for name, factory in self._feeds.items()]
        tasks.append(asyncio.create_task(self._health_loop(stop)))
        tasks.append(asyncio.create_task(self._flush_loop(stop)))
        await stop.wait()
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t
        self.health.write(self._cfg.root / "health.json", self._clock.now_ms())
        for w in self.writers.values():
            w.close()
```

- [ ] **Step 4: Implement** `src/fundr/recorder/__main__.py`

```python
"""Entry point: `python -m fundr.recorder`."""
import asyncio
import signal

from fundr.recorder.clients import AsyncHLInfo, AsyncLighterREST
from fundr.recorder.config import Clock, Config
from fundr.recorder.feeds.hl_state import HLStateFeed
from fundr.recorder.feeds.lighter_state import LighterStateFeed
from fundr.recorder.feeds.universe import UniverseFeed
from fundr.recorder.health import Health
from fundr.recorder.supervisor import Supervisor
from fundr.recorder.writer import HourlyWriter


async def _run() -> None:
    cfg = Config.from_env()
    clock = Clock()
    health = Health(clock)
    writers = {n: HourlyWriter(cfg.root, n) for n in ("hl_state", "lighter_state", "universe")}
    # One client per task, never shared: a shared connection pool is a shared queue, and a
    # stalled universe sweep sitting on it would block the HL poll — the coupling the spec's
    # architecture rules out ("each task with its own client and connection pool").
    hl_client = AsyncHLInfo()
    uni_hl_client = AsyncHLInfo()
    uni_lighter_client = AsyncLighterREST()
    hl = HLStateFeed(cfg, writers["hl_state"], health, clock, hl_client)
    lighter = LighterStateFeed(cfg, writers["lighter_state"], health, clock)
    universe = UniverseFeed(cfg, writers["universe"], health, clock, uni_hl_client,
                            uni_lighter_client, on_markets=lighter.set_markets)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    # The universe sweep runs first so the Lighter feed knows what to subscribe to.
    await universe.cycle()
    sup = Supervisor(cfg, clock, writers=writers, health=health,
                     feeds={"hl_state": lambda: hl.run(stop),
                            "lighter_state": lambda: lighter.run(stop),
                            "universe": lambda: universe.run(stop)})
    await sup.run(stop)
    await hl_client.aclose()
    await uni_hl_client.aclose()
    await uni_lighter_client.aclose()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run, expect pass**

Run: `uv run pytest tests/recorder -v`
Expected: all recorder tests pass — 41 in total across Tasks 1–7 (7 + 11 + 3 + 5 + 9 + 3 + 3).

- [ ] **Step 6: Commit**

```bash
git add src/fundr/recorder/supervisor.py src/fundr/recorder/__main__.py tests/recorder/test_supervisor.py
git commit -m "feat: recorder supervisor, entry point, and the hung-REST regression test"
```

---

### Task 8: S3 upload and retention

**Files:**
- Create: `src/fundr/recorder/upload.py`
- Test: `tests/recorder/test_upload.py`

**Interfaces:**
- Consumes: `Config`.
- Produces: `upload.Uploader(cfg, s3=None)` with:
  - `.pending() -> list[Path]` — every part whose size differs from the manifest, **including the current hour's**, plus `health.json`
  - `.sync() -> list[Path]` — uploads and records each upload in a local manifest
  - `.prune(now_ms: int) -> list[Path]` — deletes local parts older than `local_retention_days` **only if** the manifest says they are in S3
  - `.key_for(path: Path) -> str` — `recorder/v1/<relative path>`
- Uploads are idempotent: a part already in the manifest with the same size is skipped.

**Two corrections to the obvious implementation:**
- **`health.json` must be uploaded on every sync.** A `*.jsonl.gz` glob misses it, and the spec's Done-when ("`health.json` is readable from S3 without logging into the instance") and Task 10 Step 6 both require it in the bucket. It is small, it changes every minute, and it is the *only* channel the no-alerting decision leaves open.
- **The current hour is uploaded too.** Skipping it because the hour has not closed means instance loss costs up to ~70 minutes, not the spec's "minutes". Gzip members concatenate and the writer closes a complete member on every flush, so the current part is always a valid file — the whole reason that design exists. Each upload overwrites the same key with a longer prefix-identical file, which is harmless and versioned. `force` therefore has no remaining meaning and is gone.

- [ ] **Step 1: Write the failing test** `tests/recorder/test_upload.py`

```python
import json
from pathlib import Path

from fundr.recorder.config import Config
from fundr.recorder.upload import Uploader


class FakeS3:
    def __init__(self):
        self.put: list[tuple[str, str]] = []

    def upload_file(self, Filename, Bucket, Key):  # noqa: N803 - boto3's signature
        self.put.append((Bucket, Key))


def _part(root: Path, feed: str, date: str, hour: str, body: bytes = b"x") -> Path:
    p = root / feed / f"date={date}" / f"hour={hour}" / f"{feed}-{date}T{hour}.jsonl.gz"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(body)
    return p


def test_uploads_pending_parts_with_versioned_keys(tmp_path):
    s3 = FakeS3()
    u = Uploader(Config(root=tmp_path, bucket="b"), s3)
    p = _part(tmp_path, "hl_state", "2026-09-20", "10")
    sent = u.sync()
    assert sent == [p]
    assert s3.put == [("b", "recorder/v1/hl_state/date=2026-09-20/hour=10/hl_state-2026-09-20T10.jsonl.gz")]


def test_already_uploaded_parts_are_skipped(tmp_path):
    s3 = FakeS3()
    u = Uploader(Config(root=tmp_path, bucket="b"), s3)
    _part(tmp_path, "hl_state", "2026-09-20", "10")
    u.sync()
    assert u.sync() == []
    assert len(s3.put) == 1


def test_a_grown_part_is_re_uploaded(tmp_path):
    s3 = FakeS3()
    u = Uploader(Config(root=tmp_path, bucket="b"), s3)
    p = _part(tmp_path, "lighter_state", "2026-09-20", "10", b"x")
    u.sync()
    p.write_bytes(b"xxxx")
    assert u.sync() == [p]
    assert len(s3.put) == 2


def test_the_current_hour_is_uploaded_too(tmp_path):
    """Skipping the open hour costs up to ~70 minutes of data on instance loss; the spec
    says minutes. The writer closes a complete gzip member on every flush precisely so the
    open part is always a readable file."""
    import datetime as dt
    s3 = FakeS3()
    u = Uploader(Config(root=tmp_path, bucket="b"), s3)
    now = dt.datetime.now(dt.UTC)
    p = _part(tmp_path, "lighter_state", now.strftime("%Y-%m-%d"), now.strftime("%H"))
    assert u.sync() == [p]


def test_health_json_is_uploaded_every_sync(tmp_path):
    """The spec's Done-when: health.json readable from S3 without logging into the instance.
    A *.jsonl.gz glob never matches it."""
    s3 = FakeS3()
    u = Uploader(Config(root=tmp_path, bucket="b"), s3)
    health = tmp_path / "health.json"
    health.write_text('{"status": "ok"}')
    assert health in u.sync()
    assert ("b", "recorder/v1/health.json") in s3.put
    health.write_text('{"status": "degraded", "n": 1}')
    assert health in u.sync()


def test_prune_only_deletes_old_confirmed_parts(tmp_path):
    s3 = FakeS3()
    cfg = Config(root=tmp_path, bucket="b", local_retention_days=7)
    u = Uploader(cfg, s3)
    old = _part(tmp_path, "hl_state", "2026-09-01", "10")
    new = _part(tmp_path, "hl_state", "2026-09-20", "10")
    unconfirmed = _part(tmp_path, "hl_state", "2026-09-02", "11")
    u._manifest[str(old.relative_to(tmp_path))] = old.stat().st_size
    u._manifest[str(new.relative_to(tmp_path))] = new.stat().st_size
    removed = u.prune(now_ms=int(__import__("datetime").datetime(2026, 9, 20).timestamp() * 1000))
    assert removed == [old]
    assert new.exists() and unconfirmed.exists()


def test_no_bucket_configured_is_a_no_op(tmp_path):
    u = Uploader(Config(root=tmp_path, bucket=None), FakeS3())
    _part(tmp_path, "hl_state", "2026-09-20", "10")
    assert u.sync() == []
```

- [ ] **Step 2: Run, expect failure**

Run: `uv run pytest tests/recorder/test_upload.py -v`
Expected: FAIL — `ModuleNotFoundError: fundr.recorder.upload`.

- [ ] **Step 3: Implement** `src/fundr/recorder/upload.py`

```python
"""Upload closed parts to S3 and prune local copies. Runs as its own systemd timer so a slow
or failing upload can never touch the recording loop."""
import json
from datetime import UTC, datetime
from pathlib import Path

from fundr.recorder.config import Config

KEY_PREFIX = "recorder/v1"
MANIFEST_NAME = "uploaded.json"
HEALTH_NAME = "health.json"


class Uploader:
    def __init__(self, cfg: Config, s3=None):
        self._cfg = cfg
        self._s3 = s3
        self._manifest_path = cfg.root / MANIFEST_NAME
        self._manifest: dict[str, int] = {}
        if self._manifest_path.exists():
            self._manifest = json.loads(self._manifest_path.read_text())

    def _client(self):
        if self._s3 is None:
            import boto3  # imported lazily so tests never need boto3 configured
            self._s3 = boto3.client("s3")
        return self._s3

    def key_for(self, path: Path) -> str:
        return f"{KEY_PREFIX}/{path.relative_to(self._cfg.root).as_posix()}"

    def pending(self) -> list[Path]:
        """Everything whose size differs from the manifest, INCLUDING the current hour's
        part and health.json.

        The open hour is deliberately not skipped: the writer closes a complete gzip member
        on every flush, so the part on disk is always readable, and skipping it would put up
        to ~70 minutes of data at risk on instance loss when the spec allows minutes.
        health.json is not a *.jsonl.gz file and would never be globbed, yet it is the only
        thing the no-alerting decision leaves to look at from outside the instance."""
        out = []
        for p in sorted(self._cfg.root.rglob("*.jsonl.gz")):
            rel = p.relative_to(self._cfg.root).as_posix()
            if self._manifest.get(rel) == p.stat().st_size:
                continue
            out.append(p)
        health = self._cfg.root / HEALTH_NAME
        if health.exists():
            out.append(health)    # always: it is rewritten every minute and is tiny
        return out

    def sync(self) -> list[Path]:
        if not self._cfg.bucket:
            return []
        sent = []
        for p in self.pending():
            self._client().upload_file(Filename=str(p), Bucket=self._cfg.bucket,
                                       Key=self.key_for(p))
            self._manifest[p.relative_to(self._cfg.root).as_posix()] = p.stat().st_size
            sent.append(p)
        if sent:
            self._manifest_path.write_text(json.dumps(self._manifest))
        return sent

    def prune(self, now_ms: int) -> list[Path]:
        cutoff = now_ms - self._cfg.local_retention_days * 86_400_000
        removed = []
        for p in sorted(self._cfg.root.rglob("*.jsonl.gz")):
            rel = p.relative_to(self._cfg.root).as_posix()
            if self._manifest.get(rel) != p.stat().st_size:
                continue  # never delete what S3 has not confirmed
            day = rel.split("date=")[1][:10]
            day_ms = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000
            if day_ms < cutoff:
                p.unlink()
                removed.append(p)
        return removed


def main() -> None:
    cfg = Config.from_env()
    u = Uploader(cfg)
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    sent = u.sync()
    removed = u.prune(now_ms)
    print(f"uploaded {len(sent)} parts, pruned {len(removed)}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run, expect pass**

Run: `uv run pytest tests/recorder/test_upload.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/fundr/recorder/upload.py tests/recorder/test_upload.py
git commit -m "feat: S3 upload with idempotent manifest and confirmed-only pruning"
```

---

### Task 9: Deploy artefacts and the one-hour local run

**Files:**
- Create: `deploy/fundr-recorder.service`, `deploy/fundr-upload.service`, `deploy/fundr-upload.timer`, `deploy/bootstrap.sh`, `deploy/README.md`
- Create: `scripts/local_soak.py`

**Interfaces:**
- Produces: `scripts/local_soak.py` — runs the real recorder against the real venues into a temp root for N minutes, then prints per-feed record counts, per-market coverage, gap records, peak RSS, and the health status. Exit code 1 if any check fails.

- [ ] **Step 1: Write the systemd units**

`deploy/fundr-recorder.service`:

```ini
[Unit]
Description=fundr funding recorder
After=network-online.target chronyd.service
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=fundr
WorkingDirectory=/opt/fundr
Environment=FUNDR_RECORDER_DATA=/var/lib/fundr
EnvironmentFile=/etc/fundr/recorder.env
ExecStart=/opt/fundr/.venv/bin/python -m fundr.recorder
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

`StartLimitIntervalSec=0` is deliberate: with systemd's default start limit the service can give up
permanently after repeated crashes, which is the one failure the health-file-only choice cannot
catch. It belongs in **`[Unit]`**, not `[Service]` — the start-rate limiter is a unit property. In
`[Service]` systemd logs "Unknown key name" and silently keeps the default limit, so the setting
reads as present while doing nothing, which is worse than omitting it.

`deploy/fundr-upload.service`:

```ini
[Unit]
Description=fundr recorder S3 upload

[Service]
Type=oneshot
User=fundr
WorkingDirectory=/opt/fundr
Environment=FUNDR_RECORDER_DATA=/var/lib/fundr
EnvironmentFile=/etc/fundr/recorder.env
ExecStart=/opt/fundr/.venv/bin/python -m fundr.recorder.upload
```

`deploy/fundr-upload.timer`:

```ini
[Unit]
Description=fundr recorder S3 upload every 10 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=10min
AccuracySec=30s

[Install]
WantedBy=timers.target
```

- [ ] **Step 2: Write** `deploy/bootstrap.sh`

Configuration arrives in the **environment**, not as positional arguments: user-data runs the
script with no argv, so `${1:?usage}` aborted the whole bootstrap before it did anything (Task 10
passes these as exported variables).

```bash
#!/usr/bin/env bash
# Provision a clean Amazon Linux 2023 arm64 instance to run the recorder.
# Run as root on the instance. Idempotent.
#
# Configuration comes from the environment (user-data exports it), NOT from positional args:
#   FUNDR_BUCKET   required, the S3 bucket
#   FUNDR_GIT_SHA  required, the commit to deploy
#   FUNDR_REPO     required, git URL (ssh form when using a deploy key)
#   FUNDR_INSTANCE_ID  optional, defaults to the EC2 instance id
set -euo pipefail

: "${FUNDR_BUCKET:?FUNDR_BUCKET must be set in the environment}"
: "${FUNDR_GIT_SHA:?FUNDR_GIT_SHA must be set in the environment}"
: "${FUNDR_REPO:?FUNDR_REPO must be set in the environment}"

TOKEN="$(curl -sX PUT http://169.254.169.254/latest/api/token \
  -H 'X-aws-ec2-metadata-token-ttl-seconds: 60')"
INSTANCE_ID="${FUNDR_INSTANCE_ID:-$(curl -s -H "X-aws-ec2-metadata-token: ${TOKEN}" \
  http://169.254.169.254/latest/meta-data/instance-id)}"

dnf -y update
dnf -y install git chrony
systemctl enable --now chronyd            # requirement 4: trustworthy wall clock

id fundr &>/dev/null || useradd --system --home /opt/fundr --shell /usr/sbin/nologin fundr
install -d -o fundr -g fundr /opt/fundr /var/lib/fundr /etc/fundr /opt/fundr/.cache

curl -LsSf https://astral.sh/uv/install.sh | UV_INSTALL_DIR=/usr/local/bin sh

cat >/etc/fundr/recorder.env <<ENV
FUNDR_BUCKET=${FUNDR_BUCKET}
FUNDR_GIT_SHA=${FUNDR_GIT_SHA}
FUNDR_INSTANCE_ID=${INSTANCE_ID}
ENV
chown fundr:fundr /etc/fundr/recorder.env
chmod 640 /etc/fundr/recorder.env

# Read-only deploy key, written by user-data (Task 10). Absent for a public repo.
if [[ -f /etc/fundr/deploy_key ]]; then
  chown fundr:fundr /etc/fundr/deploy_key
  chmod 600 /etc/fundr/deploy_key
  export GIT_SSH_COMMAND="ssh -i /etc/fundr/deploy_key -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
fi

# -H is required: without it sudo keeps HOME=/root, and uv writes its cache and venv metadata
# into a directory the fundr user cannot read, which fails on AL2023. UV_CACHE_DIR pins the
# cache somewhere the service account owns.
run_as_fundr() {
  sudo -u fundr -H -E env "UV_CACHE_DIR=/opt/fundr/.cache" "GIT_SSH_COMMAND=${GIT_SSH_COMMAND:-ssh}" "$@"
}

run_as_fundr git -C /opt/fundr rev-parse --git-dir &>/dev/null || \
  run_as_fundr git clone "${FUNDR_REPO}" /opt/fundr
run_as_fundr git -C /opt/fundr fetch --all
run_as_fundr git -C /opt/fundr checkout "${FUNDR_GIT_SHA}"
run_as_fundr /usr/local/bin/uv --directory /opt/fundr sync --no-dev

install -m 644 /opt/fundr/deploy/fundr-recorder.service /etc/systemd/system/
install -m 644 /opt/fundr/deploy/fundr-upload.service /etc/systemd/system/
install -m 644 /opt/fundr/deploy/fundr-upload.timer /etc/systemd/system/
systemd-analyze verify /etc/systemd/system/fundr-recorder.service \
                       /etc/systemd/system/fundr-upload.service \
                       /etc/systemd/system/fundr-upload.timer
systemctl daemon-reload
systemctl enable --now fundr-recorder.service
systemctl enable --now fundr-upload.timer
systemctl --no-pager status fundr-recorder.service
```

- [ ] **Step 2b: Verify the units before the instance ever sees them**

`systemd-analyze verify` is in the script above, but run it locally too (any Linux box, or the
instance via SSM) — it is what catches a directive in the wrong section, which is exactly how
`StartLimitIntervalSec` was silently ignored in `[Service]`:

Run: `systemd-analyze verify deploy/fundr-recorder.service deploy/fundr-upload.service deploy/fundr-upload.timer`
Expected: no output. Any "Unknown key name" line is a defect, not a warning to live with.

- [ ] **Step 3: Write** `scripts/local_soak.py`

```python
"""Run the real recorder against the real venues for N minutes, then judge the result.
This is the gate before deploying: it proves the feeds, timers and health accounting work
against live data, and measures memory so the instance can be sized honestly."""
import argparse
import asyncio
import gzip
import json
import resource
import tempfile
from collections import Counter
from pathlib import Path

from fundr.recorder.clients import AsyncHLInfo, AsyncLighterREST
from fundr.recorder.config import Clock, Config
from fundr.recorder.feeds.hl_state import HLStateFeed
from fundr.recorder.feeds.lighter_state import LighterStateFeed
from fundr.recorder.feeds.universe import UniverseFeed
from fundr.recorder.health import Health
from fundr.recorder.supervisor import Supervisor
from fundr.recorder.writer import HourlyWriter

ap = argparse.ArgumentParser()
ap.add_argument("--minutes", type=int, default=60)
ap.add_argument("--root", default=None)
args = ap.parse_args()

root = Path(args.root or tempfile.mkdtemp(prefix="fundr-soak-"))


async def main() -> int:
    cfg = Config(root=root)
    clock = Clock()
    health = Health(clock)
    writers = {n: HourlyWriter(root, n) for n in ("hl_state", "lighter_state", "universe")}
    # One client per task, exactly as __main__ does it: the soak must exercise production's
    # isolation, not a shared pool that production does not have.
    hl_client, uni_hl_client, uni_lighter_client = AsyncHLInfo(), AsyncHLInfo(), AsyncLighterREST()
    hl = HLStateFeed(cfg, writers["hl_state"], health, clock, hl_client)
    lighter = LighterStateFeed(cfg, writers["lighter_state"], health, clock)
    universe = UniverseFeed(cfg, writers["universe"], health, clock, uni_hl_client,
                            uni_lighter_client, on_markets=lighter.set_markets)
    await universe.cycle()
    stop = asyncio.Event()
    sup = Supervisor(cfg, clock, writers=writers, health=health,
                     feeds={"hl_state": lambda: hl.run(stop),
                            "lighter_state": lambda: lighter.run(stop),
                            "universe": lambda: universe.run(stop)})
    task = asyncio.create_task(sup.run(stop))
    await asyncio.sleep(args.minutes * 60)
    stop.set()
    await task
    await hl_client.aclose()
    await uni_hl_client.aclose()
    await uni_lighter_client.aclose()

    rows: Counter = Counter()
    triggers: Counter = Counter()
    gaps = []
    zero_msgs = 0
    for p in root.rglob("*.jsonl.gz"):
        for line in gzip.open(p, "rt").read().splitlines():
            r = json.loads(line)
            rows[r["feed"]] += 1
            if r.get("type") == "gap":
                gaps.append(r)
            else:
                triggers[r.get("trigger")] += 1
                if r["feed"] == "lighter_state" and r.get("trigger") == "heartbeat" \
                        and r.get("n_msgs") == 0:
                    zero_msgs += 1
    peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)
    health_body = json.loads((root / "health.json").read_text())

    reasons = Counter(g["reason"] for g in gaps)
    # `not gaps` is over-strict against two live venues for an hour: a single dropped
    # websocket frame or one slow REST response is normal operation, and the recorder is
    # designed to record it and carry on. What must never appear is evidence that the HOST
    # stalled — that is the Phase 1 failure and it invalidates the run.
    fatal = {r: n for r, n in reasons.items() if r in {"suspend_or_hang", "clock_step"}}
    MAX_TRANSIENT_GAPS = 3

    print(f"root: {root}")
    print("records per feed:", dict(rows))
    print("triggers:", dict(triggers))
    print("gap records:", len(gaps), dict(reasons))
    print("lighter heartbeats with zero messages:", zero_msgs)
    print(f"peak RSS: {peak_mb:.0f} MB")
    print("health status:", health_body["status"])

    ok = (rows["hl_state"] > 0 and rows["lighter_state"] > 0 and rows["universe"] > 0
          and not fatal
          and len(gaps) <= MAX_TRANSIENT_GAPS
          and triggers.get("boundary", 0) >= (1 if args.minutes >= 60 else 0)
          and health_body["status"] == "ok")
    if fatal:
        print("FATAL gap reasons (host stall or clock step):", fatal)
    if len(gaps) > MAX_TRANSIENT_GAPS:
        print(f"too many transient gaps: {len(gaps)} > {MAX_TRANSIENT_GAPS}")
    print("SOAK PASS" if ok else "SOAK FAIL")
    return 0 if ok else 1


raise SystemExit(asyncio.run(main()))
```

- [ ] **Step 4: Run the soak for 5 minutes first** (fast feedback)

Run: `uv run python scripts/local_soak.py --minutes 5`
Expected: records for all three feeds, at most a couple of transient gap records and none reading `suspend_or_hang` or `clock_step`, health `ok`. `boundary` triggers are not expected in a 5-minute window unless it spans `HH:59`.

- [ ] **Step 5: Run the full hour, ending after an hour boundary**

Run: `uv run python scripts/local_soak.py --minutes 60`
Expected: `SOAK PASS`; **two `boundary`-triggered records per market** (`HH:59:30` and `HH:59:52`) — if the count is zero the trigger is being mislabelled, which is Task 5's rule 1, not a timing accident; peak RSS recorded for instance sizing. If peak RSS approaches 1 GB, report it — the spec's instance choice depends on this number.

- [ ] **Step 6: Write** `deploy/README.md` with: what each unit does, the environment variables, how to check status (`systemctl status`, `journalctl -u fundr-recorder`), where data lands locally and in S3, and how to read `health.json` from S3.

- [ ] **Step 7: Commit**

```bash
git add deploy scripts/local_soak.py
git commit -m "feat: deploy units, bootstrap script and local soak harness"
```

---

### Task 10: Publish the repo, provision AWS, deploy

**What actually shipped deviates from every AWS/deploy detail below** — no IAM (so no instance
profile, no Session Manager, SSH from one /32 instead), `us-east-1` moved to `eu-central-1`
(Lighter geo-blocks the US), and `deploy/user_data.sh`'s git-clone-with-deploy-key replaced by an
rsync of the working tree (no git credential on the instance). This plan text describes the
original design, not the deployed state; the deployed state, why it differs, and the exact
condition for reverting each deviation are all in
`docs/superpowers/specs/2026-09-20-phase2-recorder-design.md`, "Review corrections, third round"
onward. Read that before touching AWS from this task's instructions.

Every step here spends the user's money or touches their account. **Present each action and its
cost, and get an explicit yes, before running it** — that is the standing rule for this task, from
the spec's decision table, and it applies to the GitHub steps as well as the AWS ones. Use `boto3`
with the default profile; the local `aws` CLI binary is broken on this machine (wrong CPU type),
so do not rely on it.

**The repository has no remote.** `bootstrap.sh` clones from one, so there is nothing to deploy
until that is fixed. The user has chosen a **private GitHub repo**; `gh` is installed and
authenticated as `jerryinyang`. The instance gets **read-only** access — a deploy key — because a
recorder never pushes and an instance credential that could is a liability.

**Files:**
- Create: `deploy/aws_provision.py` (idempotent, prints a plan before acting), `deploy/user_data.sh`, `docs/phase2/recorder_runbook.md`

**Interfaces:**
- Produces: `aws_provision.py` with `plan()` (prints resources and monthly cost, changes nothing), `apply()` (creates what is missing) and `verify()` (re-reads every resource and asserts its configuration). Resources: S3 bucket (private, versioned, lifecycle: current → IA at 90 days, noncurrent expire at 7 days), IAM role + instance profile scoped to that bucket plus `AmazonSSMManagedInstanceCore`, security group with **no inbound rules**, and a `t4g.micro` Amazon Linux 2023 arm64 instance with `deploy/user_data.sh` as user-data.

- [ ] **Step 1: Create the private repo and push** (free; ask first — this publishes the work to GitHub)

Confirm the working tree contains no recorded data, no `.env` and nothing under `auth/`, then:

```bash
git -C /Users/jerryinyang/Trading/fundr status --short
cat .gitignore | grep -E 'data/|\.env|auth/'
gh repo create fundr --private --source=. --remote=origin --push
gh repo view fundr --json visibility,sshUrl -q '.visibility + " " + .sshUrl'
```
Expected: `PRIVATE git@github.com:jerryinyang/fundr.git`. If `--push` reports large files, stop:
the Phase 1 recordings belong in S3 (Step 4), not in git.

- [ ] **Step 2: Mint a read-only deploy key** (free)

```bash
ssh-keygen -t ed25519 -N '' -C 'fundr-recorder deploy key' -f /tmp/fundr_deploy_key
gh repo deploy-key add /tmp/fundr_deploy_key.pub --repo jerryinyang/fundr --title fundr-recorder
gh repo deploy-key list --repo jerryinyang/fundr
```
No `--allow-write`: the key is read-only, which is all the recorder needs. The private half goes
into user-data (Step 3) and never into the repo. A fine-grained PAT with `Contents: read` on this
one repo is an acceptable substitute if the user prefers it; a deploy key is narrower, so prefer
it. Record which was used in the runbook, and note the rotation step there.

- [ ] **Step 3: Write** `deploy/user_data.sh`

User-data runs as root with **no arguments**, which is why `bootstrap.sh` now reads its
configuration from the environment. This file is rendered by `aws_provision.py` (it substitutes
the bucket, the commit and the key) and is never committed with the key in it:

```bash
#!/bin/bash
set -euo pipefail
install -d -m 750 /etc/fundr
cat >/etc/fundr/deploy_key <<'KEY'
${DEPLOY_KEY}
KEY
chmod 600 /etc/fundr/deploy_key

export FUNDR_BUCKET="${BUCKET}"
export FUNDR_GIT_SHA="${GIT_SHA}"
export FUNDR_REPO="git@github.com:jerryinyang/fundr.git"

curl -fsSL -o /tmp/bootstrap.sh \
  --header "Accept: application/vnd.github.raw" "${BOOTSTRAP_URL}" 2>/dev/null || {
  # Private repo: fetch the script with the deploy key instead of over HTTPS.
  GIT_SSH_COMMAND="ssh -i /etc/fundr/deploy_key -o StrictHostKeyChecking=accept-new" \
    git clone --depth 1 "${FUNDR_REPO}" /tmp/fundr-bootstrap
  cp /tmp/fundr-bootstrap/deploy/bootstrap.sh /tmp/bootstrap.sh
}
bash /tmp/bootstrap.sh
```

`aws_provision.py` must template this, never hard-code the key, and must print the rendered
script **with the key redacted** as part of `plan()`.

- [ ] **Step 4: Write** `deploy/aws_provision.py`

It must: read the region from `AWS_REGION` (default `us-east-1`); name resources
`fundr-recorder-*`; refuse to touch anything it did not create (tag everything
`Project=fundr,Component=recorder`); print a plan with per-resource monthly cost; render
`user_data.sh` with the bucket, commit and deploy key; and be safe to re-run. Cost lines to print,
from the spec: instance ~$6.13, public IPv4 ~$3.60, disk ~$0.64, S3 and requests <$1, total
~$8–10/month.

- [ ] **Step 5: Run the plan, show the user, get approval, then apply**

Run: `uv run python deploy/aws_provision.py plan`
Show the output, including the redacted user-data. On an explicit yes:
`uv run python deploy/aws_provision.py apply`.

- [ ] **Step 6: Back up the irreplaceable Phase 1 recording**

This is handoff §8 action 1, still outstanding, and the bucket exists for it. Run it the moment
the bucket exists, before anything else touches the account:

```bash
uv run python -c "
import boto3, pathlib
b = 'REPLACE_WITH_BUCKET'
p = pathlib.Path('/Users/jerryinyang/Trading/fundr/data/phase1/p09/live.jsonl')
s3 = boto3.client('s3')
s3.upload_file(str(p), b, 'phase1/p09/live_2026-09-19.jsonl')
print(s3.head_object(Bucket=b, Key='phase1/p09/live_2026-09-19.jsonl')['ContentLength'],
      'bytes in S3 vs', p.stat().st_size, 'local')"
```
The two sizes must match. Record the result in the runbook.

- [ ] **Step 7: Verify the bucket's lifecycle rules and the role's scope explicitly**

Prose in a plan is not a verified configuration, and both of these are silently-wrong-forever
settings: a missing noncurrent-expiry rule accumulates versions of every 10-minute re-upload
indefinitely (the spec's corrected cost depends on it), and an over-broad role is the one
credential on the instance.

```bash
uv run python -c "
import boto3, json
b = 'REPLACE_WITH_BUCKET'
s3 = boto3.client('s3')
lc = s3.get_bucket_lifecycle_configuration(Bucket=b)['Rules']
print(json.dumps(lc, indent=1, default=str))
assert any(t.get('Days') == 90 and t.get('StorageClass') in {'STANDARD_IA', 'ONEZONE_IA'}
           for r in lc if r['Status'] == 'Enabled' for t in r.get('Transitions', [])), \
    'no current-version transition to IA at 90 days'
assert any(r.get('NoncurrentVersionExpiration', {}).get('NoncurrentDays') == 7
           for r in lc if r['Status'] == 'Enabled'), 'no noncurrent-version expiry at 7 days'
assert s3.get_bucket_versioning(Bucket=b).get('Status') == 'Enabled'
acc = s3.get_public_access_block(Bucket=b)['PublicAccessBlockConfiguration']
assert all(acc.values()), acc
print('bucket OK')"
```

```bash
uv run python -c "
import boto3, json, urllib.parse
iam = boto3.client('iam')
role = 'fundr-recorder-role'
b = 'REPLACE_WITH_BUCKET'
attached = [p['PolicyArn'] for p in iam.list_attached_role_policies(RoleName=role)['AttachedPolicies']]
print('attached:', attached)
assert attached == ['arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore'], attached
for name in iam.list_role_policies(RoleName=role)['PolicyNames']:
    doc = iam.get_role_policy(RoleName=role, PolicyName=name)['PolicyDocument']
    if isinstance(doc, str):
        doc = json.loads(urllib.parse.unquote(doc))
    print(name, json.dumps(doc, indent=1))
    for st in doc['Statement']:
        for res in ([st['Resource']] if isinstance(st['Resource'], str) else st['Resource']):
            assert res.startswith(f'arn:aws:s3:::{b}'), f'role reaches beyond the bucket: {res}'
print('role scoped to the bucket only')"
```
Expected: both print OK. A failure here is a provisioning bug to fix, not a note to file.

- [ ] **Step 8: Bootstrap and confirm the instance**

Via SSM (no SSH, no inbound ports). User-data runs the bootstrap on first boot; check
`/var/log/cloud-init-output.log` if the service is not up. Confirm:
- `systemctl is-active fundr-recorder` → `active`
- `systemctl is-enabled fundr-recorder` → `enabled`
- `systemctl show fundr-recorder -p StartLimitIntervalUSec` → `0` (proof the directive is in the section systemd actually reads)
- `journalctl -u fundr-recorder -n 50` → no repeated restarts
- `/var/lib/fundr/health.json` exists and reads `ok` within 5 minutes

- [ ] **Step 9: Verify a reboot**

Reboot the instance; confirm the service returns on its own and `health.json` recovers to `ok`.
The spec's Done-when requires surviving a reboot.

- [ ] **Step 10: Confirm S3**

After 20 minutes: parts for all three feeds under `recorder/v1/`, **plus `recorder/v1/health.json`**
(Task 8 uploads it on every sync; its absence means the uploader is still globbing only
`*.jsonl.gz`). The current hour's part must be present too, not just closed hours. Verify one
downloaded part opens with `gzip` and its last line parses — this is the check that the "closed
member" design actually produces readable files — and that its filename contains the instance id.

- [ ] **Step 11: Write** `docs/phase2/recorder_runbook.md`: resource names and region, monthly cost,
the GitHub repo and how the deploy key is rotated, how to reach the instance via SSM, how to read
health from S3, how to restart, how to redeploy a new commit, and how to stop everything and what
it costs to leave running.

- [ ] **Step 12: Commit**

```bash
git add deploy/aws_provision.py deploy/user_data.sh docs/phase2/recorder_runbook.md
git commit -m "feat: AWS provisioning for the recorder, and its runbook"
```

---

### Task 11: Day-one validation, both venues

**Files:**
- Create: `scripts/validate_day1.py`
- Create (after run): `docs/phase2/day1_validation.md`

**Interfaces:**
- Consumes: recorded parts (downloaded from S3 or read locally), `fundr.sources.hl_api`,
  `fundr.sources.lighter_api`, `fundr.funding.lighter_formula`, `fundr.analysis`.
- Produces: `validate_day1.py --root <dir>` printing, per venue, the match rate of recorded
  running funding against the venue's own settled rates, and exiting non-zero if a venue fails its
  expectation.
- This script may import polars and `fundr.sources`; it runs on the operator's machine, not on the
  instance.

**Complete hours only, mirroring P7.** A 24-hour download always has a partial first hour (the
recorder started mid-hour) and a partial last hour (the download cut it off). Judged without a
filter those two hours fail by construction and the script prints `LIGHTER FAIL` on a perfectly
healthy recorder — the worst possible outcome, because Step 3 then sends the operator hunting a
bug that is not there. P7 solved the same problem with `lag_s <= 120`: the running premium is
*cumulative*, so a late start does not matter and only a late-enough **last** reading does
([P7](../../phase1/evidence/P7-funding-formulas.md), "coverage filters"). Use the same rule here,
and print how many hours were excluded so the exclusion is never silent.

**Expectations, from Phase 1:**
- **Lighter:** the last recorded `current_funding_rate` in each hour must equal that hour's settled
  rate exactly (P9 found 21/21). Anything below 100% on complete hours means the recorder is
  missing the pre-boundary value — the failure this whole design guards against.
- **Hyperliquid:** the last recorded `funding` must be close to but not equal the settled rate,
  residual ~1e-7 to 1e-6 (P4, P9, P10). An exact match, or a much larger residual, means the feed
  or the capture is wrong.

- [ ] **Step 1: Write** `scripts/validate_day1.py`

```python
"""Day-one correctness: does the recorder's running funding behave as Phase 1 measured?
Lighter must converge exactly on the settlement that closes each hour; HL must not."""
import argparse
import gzip
import json
from pathlib import Path

import polars as pl

from fundr.analysis import attach_settled, epoch_ms, epoch_s, match_stats, reported_tolerance
from fundr.sources.hl_api import HLInfo, funding_history_frame
from fundr.sources.lighter_api import LighterAPI, fundings_frame

ap = argparse.ArgumentParser()
ap.add_argument("--root", required=True, help="directory holding recorder parts")
args = ap.parse_args()
root = Path(args.root)


HOUR_MS = 3_600_000
MAX_LAG_S = 120       # P7's coverage filter: only a late-enough LAST reading invalidates an
                      # hour, because the running premium is cumulative.


def read(feed: str) -> list[dict]:
    out = []
    for p in sorted((root / feed).rglob("*.jsonl.gz")):
        out += [json.loads(x) for x in gzip.open(p, "rt").read().splitlines()]
    return [r for r in out if r.get("type") != "gap"]


def complete_hours(df: pl.DataFrame, label: str) -> pl.DataFrame:
    """Drop hours whose last recorded reading is more than MAX_LAG_S before the boundary.
    Without this the partial first and last hours of any download fail by construction and
    the verdict is a false alarm."""
    df = df.with_columns(
        ((pl.col("hour").dt.epoch("ms") + HOUR_MS - pl.col("last_t_ms")) / 1000).alias("lag_s"))
    kept = df.filter((pl.col("n") >= 5) & (pl.col("lag_s").is_between(0, MAX_LAG_S)))
    print(f"{label}: {len(kept)} complete market-hours kept, "
          f"{len(df) - len(kept)} excluded (n < 5 or lag_s > {MAX_LAG_S}s)")
    return kept


# --- Lighter: last value in each hour must equal that hour's settlement ---------------
li = pl.DataFrame([{"market_id": r["payload"]["market_id"],
                    "symbol": r["payload"].get("symbol"),
                    "t_ms": r["t_ms"],
                    "cfr": float(r["payload"]["current_funding_rate"])}
                   for r in read("lighter_state")])
li = li.with_columns(epoch_ms("t_ms").alias("time"))
li_last = (li.sort("time")
             .group_by("market_id", pl.col("time").dt.truncate("1h").alias("hour"))
             .agg(pl.col("cfr").last().alias("cfr_last"), pl.len().alias("n"),
                  pl.col("t_ms").max().alias("last_t_ms")))
li_last = complete_hours(li_last, "Lighter")

lapi = LighterAPI()
t0, t1 = int(li["t_ms"].min()) // 1000, int(li["t_ms"].max()) // 1000
settled = pl.concat([fundings_frame(lapi.fundings(m, "1h", t0 - 3600, t1 + 7200, 30), m)
                     for m in li["market_id"].unique().to_list()])
ltol = reported_tolerance(settled["rate_str"].to_list())
j = attach_settled(li_last,
                   settled.select("market_id", "settle_time",
                                  pl.col("signed_rate").alias("settled")),
                   "market_id").drop_nulls("settled")
lighter_stats = match_stats(j["cfr_last"], j["settled"], ltol)
print("Lighter last-in-hour vs closing settlement:", lighter_stats)

# --- Hyperliquid: close but never exact -----------------------------------------------
hl_rows = read("hl_state")
hl = pl.DataFrame([{"coin": r["payload"]["coin"], "t_ms": r["t_ms"],
                    "funding": float(r["payload"]["ctx"]["funding"])}
                   for r in hl_rows if r["payload"]["ctx"].get("funding") is not None])
hl = hl.with_columns(epoch_ms("t_ms").alias("time"))
hl_last = (hl.sort("time")
             .group_by("coin", pl.col("time").dt.truncate("1h").alias("hour"))
             .agg(pl.col("funding").last().alias("funding_last"), pl.len().alias("n"),
                  pl.col("t_ms").max().alias("last_t_ms")))
hl_last = complete_hours(hl_last, "HL")
api = HLInfo()
coins = hl["coin"].unique().to_list()[:20]  # 20 coins is plenty for the check
hl_settled = funding_history_frame(
    [r for c in coins for r in api.funding_history(c, int(hl["t_ms"].min()) - 3_600_000,
                                                   int(hl["t_ms"].max()) + 7_200_000)])
htol = reported_tolerance(hl_settled["funding_rate_str"].to_list())
hj = attach_settled(hl_last,
                    hl_settled.select("coin", "settle_time",
                                      pl.col("funding_rate").alias("settled")),
                    "coin").drop_nulls("settled")
hl_stats = match_stats(hj["funding_last"], hj["settled"], htol)
resid = (hj["funding_last"] - hj["settled"]).abs()
print("HL last-in-hour vs closing settlement:", hl_stats)
print("HL residual: median", float(resid.median()), "max", float(resid.max()))

lighter_ok = lighter_stats["n"] > 0 and lighter_stats["rate"] == 1.0
hl_ok = hl_stats["n"] > 0 and 1e-9 < float(resid.median()) < 1e-4
print("LIGHTER", "PASS" if lighter_ok else "FAIL")
print("HL", "PASS" if hl_ok else "FAIL")
raise SystemExit(0 if (lighter_ok and hl_ok) else 1)
```

- [ ] **Step 2: Run it after 24 hours of recording**

Download a day of parts from S3 to a local directory, then:
Run: `uv run python scripts/validate_day1.py --root <downloaded-dir>`
Expected: `LIGHTER PASS` (100% exact on complete hours) and `HL PASS` (median residual between
1e-9 and 1e-4, no exact match). The excluded-hours lines should show roughly two exclusions per
market — the partial first and last hour of the download — and no more. Many more than that is a
coverage problem to investigate before reading the verdict.

- [ ] **Step 3: If Lighter is below 100%**, do not paper over it. Check, in order: whether boundary
snapshots exist for the failing hours (`trigger == "boundary"` — there should be two per market
per hour, at `HH:59:30` and `HH:59:52`), whether those hours survived the `lag_s <= 120` filter,
and whether the last `change` record precedes the boundary. Report which, and fix the capture rule
before continuing — this is the single behaviour the recorder exists to get right.

- [ ] **Step 4: Write** `docs/phase2/day1_validation.md` with the command, the output, the verdict
per venue, and any hours excluded and why.

- [ ] **Step 5: Commit**

```bash
git add scripts/validate_day1.py docs/phase2/day1_validation.md
git commit -m "feat: day-one validation of recorded funding against both venues"
```

---

## After Task 11

The spec's Done-when also requires seven consecutive days at `status: ok` and the Lighter formula
re-validation (≥100 off-baseline market-hours, more than one day, at least one negative-settling
hour, plus a multiplier ≠ 1 market if one exists). Those are elapsed-time gates, not tasks: check
`health.json` daily, and re-run `probes/p07_lighter_formula_check.py` against the recorder's
premium history once a week of data exists. Only then is Phase 2a complete and the backfill
(Phase 2b) worth starting.
