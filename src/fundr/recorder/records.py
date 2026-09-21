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
