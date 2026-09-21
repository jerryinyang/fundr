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
