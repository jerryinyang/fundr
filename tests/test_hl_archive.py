import io

import boto3
import lz4.frame
import pytest
from botocore.response import StreamingBody
from botocore.stub import Stubber

from fundr.sources import hl_archive


def _archive(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNDR_DATA", str(tmp_path))
    s3 = boto3.client("s3", region_name="ap-northeast-1", aws_access_key_id="x", aws_secret_access_key="x")
    return hl_archive.HLArchive(s3=s3), Stubber(s3)


def test_download_writes_file_and_ledger(tmp_path, monkeypatch):
    arc, stub = _archive(tmp_path, monkeypatch)
    data = lz4.frame.compress(b"time,coin,funding\n2023-01-01T00:00:00,BTC,0.0000125\n")
    params = {"Bucket": "hyperliquid-archive", "Key": "asset_ctxs/20230101.csv.lz4", "RequestPayer": "requester"}
    stub.add_response("head_object", {"ContentLength": len(data)}, params)
    stub.add_response("get_object", {"Body": StreamingBody(io.BytesIO(data), len(data))}, params)
    with stub:
        path = arc.download("hyperliquid-archive", "asset_ctxs/20230101.csv.lz4")
    df = hl_archive.read_csv_lz4(path)
    assert df.columns == ["time", "coin", "funding"]
    assert arc.spent_usd() > 0


def test_download_refuses_over_budget(tmp_path, monkeypatch):
    arc, stub = _archive(tmp_path, monkeypatch)
    # Priced against the real archive bucket (us-east-1, $0.09/GB): 40 GB ≈ $3.60. Deliberately
    # several times any cap this project would plausibly set -- at 20 GB the fixture is $1.80,
    # which a $2.00 Phase 2b cap would have swallowed, leaving this test asserting nothing.
    params = {"Bucket": hl_archive.ARCHIVE_BUCKET, "Key": "k", "RequestPayer": "requester"}
    stub.add_response("head_object", {"ContentLength": 40 * 10**9}, params)
    with stub, pytest.raises(hl_archive.BudgetExceeded):
        arc.download(hl_archive.ARCHIVE_BUCKET, "k")


def test_egress_is_priced_per_bucket():
    # Verified 2026-09-21 with unauthenticated HEADs reading `x-amz-bucket-region`: the archive
    # is us-east-1 and the node-data bucket is ap-northeast-1. One global rate cannot price both.
    assert hl_archive.egress_usd_per_gb(hl_archive.ARCHIVE_BUCKET) == 0.09
    assert hl_archive.egress_usd_per_gb(hl_archive.NODE_BUCKET) == 0.114
    # An unknown bucket is priced at the dearer region: the guard must never under-bill.
    assert hl_archive.egress_usd_per_gb("something-else") == 0.114


def test_ledger_prices_each_bucket_at_its_own_rate(tmp_path, monkeypatch):
    size = 10**9  # 1 GB from each bucket
    charged = {}
    for bucket in (hl_archive.ARCHIVE_BUCKET, hl_archive.NODE_BUCKET):
        arc, stub = _archive(tmp_path / bucket, monkeypatch)
        data = b"x" * 8
        params = {"Bucket": bucket, "Key": "k", "RequestPayer": "requester"}
        stub.add_response("head_object", {"ContentLength": size}, params)
        stub.add_response("get_object", {"Body": StreamingBody(io.BytesIO(data), len(data))},
                          params)
        with stub:
            arc.download(bucket, "k")
        charged[bucket] = arc.spent_usd()
    assert charged[hl_archive.ARCHIVE_BUCKET] == pytest.approx(0.09 + 2 * hl_archive.REQUEST_USD)
    assert charged[hl_archive.NODE_BUCKET] == pytest.approx(0.114 + 2 * hl_archive.REQUEST_USD)


def test_list_keys_uses_requester_pays(tmp_path, monkeypatch):
    arc, stub = _archive(tmp_path, monkeypatch)
    stub.add_response(
        "list_objects_v2",
        {"Contents": [{"Key": "asset_ctxs/20230101.csv.lz4", "Size": 10}], "IsTruncated": False},
        {"Bucket": "hyperliquid-archive", "Prefix": "asset_ctxs/", "RequestPayer": "requester", "MaxKeys": 1000},
    )
    with stub:
        keys = arc.list_keys("hyperliquid-archive", "asset_ctxs/")
    assert keys[0]["key"] == "asset_ctxs/20230101.csv.lz4"
