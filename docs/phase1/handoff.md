# Phase 1 → Phases 2–11 Handoff

Audience: whoever plans or executes Phase 2 onward, with no memory of Phase 1. Read this
end to end and you can plan Phase 2 without opening anything else; each claim links the
note to open when you need the workings.

Parent research design: [`../funding_research_design.md`](../funding_research_design.md)
(hypotheses H1–H4, Targets A/B/C, Phases 2–11). Phase 1's own brief:
[`../superpowers/specs/2026-09-19-phase1-data-probe-design.md`](../superpowers/specs/2026-09-19-phase1-data-probe-design.md).
Phase 1's deliverables: [`data_audit.md`](data_audit.md) (the full source matrix and
funding semantics) and [`decision_map.md`](decision_map.md) (finding → consequence, 19
rows). The eleven evidence notes are in [`evidence/`](evidence/), one per probe (P0–P10);
they are the only place a number in this document ever comes from.

---

## 1. Where the research stands

Phase 1 asked one question above all others: **can we see, historically, what the funding
rate *would have been* part-way through an hour, before that hour settled?** The answer is
no, on both venues, for different reasons. Everything else follows from that.

- **Targets A and B are fully supported by data that already exists.** Both venues publish
  a complete, gap-free history of what funding actually settled at, every hour, back to
  each market's listing — Hyperliquid to 2023, Lighter to 2025-01-17 for the oldest sample
  market. The two series can be put on one basis exactly (§3), and their hours pair
  one-to-one with no offset, so the cross-venue spread `S_t = F_HL − F_Lighter` is well
  defined at hourly resolution for the whole overlap.
  ([P4](evidence/P4-hl-api-crosscheck.md), [P5](evidence/P5-lighter-fundings.md))
- **Target C is prospective-only on both venues.** Hyperliquid *does* archive a running,
  per-minute funding value back to 2023-05-20, but it is an approximation that never lands
  on the settled rate: 0 of 64, 0 of 108 and 0 of 27 off-baseline coin-hours matched on the
  three analysed dates, with residuals of 1e-7 to 1e-6 — 2,000× to 10,700× the venue's own
  reported precision of 1e-10. Lighter's running value *is* exact (it equals the closing
  settlement on 21/21 profiled hours and 11/11 of the hours where the rate actually
  changed), but nobody archived it: Lighter's API rejects sub-hourly funding resolutions
  and the third-party vendor 0xArchive mirrors only the *settled* series. So Target C's
  data starts the day a recorder is switched on.
  ([P1](evidence/P1-hl-asset-ctxs.md), [P4](evidence/P4-hl-api-crosscheck.md),
  [P9](evidence/P9-live-probe.md), [P8](evidence/P8-oxarchive.md),
  [P10](evidence/P10-rebuild.md); decision recorded in
  [`data_audit.md` §5](data_audit.md#5-target-c-decision))
- **Exact reconstruction was tested and fails on both venues.** HL's is "not attemptable by
  construction" — the formula averages premium every 5 s, the archive holds one sample per
  minute; the coarse attempt scored 0/264 off-baseline hours. Lighter's is more absolute:
  no historical premium input exists anywhere to attempt a rebuild with.
  ([P10](evidence/P10-rebuild.md))
- **Both venues' funding formulas are now confirmed against real settled data** (§3), which
  means a later phase can model the *premium path* and map it through a known function
  rather than predicting the rate directly. ([P7](evidence/P7-funding-formulas.md))
- **The whole probe cost $0.0118 of AWS charges and 32 0xArchive credits**, no paid tier.
  ([`data_audit.md` Done check](data_audit.md#done-check))

The single most consequential consequence: **the always-on recorder is Phase 2's first
task, not its last.** Target C's clock starts when the recorder starts, and no day of delay
can be recovered later.

The two venues' Target C failures are *not* the same failure, and the distinction should
survive into Phase 11. HL has three years of archived running funding that is close but
never exact; if a later phase chooses to define Target C against an *approximate* published
value, HL has history to work with — that would be a redefinition of the target, not a
reversal of this decision. Lighter has nothing archived at any accuracy, and its running
value, once recorded, is exact. ([`data_audit.md` §5](data_audit.md#5-target-c-decision))

---

## 2. The data that exists

The authoritative, per-row version of this is the source matrix in
[`data_audit.md` §1](data_audit.md#1-venuesource-matrix) (18 rows: endpoint, coverage start,
resolution, key fields, timestamp convention, missingness, cost, status, evidence) and the
per-feature hierarchy in [`data_audit.md` §4](data_audit.md#4-final-source-hierarchy). What
follows is enough to plan Phases 2–7 without opening it.

Everything Phase 1 touched is **free** except the two Hyperliquid S3 archives
(requester-pays) and 0xArchive's paid tiers.

### Per feature, per venue

| Feature | Hyperliquid | Lighter |
|---|---|---|
| **Funding (settled)** | info API `fundingHistory`, 1 h, free, back to each market's listing (BTC 2023-05-20 per 0xArchive's mirror; HL's own start never queried directly). Fields `coin, fundingRate, premium, time`. ([P4](evidence/P4-hl-api-crosscheck.md)) | `/api/v1/fundings` `resolution=1h`, free, no auth, from each market's listing: BTC 2025-01-17 08:00Z (14,644 h), JUP 13,719 h, ENA 13,507 h, KAITO 12,954 h, ONDO 12,326 h, ETHFI 9,619 h, PONS 402 h. **Zero gaps > 1 h in all seven.** 750 rows per call. ([P5](evidence/P5-lighter-fundings.md)) |
| **Funding (running / intra-hour)** | `asset_ctxs` S3 archive `funding`, **1 row per coin per minute**, 2023-05-20 → present, requester-pays. Live: `metaAndAssetCtxs.funding` at whatever cadence you poll. Approximate — see §1 and §3. ([P1](evidence/P1-hl-asset-ctxs.md)) | `market_stats` websocket `current_funding_rate`. **Live only — no history anywhere.** Exact against settlement. ([P9](evidence/P9-live-probe.md)) |
| **Premium** | `fundingHistory.premium` (the hour's average, settled rows) reproduces settled funding exactly; `asset_ctxs.premium` per minute from 2023-05-20 for intra-hour. The venue's true 5-second samples are not published. ([P4](evidence/P4-hl-api-crosscheck.md), [P7](evidence/P7-funding-formulas.md)) | `market_stats` websocket `premium` — **live only, and a running hourly average, not a spot sample** (§3). No historical premium exists on Lighter's API, on 0xArchive REST, or anywhere else. ([P6](evidence/P6-lighter-market-state.md), [P7](evidence/P7-funding-formulas.md), [P10](evidence/P10-rebuild.md)) |
| **Open interest** | `asset_ctxs.open_interest`, per minute from 2023-05-20, complete. ([P1](evidence/P1-hl-asset-ctxs.md)) | **No native history** — 13 endpoints checked; `open_interest` exists only as a current value. 0xArchive `/v1/lighter/openinterest` gives ~10 s cadence from 2025-08-25 at 84.2–97.4% completeness by coin. ([P6](evidence/P6-lighter-market-state.md), [P8](evidence/P8-oxarchive.md)) |
| **Perp price** | `asset_ctxs` `mark_px`/`mid_px`/`prev_day_px` per minute. `candleSnapshot` reaches back only ~3.5 days at 1 m (a nominal 5-day request returned ~5,050 rows spanning exactly 3.50 days; a window 29–30 days back returned 0 rows). ([P1](evidence/P1-hl-asset-ctxs.md), [P4](evidence/P4-hl-api-crosscheck.md)) | `/api/v1/candles` and `/api/v1/markPriceCandles`, ≥ 1 year at 1 h and ≥ 180 days at 1 m (true start not measured). ([P6](evidence/P6-lighter-market-state.md)) |
| **Spot / index price** | `asset_ctxs.oracle_px` per minute from 2023-05-20 — the venue's own funding input. ([P1](evidence/P1-hl-asset-ctxs.md)) | `index_price` is **live only** on Lighter's API. 0xArchive's `openinterest` rows carry `index_price` and `oracle_price` from 2025-08-25 — schema verified, **never validated as a price series**. ([P6](evidence/P6-lighter-market-state.md), [P8](evidence/P8-oxarchive.md)) |
| **Volume** | `asset_ctxs.day_ntl_vlm` per minute — a running **daily** notional, so hourly volume is a difference with a UTC-midnight reset, not a level. ([P1](evidence/P1-hl-asset-ctxs.md)) | `/api/v1/candles` `v` (base) and `V` (quote) — true per-bucket volume. ([P6](evidence/P6-lighter-market-state.md)) |
| **Signed trade flow** | `node_fills_by_block` (S3, hourly files, 2025-07-27 → today) continued back by `node_fills` (2025-05-25 → 2025-07-27, exact handoff). `side` + `crossed` + `dir` give the aggressor. Whole-network files, ~$27.15/year to mirror a full year; cost does **not** scale down with fewer coins. `node_trades` is unusable (last leaf decodes to 0 lines). Live `trades` websocket also works. ([P3](evidence/P3-hl-node-fills.md), [P9](evidence/P9-live-probe.md)) | **No usable historical source.** `/api/v1/trades` returns HTTP 400 without auth; 0xArchive's Lighter fills finalize **16.7–36 h behind** real time. Live `trade:<market_id>` websocket works; the aggressor must be derived from `is_maker_ask`. ([P6](evidence/P6-lighter-market-state.md), [P8](evidence/P8-oxarchive.md), [P9](evidence/P9-live-probe.md)) |
| **Listings / delistings** | Listing datable from per-date coin presence in `asset_ctxs`, or the first `1d` candle (see the caveat in §7). `is_delisted` live on `meta`/`metaAndAssetCtxs`. ([P1](evidence/P1-hl-asset-ctxs.md), [P9](evidence/P9-live-probe.md)) | `/api/v1/orderBooks` `created_at` (exact listing time) + `status`. **No delisting timestamp exists at all** — 22 of 246 markets are `inactive` with an identical field set and no date. ([P6](evidence/P6-lighter-market-state.md)) |
| **Settlement timestamps** | `fundingHistory.time`; `predictedFundings.nextFundingTime` live. | `/api/v1/fundings.timestamp`; `funding_timestamp` live. |
| **Order book** | L2 archive: full 20-level snapshots every ~5.4 s (median 5,388 ms), 2023-04-15 → present, ~$1.50/year for the 7 sample coins. Only 178 of 234 markets have a file in a given hour. ([P2](evidence/P2-hl-l2.md)) | 0xArchive Lighter L3 is a **current snapshot only** (no time parameter). No historical equivalent. ([P8](evidence/P8-oxarchive.md)) |

"Time since settlement" and "time until settlement" are computable for **every** historical
timestamp on both venues from the hour grid alone — both settle hourly, on the hour, with an
explicit timestamp on every settled row. No gap, no approximation.
([`decision_map.md`](decision_map.md))

### Prospective-only list — the recorder is the only source

Lighter running funding (`current_funding_rate`), Lighter premium, Lighter index price
(unless 0xArchive's is validated first), Lighter open interest before 2025-08-25 (and before
today without a paid tier), Lighter signed trade flow, Lighter delisting timestamps, and an
exact-to-settlement HL running-funding series.
([`data_audit.md` §4](data_audit.md#4-final-source-hierarchy))

### 0xArchive, in one paragraph

A useful fallback, not a requirement. Its **HL** coverage is excellent: every data type,
every one of the 7 sample coins, 100% complete with zero gaps from each market's listing,
funding at ~61 s cadence, and HL rows carry `premium`. Its **Lighter** coverage starts
2025-08-25 (the vendor's ingest start, not the markets' listings), runs at ~10 s median
cadence, is 84.2–97.4% complete by coin, carries **no `premium` field**, and its
`funding_rate` is a fraction (Lighter's native percent ÷ 100; median ratio exactly 0.01 over
466 joined rows). Funding and OI report identical coverage per coin — one shared upstream
feed, not independent checks. Its `total_records` field is unreliable (900–9,000× its own
`sample_count`) and must not be used for budgeting. Free tier restricts data calls to roughly
the last 30 days (**inferred** from the tier description, never tested with an older request).
Build is $49/month for 80M credits; a 50-market × 1-year Lighter funding + OI + trades
backfill is estimated at 480,000–510,000 credits, about 0.6% of that quota.
([P8](evidence/P8-oxarchive.md))

---

## 3. Semantics that will bite

These are the details that silently corrupt a pipeline if forgotten. They are all confirmed
against real data unless labelled otherwise.

**Hyperliquid funding.** A plain dimensionless **fraction, per hour**, already signed.
Reported to up to 10 decimal places, so the tolerance is `1e-10`. The interest-rate baseline
is `0.0001 / 8 = 0.0000125` per hour — the value funding sits at whenever premium stays
inside the clamp band. Positive means longs pay shorts, checked on data at **245/245**
off-baseline coin-hours. Settlement is hourly, and a `fundingHistory` row is stamped a few
milliseconds past the hour boundary. ([P4](evidence/P4-hl-api-crosscheck.md))

**Lighter funding.** **Percent**, per hour, reported **truncated (not rounded) to 4 decimal
places** — the baseline `0.0100 / 8 = 0.00125` is reported as `0.0012`, and only truncation
reproduces the settled string. `rate` is an **unsigned magnitude**; the sign lives in a
separate `direction` field (`long` → positive, longs pay shorts; `short` → negative), the
same sense as HL, verified against a genuinely negative market (`CAP`, market 212). Funding
period is a **per-market configuration**; all sampled markets are 1 h, so assert `1h` per
market rather than assuming it. ([P5](evidence/P5-lighter-fundings.md),
[P9](evidence/P9-live-probe.md))

**Settlement-timestamp convention.** On both venues, the row stamped `T` reports the interval
`[T − 1h, T)` that just closed. Lighter's is **verified directly against the live API** (at
23:53:58Z the latest row was stamped 23:00, with no row yet for the open interval). HL's is an
**inference** from P4's alignment test — closing alignment has a near-zero mean signed error,
opening alignment is one-signed and ~30× larger. Carry that label: if a later result looks
systematically shifted by one hour, re-test HL's alignment first.
([P5](evidence/P5-lighter-fundings.md), [P4](evidence/P4-hl-api-crosscheck.md))

**Cross-venue normalisation.** Common basis: **per-hour signed fraction, positive = longs pay
shorts.** HL is used as-is. Lighter becomes `F = (rate / 100)` if `direction == "long"`, else
`−(rate / 100)`. A raw Lighter `rate` used as-is is 100× too large and unsigned — the single
most likely silent bug in Phase 4. Hours pair one-to-one with no offset. **The effective
precision of any spread is `1e-6`, not `1e-10`** — Lighter's 4-dp truncation binds — which sets
the floor on what a "small" spread change can mean.
([`data_audit.md` §2](data_audit.md#2-funding-semantics), [`decision_map.md`](decision_map.md))

**What each live field means:**

- **HL `funding`** (live `metaAndAssetCtxs`, and per-minute in the archive) — a *running
  estimate of the settlement that will close the current hour*, not the last settled rate. It
  changes within the hour on 57–87% of coin-hours in the archive and 76.2% live. It converges
  toward the closing settlement but **never equals it** off-baseline: residual 1e-7 to 1e-6,
  i.e. 2,000×–10,700× the reported tolerance. Every exact "match" in the data is a trivial
  baseline hour. The cause is consistent with a true 5-second average being polled at ~60 s
  (**hypothesis**, not confirmed). ([P1](evidence/P1-hl-asset-ctxs.md),
  [P4](evidence/P4-hl-api-crosscheck.md), [P9](evidence/P9-live-probe.md))
- **HL `predictedFundings`** — HL's own forward-looking predicted rate for the *next*
  settlement, plus `nextFundingTime` and `fundingIntervalHours`, for HL and for other venues
  it tracks (Binance/Bybit at 4 h). Live snapshot, no history.
  ([P4](evidence/P4-hl-api-crosscheck.md))
- **Lighter `current_funding_rate`** (`market_stats` websocket) — a running estimate of the
  **currently-accruing** interval, and its last in-hour value **exactly equals** that interval's
  settlement: 21/21 profiled hours, 11/11 informative hours, exactly, not within tolerance.
  ([P9](evidence/P9-live-probe.md))
- **Lighter `funding_rate` + `funding_timestamp`** (same websocket) — the **last completed**
  settlement, one interval *behind* `current_funding_rate` (21/21). Do not mistake it for the
  current rate. ([P9](evidence/P9-live-probe.md))
- **Lighter `premium`** (same websocket) — a **running average since the last settlement**, not
  a spot sample. It resets to `0.0000` at each hour boundary and its minute-to-minute change
  decays as ~1/k, the signature of a cumulative mean. **Do not average it over the hour**:
  the mean of in-hour snapshots reproduces settled funding on 0/12 off-baseline hours, while
  the *last* in-hour value reproduces it on 16/16. Store the raw field and treat the last
  pre-boundary reading as the hour's premium. ([P7](evidence/P7-funding-formulas.md))
- **`/api/v1/funding-rates`** (Lighter REST) — third-party reference rates across binance,
  bybit, hyperliquid and lighter, **not** Lighter's own historical series. A past `timestamp`
  parameter is accepted and silently ignored (byte-identical response).
  ([P6](evidence/P6-lighter-market-state.md))

**The two funding formulas, both confirmed on data** ([P7](evidence/P7-funding-formulas.md),
shipped as tested code in [`../../src/fundr/funding/`](../../src/fundr/funding/)):

```
HL (fraction/hour):   F_8h     = P + clamp(0.0001 − P, −0.0005, +0.0005)
                      F_hourly = clamp(F_8h / 8, −0.04, +0.04)
                      P = fundingHistory.premium (the hour's average)
                      → 245/245 off-baseline and 494/494 total hours within 1e-10

Lighter (percent/hour): smallClamped = P + clamp(0.01 − P, −0.05, +0.05)
                        rate_pct     = trunc4( clamp(smallClamped, −4, +4) / 8 )
                        signed by `direction`, then /100 for the common basis
                        P = the hour's FINAL running-average premium
                        → 16/16 off-baseline and 28/28 total, exact to the reported 4-dp string
```

Lighter's `funding_premium_multiplier: 100` means an effective multiplier of **1.0** (the API
reports hundredths); all three rival placements of the multiplier score 0/16.
`current_funding_rate` is that same formula applied to that same running premium — 1,211/1,211
exact from 15 minutes into the hour onward, 91.4% exact across all minutes. Both formulas'
sample-size and untested-branch caveats are in §7.

---

## 4. The recorder Phase 2 must build

**Why it is the critical path.** Target C exists only from the moment the recorder starts, on
both venues, and Phase 11 runs entirely on what it collects. Lighter's premium and running
funding, Lighter's OI and index price before a paid backfill, Lighter's signed trade flow and
delisting dates, and an exact-to-settlement HL running series all exist *nowhere else*. Build
and start it before any bulk historical extraction; the backfills in §8 can wait, this cannot.

**Everything it needs is live, free and unauthenticated on both venues today.** P9 found no
field missing that the design asked for. ([P9](evidence/P9-live-probe.md))

**Fields to capture, per market, per minute:**

- **Hyperliquid `metaAndAssetCtxs`** (REST poll): `coin, funding, premium, open_interest,
  oi_notional_usd, mark_px, oracle_px, day_ntl_vlm, is_delisted`. Add
  `predictedFundings` (next-settlement rate and `nextFundingTime`) for the HL side of Target C.
  Not polled by P9 but available on other HL endpoints if wanted: `mid_px`, impact bid/ask.
- **Lighter `market_stats` websocket** (one channel carries almost everything):
  `current_funding_rate, funding_rate, funding_timestamp, premium, mark_price, index_price,
  mid_price, last_trade_price, best_bid_price, best_ask_price, open_interest,
  open_interest_limit, daily_base_token_volume, daily_quote_token_volume,
  daily_price_change/high/low, funding_clamp_big/small, base_interest_rate`. It emits about one
  message per 1.3 s per market (median 47 per market per minute); `premium` and
  `current_funding_rate` refresh about once a minute.
- **Trade flow, continuous, both venues**: HL `trades` websocket (`side` is the aggressor);
  Lighter `trade:<market_id>` websocket (derive the taker from `is_maker_ask`).
- **Universe snapshot every cycle**: HL `meta`/`metaAndAssetCtxs` `is_delisted`, Lighter
  `/api/v1/orderBooks` `status` + `created_at`. This is the *only* way future Lighter
  delistings will ever be dated.
- **An `n_msgs`-style counter on every websocket snapshot** — see requirement 2; it is the only
  reason P9's own failure could be diagnosed.

**The five requirements, all mandatory, all produced by P9's own 95-minute data loss**
([P9, "Requirements this places on the Phase 2 recorder"](evidence/P9-live-probe.md)):

1. **Never gate feed recording on a REST poll.** Websocket snapshots write on their own timer,
   in a task no HTTP call can block. In P9 one hung REST call silently ended the Lighter
   websocket recording even though the feed was live.
2. **Detect a stalled feed explicitly.** Keep a message counter on every snapshot; after 2–3
   consecutive zero-message intervals force a reconnect and alert, and skip or mark the snapshot
   `stale` — never silently re-emit the last value.
3. **Enforce timeouts out-of-band.** A client-level `timeout=30` did not bound an **84-minute**
   read. Wrap every network call in an external watchdog (`asyncio.wait_for` or equivalent).
4. **Detect wall-clock jumps and host suspension.** Treat any inter-cycle gap beyond ~2 poll
   intervals as a coverage hole and write an explicit gap record; re-check the run deadline
   *inside* the cycle (P9's run overran its own deadline by 77 minutes).
5. **Alert on coverage, not record counts.** The lost hour showed up as 1,631 vs 1,638 records —
   a 0.4% difference no count-based check would catch. Monitor expected-versus-actual snapshots
   per market per hour.

**What actually happened in P9, for context.** The feed never stalled: all 1,631 snapshots
carried `n_msgs > 0` (min 2, median 47, max 366), and the two reconnects during normal operation
recovered cleanly (~1 per 1.9 h over 3h52m). The recorder wrote websocket snapshots only after a
successful REST poll, inside the same `try`; one `order_book_details` call blocked for 84 minutes
and the cycle's snapshot was never written. Host suspension is the leading explanation for the
84-minute block and an earlier 649-second gap — **inference only**, no host logs were kept; a
venue outage is ruled out.

**Known bug history of [`../../probes/p09_live.py`](../../probes/p09_live.py) — it is a probe,
not the recorder.** Do not adopt it as the recorder; build the recorder fresh against the five
requirements. Its history: (a) the original version subscribed and snapshotted but caught only
`OSError`/`ConnectionClosed` on the websocket task and wrote the snapshot from inside the poll
function; (b) a first fix moved the snapshot onto the event loop and broadened the reconnect
handler; (c) the final review-wave commit (`32bf4c0`) moved `write_ws_snapshot()` out of the
`try` and wrapped the REST poll in `asyncio.wait_for(..., timeout=45)`. That addresses
requirements 1 and 3 in the probe; **requirements 2, 4 and 5 are not implemented anywhere**.
Note also that P9's "Open issues" section still states the probe couples websocket writing to a
successful REST poll — that sentence is stale relative to the committed code (see §7).

---

## 5. What each later phase must do differently

Condensed from [`decision_map.md`](decision_map.md), which carries the evidence links for every
row.

**Phase 2 — Historical data collection.**
- Build and start the recorder **first** (§4), before any bulk extraction.
- Do **not** treat the last ~5 days of the HL `asset_ctxs` archive as authoritative: validate
  every downloaded day against the expected 1,440 rows per coin, quarantine short days,
  re-download recent days later to pick up backfill, and fill the tail from the recorder or from
  0xArchive's HL mirror.
- Store both the raw volume field and a derived hourly volume with a provenance flag — HL's
  `day_ntl_vlm` is a cumulative daily notional needing differencing and a UTC-midnight reset
  handler; Lighter's candle volume is already per bucket.
- Keep a source/provenance field on every dataset, as the parent design requires — Phase 1
  leaves a genuinely mixed set of native, vendor and prospective sources.

**Phase 3 — Universe construction.**
- Add a symbol-alias table (HL `k*` ↔ Lighter `1000*`) **before** fixing the universe, or a whole
  class of high-funding memecoin markets — exactly the mid/small-cap segment this research
  targets — is dropped silently. Note the denomination differs by 1000×, so prices and sizes need
  scaling; funding *rates* are unaffected.
- Define the point-in-time universe by **HL OI** (as P0's own rule does) and it is point-in-time
  back to 2023 with no Lighter dependency. A Lighter-OI or combined-OI ranking is either
  vendor-sourced from 2025-08-25 at 84–97% completeness or prospective.
- Require a minimum history length per market before it enters Target B; newly listed markets
  (PONS had 17 days) enter mid-sample.
- Entry is datable exactly on both venues; **exit is not datable on Lighter from history**. Either
  infer a delisting from the last trade/candle before the market went inactive (a proxy, not
  validated) or accept that pre-recorder Lighter exits are undated — and carry a survivorship-risk
  flag either way.

**Phase 4 — Target construction.**
- Normalise every Lighter rate to the common basis before anything else, and add an assertion
  that no Lighter rate enters a model without passing through the signing function (§3).
- Pair hours one-to-one with no offset; carry the inference label on HL's settlement convention.
- Construct Target C **only on the prospective window** — the parent design's third branch
  ("neither available: collect prospectively").
- Treat `1e-6`, not `1e-10`, as the meaningful resolution of any spread.

**Phase 5 — EDA.** Both targets' inputs are hourly and gap-free over the overlap, so the standard
distributional and autocorrelation work needs no special handling. The only structural constraint
is the study window: the both-venue history starts 2025-01-17 at the earliest (Lighter binds) and
per-market at each Lighter listing — roughly 20 months for the longest-lived markets, ~13 months
for a mid-cap like ETHFI.

**Phase 6 — Baselines.** No Phase 1 finding constrains the baselines themselves; they run on the
same hourly settled series as Targets A and B.

**Phase 7 — Feature-based forecasting.**
- Intra-hour features (OI change within the hour, price drift since settlement, impact-book skew)
  are **historical on HL, prospective-or-vendor on Lighter**. Choose explicitly between (a)
  restricting the cross-venue feature set to what both venues support historically — hourly
  features on the Lighter side — or (b) buying 0xArchive Build for a ~10 s Lighter OI/price
  history from 2025-08-25 at 84–97% completeness with a vendor provenance flag.
- Premium as a feature is historical on HL, prospective-only on Lighter, with the
  "do not average the running mean" rule from §3.
- Trade-imbalance features are asymmetric: HL from 2025-05-25, Lighter only from the recorder's
  start (or at a 16–36 h lag via a paid tier, which is fine for research but not for anything
  live). Either build the cross-venue imbalance feature only over the recorder window, or use it
  as an HL-only feature and say so.
- Basis is historical on HL (`oracle_px`) and on Lighter only via an unvalidated vendor series or
  the recorder. Note the venues use different reference prices in their own funding maths — HL
  oracle, Lighter index — so a cross-venue basis feature is not strictly like-for-like.
- Time-since/until-settlement features are safe historically on both venues.
- Any model mixing HL intra-hour features with Lighter hourly ones must record which side each
  feature came from.

**Phase 8 — Individual vs cross-venue.** Feature sets must be *comparable* across the three
comparisons, which is exactly where the asymmetries above bite; if HL gets intra-hour features
Lighter cannot have, the H3 comparison is confounded. Decide the symmetric feature set here, not
in Phase 7.

**Phases 9–10 — Simulation and robustness.** Use the derived hourly volume series on both venues
so liquidity constraints are comparable. Phase 10's "venue combinations" and "market universe"
robustness dimensions inherit the alias problem (Phase 3) and the vendor-vs-native provenance
split; segment results by provenance.

**Phase 11 — Intrahour funding study.**
- Runs on the prospective dataset; **its start date is the recorder's start date**.
- Lighter's recorded running value is exact, so its Target C is a clean "realized − published"
  series. HL's is an approximation, so an HL Target C must be defined against either the recorded
  live value or `predictedFundings`, and that choice must be stated.
- Because both venues' funding is fully explained by a published premium, Phase 11 can model the
  *premium path* and map it through the confirmed formula — a materially stronger formulation than
  predicting the rate directly.
- Before any Phase 11 result leans on Lighter's formula, re-validate it (§8, action 4).
- Optionally, HL's three years of archived approximate running funding can support a *redefined*,
  weaker Target C — a separate decision, not this one.

---

## 6. The library you inherit

[`../../src/fundr/`](../../src/fundr/) is tested, reusable source code written for Phase 2 to
inherit. `uv run pytest -q` → **34 passed in 0.57 s** (2026-09-20).

| Module | What it does |
|---|---|
| [`sources/hl_api.py`](../../src/fundr/sources/hl_api.py) | HL info endpoint: `meta_and_asset_ctxs`, `predicted_fundings`, `candle_snapshot`, paginated `funding_history`; frame builders `asset_ctxs_frame` (adds `oi_notional_usd`) and `funding_history_frame` |
| [`sources/hl_archive.py`](../../src/fundr/sources/hl_archive.py) | Requester-pays S3: list/download/lz4-decode for both buckets, with the spend guard; `read_csv_lz4`, `parse_time` |
| [`sources/lighter_api.py`](../../src/fundr/sources/lighter_api.py) | Lighter REST (`order_books`, `order_book_details`, `funding_rates`, `fundings`, paged `fundings_all` at 700 rows per call, `candles`), the `market_stats` websocket stream, `lighter_signed_rate`, `fundings_frame` |
| [`sources/oxarchive.py`](../../src/fundr/sources/oxarchive.py) | 0xArchive REST client with per-call credit/header logging and cursor paging (note: `get_all` truncates silently at `max_pages`) |
| [`funding/hl_formula.py`](../../src/fundr/funding/hl_formula.py) | The confirmed HL formula and its constants (`BASELINE_HOURLY = 0.0000125`) |
| [`funding/lighter_formula.py`](../../src/fundr/funding/lighter_formula.py) | The confirmed Lighter formula **in percent**, with the truncate-toward-zero-at-4-dp rule and the documented big-clamp ordering; its docstring records which parts are data-confirmed and which are docs-only |
| [`analysis.py`](../../src/fundr/analysis.py) | `epoch_ms`/`epoch_s`, `hourly_profile`, `gap_scan`, `reported_tolerance`, `match_stats`, `rebuild_verdict` (the spec's P10 pass/near-miss/fail rule, `min_off_baseline=100`), `attach_settled` (pairs each hour with the settlement that closes it) |
| [`sample.py`](../../src/fundr/sample.py) | The P0 sampling rule: both-venue candidates ranked by HL OI notional, picks at ranks 11/18/25/33/40 + ~80 + BTC control |
| [`store.py`](../../src/fundr/store.py) | Where raw output goes: `data_root()` from `$FUNDR_DATA` (default `data/phase1`), plus `save_json`/`append_jsonl`/`load_json` |

**Conventions baked in — keep them:**

- **Naive UTC `Datetime(ms)` everywhere.** `epoch_ms`/`epoch_s` and `parse_time` all cast to
  millisecond precision, because `attach_settled` joins on `settle_time` and a microsecond/
  millisecond mismatch raises a `SchemaError`. This bit P4 and is fixed at the source with
  regression tests. ([P4](evidence/P4-hl-api-crosscheck.md))
- **Units are per venue, not global.** HL code works in fractions; Lighter code works in percent.
  `lighter_formula.hourly_rate` returns percent per hour, and `lighter_signed_rate` operates on
  whatever units it is given — divide by 100 consistently at the boundary.
- **Tolerances come from the data, not from constants.** `reported_tolerance()` derives one unit
  in the last reported decimal place from the venue's own strings.
- **Spend guard.** `HLArchive` estimates and logs every billed S3 call to
  `data/phase1/aws_ledger.jsonl` and refuses anything that would pass `BUDGET_USD = 0.80`. Phase 2
  will need a higher, deliberately chosen cap — raise it consciously, don't remove the guard.
- **Evidence-note discipline.** Every probe writes one note in
  [`evidence/`](evidence/) with fixed sections (what ran, what came back, findings, status,
  open issues); "verified" requires a real file or response behind it, and anything from docs or a
  third party stays labelled as an inference until checked on data. Phase 2 should keep this — it
  is what makes the audit auditable, and what let P9's stall be diagnosed after the fact.

**How to run a probe:**

```bash
FUNDR_DATA=/Users/jerryinyang/Trading/fundr/data/phase1 uv run python probes/p05_lighter_fundings.py
```

Python 3.13 via `uv`; dependencies `boto3, httpx, lz4, polars, websockets` (+ `pytest`). AWS uses
the machine's default profile (the `aws` CLI is broken in this environment — `bad CPU type`; use
boto3). 0xArchive needs `OXARCHIVE_API_KEY` in the environment; never commit it.

**Where raw data lives.** Everything under `$FUNDR_DATA` is **gitignored** — the repo has
`**/data/*`, plus global `*.csv`/`*.json` rules with a narrow negation for `tests/fixtures/**`.
On this machine the Phase 1 outputs are at `/Users/jerryinyang/Trading/fundr/data/phase1/`, one
directory per probe (`p00`…`p10`, plus `s3/` for downloaded archive files and
`aws_ledger.jsonl`). Committed artefacts are only the three fixtures in
[`../../tests/fixtures/`](../../tests/fixtures/): a trimmed `asset_ctxs` sample and the two
formula case files.

---

## 7. Caveats and risks that must travel

1. **Lighter's confirmed formula rests on a single window.** 28 market-hours (16 off-baseline)
   from one 3h52m recording on 2026-09-19, 7 markets, **all settling positive**. The 4% big clamp
   was never approached (largest value ~28× below it), so whether it sits before or after the `/8`
   is **docs-only**; whether the multiplier also scales the small clamp is untestable on crypto
   markets (effective multiplier 1.0); no market with multiplier ≠ 1 (RWA 1/2, Pre-IPO 1/100) was
   tested. What makes it convincing despite the size is that all 16 off-baseline hours reproduce
   the **exact 4-dp string**, not merely a value within tolerance.
   ([P7](evidence/P7-funding-formulas.md))
2. **`data/phase1/p09/live.jsonl` is irreplaceable.** 6,777 records; it is the only Lighter
   premium history that exists anywhere, live or archived, and it is gitignored and on one machine.
   If it is lost, Lighter's formula confirmation cannot be re-derived from 2026-09-19 — only
   re-earned by a fresh recording. A same-disk copy exists in `data/phase1/_backup/`; an
   **off-machine backup is still outstanding** and is action 1 in §8.
   ([`data_audit.md` §6](data_audit.md#6-known-gaps))
3. **The HL archive's recent days can be badly incomplete.** 2026-09-15 held 156 of 1,440 rows and
   09-16 held 505 of 1,440, and **neither was the newest file at download time**; 09-17 was
   complete; 09-18 held 596. The cause is unexplained and unverified against any HL-side status
   source. Files start cleanly at 00:00:00Z with no internal gaps and simply stop early.
   ([P1](evidence/P1-hl-asset-ctxs.md), [P10](evidence/P10-rebuild.md))
4. **Symbol matching is unsolved.** HL's `k`-prefixed markets (`kPEPE`, `kBONK`, `kFLOKI`,
   `kSHIB`, …) are Lighter's `1000`-prefixed ones and do not join on an exact symbol match. The
   both-venue candidate set was 95 markets out of 233 HL and 246 Lighter; those pairs were silently
   excluded. ([P0](evidence/P0-sample.md))
5. **Unresolved inferences, carried forward as labels, not facts:**
   - HL's settlement-stamp convention (`[T−1h, T)`) — inferred from an alignment test, not docs.
   - HL's `asset_ctxs` `time` = the snapshot instant of a per-minute poll — inferred from cadence
     and per-minute value changes, not confirmed against HL docs.
   - HL's 1e-7–1e-6 residual is *consistent with* 5-second averaging polled at 60 s; the negative
     correlation (−0.488) between time-into-hour and error is *consistent with* convergence. Both
     are hypotheses.
   - Lighter `/fundings` `value` looks like `rate/100 × mark price`; inferred from price-ratio
     matching, never confirmed.
   - P9's two recording stalls are attributed to host suspension by inference; no host logs exist.
   - 0xArchive's free tier reaching only ~30 days, and Build unlocking older calls, are both
     inferred from tier descriptions and never tested.
   - 0xArchive's WS replay reportedly carries a Lighter `premium` — observed once, untested; the
     REST route does not.
   - A Lighter delisting inferred from the last trade/candle before `inactive` is an unvalidated
     proxy.
6. **Coverage gaps Phase 1 knowingly left open** ([`data_audit.md` §6 and "Known gaps carried out
   of Phase 1"](data_audit.md#6-known-gaps)):
   - No analysed archive date exercises 2023–2024; the oldest analysed date is 2025-02-21, so
     pre-2025 schema stability rests only on the first probe run's pass over 2023-05-20.
   - HL's `fundingHistory` coverage start was **never queried directly** for any sample coin; the
     dates used come from 0xArchive's mirror. HL listing dates also differ between sources and
     neither is confirmed — P1's candle-derived BTC date (2020-08-19) predates Hyperliquid's
     existence and is an artefact.
   - Continuity *inside* the HL node-fills date ranges was never scanned (first/last leaf only),
     and whether the sample coins ever appear namespaced (`xyz:MU` style) was not checked.
   - The L2 archive was sampled for 2 hours on 1 of 1,230 dates — no real gap scan exists, and only
     178 of 234 markets have a file in a given hour.
   - Lighter `/api/v1/exchangeMetrics` and `/executeStats` are unclassified (HTTP 400 on guessed
     parameters).
   - Lighter's `1d` funding resolution returns only the last ~2 days regardless of the range asked
     for; `count_back` is ignored whenever both `start` and `end` are given.
   - PONS contributes nothing to the old/mid archive dates — P10's old-window rebuild ran on 4
     mid-cap coins, not 5.
   - 0xArchive credit use is 32–33 (the vendor's counter is not monotonic between reads), and the
     account's quota changed from 50,000 to 200,000 mid-probe, vendor-side and unexplained.
7. **One stale sentence in the evidence.** P9's "Open issues" still says
   `probes/p09_live.py` couples websocket-snapshot writing to a successful REST poll and was left
   unchanged on purpose. Commit `32bf4c0` subsequently decoupled them and bounded the poll with
   `asyncio.wait_for(..., timeout=45)`. The *finding* and the five requirements are unaffected —
   only that one sentence about the probe's current code is out of date.

---

## 8. Recommended next actions, in order

1. ~~**Back up `data/phase1/p09/live.jsonl` off this machine** (cloud storage or a second disk).~~
   **Done.** It is in S3 at
   `s3://fundr-recorder-<AWS_ACCOUNT_ID>-us-east-1/phase1/p09/live_2026-09-19.jsonl`, 5,557,972 bytes,
   verified against the local copy. It is the only Lighter premium history in existence and
   Lighter's formula confirmation depends on it; the pre-existing `_backup/` copy was on the same
   disk and did not discharge this on its own. See the recorder runbook's "Prior art in the
   bucket" (`docs/phase2/recorder_runbook.md`) for the operational note: do not delete it.
   ([`data_audit.md` §6](data_audit.md#6-known-gaps))
2. **Build the always-on recorder and start it** (§4) — before any backfill, before any modelling.
   Every day it is not running is a day of Target C that cannot be recovered. Build it fresh
   against the five requirements rather than adapting `probes/p09_live.py`, and give it the full
   field list in §4 including the per-cycle universe snapshot (the only future source of Lighter
   delisting dates).
3. **Take the free second Lighter recording that retires the single-window caveat.** This is the
   recorder's first job, and it costs nothing: once it has run for a day or two across markets
   that settle negative as well as positive, re-run
   [`../../probes/p07_lighter_formula_check.py`](../../probes/p07_lighter_formula_check.py)
   against the new premium history. Even a handful of additional days moves Lighter's evidence
   from 16 off-baseline hours in one 4-hour window to a sample comparable with HL's 245.
4. **Set the bar for Phase 11's dependence on Lighter's formula, and meet it before relying on
   it.** Concretely, before any Phase 11 result leans on the Lighter formula, the re-validation in
   action 3 should cover: **at least 100 off-baseline market-hours** (the threshold
   `analysis.rebuild_verdict` already uses), **hours spanning more than one day**, **at least one
   genuinely negative-settling hour** (`direction == "short"`), and — if any such market is ever in
   scope — **one market with `funding_premium_multiplier` ≠ 1** (RWA or Pre-IPO), which is the only
   way to test whether the multiplier scales the small clamp. The 4% big clamp will almost
   certainly stay untested; record it as docs-only rather than pretending otherwise.
5. **Query HL's `fundingHistory` coverage start directly, per sample coin.** One free call each;
   it settles the listing-date disagreement described in §7.6 and fixes the true start of the
   Target A study window on the HL side.
6. **Backfill Targets A and B from free native sources**: HL `fundingHistory` and Lighter
   `/api/v1/fundings` `1h`, both from each market's listing, with 0xArchive as an independent
   cross-check where it reaches. Add the archive-day validation and re-download logic from §5
   before pulling any `asset_ctxs` history.
7. **Fix the universe before fixing the sample**: add the `k*` ↔ `1000*` alias table and re-check
   for other denomination prefixes, then define the point-in-time universe by HL OI.
8. **Decide on 0xArchive Build ($49/month) last, and only on evidence.** It is not needed for the
   core plan: Targets A and B run on free native data and Target C is prospective regardless. Buy
   it only if Phase 7 wants Lighter intra-hour OI/index-price history back to 2025-08-25, or Phase
   3 wants a Lighter-OI point-in-time universe before the recorder's start. If bought: **first test
   one call with `start` older than 30 days** to confirm the tier actually unlocks history (this is
   inferred, not tested), validate the Lighter `index_price` series against Lighter's own live
   field over a window the recorder covers, and budget one month rather than a subscription — the
   backfill is a one-off. ([P8](evidence/P8-oxarchive.md), [`decision_map.md`](decision_map.md))

---

## Where to look for more

| Question | Note |
|---|---|
| Which markets the probes used, and the symbol-matching problem | [P0](evidence/P0-sample.md) |
| HL `asset_ctxs` schema, cadence, coverage, incomplete recent files | [P1](evidence/P1-hl-asset-ctxs.md) |
| HL L2 archive structure, depth, cost | [P2](evidence/P2-hl-l2.md) |
| HL node fills / trades: coverage, fields, cost | [P3](evidence/P3-hl-node-fills.md) |
| HL API: settled funding, units/sign/precision, candle limit, alignment test | [P4](evidence/P4-hl-api-crosscheck.md) |
| Lighter settled funding: coverage, cap, resolutions, units, sign | [P5](evidence/P5-lighter-fundings.md) |
| Lighter market state: which endpoints are historical, what is missing | [P6](evidence/P6-lighter-market-state.md) |
| Both funding formulas, parameters, what `premium` means | [P7](evidence/P7-funding-formulas.md) |
| 0xArchive coverage, units, lag, credits, tiers | [P8](evidence/P8-oxarchive.md) |
| What live fields mean; the recorder requirements and the stall diagnosis | [P9](evidence/P9-live-probe.md) |
| The rebuild tests and why neither venue is attemptable | [P10](evidence/P10-rebuild.md) |
| Full source matrix, funding semantics, source hierarchy, Target C decision | [`data_audit.md`](data_audit.md) |
| Finding → phase consequence, with evidence links | [`decision_map.md`](decision_map.md) |
