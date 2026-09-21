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
