"""Hyperliquid requester-pays S3 archives. Every call is charged to the user's AWS account,
so each one is estimated and logged first, and refused if it would pass BUDGET_USD.

The two buckets are in DIFFERENT regions, so egress is priced per bucket. Determined
2026-09-21 by unauthenticated HEAD requests, reading the `x-amz-bucket-region` response header:

    hyperliquid-archive   -> us-east-1       ($0.09/GB outbound)
    hl-mainnet-node-data  -> ap-northeast-1  ($0.114/GB outbound)

A redirecting endpoint is not evidence of region. Requesting `hyperliquid-archive` through the
ap-northeast-1 endpoint answers `301 Moved Permanently`, and an earlier revision read that
redirect as "the bucket is in Tokyo" and mispriced the archive at $0.114/GB. The 301 itself
carries `x-amz-bucket-region: us-east-1`; the header is the evidence, the endpoint is not. The
same redirect is why the client below defaults to `region="ap-northeast-1"` and still reads the
us-east-1 bucket without ever failing."""
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
# Outbound list price per bucket, by the bucket's own region (see the module docstring). AWS's
# 100 GB/month free outbound allowance may make the actual invoice $0; the guard counts list
# price on purpose.
EGRESS_USD_PER_GB = {
    ARCHIVE_BUCKET: 0.09,   # us-east-1
    NODE_BUCKET: 0.114,     # ap-northeast-1
}
# An unknown bucket is priced at the dearer of the two: over-estimating stops early, and the
# guard exists to stop early.
DEFAULT_EGRESS_USD_PER_GB = 0.114


def egress_usd_per_gb(bucket: str) -> float:
    return EGRESS_USD_PER_GB.get(bucket, DEFAULT_EGRESS_USD_PER_GB)


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

    def _charge(self, op: str, bucket: str, target: str, nbytes: int = 0) -> None:
        usd = REQUEST_USD + nbytes / 1e9 * egress_usd_per_gb(bucket)
        if self.spent_usd() + usd > BUDGET_USD:
            raise BudgetExceeded(f"{op} {target}: would reach ${self.spent_usd() + usd:.3f} > ${BUDGET_USD}")
        self._ledger.parent.mkdir(parents=True, exist_ok=True)
        with self._ledger.open("a") as f:
            f.write(json.dumps({"op": op, "bucket": bucket, "target": target,
                                "bytes": nbytes, "usd": usd}) + "\n")

    def list_prefixes(self, bucket: str, prefix: str) -> list[str]:
        out, token = [], None
        while True:
            self._charge("list", bucket, f"{bucket}/{prefix}")
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
            self._charge("list", bucket, f"{bucket}/{prefix}")
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
        self._charge("head", bucket, f"{bucket}/{key}")
        size = self._s3.head_object(Bucket=bucket, Key=key, RequestPayer="requester")["ContentLength"]
        self._charge("get", bucket, f"{bucket}/{key}", size)
        body = self._s3.get_object(Bucket=bucket, Key=key, RequestPayer="requester")["Body"].read()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
        return dest


def decode_lz4(path: Path) -> bytes:
    return lz4.frame.decompress(Path(path).read_bytes())


def read_csv_lz4(path: Path) -> pl.DataFrame:
    return pl.read_csv(io.BytesIO(decode_lz4(path)), infer_schema_length=10000)


def parse_time(df: pl.DataFrame) -> pl.DataFrame:
    """asset_ctxs `time` column → Datetime(ms), naive UTC. Extend here if P1 finds another format."""
    dtype = df.schema["time"]
    if dtype == pl.String:
        # Real archive format observed in P1: "2023-05-20T02:50:04Z" (whole-second UTC, always 20 chars).
        return df.with_columns(pl.col("time").str.to_datetime("%Y-%m-%dT%H:%M:%SZ", time_unit="ms"))
    if dtype.is_integer():
        return df.with_columns(pl.from_epoch("time", time_unit="ms").dt.cast_time_unit("ms"))
    raise ValueError(f"unexpected time dtype {dtype}; inspect and extend parse_time")
