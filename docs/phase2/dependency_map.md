# What needs the recorder, and what does not

Date: 2026-09-21
Sources: [`../phase1/handoff.md`](../phase1/handoff.md) §2 and §5,
[`../phase1/data_audit.md`](../phase1/data_audit.md) §1 and §4,
[`../funding_research_design.md`](../funding_research_design.md) Phases 2–11.

## The decision

**The recorder does not gate any phase except Target C and Phase 11.** It starts immediately —
it is the only clock that cannot be rewound — but every other phase proceeds in parallel on data
that already exists. No phase waits for the seven-day reliability window, and none waits for the
recorder at all unless it is listed under "Needs the recorder" below.

This is recorded because the opposite assumption is the expensive one: treating Phase 2a as a
prerequisite would idle Phases 2b–10 for weeks against data that is already free and complete.

## Runs on data that already exists

| Phase | What it needs | Where it comes from |
|---|---|---|
| **2b — historical collection** | Settled hourly funding, both venues | HL `fundingHistory` (free, from each market's listing); Lighter `/api/v1/fundings` `1h` (free, no auth, 13,000–14,600 h per sample market, zero gaps > 1 h) |
| **3 — universe** | Point-in-time market size | HL `asset_ctxs` open interest, one row per coin per minute, 2023-05-20 → present; listings datable from per-date coin presence and Lighter `created_at` |
| **4 — Targets A and B** | Settled funding on both venues, aligned | As Phase 2b; both venues settle hourly on the hour with explicit timestamps, so time-since/until-settlement is exact for every historical row |
| **5 — exploratory analysis** | Targets A and B | Phase 4's output |
| **6 — baselines** | Targets A and B | Phase 4's output |
| **8 — single-venue vs cross-venue** | Targets A and B | Phase 4's output |
| **9 — trading simulation** | Targets, prices, fees | Phase 4's output plus venue price history and published fees |
| **10 — robustness** | Everything above | Phases 4–9 |

## Needs the recorder

| Phase | Why |
|---|---|
| **4 — Target C only** | The published-versus-settled gap exists only from the moment it is recorded. Lighter's running funding is live-only and exact against settlement; HL's archived running value never equals settlement. No archive preserved either. |
| **11 — intra-hour study** | Runs entirely on Target C. |

## The one real qualification: Phase 7's features are lopsided

Phase 7 (feature-based forecasting) can start on history, but the two venues are not equally
served, and this shapes what features are honest to use:

| Feature | Hyperliquid, historically | Lighter, historically |
|---|---|---|
| Open interest | Per minute from 2023-05-20, complete | **None native.** 0xArchive only, from 2025-08-25, 84.2–97.4% complete, paid tier |
| Premium | Per minute from 2023-05-20 | **None, anywhere.** Recorder only |
| Index price (for basis) | `oracle_px` per minute from 2023-05-20 — the venue's own funding input | Live only. 0xArchive carries `index_price`, schema verified but **never validated as a price series** |
| Perp price, volume | Per minute from 2023-05-20 | `/api/v1/candles`, ≥ 1 year at 1 h, ≥ 180 days at 1 m |
| Signed trade flow | `node_fills` + `node_fills_by_block`, 2025-05-25 → present, ~$27/year | **No usable historical source**; vendor fills finalize 16.7–36 h late |

So: cross-venue **targets** are fully supported by history; cross-venue **features** are not. Phase
7 should either restrict itself to features both venues support historically, or treat
Lighter-side open interest, basis and trade flow as recorder-forward features and say so in its
results. This is a modelling constraint, not a scheduling one.

## Consequences for sequencing

1. Start the recorder and let it run. Do not wait on it.
2. Run Phase 2b (backfill of Targets A and B) immediately and in parallel — free on both venues.
3. Phases 3–6 and 8–10 follow from the backfill on their own schedule.
4. Revisit Phase 7's Lighter-side features once the recorder has history, or buy the 0xArchive
   Build tier if Phase 7 wants Lighter open interest before the recorder's start
   ([`handoff.md`](../phase1/handoff.md) §8 action 8).
5. The recorder's seven-day reliability window and the Lighter formula re-validation are
   background gates. They block nothing except Phase 11.
