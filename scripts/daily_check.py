#!/usr/bin/env python3
"""Daily health check for the always-on funding recorder.

This is the documented daily procedure for the seven-consecutive-days-at-`ok` gate (see
docs/phase2/recorder_runbook.md, "Daily check"): there is no alerting, so this script -- run by
a human, cron, or a scheduled agent -- is the only thing that would ever notice the recorder had
stopped. Pulls /var/lib/fundr/health.json (and a disk-free reading) from the recorder instance
over SSH, prints a one-screen summary, and exits non-zero if anything is wrong. Never writes
anything to the instance -- every remote command here is read-only (sudo cat, df).

With no S3 uploads configured yet (see docs/phase2/recorder_runbook.md, "Two deliberate
deviations"), the instance's local volume is currently the ONLY copy of the recording, which is
why disk-free is checked here and not left for later.

Two gates exist because `health.json` can be WRONG in ways that look fine at a glance:

- `status` is only ever updated by the live recorder process. If the process dies and never
  comes back (the exact failure mode this script exists to catch), the file simply stops being
  rewritten and freezes at whatever it last said -- often `ok`. A `generated_ms` freshness check
  is the only way to tell "the recorder is fine" from "the recorder died an hour ago and nobody
  told this file." (`fundr.recorder.health.Health` ticks health at least once every ~60s while
  running; 5 minutes is a generous multiple of that.)
- `status` can read `ok` in the first few minutes after a (re)start purely because every feed's
  coverage is still `null` -- not enough of the trailing window has elapsed to judge it (see the
  runbook: "status reads ok during that window on no evidence"). All-null coverage is "not yet
  known", not "ok", and is reported and failed accordingly.

Usage:
    uv run python scripts/daily_check.py
    uv run python scripts/daily_check.py --host ec2-user@1.2.3.4 --key /path/to/key.pem

Exit codes:
    0  status is "ok", health.json is fresh, and at least one feed has real (non-null) coverage
    1  reachable but status is not "ok", health.json is stale, coverage is all-null, or disk is
       critically low
    2  could not reach the instance, could not read health.json, or could not find the SSH key
"""
import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# The recorder runs in eu-central-1: Lighter refuses websocket connections from US jurisdictions.
# See docs/phase2/recorder_runbook.md, "Why Frankfurt".
DEFAULT_HOST = "ec2-user@18.196.234.242"
DEFAULT_KEY = "auth/fundr-recorder-eu.pem"
DEFAULT_DATA_DIR = "/var/lib/fundr"
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=accept-new"]
SSH_TIMEOUT_S = 20
# Health.py ticks (advances its clock, and can rewrite the file) on every event, and the
# recorder feeds poll/heartbeat at most every ~60s -- see fundr.recorder.health.Health. Five
# minutes is a generous multiple of that: enough to absorb a slow write cycle, not so generous
# that a genuinely dead recorder gets another 20+ minutes of false "ok".
STALE_AFTER_S = 5 * 60


def resolve_key_path(key: str) -> Path:
    """`auth/` is gitignored and lives only in the main checkout, not in every git worktree
    (worktrees only carry tracked files) -- so a bare relative default fails whenever this is
    run from a worktree, exactly the layout this repo uses for phase work. Try, in order: the
    path as given (absolute, or relative to cwd), then relative to each worktree's root as
    reported by `git worktree list` (the first entry is always the main checkout). Raise with
    every location tried, so a missing key is never a silent SSH timeout."""
    p = Path(key)
    if p.is_absolute():
        if p.exists():
            return p
        print(f"ERROR: SSH key not found at {p}", file=sys.stderr)
        raise SystemExit(2)

    tried = [Path.cwd() / p]
    try:
        out = subprocess.run(["git", "worktree", "list", "--porcelain"],
                             capture_output=True, text=True, timeout=5)
        if out.returncode == 0:
            for line in out.stdout.splitlines():
                if line.startswith("worktree "):
                    tried.append(Path(line.split(" ", 1)[1]) / p)
    except (OSError, subprocess.TimeoutExpired):
        pass

    for candidate in tried:
        if candidate.exists():
            return candidate
    print(f"ERROR: could not find SSH key {key!r}. Tried:\n  " +
          "\n  ".join(str(c) for c in tried), file=sys.stderr)
    raise SystemExit(2)


def ssh_run(host: str, key: Path, remote_cmd: str) -> subprocess.CompletedProcess:
    cmd = ["ssh", "-i", str(key), *SSH_OPTS, host, remote_cmd]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=SSH_TIMEOUT_S)


def fmt_ms(ms: int | None) -> str:
    if ms is None:
        return "never"
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    age_s = (datetime.now(timezone.utc) - dt).total_seconds()
    return f"{dt.strftime('%Y-%m-%dT%H:%M:%SZ')} ({age_s:.0f}s ago)"


def evaluate(health: dict, now_ms: int | None = None) -> tuple[int, list[str]]:
    """Pure judgement over an already-parsed health.json, separated from the SSH/print
    plumbing so the freshness and coverage gates can be unit tested without a live instance."""
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    notes: list[str] = []
    exit_code = 0

    status = health.get("status", "unknown")
    if status != "ok":
        notes.append(f"NOT OK: status is '{status}'")
        exit_code = max(exit_code, 1)

    generated_ms = health.get("generated_ms")
    if generated_ms is None:
        notes.append("STALE: health.json has no generated_ms")
        exit_code = max(exit_code, 1)
    else:
        age_s = (now_ms - generated_ms) / 1000
        if age_s > STALE_AFTER_S:
            notes.append(f"STALE: health.json last generated {age_s:.0f}s ago "
                         f"(> {STALE_AFTER_S}s) -- the recorder may be dead, or the health "
                         f"writer stuck, and the last-known status can no longer be trusted")
            exit_code = max(exit_code, 1)

    feeds = health.get("feeds", {})
    coverages = [f.get("coverage") for f in feeds.values()]
    if feeds and all(c is None for c in coverages):
        notes.append("UNKNOWN: every feed reports null coverage -- not enough of the trailing "
                     "window has elapsed to judge this recorder yet (status 'ok' here is not "
                     "evidence of anything)")
        exit_code = max(exit_code, 1)

    return exit_code, notes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=DEFAULT_HOST, help=f"ssh target (default: {DEFAULT_HOST})")
    ap.add_argument("--key", default=DEFAULT_KEY, help=f"path to the SSH private key (default: {DEFAULT_KEY})")
    ap.add_argument("--data-dir", default=DEFAULT_DATA_DIR, help=f"remote data dir (default: {DEFAULT_DATA_DIR})")
    args = ap.parse_args(argv)

    key_path = resolve_key_path(args.key)

    try:
        health_r = ssh_run(args.host, key_path, f"sudo cat {args.data_dir}/health.json")
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
        df_r = ssh_run(args.host, key_path, f"df -Pk {args.data_dir} | tail -1")
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

    exit_code, notes = evaluate(health)

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
                    notes.append("WARNING: less than 1 GB free on the instance -- this is the "
                                 "ONLY copy of the recording (no S3 uploads configured yet).")
                    exit_code = max(exit_code, 1)
        else:
            print(f"disk free: unrecognised df output: {disk_line!r}")
    else:
        print("disk free: could not read (df failed over SSH)")
        exit_code = max(exit_code, 1)

    if notes:
        print()
        for note in notes:
            print(note)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
