# Recorder runbook

Operating notes for the always-on funding recorder deployed on AWS. Written 2026-09-21 when the
instance was provisioned; rewritten the same day when the recorder was **moved from `us-east-1` to
`eu-central-1`** because Lighter geo-blocks US jurisdictions (see *Why Frankfurt*).

## What is deployed

| Resource | Id / value |
|---|---|
| Region | `eu-central-1` (Frankfurt) |
| Instance | `i-02348231aa3d6e4bf` — `t4g.micro`, Amazon Linux 2023 arm64 |
| AMI | `ami-0ffe0f8c24ca948e6` (`al2023-ami-2023.12.20260918.0-kernel-6.1-arm64`) |
| Public IP | `18.196.234.242` — **dynamic, not an Elastic IP** (see *Stop, start, terminate*) |
| Root volume | `vol-060f08bf9c9eb3d21` — 8 GB `gp3`, encrypted, delete-on-termination |
| Security group | `sg-0eb3c8047f99b3d53` (`fundr-recorder-sg`) in `vpc-0a0077d7fa120ac58` |
| Key pair | `fundr-recorder-eu` (`key-0d0cbb8e9ff8a56c2`, ed25519) |
| Private key | `/Users/jerryinyang/Trading/fundr/auth/fundr-recorder-eu.pem`, mode 0600 — `auth/` is gitignored |
| S3 bucket | `fundr-recorder-<AWS_ACCOUNT_ID>-us-east-1` — private, versioned, **still in `us-east-1`**; lifecycle: noncurrent expire 7 d, current → IA 90 d |
| Tags | `Project=fundr`, `Component=recorder` on instance, volume, security group and key pair |
| Code on the box | `/opt/fundr`, owned by the `fundr` system user |
| Data on the box | `/var/lib/fundr` |

Everything is created and re-checked by `deploy/aws_provision.py` (`plan` changes nothing,
`apply` creates what is missing; both are safe to re-run):

```bash
uv run python deploy/aws_provision.py plan
```

It **defaults to `eu-central-1`** and picks the matching key name (`fundr-recorder-eu`)
automatically. `AWS_REGION` still overrides it, but be careful: the script finds the existing
instance by tag *within one region*, so pointing it at another region makes it report "nothing
exists" and launch a **duplicate** recorder. If you ever do run it elsewhere, follow up with the
all-region audit under *Stop, start, terminate*.

The bucket stays in `us-east-1` on purpose: it holds the irreplaceable Phase 1 recording and the
preserved us-east-1 partial, and S3 is reachable from any region. Once uploads are enabled this
costs roughly $0.02/GB in cross-region transfer — at ~40–50 MB/day, cents per month.

## Monthly cost

Frankfurt is dearer than N. Virginia: `t4g.micro` is $0.0092/hr against $0.0084, and gp3 is
$0.0952/GB-month against $0.08.

| Item | $/month |
|---|---|
| `t4g.micro` on demand, 730 h × $0.0092 | 6.72 |
| Public IPv4 address, 730 h × $0.005 | 3.65 |
| 8 GB gp3 root volume × $0.0952/GB | 0.76 |
| S3 storage and requests (uploads currently disabled) | ~0.10 |
| Key pair, security group | 0.00 |
| **Total** | **~$11.23** |

That is ~$0.71/month more than us-east-1 cost, which is the price of having a Lighter feed at all.
Leaving it running costs that every month whether or not anyone reads the data. Stopping the
instance removes the instance and IPv4 charges (~$10.37) and leaves the ~$0.76 volume charge, which
is what keeps the recording. There is no free tier credit assumed in these numbers.

## Why Frankfurt

The recorder ran in `us-east-1` from 10:35Z to 12:0xZ on 2026-09-21 and recorded Hyperliquid
perfectly (coverage 1.00), but **Lighter's edge refused every websocket handshake from the
instance**:

```
HTTP/1.1 400 Bad Request     (Server: CloudFront)
{"code": 20558, "message": "You are accessing Lighter from a restricted jurisdiction..."}
```

The same handshake succeeded from the operator's laptop, so the block is by client IP jurisdiction,
not a bug. `lighter_state` coverage sat at 0.0 with an open gap — the feed the whole design exists
to capture. Whether to run from another jurisdiction is a **terms-of-service judgement about
Lighter's jurisdiction restrictions, not an engineering one**; it was put to the user in those
terms and **the user made the call to relocate**.

From `eu-central-1` the same handshake is accepted:

```
HTTP/1.1 101 Switching Protocols
X-Amz-Cf-Pop: FRA56-P4
{"session_id":"...","type":"connected"}
```

**Nothing else changed.** Same instance type, same volume, same single-source security group, still
no instance profile, `FUNDR_BUCKET` still empty. **No proxy, VPN or traffic-masking software is
installed or permitted** — this is a plain relocation of where the instance runs. If Lighter's
restrictions ever extend to the EU, the answer is to stop and re-decide, not to disguise traffic.

### The us-east-1 partial recording is preserved

`s3://fundr-recorder-<AWS_ACCOUNT_ID>-us-east-1/recorder/us-east-1-partial/` holds the old instance's
entire `/var/lib/fundr` as it stood at termination — Hyperliquid and universe data for hours 10–12
of 2026-09-21, plus `health.json` and the Lighter gap records that are the evidence of the block.

**Do not blindly concatenate it with the Frankfurt recording.** It carries a different instance id
(`i-005b76078218b1256`) in every filename and in every record's provenance, and there is a
~5-minute cutover gap between the two.

## SSH in

```bash
ssh -i /Users/jerryinyang/Trading/fundr/auth/fundr-recorder-eu.pem ec2-user@18.196.234.242
```

The security group allows inbound TCP 22 **from one address only** — the operator's public IP as a
/32 (set this to your current IP; find it with `curl https://checkip.amazonaws.com`). Password
authentication is off in the AMI, so the key file is the only way in. Never commit it, never copy
it to another machine you do not control.

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
ec2 = boto3.client('ec2', region_name='eu-central-1')
sg = ec2.describe_security_groups(GroupIds=['sg-0eb3c8047f99b3d53'])['SecurityGroups'][0]
print([r['IpRanges'] for r in sg['IpPermissions']])"
# then, for each CIDR you no longer use:
uv run python -c "
import boto3
boto3.client('ec2', region_name='eu-central-1').revoke_security_group_ingress(
    GroupId='sg-0eb3c8047f99b3d53',
    IpPermissions=[{'IpProtocol':'tcp','FromPort':22,'ToPort':22,
                    'IpRanges':[{'CidrIp':'OLD.IP.HERE/32'}]}])"
```

`uv run python deploy/aws_provision.py verify` re-reads every provisioned resource and asserts
its configuration (security group has exactly one ingress rule, tcp/22, from exactly one /32;
instance has no instance profile; the root volume is encrypted; the bucket has versioning
enabled, public-access-block all-true, and both lifecycle rules), printing PASS/FAIL per
assertion and exiting non-zero on any failure. Run it after any change to the account or after
pruning ingress rules above, to confirm nothing else moved.

## Daily check (the seven-day gate's only defence)

There is no alerting on this recorder. `scripts/daily_check.py` is the documented daily
procedure for the spec's "seven consecutive days at `status: ok`" Done-when clause — run it once
a day, by hand or on a schedule, for the full seven days:

```bash
uv run python scripts/daily_check.py
```

It SSHes in read-only (never writes to the instance), prints status, per-feed coverage /
reconnects / open gaps / last-write age, and disk free, and exits:

- **0** — `status: ok`, `health.json` was generated recently, and at least one feed has real
  (non-null) coverage. The only exit code that counts as a good day.
- **1** — reachable, but something is wrong: `status` isn't `ok`, `health.json` is stale (see
  below), every feed's coverage is still `null`, or disk free has dropped under 1 GB.
- **2** — could not reach the instance, read `health.json`, or find the SSH key at all.

**Why staleness matters as much as `status` itself**: `health.json` is only rewritten by the live
recorder process. If that process dies and never restarts, the file freezes at its last value —
often `ok` — and a check that only reads `status` would report a healthy recorder for as long as
the file happens to say so, which is exactly the venv-destruction-style incident this design has
no other defence against. `daily_check.py` therefore also fails if `generated_ms` is more than
five minutes old, regardless of what `status` says.

Run it against a different instance or key with `--host`/`--key`/`--data-dir`; see `--help`.

## Read health (manual / ad hoc)

```bash
ssh -i .../fundr-recorder-eu.pem ec2-user@18.196.234.242 'sudo cat /var/lib/fundr/health.json' | jq .
```

`status` is `ok`, `degraded` or `broken`, judged over a trailing 60-minute window. Per feed:
`coverage`, `last_write_ms`/`last_event_ms`, `reconnects_window`, `open_gap_from_ms`. A freshly
started recorder reports `null` coverage for the first minutes — that is expected, not a fault, and
**`status` reads `ok` during that window on no evidence**, so treat `ok` with `null` coverage as
"not yet known". Expect real numbers within ~2–6 minutes of a start.

`coverage` is *received ÷ expected*, so it is **not** capped at 1.0. On Lighter it sits around
**30×** because the expected-rate model assumes the 60-second heartbeat while the feed is
event-driven and every premium change writes a record. High is healthy; the failure signature is
`0.0` with a non-null `open_gap_from_ms`, which is exactly what us-east-1 showed.

Data on disk:

```bash
ssh ... 'sudo find /var/lib/fundr -name "*.jsonl.gz" | tail; sudo du -sh /var/lib/fundr'
```

Files are `/var/lib/fundr/<feed>/date=<YYYY-MM-DD>/hour=<HH>/<feed>-<instance-id>-<YYYYMMDD>T<HH>.jsonl.gz`
for the three feeds `hl_state`, `lighter_state`, `universe`.

**The current hour's file is an open gzip stream** and will fail `gzip -t` until the hour rolls.
That is normal; do not treat it as corruption.

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
properties were re-verified on this instance: a `systemctl reboot` brought it back with the service
`active`/`enabled` six seconds after boot, no manual step, and `health.json` back to `ok` with all
three feeds inside two minutes.

## Redeploy a new commit

The instance has **no git credential and no repo clone** — that is on purpose, so that no secret
that could reach GitHub ever lives on the box. Deployment is an rsync of the working tree followed
by the bootstrap:

```bash
cd /Users/jerryinyang/Trading/fundr/.worktrees/phase2a-recorder
SHA=$(git rev-parse HEAD)
KEY=/Users/jerryinyang/Trading/fundr/auth/fundr-recorder-eu.pem
IP=18.196.234.242

rsync -az --delete -e "ssh -i $KEY" --rsync-path="sudo rsync" \
  --exclude '.git' --exclude '.venv' --exclude 'data' --exclude 'auth' \
  --exclude '.superpowers' --exclude '__pycache__' --exclude '.pytest_cache' \
  --exclude '.ruff_cache' --exclude '.cache' --exclude '.local' \
  ./ ec2-user@$IP:/opt/fundr/

ssh -i $KEY ec2-user@$IP \
  "sudo env FUNDR_BUCKET= FUNDR_GIT_SHA=$SHA bash /opt/fundr/deploy/bootstrap.sh"
```

`bootstrap.sh` ends with an explicit `systemctl restart fundr-recorder.service` (and
`fundr-upload.timer`) — no separate manual restart is needed. This was not always true:
`enable --now` is a no-op on an already-running unit, so an earlier version of this script would
rewrite `/etc/fundr/recorder.env` with the new `FUNDR_GIT_SHA` while the still-running process kept
stamping the *old* sha into every record's provenance. Fixed in code (`bootstrap.sh`, sixth review
round); one rsync + one bootstrap run is now sufficient to redeploy.

> **`--exclude '.local'` is load-bearing — do not drop it.** `uv` installs its *managed Python
> interpreter* under `/opt/fundr/.local/share/uv/python/`, and the deploying worktree has no
> `.local`, so a `--delete` rsync without this exclude deletes the interpreter out from under the
> virtualenv. The symptoms are `Ignoring existing virtual environment linked to non-existent Python
> interpreter` and `error: Python installation is missing a _sysconfigdata_ file`, and the already-
> running service keeps going on its open file handles — so **nothing looks broken until the next
> restart or reboot, which then fails**. This happened once, on 2026-09-21. Recovery:
>
> ```bash
> sudo rm -rf /opt/fundr/.local /opt/fundr/.cache /opt/fundr/.venv
> sudo install -d -o fundr -g fundr /opt/fundr/.cache
> sudo -u fundr -H -E env UV_CACHE_DIR=/opt/fundr/.cache \
>   /usr/local/bin/uv --directory /opt/fundr sync --no-dev
> sudo systemctl restart fundr-recorder
> ```
>
> After any redeploy, **restart the service and confirm it actually comes back** rather than
> trusting that `active` means the on-disk venv is intact.

`bootstrap.sh` is otherwise idempotent: it re-runs `uv sync --no-dev`, reinstalls and re-verifies
the unit files, and rewrites `/etc/fundr/recorder.env`. `--delete` does not remove the excluded
`.venv`, `.cache` or `.local` on the receiver, so the virtualenv is reused.

A redeploy **restarts the recorder**, which costs a few seconds of every feed. Prefer to batch
changes rather than redeploy repeatedly.

`FUNDR_REPO` is left unset on purpose: with it set, the bootstrap would clone and check out
instead, which is the path to use once a deploy key or a public repo exists.

The source of truth for the code is the private GitHub repo `git@github.com:jerryinyang/fundr.git`
(branches `main` and `phase2a-recorder`). The instance never talks to it.

## Stop, start, terminate

```bash
# stop (keeps the volume and the data; ~$0.76/month)
uv run python -c "import boto3;boto3.client('ec2',region_name='eu-central-1').stop_instances(InstanceIds=['i-02348231aa3d6e4bf'])"
# start again
uv run python -c "import boto3;boto3.client('ec2',region_name='eu-central-1').start_instances(InstanceIds=['i-02348231aa3d6e4bf'])"
# terminate -- DESTROYS the volume and every unuploaded recording
uv run python -c "import boto3;boto3.client('ec2',region_name='eu-central-1').terminate_instances(InstanceIds=['i-02348231aa3d6e4bf'])"
```

**Before any terminate, copy `/var/lib/fundr` off the box** — with uploads disabled the volume is
the only copy. That is what `recorder/us-east-1-partial/` in the bucket is:

```bash
rsync -az -e "ssh -i $KEY" --rsync-path="sudo rsync" ec2-user@$IP:/var/lib/fundr/ ./local-copy/
```

**The public IP is not elastic.** A reboot keeps it; a stop/start assigns a **new** one, and every
command above that hard-codes `18.196.234.242` must then be updated — including `DEFAULT_HOST` in
`scripts/daily_check.py`. Find the current address with
`uv run python deploy/aws_provision.py plan`.

While the recorder's whole purpose is a continuous seven-day record, stopping the instance creates
a hole in the data that cannot be filled in later. Prefer leaving it running.

### Audit every region

An orphan instance in a region nobody looks at is the expensive failure mode. After any
provisioning mishap, or any run of `aws_provision.py` against a non-default region:

```bash
uv run python -c "
import boto3
for r in sorted(x['RegionName'] for x in boto3.client('ec2', region_name='eu-central-1').describe_regions()['Regions']):
    for res in boto3.client('ec2', region_name=r).describe_instances()['Reservations']:
        for i in res['Instances']:
            if i['State']['Name'] != 'terminated':
                print(r, i['InstanceId'], i['InstanceType'], i['State']['Name'])"
```

Exactly one line should come back: the Frankfurt recorder.

## To finish the S3 setup

**This is the one outstanding piece of work.** Until it is done the recording exists in exactly one
place, the Done-when clause "`health.json` is readable from S3 without logging into the instance"
is unmet, and port 22 stays open. It needs IAM permissions the current credentials do not have, so
it must be done by an account administrator (or by granting `iam:*` to `xeno-admin`).

1. **Create the role** `fundr-recorder-role`, trust policy `ec2.amazonaws.com`, with:
   - the managed policy `arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore` (this is what
     enables Session Manager), and
   - an inline policy allowing `s3:PutObject`, `s3:GetObject`, `s3:ListBucket` and `s3:HeadObject`
     on `arn:aws:s3:::fundr-recorder-<AWS_ACCOUNT_ID>-us-east-1` and
     `arn:aws:s3:::fundr-recorder-<AWS_ACCOUNT_ID>-us-east-1/*` **and nothing else**. An over-broad
     policy here is the single credential on the instance; keep it to the one bucket.
   IAM is global, so the role works for a `eu-central-1` instance unchanged.
2. **Create the instance profile** of the same name, add the role to it, and associate it with
   `i-02348231aa3d6e4bf` (`ec2:AssociateIamInstanceProfile`, region `eu-central-1`). No restart or
   redeploy is needed; the instance picks the credentials up from IMDS within a minute or two.
3. **Turn uploads on**: set `FUNDR_BUCKET=fundr-recorder-<AWS_ACCOUNT_ID>-us-east-1` in
   `/etc/fundr/recorder.env` and `sudo systemctl restart fundr-recorder fundr-upload.timer`. The
   next timer firing backfills everything still on disk — the upload manifest is idempotent and
   local pruning is confirmed-only, so nothing is lost or duplicated. Confirm with:
   ```bash
   uv run python -c "
   import boto3
   r = boto3.client('s3').list_objects_v2(Bucket='fundr-recorder-<AWS_ACCOUNT_ID>-us-east-1',
                                          Prefix='recorder/v1/')
   print(r['KeyCount'], [o['Key'] for o in r.get('Contents', [])][:10])"
   ```
   Expect parts for all three feeds plus `recorder/v1/<instance-id>/health.json`.
4. **Close port 22** once a Session Manager shell (`aws ssm start-session --target
   i-02348231aa3d6e4bf --region eu-central-1`) is confirmed working: revoke the tcp/22 rule from
   `sg-0eb3c8047f99b3d53` (command in *SSH in*) and delete `auth/fundr-recorder-eu.pem`.

**What it unlocks:** instance loss bounded to ten minutes instead of "everything since the volume
was created"; health readable without SSH; the security footprint the design actually called for —
no inbound ports and no private key on the operator's laptop.

## Prior art in the bucket

- `s3://fundr-recorder-<AWS_ACCOUNT_ID>-us-east-1/phase1/p09/live_2026-09-19.jsonl` — the Phase 1
  Lighter premium recording, the only copy of that history in existence. **Do not delete it.**
- `s3://fundr-recorder-<AWS_ACCOUNT_ID>-us-east-1/recorder/us-east-1-partial/` — the terminated
  us-east-1 instance's recording, preserved at decommission. See *Why Frankfurt*.
