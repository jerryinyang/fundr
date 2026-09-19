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
