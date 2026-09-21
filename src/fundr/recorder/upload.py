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
