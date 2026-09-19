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
    params = {"Bucket": "b", "Key": "k", "RequestPayer": "requester"}
    stub.add_response("head_object", {"ContentLength": 20 * 10**9}, params)  # 20 GB ≈ $1.80
    with stub, pytest.raises(hl_archive.BudgetExceeded):
        arc.download("b", "k")


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
