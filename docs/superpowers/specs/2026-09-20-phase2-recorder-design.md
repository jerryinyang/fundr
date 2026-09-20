# Phase 2a — The Always-On Funding Recorder: Design

Date: 2026-09-20 (revised the same day after an independent review; see *Review corrections*)
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
  ten (H2/Phase 7), and unlike premium it is at least partly recoverable later — Hyperliquid fills
  from its archive (2025-05-25 onward, ~$27/year,
  [P3](../../phase1/evidence/P3-hl-node-fills.md)) and Lighter fills from 0xArchive's paid tier.
  **The Lighter side is weaker than a first reading suggests:** 0xArchive's *measured* Lighter
  coverage starts **2025-08-25** — its own ingest start, not per-market listing dates — and is only
  **84.2–97.4% complete** by market ([P8](../../phase1/evidence/P8-oxarchive.md)). The vendor's
  docs claim per-fill rows from January 2025; that claim is unverified and contradicted by the
  measurement. **Phase 7 must therefore treat buy/sell imbalance as a backfill decision, not as
  data already being collected, and must decide by the end of Phase 3 whether to buy the Lighter
  trade history or drop the feature** — every month of delay is a month of Lighter fills ageing
  behind a vendor whose completeness is already imperfect.

## Decisions taken during design

| Topic | Decision |
|---|---|
| Host | AWS `t4g.micro` (1 GB) in `us-east-1`, ~$6/month. This laptop is disqualified: host suspension is the leading explanation for Phase 1's 95-minute data loss |
| Storage | Hourly compressed files per feed on the instance, uploaded to a private S3 bucket; local copy kept 7 days |
| Coverage | All markets on both venues (~234 HL, ~246 Lighter), not just the research universe |
| Trades | Not recorded (see Scope) |
| Alerting | Health file only, no email. Consequence: the service must restart itself, since nothing else is watching |
| Recorder scope | Capture only, raw as received |
| Provisioning | Claude provisions the AWS resources using the user's credentials, presenting each action and its cost before running it |

## Architecture

One Python service, three independent tasks, sharing nothing that can block another. **All network
I/O is async** (`httpx.AsyncClient`, `websockets`), each task with its own client and connection
pool — see *Review corrections* #1.

| Task | Source | Cadence | Writes |
|---|---|---|---|
| HL market state | `metaAndAssetCtxs` + `predictedFundings` (REST) | 60 s | one row per market per cycle |
| Lighter market state | `market_stats` websocket, all markets | event-driven + 60 s heartbeat + forced pre-boundary snapshot | see below |
| Universe | HL `meta` + Lighter `/api/v1/orderBooks` + Lighter `/api/v1/orderBookDetails` | 5 min | market list with status, listing dates, and per-market funding parameters |

### Lighter capture rule (the heart of the design)

Lighter's `premium` is a running mean that resets at each settlement, and **only the last in-hour
reading reproduces the settled rate** (16/16 hours; a mean of snapshots scored 0/12 —
[P7](../../phase1/evidence/P7-funding-formulas.md)). Those fields refresh about once a minute, so
a fixed 60-second timer can alias against them and permanently drop the final value. The recorder
therefore writes a Lighter record when **any** of these occurs:

1. `premium` or `current_funding_rate` changes value for that market (event-driven — never miss a
   distinct value);
2. a 60-second heartbeat elapses (so a quiet market still proves the feed is alive);
3. **the hour is about to close** — a forced snapshot of every market at `HH:59:45` and again at
   `HH:59:57`, which is what guarantees the last pre-settlement reading is on disk.

Each record carries the message counter and a separate counter of how many times those two fields
changed in the interval.

### Universe task

Runs every 5 minutes (not 15: it is the only source Lighter delisting dates will ever have, so its
cadence is the resolution of every future delisting timestamp). It also pulls
`/api/v1/orderBookDetails` for all markets, which is the **only** place
`funding_premium_multiplier`, `funding_clamp_small/big` and `base_interest_rate` appear
([P6](../../phase1/evidence/P6-lighter-market-state.md)) — without it, Phase 1's open question
about markets whose multiplier ≠ 1 can never be answered.

New markets found by the universe task are subscribed on the Lighter feed **without dropping the
socket**; delisted ones are unsubscribed. A market listed mid-run otherwise loses its first hours
of premium, and newly-listed mid caps are exactly the research universe.

```
src/fundr/recorder/
  config.py       # venues, cadences, paths, bucket name
  writer.py       # hourly rotating .jsonl.gz per feed; member closed + fsync each upload interval
  health.py       # per-feed, per-market coverage counters → health.json
  supervisor.py   # starts the tasks, restarts a dead task, owns the watchdogs
  feeds/
    hl_state.py       # async REST poll
    lighter_state.py  # websocket + event/heartbeat/boundary snapshot logic + dynamic subscribe
    universe.py       # both venues' market lists + Lighter funding parameters
  upload.py       # S3 sync; its own entrypoint, run on a timer
deploy/
  fundr-recorder.service   # systemd: Restart=always, RestartSec=10, StartLimitIntervalSec=0
  fundr-upload.service / .timer
  bootstrap.sh             # instance setup from a clean AMI, incl. chrony time sync
tests/recorder/            # offline tests with fake feeds
```

The recorder imports **no polars** — `fundr.sources`' heavy imports stay out of its path so the
service's memory footprint is small and its startup is fast. REST calls reuse the endpoint
knowledge in `fundr.sources`, re-expressed against `httpx.AsyncClient`; the websocket handling is
new code (the probe's is what failed in Phase 1, and the handoff says not to adopt it).

## Record format

```json
{"t_ms": 1789812743255, "mono_ns": 81234567890, "feed": "lighter_state", "venue": "lighter",
 "seq": 41822, "rec_ver": "2a.1", "git_sha": "abc1234",
 "n_msgs": 47, "n_funding_changes": 1, "trigger": "boundary", "stale": false,
 "payload": { ... exactly as received ... }}
```

- `t_ms` wall clock, `mono_ns` monotonic — the pair distinguishes an NTP step from host suspension
  (requirement 4 would otherwise false-positive on every clock correction).
- `seq` monotonic per feed, **including on gap records**, so a missing record is always detectable.
- `rec_ver` / `git_sha` — provenance, which the parent design requires of every dataset.
- `trigger` — `change | heartbeat | boundary`, so analysis can tell why a row exists.
- Gap records: `{"t_ms", "mono_ns", "feed", "seq", "type": "gap", "from_ms", "to_ms", "reason"}`.

Files: `<feed>/date=YYYY-MM-DD/hour=HH/<feed>-<instance-id>-<hour>.jsonl.gz`, identical locally and
in S3 under `recorder/v1/`. Hourly parts are immutable once closed, so an upload never rewrites a
file it has already stored.

## The five requirements, and the mechanism for each

From [P9](../../phase1/evidence/P9-live-probe.md), all produced by Phase 1's own 95-minute loss:

1. **Never gate feed recording on a REST poll.** Each task owns its timer, connection and file, and
   all I/O is async so no call can block another task's loop. Tested explicitly (see Testing).
2. **Detect a stalled feed.** Two consecutive zero-`n_msgs` intervals force a reconnect; snapshots
   are marked `stale` until traffic resumes. A stale value is never written as fresh.
3. **Enforce timeouts out-of-band.** Every call runs under `asyncio.wait_for` — which is only
   effective because the calls are genuinely async. A client-level 30 s timeout failed to bound an
   84-minute read in Phase 1. A timeout writes a gap record; the loop continues.
4. **Detect wall-clock jumps.** Each cycle compares both clocks; a monotonic gap beyond two
   intervals is host suspension or a hang, a wall-clock-only jump is a time correction. Both are
   written as gap records naming which.
5. **Alert on coverage, not counts.** `health.json`, rewritten every minute and uploaded with the
   data, carries `status` plus per-feed, per-market expected-vs-received counts, last message time,
   reconnect count and open gaps. Phase 1's lost hour was a 0.4% count difference — invisible to
   any count-based check.

**`status` thresholds** (so the field is testable): `ok` = every active market ≥ 95% of expected
snapshots in the last hour, no open gap older than 5 minutes, both feeds reconnected < 5 times in
the hour. `degraded` = any market between 50% and 95%, or an open gap under 30 minutes.
`broken` = any feed below 50%, an open gap over 30 minutes, or no write in 5 minutes.

Restart policy: systemd restarts the process on crash and on boot, with `RestartSec=10` and
`StartLimitIntervalSec=0` so it can never exhaust a start limit and stay dead — the failure mode
the no-alerting choice is least able to survive. The supervisor restarts an individual task that
dies without taking the others down.

## AWS footprint

- `t4g.micro` (1 GB), 8 GB gp3, `us-east-1`. 512 MB was rejected: ~246 Lighter markets at roughly
  190 messages/second plus gzip leaves too little headroom. Region is the cheap one; the earlier
  "close to the venues" rationale was an untested inference and irrelevant at a 60-second cadence.
- **No inbound ports.** Shell via AWS Session Manager.
- Instance role scoped to the one S3 bucket; no access keys on the instance.
- One private, versioned bucket, with **both** a current-version transition (90 days → cheaper
  storage) and a **noncurrent-version expiry (7 days)** — without the second rule, versioning plus
  frequent uploads accumulates noncurrent copies indefinitely.
- Uploads every 10 minutes; each hourly part is closed and fsynced before upload, so what lands in
  S3 is always a complete, readable file. Local files deleted after 7 days once confirmed in S3.
- **Cost, stated honestly: ~$8–10/month.** Instance ~$6.13, public IPv4 ~$3.60/month (charged since
  2024, and required unless one pays ~$30/month for VPC interface endpoints — so Session Manager is
  not free either way), disk ~$0.64, S3 and requests under $1. Volume ~700k rows/day, ~30 MB
  compressed.

## Testing

**Offline unit tests** (fake feeds, no network):
- writer rotation, atomicity, and that a closed hourly part is complete and readable;
- coverage counters and the `status` thresholds above;
- stall detection (two empty intervals → reconnect + `stale`);
- clock handling (simulated suspension vs simulated NTP step → the right gap record);
- envelope and sequence numbering, including `seq` continuity across gap records;
- **the Phase 1 bug itself**: a REST poll that never returns, asserting that Lighter snapshots keep
  being written throughout and that the poll is cancelled by its watchdog;
- the Lighter capture rule: a synthetic feed whose `premium` changes at `HH:59:50` must produce a
  boundary record carrying that value.

**One-hour local run before deploying:** snapshot counts per market, non-zero message counters, no
gaps, memory and CPU headroom measured, and Lighter values matching the venue live.

**First-day validation on the instance, both venues:**
- Lighter: recorded running funding vs settled rates from the venue's own API for the same hours —
  should reproduce P9's 21/21 exact match.
- Hyperliquid: recorded running funding vs settled, expecting P9's residual of ~1e-7–1e-6 and no
  exact match off-baseline. A different picture means the HL feed or the capture is wrong.

## Done when

- The service runs under systemd on the instance, survives a reboot, and has recorded **seven
  consecutive days** at `status: ok`, with per-market coverage ≥ 95% of expected snapshots per hour
  for every market active in that hour (expected counts derived from the universe feed, so markets
  listed or delisted mid-window are handled).
- `health.json` is readable from S3 without logging into the instance.
- Day-one validation passed on both venues.
- The Lighter formula re-validation from Phase 1's open caveat passes on the new data: **≥100
  off-baseline market-hours, spanning more than one day, including at least one negative-settling
  hour**, and — now possible because the universe task records it — **a check of at least one
  market with `funding_premium_multiplier` ≠ 1 if any exists in the recorded universe**
  ([handoff §8 action 4](../../phase1/handoff.md)).
- The AWS resources, their cost, and how to reach them are documented in the repo.

## Risks

- **Silent failure.** With no email alerting (user's choice), a broken recorder is noticed only
  when someone reads `health.json`. Mitigated by systemd restart policy and per-task supervision;
  not eliminated.
- **A recorder bug corrupts the record.** Mitigated by capturing raw and validating on day one.
- **Venue changes a field or endpoint.** Surfaces as degraded coverage rather than silence.
- **Instance loss.** Upload every 10 minutes bounds the loss to minutes.
- **Lighter trade history ages.** See Scope: the backfill decision has a deadline.

## Review corrections

An independent review of the first draft found issues that would have silently lost the data the
recorder exists to capture. All are addressed above; recorded here so the reasoning is not lost:

1. **Sync clients cannot be bounded by `asyncio.wait_for`.** `fundr.sources.hl_api` and
   `lighter_api` use `httpx.Client` (synchronous); awaiting one from the event loop blocks the loop
   and every task on it — Phase 1's bug in a new form. Now: all I/O async, per-task clients.
2. **A fixed 60-second snapshot aliases against Lighter's ~1-minute field refresh** and can drop the
   last in-hour premium — the single value that reproduces settled funding. Now: event-driven plus
   heartbeat plus forced pre-boundary snapshots.
3. **`funding_premium_multiplier` was not captured anywhere**, making one of the Done-when criteria
   unmeetable. Now: `orderBookDetails` in the universe task.
4. **New Lighter listings were never subscribed** until a restart. Now: dynamic subscription.
5. **Lighter fill backfill start was wrong** (docs' January 2025 vs measured 2025-08-25, 84–97%
   complete), so the trades deferral rested on an overstated fallback. Now corrected, with a
   decision deadline.
6. **Daily files flushed only at rotation** contradicted the 10-minute upload claim. Now: immutable
   hourly parts, fsynced before upload.
7. **Cost understated** (~2×): the IPv4 charge and unbounded noncurrent versions. Now corrected,
   with a noncurrent-expiry rule.
8. **No test for requirement 1** — the actual Phase 1 bug. Now tested explicitly.
9. **512 MB was too small** for the websocket fan-out plus gzip. Now `t4g.micro`, with polars kept
   out of the import path and headroom measured in the local run.
10. **Done-when was not testable** (no thresholds, no tolerance). Now defined.
11. **systemd `Restart=always` alone** can give up permanently at the start limit. Now
    `StartLimitIntervalSec=0`, plus chrony and monotonic-clock handling.
12. **Envelope gaps**: no `seq` on gap records, no provenance, no count of funding-field changes.
    Now included.
13. **Day-one validation checked Lighter only.** Now both venues.
14. **Universe at 15 minutes** would have capped future Lighter delisting timestamps at
    15-minute resolution. Now 5 minutes.

Still outstanding from [handoff §8 action 1](../../phase1/handoff.md), and made step one of
provisioning: copy `data/phase1/p09/live.jsonl` — the only Lighter premium history in existence —
into the new bucket.
