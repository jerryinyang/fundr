#!/usr/bin/env python3
"""Daily health check for the always-on funding recorder.

Pulls /var/lib/fundr/health.json (and a disk-free reading) from the recorder instance over
SSH, prints a one-screen summary, and exits non-zero if status is not "ok". Never writes
anything to the instance -- every remote command here is read-only (sudo cat, df).

With no S3 uploads configured yet (see docs/phase2/recorder_runbook.md, "Two deliberate
deviations"), the instance's local volume is currently the ONLY copy of the recording, which is
why disk-free is checked here and not left for later.

Usage:
    uv run python scripts/daily_check.py
    uv run python scripts/daily_check.py --host ec2-user@1.2.3.4 --key /path/to/key.pem

Exit codes:
    0  status is "ok"
    1  reachable but status is "degraded" or "broken" (or unparseable/missing)
    2  could not reach the instance or read health.json at all
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone

# The recorder runs in eu-central-1: Lighter refuses websocket connections from US jurisdictions.
# See docs/phase2/recorder_runbook.md, "Why Frankfurt".
DEFAULT_HOST = "ec2-user@18.196.234.242"
DEFAULT_KEY = "auth/fundr-recorder-eu.pem"
DEFAULT_DATA_DIR = "/var/lib/fundr"
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=accept-new"]
SSH_TIMEOUT_S = 20


def ssh_run(host: str, key: str, remote_cmd: str) -> subprocess.CompletedProcess:
    cmd = ["ssh", "-i", key, *SSH_OPTS, host, remote_cmd]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=SSH_TIMEOUT_S)


def fmt_ms(ms: int | None) -> str:
    if ms is None:
        return "never"
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    age_s = (datetime.now(timezone.utc) - dt).total_seconds()
    return f"{dt.strftime('%Y-%m-%dT%H:%M:%SZ')} ({age_s:.0f}s ago)"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=DEFAULT_HOST, help=f"ssh target (default: {DEFAULT_HOST})")
    ap.add_argument("--key", default=DEFAULT_KEY, help=f"path to the SSH private key (default: {DEFAULT_KEY})")
    ap.add_argument("--data-dir", default=DEFAULT_DATA_DIR, help=f"remote data dir (default: {DEFAULT_DATA_DIR})")
    args = ap.parse_args()

    try:
        health_r = ssh_run(args.host, args.key, f"sudo cat {args.data_dir}/health.json")
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"ERROR: could not reach {args.host}: {e}", file=sys.stderr)
        return 2
    if health_r.returncode != 0:
        print(f"ERROR: could not read {args.data_dir}/health.json over SSH:\n{health_r.stderr.strip()}",
              file=sys.stderr)
        return 2
    try:
        health = json.loads(health_r.stdout)
    except json.JSONDecodeError as e:
        print(f"ERROR: health.json did not parse as JSON: {e}", file=sys.stderr)
        return 2

    try:
        df_r = ssh_run(args.host, args.key, f"df -Pk {args.data_dir} | tail -1")
        disk_line = df_r.stdout.strip() if df_r.returncode == 0 else None
    except (subprocess.TimeoutExpired, OSError):
        disk_line = None

    status = health.get("status", "unknown")
    print(f"=== fundr recorder daily health check -- {args.host} ===")
    print(f"status:    {status}")
    print(f"generated: {fmt_ms(health.get('generated_ms'))}")
    print(f"window:    {health.get('window_ms', 0) / 60000:.0f} min trailing")
    print()
    print(f"{'feed':<16}{'coverage':>10}  {'reconnects':>10}  {'open gap since':<28}{'last write':<28}")
    feeds = health.get("feeds", {})
    for feed in sorted(feeds):
        f = feeds[feed]
        cov = f.get("coverage")
        cov_s = f"{cov:.3f}" if cov is not None else "n/a"
        gap_from = f.get("open_gap_from_ms")
        gap_s = fmt_ms(gap_from) if gap_from else "none"
        print(f"{feed:<16}{cov_s:>10}  {f.get('reconnects_window', 0):>10}  {gap_s:<28}{fmt_ms(f.get('last_write_ms')):<28}")
    print()

    exit_code = 0 if status == "ok" else 1

    if disk_line:
        # df -P: filesystem, 1024-blocks, used, available, capacity%, mounted-on
        parts = disk_line.split()
        if len(parts) >= 6:
            _fs, _size_kb, _used_kb, avail_kb, pct_used, mount = parts[:6]
            try:
                avail_gb = int(avail_kb) / 1024 / 1024
            except ValueError:
                avail_gb = None
            if avail_gb is not None:
                print(f"disk free on {mount}: {avail_gb:.2f} GB ({pct_used} used)")
                if avail_gb < 1.0:
                    print("WARNING: less than 1 GB free on the instance -- this is the ONLY copy of "
                          "the recording (no S3 uploads configured yet).")
                    exit_code = max(exit_code, 1)
        else:
            print(f"disk free: unrecognised df output: {disk_line!r}")
    else:
        print("disk free: could not read (df failed over SSH)")
        exit_code = max(exit_code, 1)

    if status != "ok":
        print(f"\nNOT OK: status is '{status}'")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
