# fundr

Research code for a study of hourly perpetual-futures funding rates on **Hyperliquid** and
**Lighter**, asking whether funding is forecastable well enough to trade as a cross-venue spread.

> **Status: concluded, and the answer is no — under taker execution.**
> Round-turn cost measures ~33 bp against 1.03–9.78 bp of unconditional carry, and the price
> basis between the two venues drifts ~20 bp over a 24-hour hold. Net of both, nothing in the
> panel pays. **No model was ever fitted**; the negative result came from measuring costs.
> Full reasoning: **[`docs/README.md`](docs/README.md)**.

## What is here

Four phases of work: source discovery, a live recorder, a full historical backfill, a
point-in-time universe, and constructed forecasting targets — plus the measurements that ended
the study.

```
docs/       the research record; start at docs/README.md
src/fundr/  library: datasets, venue APIs, funding formulas, universe, targets, premium
scripts/    backfills, QA, universe and target builds, diagnostics
probes/     Phase 1 source-discovery probes
deploy/     recorder provisioning (AWS resources since deleted)
tests/      366 tests, offline, fixture-based
```

## Quick start

Requires [uv](https://docs.astral.sh/uv/) and Python 3.13.

```bash
uv sync
uv run pytest tests/ -q      # 366 tests, no network, no data needed
```

**The datasets are not in this repo** — they were deleted when the research concluded, and are
gitignored regardless (9.6 GB). Everything is reproducible from the venues' public APIs:

```bash
uv run python scripts/backfill_hl_funding.py        # free
uv run python scripts/backfill_lighter_funding.py   # free
uv run python scripts/backfill_lighter_candles.py   # free
uv run python scripts/snapshot_markets.py --venue both
uv run python scripts/qa_backfill.py                # coverage + alignment report
```

One dataset is **billed**: `scripts/backfill_hl_archive.py` pulls Hyperliquid's per-minute market
state from a requester-pays S3 bucket (~$0.92 for 1,218 days, 275M rows). It refuses to start
unless `FUNDR_DATA` matches the target data root.

Then build the universe and targets:

```bash
uv run python scripts/build_universe.py             # rank columns; no size gate
uv run python scripts/build_targets.py              # 2 views x 4 horizons
uv run python -m scripts.universe_diagnostics       # the size-rule measurement
uv run python scripts/measure_basis.py              # the hedge measurement
```

## Conventions that matter

These are the ones that caused real bugs. They are enforced in code, not just documented.

- **Common basis.** Hyperliquid reports a signed per-hour fraction; Lighter reports an unsigned
  *percent* with a separate direction field. Always join on `signed_rate_fraction`.
  `targets.assert_common_basis` tests values, not column names.
- **Never `shift` to build a lag.** Hyperliquid's settled grid has 1,789 eight-hour intervals and
  213 single-hour holes; a one-row shift pairs rates 2 or 8 hours apart on 2,002 occasions and
  looks like noise. Join on an explicit hour difference.
- **Point-in-time or nothing.** Every rank comes from size recorded at or before the hour it
  labels. Size means *notional* — ranking base units ranks 1 BTC against 1,000,000 PEPE.
- **Flags are flags.** `joint_baseline` and `venue_clamped` never delete a row; the legitimate
  exclusions are named functions (`skill_metric_rows`, `tuning_rows`).
- **polars only, never pandas.**

## Reading the docs

`docs/README.md` is the whole story in one file. Beyond it: `docs/phase1/handoff.md` for data
semantics, `docs/phase2/datasets.md` for what was collected, and
`docs/phase3/universe.md` + `docs/phase4/targets.md` for what a later phase must handle.

Several figures in this project were corrected, some more than once. Where that happened the
documents say what they used to say and why it changed — a correction block is a record, not
clutter.

## Licence

No licence is attached. Published as a research record.
