"""Hyperliquid requester-pays S3 archives. Every call is charged to the user's AWS account,
so each one is estimated and logged first, and refused if it would pass BUDGET_USD."""
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
EGRESS_USD_PER_GB = 0.09


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

    def _charge(self, op: str, target: str, nbytes: int = 0) -> None:
        usd = REQUEST_USD + nbytes / 1e9 * EGRESS_USD_PER_GB
        if self.spent_usd() + usd > BUDGET_USD:
            raise BudgetExceeded(f"{op} {target}: would reach ${self.spent_usd() + usd:.3f} > ${BUDGET_USD}")
        self._ledger.parent.mkdir(parents=True, exist_ok=True)
        with self._ledger.open("a") as f:
            f.write(json.dumps({"op": op, "target": target, "bytes": nbytes, "usd": usd}) + "\n")

    def list_prefixes(self, bucket: str, prefix: str) -> list[str]:
        out, token = [], None
        while True:
            self._charge("list", f"{bucket}/{prefix}")
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
            self._charge("list", f"{bucket}/{prefix}")
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
        self._charge("head", f"{bucket}/{key}")
        size = self._s3.head_object(Bucket=bucket, Key=key, RequestPayer="requester")["ContentLength"]
        self._charge("get", f"{bucket}/{key}", size)
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
