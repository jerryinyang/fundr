# Day-one validation

Status: **HL pass on real data; Lighter re-check pending elapsed time.**

## What ran

```bash
uv run python scripts/validate_day1.py --root <downloaded-dir>
```

Data was pulled read-only over SSH (never written to on the instance) from the **us-east-1**
recorder (`i-005b76078218b1256`, terminated after this check — see *Situation update* below) into
a local scratch directory, `hl_state/` and `lighter_state/` only:

```bash
rsync -az -e "ssh -i auth/fundr-recorder.pem" --rsync-path="sudo rsync" \
  ec2-user@54.85.161.66:/var/lib/fundr/hl_state  <scratch>/
rsync -az -e "ssh -i auth/fundr-recorder.pem" --rsync-path="sudo rsync" \
  ec2-user@54.85.161.66:/var/lib/fundr/lighter_state  <scratch>/
```

The recorder had been running roughly 10:39–11:24 UTC on 2026-09-21 at that point — under an
hour, not a full day. Two clock hours are present: `10:00–11:00` (started ~20 min in, but its
last reading lands 12 s before the boundary) and `11:00–11:2x` (still open when pulled). This is
day-*zero* validation, not day-one; the seven-day and Lighter-formula gates in the brief's "After
Task 11" section are unaffected and still pending.

## Output (verbatim)

```
Lighter: no data (feed produced zero non-gap records)
HL: 234 complete market-hours kept, 234 excluded (n < 5 or lag_s outside [0, 120]s)
HL last-in-hour vs closing settlement (all coin-hours): {'n': 234, 'n_match': 168, 'rate': 0.717948717948718, 'mean_signed_error': 9.62393162393412e-10}
HL residual (all): median 0.0 max 1.025899999999993e-06
HL off-baseline (|settled| > 1.5e-05): {'n': 62, 'n_match': 0, 'rate': 0.0, 'mean_signed_error': 5.711290322581583e-09}
HL residual (off-baseline): median 7.744999999999872e-08 max 1.025899999999993e-06 min 2.999999999999802e-09
LIGHTER NO DATA
HL PASS
```

(Script exit code: 1 — non-zero because Lighter has no data yet, not because HL failed.)

## Hours included / excluded

- **234 market-hours kept**: every HL market's `10:00–11:00` hour. Its last recorded reading
  lands at `10:59:48 UTC`, 12 s before the boundary — well inside the `lag_s <= 120` filter —
  even though the recorder only started polling partway through the hour. This is exactly the
  behaviour the brief's correction describes: a late **start** doesn't invalidate an hour, only a
  late-enough **last** reading does, because the running premium is cumulative.
- **234 market-hours excluded**: every HL market's `11:00–11:2x` hour, still open when the data
  was pulled (`lag_s` of ~35 min, far outside the 120 s window). Correctly excluded, not a
  coverage problem.
- **Lighter: 0 kept, 0 excluded** — the feed produced only `gap` records in this window (the
  known `us-east-1` geo-block, see *Situation update*), so there is nothing to group into hours
  in the first place.

## Verdict per venue

### Hyperliquid — **PASS**, with numbers

Per P4/P9's own methodology (not the brief draft's plain aggregate), the 234 kept coin-hours
split into 168 **baseline** hours (settled funding pinned at HL's interest floor `±0.0000125`, or
`0.0` for a few zero-interest markets — a constant value that "matches itself" trivially at every
sample) and 62 **off-baseline** hours (the population that is actually informative).

- All 234: 168/234 exact matches (71.8%) — this number is not meaningful on its own; see below.
- **Off-baseline 62 coin-hours: 0/62 exact matches (0.0%)**, residual `|last − settled|`: median
  **7.74e-8**, min **3.0e-9**, max **1.03e-6**.

This matches Phase 1's finding exactly (P4: off-baseline residual ~4e-7 to 1.3e-6 across three
archived days; P9: mean 2.52e-7, median 2.07e-7, max 1.07e-6 live). The recorder's running
`funding` value never exactly equals the settled rate off-baseline, and the residual is in the
expected ~1e-9–1e-4 band with no exact match — the behaviour the design specifically depends on.

**Why the script now reports off-baseline separately.** The brief's draft check took the median
residual over *all* kept hours. On this sample, 168/234 (72%) hours happened to be baseline, which
pins their residual at exactly 0 and pulled the *aggregate* median for all 234 down to 0.0 too —
which reads as "HL matched exactly" even though every off-baseline hour (the only population that
can distinguish a working recorder from a broken one) never matched. `scripts/validate_day1.py`
was changed to split baseline vs. off-baseline (mirroring `fundr.analysis.rebuild_verdict`,
already used elsewhere in this codebase) and judge on the off-baseline population, exactly as P4
and P9 both did. Investigated per the task's "judge honestly" instruction rather than accepted at
face value.

### Lighter — **blocked in this recording; not yet re-validated**

`lighter_state` produced zero non-gap records in the pulled window — 100% `gap` records, matching
the previously-established `us-east-1` geo-block (`{"code":20558,"message":"You are accessing
Lighter from a restricted jurisdiction..."}`). This is the expected, correctly-handled failure
mode: the recorder retried, logged the gap, and did not affect the other two feeds. Nothing in
this run indicates the *capture logic* is wrong — there is simply no Lighter data to check.

**Full check is implemented and ready** (`validate_lighter()` in `scripts/validate_day1.py`): last
`current_funding_rate` per complete market-hour, whatever its `trigger`, joined against
`LighterAPI.fundings()`'s settled rate, required to match **exactly** (P9: 21/21 on the operator's
laptop, unblocked). It will report `Lighter: no data` until real Lighter records exist in the
pulled window.

## Situation update since this task was scoped

While this task was running, the recorder was **relocated from `us-east-1` to `eu-central-1`**
(Frankfurt) — a user decision recorded in `docs/phase2/recorder_runbook.md` ("Why Frankfurt"),
not something this task initiated or was asked to arrange. The `us-east-1` instance
(`i-005b76078218b1256`) that produced the recording validated above was decommissioned after its
data was preserved to S3 (`recorder/us-east-1-partial/`); the current instance is
`i-02348231aa3d6e4bf` at `18.196.234.242`.

Confirmed directly in this session:
- `uv run python scripts/daily_check.py` against the new instance reports **`status: ok`**, all
  three feeds live, `lighter_state` coverage `29.78` (high coverage is healthy for an
  event-driven feed judged against a heartbeat-based expectation — see the runbook).
- A `lighter_state` part pulled from the new instance contains real `market_stats` records (e.g.
  `ETH`, `current_funding_rate: "0.0012"`, `trigger: "change"`), not just gap records — Lighter's
  websocket now accepts the handshake from Frankfurt.

**No complete Lighter hour exists yet on the new instance** — it had been recording for only a
few minutes when checked, so `validate_day1.py` cannot yet produce a real Lighter verdict there.
**Next step, not done here**: re-run `validate_day1.py --root <frankfurt-pull>` once at least one
hour has closed (any time after the top of the next hour), which is the first real opportunity to
confirm Lighter's 100%-exact-match behaviour against a live, unblocked recording.

## What remains before Phase 2a's Done-when is met

Per the brief's "After Task 11" section, elapsed-time gates, not tasks:

1. **Seven consecutive days at `status: ok`** — the clock effectively restarted with the Frankfurt
   move; the `us-east-1` partial (broken, by design, on Lighter) does not count toward this.
2. **Lighter formula re-validation** (`probes/p07_lighter_formula_check.py` against the recorder's
   own premium history) — needs ≥100 off-baseline market-hours, more than one day, at least one
   negative-settling hour, and a multiplier ≠ 1 market if one exists. Cannot start until Frankfurt
   has accumulated real Lighter history.
3. **`health.json` readable from S3 without SSH** — blocked on IAM permissions per the runbook's
   "To finish the S3 setup"; unrelated to this task.
4. **Re-run the Lighter half of this validation** once ≥1 complete hour exists on the Frankfurt
   instance, to confirm the 100%-exact-match behaviour P9 found on the laptop also holds from the
   deployed recorder.
