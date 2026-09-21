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
