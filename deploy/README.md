# fundr recorder deployment

Runs the always-on funding recorder on a single small EC2 instance (Amazon Linux 2023,
arm64). Two systemd units plus a timer; a bootstrap script provisions a clean box.

## Units

- **`fundr-recorder.service`** — the recorder daemon (`python -m fundr.recorder`). `Type=simple`,
  `Restart=always` with `RestartSec=10`, and `StartLimitIntervalSec=0` in `[Unit]` so systemd's
  default start-rate limiter never gives up on it permanently after repeated crashes (that
  setting only has effect in `[Unit]` — in `[Service]` systemd logs "Unknown key name" and
  silently keeps the default limiter, which is worse than omitting it because it *looks*
  configured). Runs as the unprivileged `fundr` user out of `/opt/fundr`, writing data under
  `/var/lib/fundr` (`FUNDR_RECORDER_DATA`). Depends on `chronyd` for a trustworthy wall clock
  (`After=`, `Wants=network-online.target`).

- **`fundr-upload.service`** — `Type=oneshot` run of `python -m fundr.recorder.upload`. Uploads
  closed hourly parts to S3 (idempotent manifest, confirmed-only pruning of local copies) and
  is deliberately synchronous and separate from the recorder's event loop, so a slow or hung
  S3 call can never starve a feed task.

- **`fundr-upload.timer`** — fires the upload oneshot on `OnBootSec=2min` then every
  `OnUnitActiveSec=10min` (`AccuracySec=30s`).

## Environment variables

Set in `/etc/fundr/recorder.env` (mode 640, owned by `fundr:fundr`), loaded via
`EnvironmentFile=` by both units:

| Variable | Meaning |
|---|---|
| `FUNDR_BUCKET` | S3 bucket the uploader writes to. Empty/unset makes the uploader a no-op. |
| `FUNDR_GIT_SHA` | Commit deployed; stamped into every record's `git_sha` field for provenance. |
| `FUNDR_INSTANCE_ID` | Distinguishes recorders sharing a bucket in output filenames/keys; defaults to the EC2 instance id if not set. |

`FUNDR_RECORDER_DATA` is set directly in each unit's `[Service]` section (`/var/lib/fundr`),
not in the env file, since it never varies per-deploy.

## Bootstrap

`bootstrap.sh` provisions a clean instance: installs `git` and `chrony` (enabling `chronyd`
immediately — an untrustworthy wall clock would poison every timestamp the recorder writes),
creates the `fundr` system user, installs `uv`, clones/checks out the repo at `FUNDR_GIT_SHA`,
runs `uv sync --no-dev`, installs the three unit files, verifies them with
`systemd-analyze verify`, and enables/starts the recorder service and upload timer. It is
idempotent — safe to re-run against an already-provisioned box (e.g. to redeploy a new SHA).

Configuration arrives via the **environment**, not positional arguments (user-data invokes
the script with no argv): `FUNDR_BUCKET` and `FUNDR_GIT_SHA` are required, though `FUNDR_BUCKET`
may legitimately be **empty** (no S3 credential → the uploader no-ops). `FUNDR_REPO` is
**optional**: set it to have the script clone and check out the code; leave it unset when the
working tree has already been delivered to `/opt/fundr` out of band, which is what the current
deployment does (`rsync` over SSH — see `docs/phase2/recorder_runbook.md`), because cloning a
private repo from the instance would require putting a git credential on the instance.
`FUNDR_INSTANCE_ID` is optional and defaults to the EC2 instance id read from IMDSv2.

Note: `sudo -u fundr -H` is required, not just `-u fundr` — without `-H`, `HOME` stays `/root`
under sudo, and `uv` then tries to write its cache/venv metadata somewhere the `fundr` user
cannot read, which fails on AL2023. `UV_CACHE_DIR=/opt/fundr/.cache` pins the cache to a
directory the service account owns.

`FUNDR_REPO` is the git URL for the private repository holding this code (SSH form, with a
read-only deploy key at `/etc/fundr/deploy_key`, when the repo is private). The deployed
instance does **not** use it: it holds no git credential at all, and the tree is pushed to it by
`rsync`. `docs/phase2/recorder_runbook.md` has the exact redeploy command.

## Checking status

```bash
systemctl status fundr-recorder.service
systemctl status fundr-upload.timer
journalctl -u fundr-recorder -f          # tail live logs
journalctl -u fundr-upload -n 50         # last upload run's log
systemctl list-timers fundr-upload.timer
```

## Where data lands

- **Locally**: `/var/lib/fundr/<feed>/date=<YYYY-MM-DD>/hour=<HH>/<feed>-<instance-id>-<YYYYMMDD>T<HH>.jsonl.gz`
  (one gzip-compressed JSONL part per feed per hour, filename carrying the instance id so two
  recorders sharing a bucket never collide on a part — see `HourlyWriter.path_for`), plus
  `/var/lib/fundr/health.json` (health snapshot, rewritten atomically on every supervisor tick)
  and `/var/lib/fundr/uploaded.json` (upload manifest). Local parts are pruned after
  `local_retention_days` (default 7) once confirmed uploaded.
- **In S3**: under `s3://<FUNDR_BUCKET>/recorder/v1/...`, mirroring the same per-feed/per-hour
  key layout, plus `recorder/v1/<instance-id>/health.json` alongside the data — health.json's
  key is scoped by instance id explicitly (`Uploader.key_for`) since, unlike a part's filename,
  `health.json`'s name carries no instance id on its own.

## Reading `health.json` from S3

```bash
aws s3 cp s3://$FUNDR_BUCKET/recorder/v1/<instance-id>/health.json - | jq .
```

The top-level `status` is `ok`, `degraded` or `broken`, judged over a **trailing 60-minute**
window (not the calendar hour) so a freshly-started market is not misjudged. Per feed:
`coverage` (received vs. expected events in the window), `last_write_ms` /
`last_event_ms` (staleness), `reconnects_window`, and `open_gap_from_ms` (non-null while a
gap is open). Per market under each feed: `received_window`, `expected_window`,
`expected_hour`, and `coverage` (`null` until at least one event's worth of window has
elapsed, so a market seconds old is reported but not judged).
