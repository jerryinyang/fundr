# Phase 2a — The Always-On Funding Recorder: Design

Date: 2026-09-20 (revised the same day after an independent review; revised again 2026-09-21 after
a final review of the implementation plan, and amended again after deployment — see *Review
corrections*, *Review corrections, second round*, *Review corrections, third round* and
*Review corrections, fourth round*)
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
| Host | AWS `t4g.micro` (1 GB), ~$6/month. This laptop is disqualified: host suspension is the leading explanation for Phase 1's 95-minute data loss. Region was `us-east-1`; **now `eu-central-1`** — Lighter geo-blocks US jurisdictions (*fourth round*) |
| Storage | Hourly compressed files per feed on the instance, uploaded to a private S3 bucket; local copy kept 7 days |
| Coverage | All markets on both venues (~234 HL, ~246 Lighter), not just the research universe |
| Trades | Not recorded (see Scope) |
| Alerting | Health file only, no email. Consequence: the service must restart itself, since nothing else is watching |
| Recorder scope | Capture only, raw as received |
| Provisioning | Claude provisions the AWS resources using the user's credentials, presenting each action and its cost before running it |

## Architecture

One Python service, three independent tasks, sharing nothing that can block another. **All network
I/O is async** (`httpx.AsyncClient`, `websockets`), each task with its own client and connection
pool — see *Review corrections* #1. Carve-out: the S3 uploader (`upload.py`) runs synchronously
as a separate systemd `oneshot` unit on a timer, not inside this service's event loop, so it
cannot starve a feed task — the reason the async constraint exists does not apply to it.

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
3. **the hour is about to close** — a forced snapshot of every market at `HH:59:30` and again at
   `HH:59:52`, which is what guarantees the last pre-settlement reading is on disk. (Originally
   `HH:59:45`/`HH:59:57`; 3 seconds leaves no margin for clock skew, and a fast clock would file
   the hour's single most important record into the *next* hour's partition.)

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
  fundr-recorder.service   # systemd: [Service] Restart=always/RestartSec=10,
                           #          [Unit] StartLimitIntervalSec=0 (it is a Unit directive;
                           #          in [Service] systemd ignores it and keeps the default)
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
 "seq": 41822, "run_id": "3f2b...", "rec_ver": "2a.1", "git_sha": "abc1234",
 "n_msgs": 47, "n_funding_changes": 1, "trigger": "boundary", "stale": false,
 "age_ms": 12000, "ws_ts": 1789812741, "ws_type": "update/market_stats",
 "payload": { ... exactly as received ... }}
```

- `t_ms` wall clock, `mono_ns` monotonic — the pair distinguishes an NTP step from host suspension
  (requirement 4 would otherwise false-positive on every clock correction).
- `seq` monotonic per feed, **including on gap records**, so a missing record is always detectable.
- `run_id` — a uuid4 minted once per process. `seq` restarts at 1 on every process start, so
  without it a restart is indistinguishable from a lost block of records: group on `run_id` first,
  then look for holes in `seq`.
- `rec_ver` / `git_sha` — provenance, which the parent design requires of every dataset.
- `trigger` — `change | heartbeat | boundary`, so analysis can tell why a row exists.
- `age_ms` — how old the captured values were when the record was written, per market. A snapshot
  is never written as if fresh; a stale one carries its real age and `stale: true`.
- `ws_ts` / `ws_type` — the venue's own message timestamp and type. Capture-time-only data; the
  venue's stamp is not recoverable later.
- Gap records: `{"t_ms", "mono_ns", "feed", "seq", "run_id", "type": "gap", "from_ms", "to_ms",
  "reason"}`.

Files: `<feed>/date=YYYY-MM-DD/hour=HH/<feed>-<instance-id>-<hour>.jsonl.gz`, identical locally and
in S3 under `recorder/v1/`. The **instance id** (`FUNDR_INSTANCE_ID`, defaulting to the hostname)
is not optional: without it two recorders sharing the bucket collide on one key. An hourly part is
appended to while its hour is open and is immutable once closed; because gzip members concatenate
and each flush closes a complete member, the open part is a valid readable file at all times and
is uploaded along with the closed ones.

## The five requirements, and the mechanism for each

From [P9](../../phase1/evidence/P9-live-probe.md), all produced by Phase 1's own 95-minute loss:

1. **Never gate feed recording on a REST poll.** Each task owns its timer, connection and file, and
   all I/O is async so no call can block another task's loop. Tested explicitly (see Testing).
2. **Detect a stalled feed.** Two consecutive zero-`n_msgs` **heartbeat** intervals force a
   reconnect — heartbeats only, because the two boundary snapshots are 22 seconds apart and
   counting them as intervals would trip the rule at every hour boundary. A stale value is never
   written as fresh: freshness is tracked **per market** (one busy market must not make 245 silent
   ones look alive), every record carries `age_ms`, and anything older than two heartbeat
   intervals is `stale: true`.
3. **Enforce timeouts out-of-band.** Every call runs under `asyncio.wait_for` — which is only
   effective because the calls are genuinely async. A client-level 30 s timeout failed to bound an
   84-minute read in Phase 1. A timeout writes a gap record; the loop continues.
4. **Detect wall-clock jumps.** Each cycle compares both clocks; a monotonic gap beyond two
   intervals is host suspension or a hang, a wall-clock-only jump is a time correction. Both are
   written as gap records naming which.
5. **Alert on coverage, not counts.** `health.json`, rewritten every minute and uploaded with the
   data **on every upload cycle**, carries `status` plus per-feed, per-market expected-vs-received
   counts, last message time, reconnect count and open gaps. Phase 1's lost hour was a 0.4% count
   difference — invisible to any count-based check. Coverage counts **venue** events, never the
   recorder's own timer: counting our own writes gives a market whose subscription died silently a
   perfect score forever. Expected counts come from the universe feed's active market list, so a
   listed-but-never-heard-from market has an entry reading zero rather than no entry at all.

**`status` thresholds** (so the field is testable): `ok` = every active market ≥ 95% of expected
snapshots in the last hour, no open gap older than 5 minutes, both feeds reconnected < 5 times in
the hour. `degraded` = any market between 50% and 95%, or an open gap under 30 minutes.
`broken` = any feed below 50%, an open gap over 30 minutes, or no write in 5 minutes *(per feed,
scaled by that feed's own cadence — see
[Review corrections, sixth round](#review-corrections-sixth-round))*.

**"In the last hour" means a trailing 60-minute window, prorated.** Read as the *calendar* hour it
is unimplementable: a feed expecting 60 snapshots an hour scores 1/60 = 1.7% one minute past
`HH:00` and reads `broken` until roughly `HH:30`, every hour, so the Done-when criterion of seven
consecutive days at `ok` could never be met. Coverage is therefore judged over the trailing 60
minutes, with each market's expectation scaled by how much of that window has elapsed since the
market was first seen; a market younger than one prorated expected event is reported but not
judged. All of this runs off a single, never-decreasing clock reading — a shared counter that can
roll backwards wipes live coverage whenever a record lands on the wrong side of a boundary.

Restart policy: systemd restarts the process on crash and on boot, with `RestartSec=10` and
`StartLimitIntervalSec=0` so it can never exhaust a start limit and stay dead — the failure mode
the no-alerting choice is least able to survive. The supervisor restarts an individual task that
dies without taking the others down.

## AWS footprint

- `t4g.micro` (1 GB), 8 GB gp3, `us-east-1`. 512 MB was rejected: ~246 Lighter markets at roughly
  190 messages/second plus gzip leaves too little headroom. Region is the cheap one; the earlier
  "close to the venues" rationale was an untested inference and irrelevant at a 60-second cadence.
  *(Region superseded: the recorder runs in `eu-central-1`, because Lighter refuses websocket
  connections from US jurisdictions and the cheap region is therefore unusable. See
  [Review corrections, fourth round](#review-corrections-fourth-round).)*
- **No inbound ports.** Shell via AWS Session Manager. *(Not what was deployed — the account
  denies all IAM, so there is no instance role and therefore no Session Manager. See
  [Review corrections, third round](#review-corrections-third-round).)*
- Instance role scoped to the one S3 bucket; no access keys on the instance. *(Also not deployed,
  same reason: the instance has no profile, and the recorder writes to local EBS only.)*
- One private, versioned bucket, with **both** a current-version transition (90 days → cheaper
  storage) and a **noncurrent-version expiry (7 days)** — without the second rule, versioning plus
  frequent uploads accumulates noncurrent copies indefinitely.
- Uploads every 10 minutes; each hourly part is closed and fsynced before upload, so what lands in
  S3 is always a complete, readable file. The **current** hour's part and `health.json` go up on
  every cycle too — skipping the open hour would put up to ~70 minutes of data at risk on instance
  loss, and `health.json` is the only thing the no-alerting decision leaves visible from outside.
  Local files deleted after 7 days once confirmed in S3.
- **Cost, stated honestly: ~$8–10/month** as originally scoped for `us-east-1` with an instance
  role. *(Superseded by what actually shipped: ~$11.23/month in `eu-central-1` with no instance
  role — Frankfurt's `t4g.micro` and gp3 rates are both higher, and the region move itself was
  necessary because Lighter geo-blocks `us-east-1`. See
  [Review corrections, fourth round](#review-corrections-fourth-round) for the exact breakdown;
  this original estimate is left here only as the pre-deployment baseline it was.)* Instance
  ~$6.13, public IPv4 ~$3.60/month (charged since
  2024, and required unless one pays ~$30/month for VPC interface endpoints — so Session Manager is
  not free either way), disk ~$0.64, S3 and requests under $1. **Volume ~1M+ rows/day, ~40–50 MB
  compressed** (the earlier ~700k was too low: Hyperliquid alone is 234 markets × 1440 polls =
  337k, and Lighter contributes ~246 markets × 60 heartbeats = 354k plus one record per
  premium/rate change plus two boundary records per market per hour, ~12k more). S3 costs are
  unaffected at this scale; the number matters for disk headroom and for sizing the backfill.

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

## Review corrections, second round

A final review of the implementation plan (`docs/superpowers/plans/2026-09-20-phase2a-recorder.md`)
found defects that reach back into this document. Recorded here so the reasoning survives:

1. **"≥ 95% of expected snapshots in the last hour" was unimplementable as written.** Read as the
   calendar hour it scores every feed `broken` for the first ~30 minutes of every hour, so seven
   consecutive days at `ok` could never happen. Now defined as a **trailing 60-minute window with
   prorated expectations**, off a single never-decreasing clock reading. See *The five
   requirements*, item 5.
2. **Boundary offsets moved from `HH:59:45`/`HH:59:57` to `HH:59:30`/`HH:59:52`.** Three seconds
   leaves no margin for clock skew, and a fast clock would write the hour's critical record into
   the next hour's partition — losing exactly the value the whole design exists to capture.
3. **The two-interval silence rule is scoped to heartbeat intervals.** The 22-second gap between
   the boundary snapshots would otherwise force a reconnect at every hour boundary.
4. **Staleness is per market, with `age_ms` on every record.** A single global silence counter let
   one active market mask every silent one, and reconnects wrote stale values as fresh.
5. **The envelope gained `run_id`.** `seq` restarts at 1 on every process start, so gap detection
   across a restart was impossible; `run_id` partitions the sequence.
6. **The instance id in filenames is mandatory, not decorative** — two instances would otherwise
   overwrite each other on the same S3 key.
7. **Coverage counts venue events and expectations come from the universe feed.** Counting the
   recorder's own writes gave a dead subscription a perfect score, and a listed-but-silent market
   had no health entry at all. This is what the Done-when clause already required.
8. **The venue's own message timestamp is captured** (`ws_ts`/`ws_type`). Keeping only
   `market_stats` discarded capture-time-only data permanently.
9. **`health.json` and the current hour's part are uploaded every cycle.** `health.json` was never
   uploaded at all despite the Done-when requiring it in S3, and skipping the open hour made
   "instance loss bounded to minutes" untrue by up to ~70 minutes.
10. **Volume estimate corrected** from ~700k to ~1M+ rows/day; the old figure did not add up
    against 234 HL markets × 1440 polls alone.
11. **`StartLimitIntervalSec=0` belongs in `[Unit]`.** In `[Service]` systemd ignores it and keeps
    the default start limit — the setting read as present while doing nothing, which is the one
    failure the no-alerting choice cannot survive.
12. **`funding_premium_multiplier` is in hundredths** (live: `100` on 137 markets, `50` on 96, `1`
    on 2; P7 confirmed `100` means 1.0). The recorder stores it raw; Phase 4 divides once. Noted so
    it is not scaled twice — and the 98 non-1.0 markets answer Phase 1's open question directly.
13. **The repository had no remote**, so `bootstrap.sh`'s clone could never have run. Now: a
    private GitHub repo with a read-only deploy key, and configuration passed to the bootstrap
    through the user-data environment rather than positional arguments user-data cannot supply.

## Review corrections, third round

Deploying (Task 10) hit a constraint no amount of design could have anticipated: **the AWS
account denies every IAM action**. `iam:CreateRole` and `iam:ListInstanceProfiles` both return
`AccessDenied` for the available credentials; S3 and EC2 work normally. Two of this document's
AWS-footprint assumptions therefore could not be honoured, and the deployment deliberately
deviates from them. Recorded here so the deviation is visible and reversible:

1. **No instance profile, so no Session Manager, so SSH.** Session Manager requires the instance
   to assume a role carrying `AmazonSSMManagedInstanceCore`. With IAM denied, that role cannot be
   created, and an instance with no profile can never register with SSM. The deployment instead
   uses an EC2 key pair and a security group whose only inbound rule is **TCP 22 from the
   operator's current public IP as a /32**, with key-only authentication (Amazon Linux 2023 ships
   `PasswordAuthentication no`). **Cost of the deviation:** port 22 is reachable from one address
   instead of from nowhere, and the private key at `auth/fundr-recorder.pem` (gitignored, mode
   0600) becomes a credential that can reach the box. The operator's IP changes; the security
   group rule must then be updated, which is a routine documented in the runbook.
2. **No instance role, so no S3 uploads: the recorder records to local disk only.** The uploader
   reads `FUNDR_BUCKET`; it is left **empty** in `/etc/fundr/recorder.env`, so every timer firing
   is a no-op and nothing is written to the bucket. **Cost of the deviation:** the "instance loss
   bounded to minutes" property is gone — the only copy of the recording lives on the instance's
   8 GB gp3 volume. That volume persists across reboots and stop/starts, and at ~30 MB/day
   (measured in the local soak, compressed) 8 GB is months of headroom, but an instance
   *termination* or a volume failure loses the data. `health.json` is likewise readable only over
   SSH, not from S3, so the Done-when clause "readable from S3 without logging into the instance"
   is **not satisfied** until the role exists. The bucket itself is already provisioned correctly
   (private, versioned, both lifecycle rules) and already holds the irreplaceable Phase 1
   recording at `phase1/p09/live_2026-09-19.jsonl`.

**Exact condition for reverting both deviations.** When the account grants IAM (or an
administrator does this on the user's behalf):

1. Create role `fundr-recorder-role` trusted by `ec2.amazonaws.com`, with an inline policy
   allowing `s3:PutObject`, `s3:GetObject`, `s3:ListBucket` and `s3:HeadObject` on
   `arn:aws:s3:::fundr-recorder-<AWS_ACCOUNT_ID>-us-east-1` and `.../*` **only**, plus the managed
   policy `arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore`.
2. Create the matching instance profile and associate it with the running instance
   (`ec2:AssociateIamInstanceProfile` — no restart or redeploy needed).
3. Set `FUNDR_BUCKET=fundr-recorder-<AWS_ACCOUNT_ID>-us-east-1` in `/etc/fundr/recorder.env` and
   `systemctl restart fundr-upload.timer`; the next firing backfills everything still on disk,
   because the upload manifest is idempotent and pruning is confirmed-only.
4. Revoke the inbound TCP 22 rule from `fundr-recorder-sg` once a Session Manager shell is
   confirmed working, and delete the local private key.

Until step 4 lands, the spec's "no inbound ports" line and its instance-role line describe the
intended end state, not the deployed one.

## Review corrections, fourth round

The recorder was deployed to `us-east-1` and recorded Hyperliquid correctly, but **Lighter's edge
refused every websocket handshake from the instance**, returning `HTTP/1.1 400 Bad Request` with
`{"code": 20558, "message": "You are accessing Lighter from a restricted jurisdiction..."}`. The
same handshake succeeded from the operator's laptop, so the refusal is by client-IP jurisdiction,
not a bug in the recorder. `lighter_state` coverage sat at `0.0` with an open gap and a steady
reconnect count — correct recorder behaviour, but no Lighter data at all, which is the feed the
whole design exists to capture (*Lighter capture rule*, above).

Whether to run the recorder from a different jurisdiction is a **terms-of-service judgement about
Lighter's jurisdiction restrictions, not an engineering one.** It was put to the user in those
terms, and **the user made the call** to relocate. That decision is recorded here as the user's,
not the implementer's.

**The recorder therefore runs in `eu-central-1` (Frankfurt).** A probe from that region returned
`WS=ACCEPTED HTTP/1.1 101 Switching Protocols`, and the deployed instance confirms it: 1,135
`lighter_state` records in the first hour, every one with `n_msgs > 0`, zero `ws_error` records
and zero gaps. Nothing else about the design changed — same instance type, same volume, same
single-SSH-source security group, still no instance profile, `FUNDR_BUCKET` still empty. **No
proxy, VPN or traffic-masking software is installed**; the move is a plain relocation of where the
instance runs.

**Cost of the deviation:** Frankfurt is dearer than N. Virginia. `t4g.micro` is $0.0092/hr against
$0.0084 and gp3 is $0.0952/GB-month against $0.08, so the monthly total moves from ~$10.52 to
~$11.23 — about $0.71/month for a feed that was otherwise unobtainable. `deploy/aws_provision.py`
carries a per-region rate table so `plan` prints the right number.

**The us-east-1 partial recording is preserved.** Before the old instance was terminated its
`/var/lib/fundr` was copied to
`s3://fundr-recorder-<AWS_ACCOUNT_ID>-us-east-1/recorder/us-east-1-partial/` — ~2.6 hours of
Hyperliquid and universe data (plus the geo-blocked Lighter gap records, kept as the evidence of
the block). It is a separate prefix from `recorder/v1/`, carries a different instance id in every
filename and record, and must not be concatenated with the Frankfurt recording without accounting
for the ~5-minute cutover gap between them.

**What did not change:** the two third-round deviations still stand. The account still denies IAM,
so the Frankfurt instance still has no instance profile, still no Session Manager, still SSH from
one /32 (with a new per-region key pair, `fundr-recorder-eu`, private key at
`auth/fundr-recorder-eu.pem`, gitignored, mode 0600), and `FUNDR_BUCKET` is still empty — the
recording still lives only on the instance's EBS volume. The revert procedure above applies
unchanged, except that the bucket
(`fundr-recorder-<AWS_ACCOUNT_ID>-us-east-1`) is in `us-east-1` while the instance is in
`eu-central-1`; that is fine for S3 (cross-region access is a data-transfer charge, not a
permission problem) and adds roughly $0.02/GB egress to the upload cost once uploads are enabled.

## Review corrections, fifth round

**Deviation: deployment is an rsync of the working tree, not `deploy/user_data.sh` cloning over
git.** The brief called for the instance to fetch its own code at boot, via `user_data.sh`
cloning the private repo with a read-only deploy key stored on the instance
(`task-10-brief.md`, "Mint a read-only deploy key"). Task 10 deployed differently:
`deploy/bootstrap.sh` accepts `FUNDR_REPO` as optional, and when it is unset (as it is on both
the `us-east-1` and `eu-central-1` instances), it does not clone anything — it expects the
working tree to already be present at `/opt/fundr`, delivered by `rsync --rsync-path="sudo
rsync"` over the operator's existing SSH connection (see the runbook, "Redeploy a new commit").

**Why:** a deploy key is read-only for *code*, but it is still a private key that has to live on
the instance to be useful, and this design's stated goal is that **no secret that could reach
GitHub exists on the box at all** — the instance already carries no S3 credential and no instance
role by the third-round deviation; adding a git deploy key back in would reopen exactly the kind
of standing credential the rest of the design avoids. rsync needs no credential on the receiving
end beyond the SSH access the operator already has to deploy anything else (env, systemd units).

**Cost of the deviation:** deployment is no longer self-contained at boot — a fresh instance
cannot bootstrap itself from `user_data.sh` alone; it needs one rsync push from the operator's
machine before the first `bootstrap.sh` run. This is a manual step, not an automated one, on top
of what the brief specified. It does not affect the running recorder or its data, only how a new
instance or a new commit gets code onto the box.

**Condition for reverting:** if a public repo (or a secrets manager delivering a short-lived,
narrowly-scoped deploy token at boot rather than a static key file) becomes available, set
`FUNDR_REPO` and `bootstrap.sh` will clone/checkout instead of expecting an out-of-band rsync,
matching the brief's original design without further code changes.

## Review corrections, sixth round

A branch-wide review of the shipped code against this spec found two places where `health.py`
did not match the wording above. One is a deliberate, permanent deviation being recorded here for
the first time; the other was an implementation bug that has now been fixed in code rather than
in the spec, because the spec's behaviour is the one the seven-day gate actually needs.

**1. Deviation (permanent): the "no write in 5 minutes" `broken` threshold is per-feed, scaled by
cadence, not a flat 5 minutes.** `Health.set_cadence` / `_stale_threshold_ms` (`health.py:45-58`)
compute each feed's own stale threshold as `max(5 minutes, 3 × that feed's cadence)`. For
`hl_state` and `lighter_state` (cadences of a few seconds to a minute) this is indistinguishable
from the spec's flat 5 minutes. For `universe`, which is designed to write once every 300 seconds
(5 minutes) by cadence, a literal flat 5-minute threshold would read `broken` between every single
sweep, by construction — the exact bug the window-proration section above already fixed for
coverage, applied here to staleness instead. The per-feed threshold (900 seconds for `universe`,
three missed cycles rather than zero margin against one) is the correct fix, and the wording above
now says so. **Cost of the deviation:** a genuinely dead `universe` feed takes up to 15 minutes to
read `broken` instead of 5 — an acceptable trade against three-hourly false positives on a feed
that is not remotely time-critical.

**2. Bug, now fixed in code (not a deviation): `status()` degraded on ANY open gap, regardless of
age.** The spec says `ok` tolerates an open gap under 5 minutes old and `degraded` covers 5–30
minutes; the shipped `status()` instead treated the mere *presence* of an open gap — even one that
had been open for a fraction of a second — as enough to read `degraded`. Because `health.json` is
rewritten once a minute on its own timer (independent of when a gap opens or closes), a single bad
poll cycle whose gap happened to still be open at the instant of that minute's health write could
read `degraded` for that whole interval, purely on timing, for a blip the spec explicitly classes
as `ok`. Over seven consecutive days this is not a hypothetical: it is exactly the kind of one-off,
self-healing hiccup (one dropped HL poll, one Lighter reconnect) the coverage-not-counts design
elsewhere in this document exists to *not* punish, and it could cost an entire day of the
seven-day gate for something the recorder itself considered routine and already recovered from
before the next health write.

**Fixed in code, not in the spec**, because the spec's threshold is the one the gate needs:
`health.py` gained `OK_GAP_MS = 5 * 60_000`, and `status()`'s `degraded` condition now reads
`any(g > OK_GAP_MS for g in gaps)` instead of the bare truthiness of `gaps`. `broken`'s check
(`any(g > DEGRADED_GAP_MS for g in gaps)`, 30 minutes) was already correct and unchanged. A
regression test (`tests/recorder/test_health.py::test_a_gap_under_five_minutes_old_is_still_ok`)
pins both ends: a 30-second-old gap reads `ok`, a 5-minute-and-one-second-old gap reads `degraded`.
