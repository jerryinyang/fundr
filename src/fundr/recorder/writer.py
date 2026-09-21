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
