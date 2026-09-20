# P9 — Live-watch probe

Status: verified

## What ran
- Command: `probes/p09_live.py --minutes 250` (background, started earlier); analysis via
  `FUNDR_DATA=/Users/jerryinyang/Trading/fundr/data/phase1 uv run python probes/p09_analyze.py`
  and `probes/p09_analyze_extra.py`
- Run date (UTC): 2026-09-19. The process was alive 11:27:23Z → 16:54:35Z, but **that is not the
  data window** — corrected 2026-09-20 (Task 15 part B), see "Actual coverage per venue" below.
- Markets: the 7 P0 sample markets (ENA, PONS, ONDO, ETHFI, JUP, KAITO, BTC / Lighter market ids
  1, 26, 29, 33, 38, 64, 231)
- Recording: `data/phase1/p09/live.jsonl`, 6,777 records: `metaAndAssetCtxs` 1638,
  `predictedFundings` 1638, `orderBookDetails` 1632, `funding-rates` 233, `ws_market_stats` 1631,
  `ws_reconnect` 4 (all Lighter websocket `ConnectionClosedError`), `poll_error` 1 (one
  `ReadTimeout`). `live_aborted_run1.jsonl` (6-minute aborted first attempt) was ignored per the
  brief.

### Actual coverage per venue (corrected 2026-09-20, Task 15 part B)
The original version of this note read the process's last record timestamp (16:54:35Z) as the end
of the data. It is not: the last two records are an error pair (`ws_reconnect` + `poll_error`), not
data. Per-source first and last record (from `live.jsonl`):

| source | venue | first | last | poll cycles |
|---|---|---|---|---|
| `metaAndAssetCtxs` | HL | 11:27:23Z | **15:30:17Z** | 234 |
| `predictedFundings` | HL | 11:27:23Z | 15:30:18Z | 234 |
| `orderBookDetails` | Lighter REST | 11:27:24Z | 15:30:19Z | 233 full + 1 partial |
| `funding-rates` | both | 11:27:28Z | **15:19:28Z** | 233 |
| `ws_market_stats` | Lighter ws | 11:27:28Z | **15:19:28Z** | 233 |

- **Hyperliquid data coverage: 11:27:23Z → 15:30:17Z** (4h03m of wall clock, 234 poll cycles), with
  a 649-second hole between the 15:19:23Z and 15:30:17Z cycles.
- **Lighter `market_stats` data coverage: 11:27:28Z → 15:19:28Z** (3h52m, 233 snapshot cycles),
  contiguous at ~1/minute with no hole. Nothing was recorded from the Lighter websocket after
  15:19:28Z, although the feed itself kept running — see "Why the Lighter websocket recording
  stopped" below.
- **Nothing at all was recorded between 15:30:19Z and 16:54:35Z** for either venue.
- Do **not** read any Lighter conclusion in this note as covering 5h27m. The Lighter numbers below
  rest on 3h52m.

### Hour counts per venue (unchanged by the correction, recomputed)
The profiled-hour filter is `n_rows >= 50` one-minute samples per coin/market-hour. Clock hours
touched: 11:00 (partial, 33 samples — recording started 11:27), 12:00, 13:00, 14:00 (60 samples
each), 15:00 (partial: HL 20 cycles, Lighter 20 snapshots). So for **both** venues the hours that
pass the filter are 12:00, 13:00 and 14:00 UTC × 7 markets = **21 coin/market-hours**. The "21
fully-observed coin/market-hours per venue" figure used throughout this note was computed from the
data, not from the assumed window, and is therefore correct as it stands; the shorter Lighter
window does not change it, because the hours it removes (15:00 onward) were already excluded as
incomplete. The same is true of every derived count below (16 informative HL hours, 11 informative
Lighter hours, the 6/21 and 21/21 alignment rates). Only the theoretical denominator changes:
7 markets × 3 complete hours, not 7 × 5–6.

## What came back

### Plain script (`probes/p09_analyze.py`) — no bugs found, ran unmodified

```
{'metaAndAssetCtxs': 1638, 'predictedFundings': 1638, 'orderBookDetails': 1632, 'funding-rates': 233, 'ws_market_stats': 1631, 'ws_reconnect': 4, 'poll_error': 1}

HL share of hours where funding moves within the hour: 0.762 (16/21)
HL last-in-hour vs closing settlement: {'n': 21, 'n_match': 6, 'rate': 0.286, 'mean_signed_error': 9.34e-08}
HL last-in-hour vs opening settlement:  {'n': 21, 'n_match': 5, 'rate': 0.238, 'mean_signed_error': 6.57e-06}

Lighter share of hours where current_funding_rate moves: 0.619 (13/21)
Lighter current_funding_rate last-in-hour vs closing settlement (signed_rate): {'n': 21, 'n_match': 21, 'rate': 1.0, 'mean_signed_error': 0.0}
Lighter funding_rate field vs opening settlement (signed_rate):               {'n': 21, 'n_match': 21, 'rate': 1.0, 'mean_signed_error': 0.0}
(rate col gives identical results — no negative rows in the 7-market sample, see below)

minutes with negative current_funding_rate: 0
/funding-rates exchanges seen: ['binance', 'bybit', 'hyperliquid', 'lighter']
poll errors: 1, ws reconnects: 4
```
Full verbatim output in the run log; 21 fully-observed coin/market-hours per venue
(`n_rows >= 50`, i.e. hours with a complete ~55–60 one-minute samples) out of a theoretical
7 markets × 3 complete hours (12:00–14:00 UTC — see "Actual coverage per venue" above; the
original text said "5–6 hours", which came from the overstated window).

### Extra checks (`probes/p09_analyze_extra.py`, new — committed)

**(a) Restricting to hours where the settled rate actually changed vs. the prior settlement**
(otherwise a stale last-in-hour value would trivially "match" both the opening and closing
settlement when they're equal):
- HL: 31/56 settlements (raw `fundingHistory` rows fetched for the window) differ from the
  immediately preceding settlement for that coin. Of the 21 fully-observed profiled hours, 16
  are in this "informative" subset.
  - Closing alignment: full sample 6/21 (28.6%) → informative subset **1/16 (6.25%)**.
  - Opening alignment: full sample 5/21 (23.8%) → informative subset **0/16 (0%)**.
  - Restricting to informative hours makes the match rate *worse*, not better — the full-sample
    rate was inflated by baseline (constant-funding) hours where any alignment matches trivially.
- Lighter: 66/133 settlements differ from the prior one; 11 of the 21 profiled hours are
  informative.
  - Closing alignment: full sample 21/21 → informative subset **11/11 (100%)**.
  - Opening alignment: full sample 21/21 → informative subset **11/11 (100%)**.
  - Lighter's match rate is unaffected by the restriction — it is not an artifact of trivial
    baseline hours.

**(b) Lighter sign convention.** None of the 7 sample markets went negative anywhere in the
3h52m Lighter recording (`current_funding_rate` and `funding_rate` both ≥ 0.0012 the whole time — this
window happened to be long-pays-short across the board for these markets). Extended the check
to all Lighter markets:
- `GET /api/v1/funding-rates` (live, unauthenticated): 214 Lighter markets, 16 currently negative
  (e.g. `CAP` market_id 212 at −0.003672, `XCU` at −0.001176).
- Took market_id 212 (`CAP`). Latest `/api/v1/fundings` row: `{timestamp: 1789858800, rate:
  "0.0478", direction: "short"}` (rate is reported as an unsigned magnitude; sign lives in
  `direction`). Live `market_stats` websocket for the same market, same instant:
  `{current_funding_rate: "-0.0459", funding_rate: "-0.0478", funding_timestamp: 1789858800000}`.
- `lighter_signed_rate(0.0478, "short")` (the function in `src/fundr/sources/lighter_api.py`,
  which returns `-rate` for `direction == "short"`) = **−0.0478**, which equals the websocket's
  signed `funding_rate` (−0.0478) exactly, at the same settlement timestamp
  (`1789858800 * 1000 == 1789858800000`).
- **Sign convention confirmed**: `direction: "short"` means shorts pay longs (the signed rate is
  negative), and `direction: "long"` means longs pay shorts (signed rate positive) — the same
  sense as Hyperliquid's convention (P4) and as P5 states it, matching the codebase's existing
  `lighter_signed_rate` assumption exactly. This was previously "pending Task 12" (P5, P7); it is
  now verified against live data at a real negative-rate market, not just inferred.

**(c) HL near-miss quantified.** Restricting to the 21 fully-observed hours (`match_stats` against
the closing settlement, tolerance `1e-10` per P4/P7):
- |error| distribution: min 0, mean 2.52e-07, median 2.07e-07, max 1.07e-06 — i.e. **~2,000×–
  10,700× the reported tolerance** (mean 2,524×, median 2,067×, max 10,703×). This is the same
  scale of residual P4 found on the archived data (~4e-7 to 1.3e-6), now confirmed live and at
  finer-than-1-minute significance since the recording polls roughly every 60s just like the
  archive.
- Checked every in-hour minute sample (not just the last) against the closing settlement: 6/21
  coin-hours have *some* in-hour sample that matches exactly. All 6 are baseline hours (settled
  funding = the interest-rate floor `0.0000125`, constant all hour) — i.e. these matches are
  trivial (a constant value trivially "matches itself" at every sample), not evidence of the live
  value hitting the settled rate mid-hour in an off-baseline hour. **No off-baseline hour ever
  produced an exact in-hour match** at any sampled minute.
- Correlation between seconds-into-the-hour and |error| vs. the closing settlement, across all
  in-hour samples in the 21 hours: **−0.488** (moderate negative). This is a **hypothesis, not
  confirmed**: it is consistent with the live value converging toward the closing settlement as
  the hour progresses (matching HL's documented continuous-average formula, P7), but a 60-second
  poll cadence cannot distinguish "converges smoothly" from "jumps near the end" and the residual
  never actually reaches zero except trivially at baseline.

## Findings

### Hyperliquid: current funding is a running estimate, converges toward but does not exactly equal the closing settlement
- Moves within the hour on 76.2% of fully-observed coin-hours (16/21), consistent with P1/P4.
- Last-in-hour value is closer to the closing settlement (mean signed error 9.3e-08) than to the
  opening one (6.6e-06), same direction as P4's archived finding — this is a *running estimate of
  the next (closing) settlement*, not the last settled rate.
- It never exactly equals the closing settlement except trivially at the interest-rate baseline
  (constant-value hours). On the informative (rate-changed) subset the "match" rate falls to
  6.25%/0% — the full-sample rates in the plain script are inflated by baseline hours. Residual
  error is consistently ~1e-7–1e-6 (2,000×–10,700× the reported tolerance), a small but real and
  persistent gap, not floating-point noise.
- **For the Target C decision rule, condition (b)** ("last in-hour value equals the settled rate
  … on at least 99% of market-hours") **does not hold for HL** — confirming P4's interim reading
  with live, sub-archive-cadence data. HL's live `funding` field is a continuously-updated
  estimate of the upcoming settlement, useful prospectively, but not a source of exact historical
  settled values (P5/P10's settled-history endpoint remains the source of truth for that).

### Lighter: current_funding_rate is a running estimate of the currently-accruing interval, and it converges to be identical to the settlement that closes that interval
- **Settlement-timestamp convention (checked directly against the live API)**: a `/fundings` row
  timestamped `T` reports the interval that just ended at `T` — i.e. it covers `[T-1h, T)`.
  Checked 2026-09-19 ~23:53:58 UTC (market_id 1): the latest available row was timestamped
  `2026-09-19 23:00:00 UTC`, i.e. the settlement for the interval `[22:00, 23:00)` that had
  already closed, with no row yet for the interval `[23:00, 00:00)` still in progress at request
  time. This means the `settle_time = hour + 1h` convention this probe's `attach_settled` call
  uses for "closing settlement" pairs each hour's profiled samples with the settlement of *that
  same hour* (the interval `[hour, hour+1h)`), not with some later, future settlement.
- `current_funding_rate` moves within the hour on 61.9% of fully-observed hours (13/21) — the
  value visibly changes minute to minute while the interval it belongs to is still open.
- The **last in-hour value always exactly equals the settlement that closes that same hour**
  (21/21 = 100%, and 11/11 = 100% restricted to hours where the rate genuinely changed — not a
  baseline artifact). Given the timestamp convention above, this means: while an hourly interval
  is still accruing (has not yet closed), `current_funding_rate` is already tracking toward — and,
  by the last poll before the boundary, exactly matching — the value that interval will settle at.
  It is **not** a stale record of an already-completed settlement, and it is **not** a forecast of
  some *future* interval beyond the current one.
- `funding_rate` + `funding_timestamp` instead match the settlement that *opened* the profiled hour
  (21/21) — i.e. they hold the last *already-completed* settlement, one interval behind
  `current_funding_rate`. This is consistent with NautilusTrader's documented mapping quoted in
  P7: `current_funding_rate` = the running estimate of the upcoming/currently-accruing interval,
  `funding_rate`/`funding_timestamp` = the last completed payment.
- Because `current_funding_rate`'s value at the instant just before its interval closes is
  bit-identical to what then gets recorded as that interval's settled rate (P5's `/fundings`), the
  live field behaves as a running value that **converges to and exactly matches** the settlement
  about to become official — unlike HL's approximate convergence, which never exactly matches
  off-baseline.
- **For the Target C decision rule, condition (b) holds for Lighter on this evidence** (100% match,
  both on the full sample and the informative subset) — marked **interim** per the brief: this
  probe used the live `market_stats` websocket and the `/fundings` REST endpoint as the settled
  source, not an archived one. Target C's Outcome A specifically requires an *archived* source
  that holds intra-hour current funding; this live-only check establishes what the field means but
  does not by itself establish an archived source with the same property. The 0xArchive comparison
  (a separate task) is what tests that, and can still change which source Phase 2 uses even though
  it is not expected to change this reading of what the *field itself* means.

### What each field means (confirmed by data, not just docs)
- HL `funding` (from `metaAndAssetCtxs`/archive): continuously-updated running average premium
  formula output, tracks toward but does not equal the closing settlement.
- HL `predictedFundings`: HL's own forward-looking predicted rate for the *next* settlement, plus
  `nextFundingTime`/`fundingIntervalHours`, for HL and other tracked venues (P4). Sampled again
  here; unchanged reading.
- Lighter `current_funding_rate` (ws `market_stats`): a running estimate of the funding for the
  currently-accruing (not-yet-closed) interval — confirmed to become bit-identical to that
  interval's settlement at the hour boundary.
- Lighter `funding_rate` + `funding_timestamp` (ws `market_stats`): the last *already-completed*
  settlement (one interval behind `current_funding_rate`) and when it closed — confirmed identical
  to the settlement that opened the current profiled hour. (`/fundings` rows follow the same
  convention: a row timestamped `T` covers `[T-1h, T)`, confirmed directly against the live API —
  see the Lighter section above.)
- `/funding-rates` (REST, both `/funding-rates` endpoint queried live and each venue's field):
  aggregates current rates across `binance, bybit, hyperliquid, lighter` for cross-venue
  comparison; Lighter's entries here report the *signed* rate directly (unlike `/fundings`, which
  splits magnitude/`direction`) — confirmed via the market-212 check (`funding-rates` gave
  `-0.003672`≈ws `current_funding_rate` `-0.0459` direction/sign, though not the identical instant
  since the two calls were not simultaneous; the sign and rough magnitude agree).

### Trade-flow check (Task 8 Step 5, `probes/p09_trades_check.py`)
- HL: 19 websocket trade messages captured in 30s (`wss://api.hyperliquid.xyz/ws`,
  `trades` channel, ENA). Each fill carries `side` (`"A"`/`"B"`, aggressor side), `px`, `sz`,
  `time`, `hash`, `tid`, `users` (both counterparty addresses) — a usable signed trade-flow
  source.
- Lighter: 5 websocket messages (`trade:<market_id>` channel, market 29/ENA), each with 1+ fills.
  No explicit "side" field, but `is_maker_ask` (bool) plus `ask_id`/`bid_id` let the aggressor
  side be inferred (taker = ask side when `is_maker_ask=false`, else taker = bid side); also
  carries `price`, `size`, `usd_amount`, `tx_hash`, `ask_account_id`/`bid_account_id`, and
  pre-trade position/margin state for both counterparties. A usable signed trade-flow source,
  requiring one derivation step (`is_maker_ask` → taker side) that HL doesn't need.
- Both venues have a working live signed-trade-flow source suitable for a Phase 2 recorder.

### Live fields available for a Phase 2 recorder (from `live.jsonl`'s actual recorded keys)
- HL `metaAndAssetCtxs`: `coin, funding, premium, open_interest, oi_notional_usd, mark_px,
  oracle_px, day_ntl_vlm, is_delisted`. Missing here (not polled by this probe, but present on
  other HL endpoints per P1/P4/P6/P8): impact bid/ask prices, mid price, L2/L3 order book, signed
  trades (confirmed available separately above), node fills.
- Lighter `ws_market_stats`: `current_funding_rate, funding_rate, funding_timestamp, premium,
  mark_price, index_price, mid_price, last_trade_price, best_bid_price, best_ask_price,
  open_interest, open_interest_limit, daily_base_token_volume, daily_quote_token_volume,
  daily_price_change/high/low, funding_clamp_big/small, base_interest_rate`. Very complete —
  essentially every field a Phase 2 recorder needs is already exposed on this one websocket
  channel per sample market.
- Lighter `orderBookDetails`: static/slow-moving market config (fees, margin fractions, clamps,
  multiplier) plus `daily_chart`, `daily_trades_count` — useful supplementary metadata, not
  needed at high frequency.
- `/funding-rates` (both venues, cross-venue): `binance, bybit, hyperliquid, lighter` current
  rates in one call — cheap cross-venue snapshot.
- No fields were found to be missing that the design doc's P9 row asked for (funding, premium, OI,
  mark/index/oracle price, volume are all present on at least one live endpoint per venue; signed
  trades confirmed separately).

### Reliability (rewritten 2026-09-20, Task 15 part B — the original reading was wrong)
- 234 poll cycles were started, not ~250; the run was launched with `--minutes 250` but only 234
  cycles ran before it ended, and it ended 77 minutes *after* its own deadline (see below).
- 1 `poll_error` (`ReadTimeout('The read operation timed out')`) at 16:54:35Z. The original text
  ("logged, skipped, run continued automatically; no data loss beyond that one cycle") is
  **incorrect**: this error is where the run ended, and it cost 84 minutes of both venues' data.
- 4 Lighter `ws_reconnect` events (all `ConnectionClosedError(None, None, None)`) at **11:45:23Z,
  13:30:51Z, 15:30:15Z, 16:54:35Z**. Only the first two happened during normal operation and both
  recovered cleanly (see the stall analysis below) — 2 reconnects over 3h52m of live streaming,
  ~1 per 1.9h, not the "~1 per 1.3h" the original text extrapolated. The last two sit exactly at
  the two recording stalls and are symptoms of those, not independent events.

### Why the Lighter websocket recording stopped at 15:19:28Z (finding from this run, 2026-09-20)
Diagnosed in Task 15 part B from `live.jsonl` alone; `probes/p09_live.py` was deliberately **not**
changed — the recorder is Phase 2's to build, and this is a requirement for it, not a fix now.

Evidence, in order:
- **The feed did not stall.** Every one of the 1,631 `ws_market_stats` snapshots carries
  `n_msgs > 0` (min 2, median 47, max 366). There is no snapshot written from a stale `latest[m]`
  anywhere in the run, so the "silently repeating the last value" failure mode did **not** occur.
- **The reconnect loop worked.** Around the 11:45:23Z and 13:30:51Z reconnects the per-minute
  snapshots continue uninterrupted with healthy `n_msgs` on both sides of each event (e.g. 13:24:28
  n_msgs 29–136 across the 7 markets, 13:25:35 n_msgs 32–117). No snapshot is missing there.
- **The recording of the feed is coupled to the REST poll.** In `probes/p09_live.py`,
  `write_ws_snapshot()` is called only after `poll_once()` returns, inside the same `try`. If a REST
  call inside `poll_once()` hangs or raises, that cycle's websocket snapshot is never written, even
  though the websocket task is still receiving messages on the event loop. This is the mechanism
  that lost the data.
- **What the last cycle did.** Record counts decompose exactly: 234 cycles of `metaAndAssetCtxs`
  and `predictedFundings` (1638 = 234 × 7), but only 233 of `funding-rates` and of
  `ws_market_stats` (1631 = 233 × 7), and 1632 `orderBookDetails` = 233 × 7 + 1. Cycle 234 wrote
  its 7 `metaAndAssetCtxs` (15:30:17Z), its 7 `predictedFundings` (15:30:18Z) and exactly **one**
  `orderBookDetails` (ENA, 15:30:19Z), then blocked inside the next
  `lighter.order_book_details(...)` call for **84 minutes**, until the `ReadTimeout` at 16:54:35Z.
  The `except` branch then logged `poll_error` and skipped `write_ws_snapshot()`, and the
  `while time.time() < end` test finally failed (the deadline had passed at 15:37:23Z), so the run
  exited.
- **There were two stalls, not one.** The cycle before it also stalled: 649 s elapsed between the
  15:19:23Z cycle and the 15:30:17Z one, against a 60 s target.
- **Inference (not confirmed): the host suspended twice.** An 84-minute block on an httpx client
  built with `timeout=30` should be impossible; a websocket `ConnectionClosedError` is logged 2 s
  before the first resume (15:30:15Z) and again in the same second as the final timeout
  (16:54:35Z); and the 649 s and 84 min gaps are both wall-clock gaps with no work recorded in
  them. A suspended/sleeping laptop losing its sockets explains all of it; a venue-side outage
  explains none of it (Lighter's REST and websocket were both healthy immediately before and, per
  Task 15 part B's re-fetch, its `/fundings` history for the stalled window is complete). Not
  proven — no host logs were kept.

**Requirements this places on the Phase 2 recorder** (all labelled as findings from this run):
1. **Never gate feed recording on a REST poll.** Websocket snapshots must be written on their own
   timer, in a task that cannot be blocked by any HTTP call. One hung REST call silently ended the
   Lighter websocket recording here even though the feed was live.
2. **Detect a stalled feed explicitly**: if a snapshot interval sees `n_msgs == 0` for N
   consecutive intervals (N small, e.g. 2–3), force a reconnect and raise an alert, and either skip
   the snapshot or mark it `stale: true` — never silently re-emit `latest[m]`. This run never hit
   `n_msgs == 0`, but the code path that would repeat a stale value indefinitely exists by
   construction, and a reader cannot tell a repeated value from a fresh identical one without
   `n_msgs`. Keeping an `n_msgs`-style counter on every snapshot is itself a requirement — it is
   the only reason this diagnosis was possible.
3. **Enforce timeouts out-of-band.** A client-level `timeout=30` did not bound an 84-minute read.
   Wrap every network call in an external watchdog (`asyncio.wait_for` or equivalent) and re-issue
   or skip on expiry.
4. **Detect wall-clock jumps / host suspension.** Treat any inter-cycle gap greater than ~2 poll
   intervals as a coverage hole, write an explicit gap record, and re-check the run deadline inside
   the cycle rather than only between cycles (this run overran its own deadline by 77 minutes).
5. **Alert on coverage, not on record counts.** The missing hour here shows up as 1631 vs 1638
   records — a 0.4% difference that no count-based check would catch. Monitor expected-vs-actual
   snapshots per market per hour.

## Bug fixes
None. `probes/p09_analyze.py` ran to completion unmodified against the full 6,777-line final
recording (as it had against the partial 5,106-line log in part A) — no dtype, join, or
empty-frame errors.

## Files changed
- `probes/p09_analyze_extra.py` (new, committed) — the three controller-added checks above.
- `docs/phase1/evidence/P9-live-probe.md` (this file, new, committed).
- `docs/phase1/evidence/P5-lighter-fundings.md` (updated, committed) — sign convention verified,
  cross-venue normalisation added.
- `data/phase1/p09/hl_profile.parquet`, `data/phase1/p09/lighter_profile.parquet` — overwritten by
  this run's `p09_analyze.py`; not tracked/committed (gitignored `data/`).
- `probes/p09_analyze.py` unchanged from part A — not recommitted.

## Open issues
- HL's residual (~1e-7–1e-6, off-baseline) is still unexplained beyond the sampling-cadence
  hypothesis already recorded in P4/P10 — this probe adds a live confirmation (not just archived
  1-minute data) that the gap persists and correlates negatively with time-into-hour, but does not
  identify its exact source (still consistent with "true 5-second TWAP vs ~60s polling" from P4).
- Lighter's Target C reading is interim pending the 0xArchive comparison (next task) — this note's
  live-source reading (`current_funding_rate` = running estimate of the currently-accruing
  interval, exactly equal to that interval's settlement at the boundary) is not expected to
  change, but Outcome A also requires an *archived* source with the same property, which this
  probe does not test; which source Phase 2 should actually read from could still change.
- The cross-venue `/funding-rates` sign check for Lighter (market 212) used two calls a few
  seconds apart (REST snapshot vs. websocket), not one atomic read; sign and rough magnitude
  agree, but this is not as tight a check as the `/fundings`-vs-websocket comparison, which used
  matching timestamps.
- The two recording stalls (649 s at 15:19–15:30Z and 84 min at 15:30–16:54Z) are attributed to
  host suspension by **inference only** — the evidence rules out a venue outage and rules out a
  feed stall, but no host-level logs were kept to prove it.
- The Lighter conclusions in this note rest on 3h52m / 21 market-hours, not the 5h27m the original
  version implied. Task 15 part B, which reuses this recording as its only Lighter premium source,
  is bounded by the same 3h52m (28 market-hours after its own coverage filter, 16 off-baseline).
- `probes/p09_live.py` still couples websocket-snapshot writing to a successful REST poll. Left
  unchanged on purpose: the Phase 2 recorder is a separate build, and the requirements above are
  where this is recorded.

## Corrections log
- **2026-09-20 (Task 15 part B)**: the stated run window (11:27:23Z → 16:54:35Z, "5h27m") was the
  process lifetime, not the data window. Replaced with per-venue coverage (HL to 15:30:17Z,
  Lighter `market_stats` to 15:19:28Z); corrected the theoretical hour denominator from "7 markets
  × 5–6 hours" to 7 × 3 complete hours; rewrote the Reliability section (the `poll_error` ended the
  run rather than being survived, and the reconnect-rate extrapolation was wrong); added the stall
  diagnosis and the Phase 2 recorder requirements. **No measured result changed** — every profiled
  hour count, match rate and alignment figure in this note was computed from the data with an
  `n_rows >= 50` filter that already excluded the hours the shorter window removes.
