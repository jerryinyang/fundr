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
# Spec thresholds (docs/superpowers/specs/2026-09-20-phase2-recorder-design.md, "status
# thresholds"): ok tolerates an open gap under 5 minutes old; degraded is 5-30 minutes;
# broken is over 30 minutes OR no write in the feed's stale window. Sixth-round deviation:
# `status()` used to degrade on ANY open gap regardless of age, so a gap that opened and
# closed within seconds -- one bad HL poll cycle, say -- could still read `degraded` for a
# whole health-write interval if the write landed inside that window, silently costing a day
# of the seven-consecutive-days-at-ok gate for a blip the design explicitly calls "ok". See
# the spec's sixth review-correction round.
OK_GAP_MS = 5 * 60_000
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
        self._cadence_ms: dict[str, float] = {}

    def set_cadence(self, feed: str, seconds: float) -> None:
        """Register how often `feed` is expected to write, in seconds. A flat staleness
        threshold cannot serve every feed: universe writes once every 300 s by design, so a
        global 300 s stale threshold marks it "broken" between every single sweep, by
        construction, not because anything is wrong. Each registered feed's own stale
        threshold becomes `max(STALE_WRITE_MS, 3 * cadence)` — three missed cycles, not zero
        margin against one. Feeds that never register a cadence keep today's flat default."""
        self._cadence_ms[feed] = seconds * 1000

    def _stale_threshold_ms(self, feed: str) -> float:
        cadence = self._cadence_ms.get(feed)
        if cadence is None:
            return STALE_WRITE_MS
        return max(STALE_WRITE_MS, 3 * cadence)

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
        stale = [(now - t) > self._stale_threshold_ms(feed)
                 for feed, t in self._last_write.items()]
        for dq in self._reconnects.values():
            self._trim(dq, now)
        if (coverages and min(coverages) < BROKEN_COVERAGE) or \
           any(g > DEGRADED_GAP_MS for g in gaps) or any(stale):
            return "broken"
        if (coverages and min(coverages) < OK_COVERAGE) or any(g > OK_GAP_MS for g in gaps) or \
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
