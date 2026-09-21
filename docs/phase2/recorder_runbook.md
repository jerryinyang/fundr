# Recorder runbook

Operating notes for the always-on funding recorder deployed on AWS. Written 2026-09-21 when the
instance was provisioned.

## What is deployed

| Resource | Id / value |
|---|---|
| Region | `us-east-1` |
| Instance | `i-005b76078218b1256` — `t4g.micro`, Amazon Linux 2023 arm64 |
| AMI | `ami-007d8fad70c3dae04` (`al2023-ami-2023.12.20260918.0-kernel-6.1-arm64`) |
| Public IP | `54.85.161.66` — **dynamic, not an Elastic IP** (see *Stop, start, terminate*) |
| Root volume | 8 GB `gp3`, encrypted, delete-on-termination |
| Security group | `sg-0decca39cf155fcbd` (`fundr-recorder-sg`) in `vpc-0b802587c98109e4f` |
| Key pair | `fundr-recorder` (`key-0000c12d336be8d99`, ed25519) |
| Private key | `/Users/jerryinyang/Trading/fundr/auth/fundr-recorder.pem`, mode 0600 — `auth/` is gitignored |
| S3 bucket | `fundr-recorder-801242831140-us-east-1` — private, versioned, lifecycle: noncurrent expire 7 d, current → IA 90 d |
| Tags | `Project=fundr`, `Component=recorder` on instance, volume, security group and key pair |
| Code on the box | `/opt/fundr`, owned by the `fundr` system user |
| Data on the box | `/var/lib/fundr` |

Everything is created and re-checked by `deploy/aws_provision.py` (`plan` changes nothing,
`apply` creates what is missing; both are safe to re-run).

## Monthly cost

| Item | $/month |
|---|---|
| `t4g.micro` on demand, 730 h × $0.0084 | 6.13 |
| Public IPv4 address, 730 h × $0.005 | 3.65 |
| 8 GB gp3 root volume × $0.08/GB | 0.64 |
| S3 storage and requests (bucket holds the Phase 1 file; uploads currently disabled) | ~0.10 |
| Key pair, security group | 0.00 |
| **Total** | **~$10.52** |

Leaving it running costs that every month whether or not anyone reads the data. Stopping the
instance removes the instance and IPv4 charges (~$9.78) and leaves the ~$0.64 volume charge, which
is what keeps the recording. There is no free tier credit assumed in these numbers.

## Two deliberate deviations from the design

The AWS account **denies every IAM action** for these credentials (`iam:CreateRole`,
`iam:ListInstanceProfiles`, and even `ssm:GetParameter` on public parameters, all
`AccessDenied`). So:

1. **The instance has no instance profile**, therefore no Session Manager, therefore **SSH** from
   one IP with key-only auth.
2. **The recorder writes to local disk only.** `FUNDR_BUCKET` is deliberately **empty** in
   `/etc/fundr/recorder.env`, so the upload timer fires every 10 minutes and does nothing. The
   only copy of the recording is the instance's EBS volume. At ~30 MB/day the 8 GB volume is
   months of headroom, and the volume survives reboots and stop/starts — but **an instance
   termination destroys the data**.

Both are reversible; see *To finish the S3 setup* at the bottom. The reasoning is recorded in
`docs/superpowers/specs/2026-09-20-phase2-recorder-design.md`, *Review corrections, third round*.

## SSH in

```bash
ssh -i /Users/jerryinyang/Trading/fundr/auth/fundr-recorder.pem ec2-user@54.85.161.66
```

The security group allows inbound TCP 22 **from one address only** — the operator's public IP as a
/32, `193.19.207.103/32` at provisioning time. Password authentication is off in the AMI, so the
key file is the only way in. Never commit it, never copy it to another machine you do not control.

**When your IP changes** (new network, ISP re-assignment, VPN on or off), SSH will simply hang and
time out. Re-authorise:

```bash
cd /Users/jerryinyang/Trading/fundr           # or the worktree
uv run python deploy/aws_provision.py plan    # prints your current IP and whether it is allowed
uv run python deploy/aws_provision.py apply   # adds the missing tcp/22 rule for it
```

`apply` only ever adds; old rules accumulate. Prune stale ones occasionally:

```bash
uv run python -c "
import boto3
ec2 = boto3.client('ec2', region_name='us-east-1')
sg = ec2.describe_security_groups(GroupIds=['sg-0decca39cf155fcbd'])['SecurityGroups'][0]
print([r['IpRanges'] for r in sg['IpPermissions']])"
# then, for each CIDR you no longer use:
uv run python -c "
import boto3
boto3.client('ec2', region_name='us-east-1').revoke_security_group_ingress(
    GroupId='sg-0decca39cf155fcbd',
    IpPermissions=[{'IpProtocol':'tcp','FromPort':22,'ToPort':22,
                    'IpRanges':[{'CidrIp':'OLD.IP.HERE/32'}]}])"
```

## Read health

```bash
ssh -i .../fundr-recorder.pem ec2-user@54.85.161.66 'sudo cat /var/lib/fundr/health.json' | jq .
```

`status` is `ok`, `degraded` or `broken`, judged over a trailing 60-minute window. Per feed:
`coverage`, `last_write_ms`/`last_event_ms`, `reconnects_window`, `open_gap_from_ms`. A freshly
started recorder reports `null` coverage for the first minutes — that is expected, not a fault.
Expect `ok` within ~5–6 minutes of a start.

Data on disk:

```bash
ssh ... 'sudo find /var/lib/fundr -name "*.jsonl.gz" | tail; sudo du -sh /var/lib/fundr'
```

Files are `/var/lib/fundr/<feed>/<YYYY>/<MM>/<DD>/<HH>.jsonl.gz` for the three feeds
`hl_state`, `lighter_state`, `universe`.

## Restart, logs, status

```bash
sudo systemctl status fundr-recorder
sudo systemctl restart fundr-recorder
sudo journalctl -u fundr-recorder -n 100 --no-pager
sudo journalctl -u fundr-recorder -f            # live
sudo systemctl list-timers fundr-upload.timer
sudo journalctl -u fundr-upload -n 20 --no-pager  # "no bucket configured" while S3 is off
```

The service is `Restart=always`, `RestartSec=10`, with `StartLimitIntervalSec=0` in `[Unit]`, so
systemd never gives up on it permanently, and it is `enabled`, so it returns after a reboot. Both
properties were verified on this instance.

## Redeploy a new commit

The instance has **no git credential and no repo clone** — that is on purpose, so that no secret
that could reach GitHub ever lives on the box. Deployment is an rsync of the working tree followed
by the bootstrap:

```bash
cd /Users/jerryinyang/Trading/fundr/.worktrees/phase2a-recorder
SHA=$(git rev-parse HEAD)
KEY=/Users/jerryinyang/Trading/fundr/auth/fundr-recorder.pem
IP=54.85.161.66

rsync -az --delete -e "ssh -i $KEY" --rsync-path="sudo rsync" \
  --exclude '.git' --exclude '.venv' --exclude 'data' --exclude 'auth' \
  --exclude '.superpowers' --exclude '__pycache__' --exclude '.pytest_cache' \
  --exclude '.ruff_cache' --exclude '.cache' \
  ./ ec2-user@$IP:/opt/fundr/

ssh -i $KEY ec2-user@$IP \
  "sudo env FUNDR_BUCKET= FUNDR_GIT_SHA=$SHA bash /opt/fundr/deploy/bootstrap.sh"
```

`bootstrap.sh` is idempotent: it re-runs `uv sync --no-dev`, reinstalls and re-verifies the unit
files, rewrites `/etc/fundr/recorder.env`, and restarts the service. `--delete` does not remove the
excluded `.venv` or `.cache` on the receiver, so the virtualenv is reused.

`FUNDR_REPO` is left unset on purpose: with it set, the bootstrap would clone and check out
instead, which is the path to use once a deploy key or a public repo exists.

The source of truth for the code is the private GitHub repo `git@github.com:jerryinyang/fundr.git`
(branches `main` and `phase2a-recorder`). The instance never talks to it.

## Stop, start, terminate

```bash
# stop (keeps the volume and the data; ~$0.64/month)
uv run python -c "import boto3;boto3.client('ec2',region_name='us-east-1').stop_instances(InstanceIds=['i-005b76078218b1256'])"
# start again
uv run python -c "import boto3;boto3.client('ec2',region_name='us-east-1').start_instances(InstanceIds=['i-005b76078218b1256'])"
# terminate -- DESTROYS the volume and every unuploaded recording
uv run python -c "import boto3;boto3.client('ec2',region_name='us-east-1').terminate_instances(InstanceIds=['i-005b76078218b1256'])"
```

**The public IP is not elastic.** A reboot keeps it; a stop/start assigns a **new** one, and every
command above that hard-codes `54.85.161.66` must then be updated. Find the current address with
`deploy/aws_provision.py plan`.

While the recorder's whole purpose is a continuous seven-day record, stopping the instance creates
a hole in the data that cannot be filled in later. Prefer leaving it running.

## To finish the S3 setup

**This is the one outstanding piece of work.** Until it is done the recording exists in exactly one
place, the Done-when clause "`health.json` is readable from S3 without logging into the instance"
is unmet, and port 22 stays open. It needs IAM permissions the current credentials do not have, so
it must be done by an account administrator (or by granting `iam:*` to `xeno-admin`).

1. **Create the role** `fundr-recorder-role`, trust policy `ec2.amazonaws.com`, with:
   - the managed policy `arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore` (this is what
     enables Session Manager), and
   - an inline policy allowing `s3:PutObject`, `s3:GetObject`, `s3:ListBucket` and `s3:HeadObject`
     on `arn:aws:s3:::fundr-recorder-801242831140-us-east-1` and
     `arn:aws:s3:::fundr-recorder-801242831140-us-east-1/*` **and nothing else**. An over-broad
     policy here is the single credential on the instance; keep it to the one bucket.
2. **Create the instance profile** of the same name, add the role to it, and associate it with
   `i-005b76078218b1256` (`ec2:AssociateIamInstanceProfile`). No restart or redeploy is needed;
   the instance picks the credentials up from IMDS within a minute or two.
3. **Turn uploads on**: set `FUNDR_BUCKET=fundr-recorder-801242831140-us-east-1` in
   `/etc/fundr/recorder.env` and `sudo systemctl restart fundr-recorder fundr-upload.timer`. The
   next timer firing backfills everything still on disk — the upload manifest is idempotent and
   local pruning is confirmed-only, so nothing is lost or duplicated. Confirm with:
   ```bash
   uv run python -c "
   import boto3
   r = boto3.client('s3').list_objects_v2(Bucket='fundr-recorder-801242831140-us-east-1',
                                          Prefix='recorder/v1/')
   print(r['KeyCount'], [o['Key'] for o in r.get('Contents', [])][:10])"
   ```
   Expect parts for all three feeds plus `recorder/v1/<instance-id>/health.json`.
4. **Close port 22** once a Session Manager shell (`aws ssm start-session --target
   i-005b76078218b1256`) is confirmed working: revoke the tcp/22 rule from `sg-0decca39cf155fcbd`
   (command in *SSH in*) and delete `auth/fundr-recorder.pem`.

**What it unlocks:** instance loss bounded to ten minutes instead of "everything since the volume
was created"; health readable without SSH; the security footprint the design actually called for —
no inbound ports and no private key on the operator's laptop.

## Prior art in the bucket

`s3://fundr-recorder-801242831140-us-east-1/phase1/p09/live_2026-09-19.jsonl` — the Phase 1 Lighter
premium recording, the only copy of that history in existence. Do not delete it. It predates this
deployment and is unaffected by the uploads being off.
