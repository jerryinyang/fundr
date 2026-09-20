# Phase 2a — The Always-On Funding Recorder: Design

Date: 2026-09-20
Parent: `docs/funding_research_design.md` (Phase 2)
Predecessor: `docs/phase1/handoff.md` (§4 "The recorder Phase 2 must build")

## Why this exists

Target C — how much funding moves between the number a venue publishes during the hour and the
rate that finally settles — can only be studied from data recorded going forward. Phase 1 proved
no archive preserved it on either venue ([data_audit.md §5](../../phase1/data_audit.md)). Every
day the recorder is not running is a day of Target C that cannot be recovered, and Phase 11 runs
entirely on what it collects. It is therefore the critical path, ahead of every backfill.

Phase 1 also showed that everything needed is live, free and unauthenticated on both venues today
([P9](../../phase1/evidence/P9-live-probe.md)).

## Scope

**In scope:** a service that records funding-related market state from both venues, continuously,
to durable storage, with enough self-monitoring that a failure is visible rather than silent.

**Out of scope (explicitly):**
- Historical backfill of Targets A and B — its own cycle, after this one.
- Trades and order books. Decided during design: trade imbalance is one candidate predictor among
  ten (H2/Phase 7), and unlike premium it is recoverable later — Hyperliquid fills from its archive
  (2025-05-25 onward, ~$27/year, [P3](../../phase1/evidence/P3-hl-node-fills.md)) and Lighter fills
  from 0xArchive's paid tier (January 2025 onward,
  [P8](../../phase1/evidence/P8-oxarchive.md)). **Phase 7 must therefore treat buy/sell imbalance as
  a backfill decision, not as data already being collected.**
- Any normalisation, signing, unit conversion or derived funding. The recorder captures raw.

## Decisions taken during design

| Topic | Decision |
|---|---|
| Host | AWS `t4g.nano` in `ap-northeast-1` (near both venues' endpoints), ~$3/month. This laptop is disqualified: host suspension is the leading explanation for Phase 1's 95-minute data loss |
| Storage | Daily compressed files per feed on the instance, uploaded to a private S3 bucket; local copy kept 7 days |
| Coverage | All markets on both venues (~234 HL, ~246 Lighter), not just the research universe — avoids regret when the universe rule changes |
| Trades | Not recorded (see Scope) |
| Alerting | Health file only, no email. Consequence: the service must restart itself, since nothing else is watching |
| Recorder scope | Capture only, raw as received |
| Provisioning | Claude provisions the AWS resources using the user's credentials, presenting each action and its cost before running it |

## Architecture

One Python service, three independent tasks, sharing nothing that can block another:

| Task | Source | Cadence | Writes |
|---|---|---|---|
| HL market state | `metaAndAssetCtxs` + `predictedFundings` (REST) | 60 s | one row per market per cycle |
| Lighter market state | `market_stats` websocket, all markets | 60 s snapshot | latest values per market + message counter |
| Universe | HL `meta` + Lighter `/api/v1/orderBooks` | 15 min | full market list with status, listing dates |

The universe task is the only future source of Lighter delisting dates
([P6](../../phase1/evidence/P6-lighter-market-state.md) found no delisting timestamp anywhere).

```
src/fundr/recorder/
  config.py       # venues, cadences, paths, bucket name
  writer.py       # daily rotating .jsonl.gz per feed; atomic, flushed on rotation
  health.py       # per-feed, per-market coverage counters → health.json
  supervisor.py   # starts the tasks, restarts a dead task, owns the watchdogs
  feeds/
    hl_state.py       # REST poll
    lighter_state.py  # websocket + snapshot timer
    universe.py       # both venues' market lists
  upload.py       # S3 sync; its own entrypoint, run on a timer
deploy/
  fundr-recorder.service   # systemd unit, Restart=always
  fundr-upload.service / .timer
  bootstrap.sh             # instance setup from a clean AMI
tests/recorder/            # offline tests with fake feeds
```

`fundr.sources.hl_api` and `fundr.sources.lighter_api` are reused for REST. The websocket handling
is new code: the probe's socket handling is what failed in Phase 1, and the handoff says
explicitly not to adopt `probes/p09_live.py` as the recorder.

## Record format

Every record is the venue's raw payload inside an envelope:

```json
{"t_ms": 1789812743255, "feed": "lighter_state", "venue": "lighter", "seq": 41822,
 "n_msgs": 47, "stale": false, "payload": { ... exactly as received ... }}
```

- `t_ms` — capture time (recorder clock, UTC).
- `seq` — monotonic per feed, so a missing record is detectable.
- `n_msgs` — messages seen for that market in the interval; the counter that made Phase 1's
  failure diagnosable. Absent for REST feeds.
- `stale` — true when the snapshot repeats values from a feed that has gone quiet.
- Gap records replace the payload: `{"t_ms", "feed", "type": "gap", "from_ms", "to_ms", "reason"}`.

Files: `<feed>/date=YYYY-MM-DD/<feed>-<instance-id>-<date>.jsonl.gz`, the same layout locally and
in S3 under `recorder/v1/`.

## The five requirements, and the mechanism for each

From [P9's "Requirements this places on the Phase 2 recorder"](../../phase1/evidence/P9-live-probe.md),
all produced by Phase 1's own 95-minute loss:

1. **Never gate feed recording on a REST poll.** Each task has its own timer, connection and file.
   The failure mode is structurally impossible, not merely guarded against.
2. **Detect a stalled feed.** Two consecutive zero-`n_msgs` intervals force a reconnect, and
   snapshots are marked `stale` until traffic resumes. A stale value is never written as fresh.
3. **Enforce timeouts out-of-band.** Every network call runs under `asyncio.wait_for`. A client
   timeout of 30 s failed to bound an 84-minute read in Phase 1. A timeout writes a gap record and
   the loop continues.
4. **Detect wall-clock jumps.** Each cycle compares elapsed to expected time; more than two
   intervals is written as a gap record naming the suspected cause.
5. **Alert on coverage, not counts.** `health.json`, rewritten every minute and uploaded with the
   data, carries `status: ok | degraded | broken` plus, per feed and market, snapshots expected vs
   received this hour and today, last message time, reconnect count, and open gaps. Phase 1's lost
   hour was a 0.4% difference in record count — invisible to any count-based check.

Restart policy: systemd restarts the process on crash and on boot; the supervisor restarts an
individual task that dies without taking the others down.

## AWS footprint

- `t4g.nano`, 8 GB gp3 disk, `ap-northeast-1`.
- **No inbound ports.** Shell access via AWS Session Manager, so there is no SSH key and nothing
  exposed.
- Instance role scoped to the one S3 bucket; no access keys on the instance. The user's
  `xeno-admin` keys stay on the laptop.
- One private, versioned bucket; objects older than 90 days transition to cheaper storage.
- Uploads every 10 minutes plus a sweep at day rollover; local files deleted after 7 days once
  confirmed present in S3.
- Expected cost: **$4–5/month**, dominated by the instance. Volume ~700k rows/day, ~30 MB
  compressed.

## Testing

**Offline unit tests** (fake feeds, no network): writer rotation and atomicity; coverage counters;
stall detection (two empty intervals → reconnect + `stale`); gap detection via simulated clock
jump; envelope and sequence numbering.

**One-hour local run before deploying:** snapshot counts per market, non-zero message counters, no
gaps, and Lighter values matching the venue live.

**First-day validation on the instance:** compare recorded Lighter running funding against settled
rates from the venue's own API for the same hours — the check from
[P9](../../phase1/evidence/P9-live-probe.md), which should reproduce its 21/21 match. A failure
here means the recorder is wrong, caught on day one rather than in Phase 11.

## Done when

- The service runs on the instance under systemd, restarts on reboot, and has recorded **seven
  consecutive days** with expected-vs-actual snapshots agreeing per market per hour.
- `health.json` reports `ok`, and is readable from S3 without logging into the instance.
- Day-one validation passed.
- The Lighter formula re-validation from Phase 1's open caveat passes on the new data: **≥100
  off-baseline market-hours, spanning more than one day, including at least one negative-settling
  hour** ([handoff §8 action 4](../../phase1/handoff.md)).
- The AWS resources, their cost and how to reach them are documented in the repo.

## Risks

- **Silent failure.** With no email alerting (user's choice), a broken recorder is only noticed
  when someone reads `health.json`. Mitigated by systemd restarts and per-task supervision; not
  eliminated.
- **A recorder bug corrupts the record.** Mitigated by capturing raw and validating on day one.
- **Venue changes a field or endpoint.** The universe task and coverage counters surface it as
  degraded coverage rather than silence.
- **Instance loss.** S3 upload every 10 minutes bounds the loss to minutes, not days.
