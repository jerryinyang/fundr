#!/usr/bin/env python3
"""Idempotent AWS provisioning for the fundr recorder.

Two subcommands:

    plan    re-read every resource, print what exists, what is missing and the
            monthly cost. Changes nothing.
    apply   create whatever ``plan`` reported missing, then print the plan again.

Deliberate deviation from the spec's AWS footprint (recorded as *Review
corrections, third round* in ``docs/superpowers/specs/2026-09-20-phase2-recorder-design.md``):
**this account denies every IAM action**, so the instance gets no instance
profile.  Without a profile there is no Session Manager, so shell access is SSH
from a single IP, and there is no S3 write credential, so the recorder records
to its local EBS volume with ``FUNDR_BUCKET`` empty.  The bucket is still
provisioned (it holds the Phase 1 recording) and the deviation is reverted by
attaching a role later -- see the runbook.

The script refuses to touch anything it did not create: every resource is tagged
``Project=fundr,Component=recorder`` and an existing resource that lacks those
tags is reported as a conflict rather than adopted or modified.

Region: ``AWS_REGION``, defaulting to ``eu-central-1``.  The recorder was moved
out of ``us-east-1`` because Lighter's edge refuses websocket handshakes from US
jurisdictions -- the user's call, recorded in the spec's *Review corrections,
fourth round*.  ``find_instance`` matches on tags within one region only, so
running this against the wrong region would report "nothing exists" and cheerfully
launch a duplicate; keep the default pointed at where the recorder actually runs.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

# The recorder lives in eu-central-1 because Lighter refuses websocket connections from US
# jurisdictions -- see the runbook's "Why Frankfurt" and the spec's fourth-round deviation.
# This default is deliberate: a bare ``apply`` must not quietly provision a SECOND instance
# back in us-east-1, which would both cost money and record no Lighter data.
REGION = os.environ.get("AWS_REGION", "eu-central-1")

# EC2 key pairs do not cross regions, so each region needs its own; the name therefore tracks
# the region by default, and both it and the local private-key path stay overridable.
DEFAULT_KEY_NAMES = {"eu-central-1": "fundr-recorder-eu", "us-east-1": "fundr-recorder"}
AUTH_DIR = Path(os.environ.get("FUNDR_AUTH_DIR", "/Users/jerryinyang/Trading/fundr/auth"))
KEY_NAME = os.environ.get("FUNDR_KEY_NAME") or DEFAULT_KEY_NAMES.get(REGION, f"fundr-recorder-{REGION}")
KEY_PATH = Path(os.environ.get("FUNDR_KEY_PATH", str(AUTH_DIR / f"{KEY_NAME}.pem")))
SG_NAME = "fundr-recorder-sg"
# EC2 rejects apostrophes in group descriptions (allowed set is a-zA-Z0-9. _-:/()#,@[]+=&;{}!$*).
SG_DESCRIPTION = "fundr recorder: SSH from the operator IP only, all egress"
INSTANCE_NAME = "fundr-recorder"
INSTANCE_TYPE = "t4g.micro"
VOLUME_GB = 8
VOLUME_TYPE = "gp3"
AMI_SSM_PARAMETER = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
AMI_NAME_GLOB = "al2023-ami-2023.*-kernel-6.1-arm64"
BUCKET = "fundr-recorder-801242831140-us-east-1"

TAGS = {"Project": "fundr", "Component": "recorder"}
TAG_LIST = [{"Key": k, "Value": v} for k, v in TAGS.items()]

# Monthly cost at 730 hours, on-demand.  Per-region because t4g and gp3 both cost more in
# Frankfurt than in N. Virginia; the public IPv4 charge ($0.005/hr) is the same everywhere.
# Rates: t4g.micro $/hr, gp3 $/GB-month.
REGION_RATES = {
    "us-east-1": (0.0084, 0.08),
    "eu-central-1": (0.0092, 0.0952),
}


def costs(region: str) -> list[tuple[str, float]]:
    """Monthly cost lines for ``region``, falling back to us-east-1 rates if unknown."""
    hourly, gb_month = REGION_RATES.get(region, REGION_RATES["us-east-1"])
    return [
        (f"{INSTANCE_TYPE} on-demand, 730 h x ${hourly}", round(hourly * 730, 2)),
        ("public IPv4 address, 730 h x $0.005", 3.65),
        (f"{VOLUME_GB} GB {VOLUME_TYPE} root volume x ${gb_month}/GB", round(gb_month * VOLUME_GB, 2)),
        ("S3 storage + requests (bucket is us-east-1; uploads disabled)", 0.10),
        ("key pair, security group", 0.00),
    ]


def tagged(resource_tags: list[dict] | None) -> bool:
    """True when a resource carries both of our tags."""
    have = {t["Key"]: t["Value"] for t in (resource_tags or [])}
    return all(have.get(k) == v for k, v in TAGS.items())


def my_ip() -> str:
    """The operator's current public IP, as a /32 CIDR."""
    with urllib.request.urlopen("https://checkip.amazonaws.com", timeout=10) as r:
        return r.read().decode().strip() + "/32"


@dataclass
class Plan:
    region: str = REGION
    cidr: str = ""
    ami: str = ""
    ami_source: str = ""
    vpc: str = ""
    exists: list[str] = field(default_factory=list)
    create: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    ids: dict[str, str] = field(default_factory=dict)


def resolve_ami(ssm, ec2) -> tuple[str, str]:
    """Latest Amazon Linux 2023 arm64 AMI id, and how it was resolved.

    The public SSM parameter is the documented route, but ``ssm:GetParameter``
    is itself denied for these credentials (the same blanket denial that removed
    the instance role), so fall back to ``ec2:DescribeImages`` filtered to
    Amazon-owned AL2023 arm64 images and take the newest.
    """
    try:
        return ssm.get_parameter(Name=AMI_SSM_PARAMETER)["Parameter"]["Value"], "ssm parameter"
    except ClientError as e:
        if e.response["Error"]["Code"] not in {"AccessDeniedException", "ParameterNotFound"}:
            raise
    images = ec2.describe_images(
        Owners=["amazon"],
        Filters=[
            {"Name": "name", "Values": [AMI_NAME_GLOB]},
            {"Name": "state", "Values": ["available"]},
            {"Name": "architecture", "Values": ["arm64"]},
        ],
    )["Images"]
    if not images:
        raise SystemExit("no Amazon Linux 2023 arm64 AMI found")
    newest = max(images, key=lambda i: i["CreationDate"])
    return newest["ImageId"], f"describe-images ({newest['Name']})"


def default_vpc(ec2) -> str:
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"]
    if not vpcs:
        raise SystemExit("no default VPC in this region; provisioning stops here")
    return vpcs[0]["VpcId"]


def find_key(ec2) -> dict | None:
    try:
        return ec2.describe_key_pairs(KeyNames=[KEY_NAME])["KeyPairs"][0]
    except ClientError as e:
        if e.response["Error"]["Code"] == "InvalidKeyPair.NotFound":
            return None
        raise


def find_sg(ec2, vpc: str) -> dict | None:
    got = ec2.describe_security_groups(
        Filters=[{"Name": "group-name", "Values": [SG_NAME]}, {"Name": "vpc-id", "Values": [vpc]}]
    )["SecurityGroups"]
    return got[0] if got else None


def find_instance(ec2) -> dict | None:
    """Our recorder instance, if one is alive.  Matched on tags, not on name alone."""
    got = ec2.describe_instances(
        Filters=[
            {"Name": "tag:Project", "Values": ["fundr"]},
            {"Name": "tag:Component", "Values": ["recorder"]},
            {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]},
        ]
    )["Reservations"]
    instances = [i for r in got for i in r["Instances"]]
    return instances[0] if instances else None


def build_plan(ec2, ssm, s3, cidr: str) -> Plan:
    p = Plan(cidr=cidr)
    p.vpc = default_vpc(ec2)
    p.ami, p.ami_source = resolve_ami(ssm, ec2)

    key = find_key(ec2)
    if key is None:
        p.create.append(f"key pair {KEY_NAME} (private key -> {KEY_PATH}, mode 0600)")
    elif not tagged(key.get("Tags")):
        p.conflicts.append(f"key pair {KEY_NAME} exists but is not tagged {TAGS}; not touching it")
    else:
        p.exists.append(f"key pair {KEY_NAME} ({key['KeyPairId']}, fingerprint {key['KeyFingerprint'][:17]}...)")
        p.ids["key_pair"] = key["KeyPairId"]

    sg = find_sg(ec2, p.vpc)
    if sg is None:
        p.create.append(f"security group {SG_NAME} in {p.vpc}: inbound tcp/22 from {cidr}, all egress")
    elif not tagged(sg.get("Tags")):
        p.conflicts.append(f"security group {SG_NAME} exists but is not tagged {TAGS}; not touching it")
    else:
        rules = [
            f"{r.get('IpProtocol')}/{r.get('FromPort')} from " + ",".join(x["CidrIp"] for x in r.get("IpRanges", []))
            for r in sg["IpPermissions"]
        ]
        p.exists.append(f"security group {SG_NAME} ({sg['GroupId']}) inbound: {rules or 'none'}")
        p.ids["security_group"] = sg["GroupId"]
        if not any(cidr in r for r in rules):
            p.create.append(f"  + inbound tcp/22 from {cidr} on {sg['GroupId']} (current IP not yet allowed)")

    inst = find_instance(ec2)
    if inst is None:
        p.create.append(
            f"instance {INSTANCE_NAME}: {INSTANCE_TYPE}, AL2023 arm64 {p.ami}, "
            f"{VOLUME_GB} GB {VOLUME_TYPE}, no instance profile"
        )
    else:
        p.exists.append(
            f"instance {INSTANCE_NAME} ({inst['InstanceId']}, {inst['InstanceType']}, "
            f"{inst['State']['Name']}, ami {inst['ImageId']}, "
            f"public ip {inst.get('PublicIpAddress', '-')}, "
            f"profile {inst.get('IamInstanceProfile', {}).get('Arn', 'none')})"
        )
        p.ids["instance"] = inst["InstanceId"]
        if inst.get("PublicIpAddress"):
            p.ids["public_ip"] = inst["PublicIpAddress"]

    try:
        s3.head_bucket(Bucket=BUCKET)
        p.exists.append(f"bucket {BUCKET} (pre-existing; uploads disabled, FUNDR_BUCKET empty)")
    except ClientError as e:
        p.conflicts.append(f"bucket {BUCKET} not reachable: {e.response['Error']['Code']}")

    return p


def print_plan(p: Plan) -> None:
    print(f"region:       {p.region}")
    print(f"default vpc:  {p.vpc}")
    print(f"al2023 arm64: {p.ami}  [{p.ami_source}]")
    print(f"ssh cidr:     {p.cidr}")
    print(f"tags:         {','.join(f'{k}={v}' for k, v in TAGS.items())}")
    print(f"iam:          none -- no instance profile (account denies IAM; see the spec)")
    print()
    print("exists:")
    for line in p.exists or ["  (nothing yet)"]:
        print(f"  {line}" if not line.startswith("  ") else line)
    print()
    print("to create:")
    for line in p.create or ["  (nothing)"]:
        print(f"  {line}" if not line.startswith("  ") else line)
    if p.conflicts:
        print()
        print("CONFLICTS (nothing will be modified):")
        for line in p.conflicts:
            print(f"  {line}")
    print()
    lines = costs(p.region)
    print(f"monthly cost ({p.region}):")
    for label, amount in lines:
        print(f"  ${amount:5.2f}  {label}")
    print(f"  ------")
    print(f"  ${sum(a for _, a in lines):5.2f}  total per month")


def do_apply(ec2, ssm, s3, cidr: str) -> Plan:
    p = build_plan(ec2, ssm, s3, cidr)
    if p.conflicts:
        raise SystemExit("refusing to apply while conflicts are outstanding:\n  " + "\n  ".join(p.conflicts))

    # --- key pair -------------------------------------------------------
    if "key_pair" not in p.ids:
        if KEY_PATH.exists():
            raise SystemExit(
                f"{KEY_PATH} exists but AWS has no key pair {KEY_NAME}: "
                "a stale private key would be silently unusable. Move it aside and re-run."
            )
        created = ec2.create_key_pair(
            KeyName=KEY_NAME,
            KeyType="ed25519",
            TagSpecifications=[{"ResourceType": "key-pair", "Tags": TAG_LIST}],
        )
        KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
        # Create with 0600 from the outset; the material never exists world-readable.
        fd = os.open(KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "w") as fh:
            fh.write(created["KeyMaterial"])
        p.ids["key_pair"] = created["KeyPairId"]
        print(f"created key pair {KEY_NAME} ({created['KeyPairId']}); private key saved 0600 (not printed)")

    # --- security group -------------------------------------------------
    if "security_group" not in p.ids:
        sg = ec2.create_security_group(
            GroupName=SG_NAME,
            Description=SG_DESCRIPTION,
            VpcId=p.vpc,
            TagSpecifications=[{"ResourceType": "security-group", "Tags": TAG_LIST}],
        )
        p.ids["security_group"] = sg["GroupId"]
        print(f"created security group {SG_NAME} ({sg['GroupId']})")

    sg_id = p.ids["security_group"]
    try:
        ec2.authorize_security_group_ingress(
            GroupId=sg_id,
            IpPermissions=[
                {
                    "IpProtocol": "tcp",
                    "FromPort": 22,
                    "ToPort": 22,
                    "IpRanges": [{"CidrIp": cidr, "Description": "operator IP (R12)"}],
                }
            ],
        )
        print(f"authorised tcp/22 from {cidr} on {sg_id}")
    except ClientError as e:
        if e.response["Error"]["Code"] != "InvalidPermission.Duplicate":
            raise
        print(f"tcp/22 from {cidr} already authorised on {sg_id}")

    # --- instance -------------------------------------------------------
    if "instance" not in p.ids:
        run = ec2.run_instances(
            ImageId=p.ami,
            InstanceType=INSTANCE_TYPE,
            MinCount=1,
            MaxCount=1,
            KeyName=KEY_NAME,
            SecurityGroupIds=[sg_id],
            MetadataOptions={"HttpTokens": "required", "HttpEndpoint": "enabled"},
            BlockDeviceMappings=[
                {
                    "DeviceName": "/dev/xvda",
                    "Ebs": {
                        "VolumeSize": VOLUME_GB,
                        "VolumeType": VOLUME_TYPE,
                        "DeleteOnTermination": True,
                        "Encrypted": True,
                    },
                }
            ],
            TagSpecifications=[
                {"ResourceType": "instance", "Tags": TAG_LIST + [{"Key": "Name", "Value": INSTANCE_NAME}]},
                {"ResourceType": "volume", "Tags": TAG_LIST + [{"Key": "Name", "Value": INSTANCE_NAME}]},
            ],
        )
        iid = run["Instances"][0]["InstanceId"]
        p.ids["instance"] = iid
        print(f"launched {iid}; waiting for it to run...")
        ec2.get_waiter("instance_running").wait(InstanceIds=[iid])
        desc = ec2.describe_instances(InstanceIds=[iid])["Reservations"][0]["Instances"][0]
        p.ids["public_ip"] = desc.get("PublicIpAddress", "")
        print(f"{iid} is running at {p.ids['public_ip']}")

    return build_plan(ec2, ssm, s3, cidr)


def _check(ok_list: list[bool], name: str, cond: bool, detail: str = "") -> bool:
    mark = "PASS" if cond else "FAIL"
    suffix = f" -- {detail}" if detail else ""
    print(f"[{mark}] {name}{suffix}")
    ok_list.append(cond)
    return cond


def verify(ec2, s3) -> bool:
    """Re-read every resource this script (and the S3 setup that predates it) claims to have
    created, and ASSERT its configuration -- not just that it exists, but that it is still
    shaped the way the design requires. Every check prints PASS or FAIL; nothing here changes
    anything. This is the brief's `verify` subcommand: until now the bucket assertions in
    particular only existed as pasted `aws` output in a task report, which nobody re-runs."""
    results: list[bool] = []
    vpc = default_vpc(ec2)

    sg = find_sg(ec2, vpc)
    if _check(results, "security group exists", sg is not None):
        perms = sg["IpPermissions"]
        if _check(results, "security group has exactly one ingress rule", len(perms) == 1,
                  f"found {len(perms)}"):
            r = perms[0]
            ranges = r.get("IpRanges", [])
            _check(results, "the one ingress rule is tcp/22",
                  r.get("IpProtocol") == "tcp" and r.get("FromPort") == 22 and r.get("ToPort") == 22,
                  f"{r.get('IpProtocol')}/{r.get('FromPort')}-{r.get('ToPort')}")
            _check(results, "the one ingress rule is from exactly one /32",
                  len(ranges) == 1 and ranges[0].get("CidrIp", "").endswith("/32"),
                  str([x.get("CidrIp") for x in ranges]))

    inst = find_instance(ec2)
    if _check(results, "instance exists", inst is not None):
        profile = inst.get("IamInstanceProfile")
        _check(results, "instance has no instance profile", not profile, f"found {profile}")
        vol_ids = [b["Ebs"]["VolumeId"] for b in inst.get("BlockDeviceMappings", []) if "Ebs" in b]
        if _check(results, "instance has a root volume attached", bool(vol_ids)):
            for v in ec2.describe_volumes(VolumeIds=vol_ids)["Volumes"]:
                _check(results, f"volume {v['VolumeId']} is encrypted", bool(v.get("Encrypted")))

    try:
        vers = s3.get_bucket_versioning(Bucket=BUCKET).get("Status")
        _check(results, "bucket versioning is Enabled", vers == "Enabled", f"got {vers!r}")
    except ClientError as e:
        _check(results, "bucket versioning readable", False, str(e))

    try:
        pab = s3.get_public_access_block(Bucket=BUCKET)["PublicAccessBlockConfiguration"]
        _check(results, "bucket public-access-block is all-true", all(pab.values()), str(pab))
    except ClientError as e:
        _check(results, "bucket public-access-block readable", False, str(e))

    try:
        rules = s3.get_bucket_lifecycle_configuration(Bucket=BUCKET)["Rules"]
        _check(results, "bucket has exactly two lifecycle rules", len(rules) == 2, f"found {len(rules)}")
        has_noncurrent_expiry = any(r.get("NoncurrentVersionExpiration", {}).get("NoncurrentDays")
                                   for r in rules)
        has_ia_transition = any(
            t.get("StorageClass") == "STANDARD_IA" for r in rules for t in r.get("Transitions", []))
        _check(results, "one lifecycle rule expires noncurrent versions", has_noncurrent_expiry)
        _check(results, "one lifecycle rule transitions current objects to STANDARD_IA", has_ia_transition)
    except ClientError as e:
        _check(results, "bucket lifecycle configuration readable", False, str(e))

    print()
    passed, total = sum(results), len(results)
    print(f"{passed}/{total} checks passed")
    return all(results)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["plan", "apply", "verify"])
    ap.add_argument("--cidr", default=None, help="SSH source CIDR; defaults to this host's public IP /32")
    ap.add_argument("--json", action="store_true", help="also print resource ids as JSON")
    args = ap.parse_args(argv)

    session = boto3.session.Session(region_name=REGION)
    ec2 = session.client("ec2")
    ssm = session.client("ssm")
    s3 = session.client("s3")

    if args.action == "verify":
        return 0 if verify(ec2, s3) else 1

    cidr = args.cidr or my_ip()
    if args.action == "plan":
        p = build_plan(ec2, ssm, s3, cidr)
        print_plan(p)
    else:
        p = do_apply(ec2, ssm, s3, cidr)
        print()
        print("--- after apply ---")
        print_plan(p)

    if args.json:
        print()
        print(json.dumps(p.ids, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
