# Day-one validation

Status: **PASS on both venues**, real recorded data, current instance.

## What ran

```bash
uv run python scripts/validate_day1.py --root <downloaded-dir>
```

Data was pulled read-only over SSH (never written to on the instance, never into `data/phase1/`)
from the **current** recorder instance:

- Region: `eu-central-1` (Frankfurt)
- Instance: `i-02348231aa3d6e4bf` @ `18.196.234.242`
- Key: `auth/fundr-recorder-eu.pem`

```bash
rsync -az -e "ssh -i auth/fundr-recorder-eu.pem" --rsync-path="sudo rsync" \
  ec2-user@18.196.234.242:/var/lib/fundr/hl_state       <scratch>/
rsync -az -e "ssh -i auth/fundr-recorder-eu.pem" --rsync-path="sudo rsync" \
  ec2-user@18.196.234.242:/var/lib/fundr/lighter_state  <scratch>/
```

Two clock hours were present for both feeds: `11:00–12:00 UTC` (sealed — the recorder had been
running the whole hour) and `12:00–12:xx UTC` (still open when pulled). This is the recorder's
first sealed hour since relocating to `eu-central-1` (see *Situation* in
`docs/phase2/recorder_runbook.md`, "Why Frankfurt") — Lighter's websocket, blocked from
`us-east-1`, accepts the handshake from Frankfurt.

## Output (verbatim)

```
Lighter: 214 complete market-hours kept, 214 excluded (n < 5 or lag_s outside [0, 120]s)
Lighter last-in-hour vs closing settlement: {'n': 214, 'n_match': 214, 'rate': 1.0, 'mean_signed_error': 0.0}
HL: 234 complete market-hours kept, 234 excluded (n < 5 or lag_s outside [0, 120]s)
HL last-in-hour vs closing settlement (all coin-hours): {'n': 234, 'n_match': 162, 'rate': 0.6923076923076923, 'mean_signed_error': 1.0511111111111093e-08}
HL residual (all): median 0.0 max 4.772000000000435e-07
HL off-baseline (|settled| > 1.5e-05): {'n': 70, 'n_match': 0, 'rate': 0.0, 'mean_signed_error': 3.4995714285714205e-08}
HL residual (off-baseline): median 7.370000000000236e-08 max 4.772000000000435e-07 min 1.1000000000091882e-09
LIGHTER PASS
HL PASS
```

Script exit code: **0**.

## Hours included / excluded

- **Lighter: 214 market-hours kept** (every subscribed Lighter market's sealed `11:00–12:00 UTC`
  hour), **214 excluded** (every market's still-open `12:00–12:xx` hour, pulled well before its
  boundary). 214 kept matches the 214 markets independently confirmed to have produced boundary
  snapshots at `11:59:30` and `11:59:52` in that hour (428 boundary records = 214 × 2).
- **HL: 234 market-hours kept** (every HL market's sealed `11:00–12:00 UTC` hour — HL tracks more
  markets than Lighter, hence 234 vs. 214), **234 excluded** (the open `12:00–12:xx` hour).
- No hour was excluded for coverage reasons (`n < 5`) on either feed in this sealed window — every
  exclusion was the still-open current hour, exactly the "partial last hour" case the `lag_s`
  filter exists for.

## Verdict per venue

### Lighter — **PASS, 100% exact**

**214/214 complete market-hours match exactly** (`rate: 1.0`, `mean_signed_error: 0.0`) between
the last recorded `current_funding_rate` in the hour (last record by `t_ms`, whatever its
`trigger` — per the reviewer correction, the true pre-settlement value can arrive as a `change`
record rather than a `boundary` one) and that hour's settled rate from `/api/v1/fundings`. This
matches P9's laptop-side finding (21/21) at full production scale (214 markets, on the deployed
recorder, not a sample) and confirms the capture rule the recorder exists to get right: the
pre-boundary value is not missed. No hour failed; there is nothing to investigate.

### Hyperliquid — **PASS**

Same split as the previous (`us-east-1`) run, now on Frankfurt-recorded data, applied per
P4/P9's methodology (`fundr.analysis.rebuild_verdict`'s baseline/off-baseline split): of 234 kept
coin-hours, 162 are baseline (settled at HL's interest floor `±0.0000125` or `0.0`, matching
trivially) and **70 are off-baseline** — the informative population:

- **70/70 off-baseline coin-hours: 0 exact matches (0.0%)**, residual `|last − settled|`: median
  **7.37e-8**, min **1.1e-9**, max **4.77e-7**.

Squarely inside the expected ~1e-9–1e-4 band with no exact match, consistent with P4 (~4e-7–1.3e-6
archived) and P9 (mean 2.52e-7, max 1.07e-6 live) and with the prior `us-east-1` run (median
7.74e-8, max 1.03e-6) — the running `funding` value behaves the same way on the relocated
instance as it did before the move and as Phase 1 found independently.

## Note on the check itself

The HL API call (`fundingHistory`, one POST per coin) hit `429 Too Many Requests` partway through
234 sequential coins on the first attempt against Frankfurt data — `scripts/validate_day1.py` was
given exponential backoff-and-retry on 429 (`_hl_funding_history_with_backoff`, capped at 6
attempts / 20 s) so a transient rate limit doesn't abort the whole verdict. This is a robustness
fix to the script's own HTTP client usage, not a change to what is being checked or how a pass/fail
is judged.

## What remains before Phase 2a's Done-when is met

Both feeds now behave exactly as Phase 1 predicted, on the deployed recorder, but the elapsed-time
gates from the brief's "After Task 11" section are unaffected by a single sealed hour:

1. **Seven consecutive days at `status: ok`** — the clock restarted with the Frankfurt move on
   2026-09-21; one sealed hour is not seven days.
2. **Lighter formula re-validation** (`probes/p07_lighter_formula_check.py` against the recorder's
   own premium history) — needs ≥100 off-baseline market-hours, more than one day, at least one
   negative-settling hour, and a multiplier ≠ 1 market if one exists. Not yet attempted; one hour
   of history is far short of the sample size required.
3. **`health.json` readable from S3 without SSH** — blocked on IAM permissions per the runbook's
   "To finish the S3 setup"; unrelated to this validation.
