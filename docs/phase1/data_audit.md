# Phase 1 Data Audit

Run window: 2026-09-19 – 2026-09-20 (UTC). Sample: see [P0](evidence/P0-sample.md) — 7 markets
listed on both venues (ENA, PONS, ONDO, ETHFI, JUP, KAITO, BTC), fixed once and used by every probe.

**What this audit found, in plain terms.** Both venues publish a complete, trustworthy history of
what funding actually settled at, every hour, going back to each market's listing — Hyperliquid to
2023, Lighter to January 2025 for our oldest sample market. That part of the research (Targets A
and B, the funding level and the cross-venue spread) is fully supported by data that already
exists. What does *not* exist is a history of the *running* funding number a trader sees on screen
part-way through the hour. Hyperliquid's minute-by-minute archive keeps a running value, but it is
an approximation that never lands exactly on the rate the hour settles at — it is off by roughly
0.00004%–0.0001% of notional per hour (1e-7 to 1e-6 as a fraction), which is small in money but
thousands of times larger than the precision the venue reports, so it cannot be treated as the
real thing. Lighter's running value *is* the real thing — it converges to exactly the settled rate,
digit for digit — but nobody archived it: neither Lighter nor the third-party vendor 0xArchive kept
it, so it only exists from the moment we start recording it ourselves. The consequence is the same
on both venues: **Target C (realized minus published funding) can only be studied prospectively,
from data we record from now on.** Everything needed to record it is available live and free on
both venues today, and the recorder should be built first in Phase 2. Total cost of this entire
probe: **$0.0118** of AWS charges and **32** 0xArchive credits.

---

## 1. Venue/source matrix

Status is one of: verified / unverified / unavailable. "verified" means a real file or response in
`data/phase1/` stands behind the row.

| Venue | Dataset | Endpoint/path | Coverage start | Native resolution | Key fields | Timestamp convention | Missingness | Access/cost | Status | Evidence |
|---|---|---|---|---|---|---|---|---|---|---|
| HL | asset_ctxs | s3://hyperliquid-archive/asset_ctxs/[date].csv.lz4 | 2023-05-20 (first key; 1,217 daily files to 2026-09-18; 21 markets on day 1, 234 by 2026-09-18) | 1 row per coin per **1 minute** (median interval exactly 1m on all sampled dates) | `time, coin, funding, open_interest, prev_day_px, day_ntl_vlm, premium, oracle_px, mark_px, mid_px, impact_bid_px, impact_ask_px` (12 cols, identical schema 2023→2026) | `time` = ISO-8601 whole-second UTC with `Z`, e.g. `2026-09-18T00:00:00Z`; read as the snapshot instant of a per-minute poll (**inference** from cadence and per-minute value changes; not confirmed against HL docs) | 0 gaps >5 min and 0 nulls on the 3 analysed dates; upload lag 1 day. **Recent daily files can be badly incomplete**: 2026-09-15 156/1440 rows (ends 02:35Z), 09-16 505/1440 (ends 08:24Z), 09-17 1440/1440, 09-18 596/1440 (ends 09:55Z) — cause unexplained, not verified against any HL-side status source. Which coins exist varies by date (PONS only on the recent date) | requester-pays S3, `ap-northeast-1`; whole Phase 1 cost $0.0118 | verified | [P1](evidence/P1-hl-asset-ctxs.md), recent-file gaps [P10](evidence/P10-rebuild.md) |
| HL | L2 book | s3://hyperliquid-archive/market_data/[date]/[hour]/l2Book/[coin].lz4 | 2023-04-15 (first of 1,230 daily prefixes; last 2026-09-18) | Full snapshot every ~5.4 s (median 5,388 ms), 20 levels per side | outer `time` (ISO, µs, local receipt), `raw.data.time` (ms epoch, venue snapshot), `raw.data.levels` = [bids, asks] each `{px, sz, n}` | `raw.data.time` = exchange snapshot instant (ms epoch); outer `time` = local receipt | 178 coins have a file per hour vs 234 listed markets — not every market is archived every hour; all 7 sample coins present in both sampled hours; no gaps in those hours. Only 2 of 24 hours on 1 of 1,230 dates was sampled, so no full-history gap scan exists | requester-pays S3; ≈$1.50/year to mirror all 7 sample coins (9.38 GB egress + request charges) | verified | [P2](evidence/P2-hl-l2.md) |
| HL | node_fills_by_block / node_fills / node_trades | s3://hl-mainnet-node-data/{node_fills_by_block,node_fills,node_trades}/hourly/[date]/[hour].lz4 | `node_fills_by_block` 2025-07-27 → today; `node_fills` 2025-05-25 → 2025-07-27 (superseded, exact handoff); `node_trades` 2025-03-22 → 2025-06-21 | Hourly files, per-fill records (block-grouped in `node_fills_by_block`); whole-network, not split by coin | `coin, px, sz, side ("B"/"A"), time (ms), startPosition, dir, closedPnl, hash, oid, crossed, fee, tid, cloid, feeToken, twapId, deployerFee`; block wrapper adds `local_time, block_time, block_number, events` | `time` = fill time (ms epoch); `block_time` = block timestamp | Internal continuity **not checked** (first/last-leaf descent only, no day-by-day scan). `node_trades` last leaf decodes to 0 lines → **unusable**. Coin names are sometimes namespaced (`xyz:MU`); whether the P0 sample coins ever appear namespaced is **not checked** | requester-pays S3; ≈$27.15/year for the whole network (300.7 GB) — cost does not scale down with fewer coins | verified (`node_fills_by_block`, `node_fills`); unavailable (`node_trades`) | [P3](evidence/P3-hl-node-fills.md) |
| HL | fundingHistory | info API (`{"type":"fundingHistory"}`) | Per market, **not measured directly** (only 3 target days were pulled). 0xArchive's mirror of the same series starts at each market's listing date (BTC 2023-05-20, JUP 2023-12-03, ONDO 2024-01-20, ETHFI 2024-03-21, ENA 2024-04-02, KAITO 2025-02-20, PONS 2026-08-31), 100% complete, zero gaps | 1h | `coin, fundingRate (str), premium (str), time (ms)` | `time` = the settlement instant, a few ms past the hour boundary (e.g. `00:00:00.115`); the row stamped `T` closes the interval `[T−1h, T)` (**inference**, supported by P4's alignment test: closing alignment has near-zero mean signed error, opening alignment is one-signed and ~30× larger) | No gaps found in the 3 sampled days (156 / 156 / 182 rows for 7 coins × 24h, missing rows are markets not yet listed). Full-history gap scan **not checked** | free | verified | [P4](evidence/P4-hl-api-crosscheck.md); mirror coverage [P8](evidence/P8-oxarchive.md) |
| HL | predictedFundings | info API | live only | Snapshot (one value per coin per venue) | per coin: list of `[venue, {fundingRate, nextFundingTime, fundingIntervalHours}]` for `HlPerp` (1h) plus `BinPerp`/`BybitPerp` (4h) | `nextFundingTime` = ms epoch of the next settlement | 234 coins returned; no history whatsoever (live snapshot by construction) | free | verified | [P4](evidence/P4-hl-api-crosscheck.md) |
| HL | candleSnapshot | info API | last 5,000 candles — and the real limit is tighter: a 5-day `BTC 1m` request returned 5,042–5,054 rows spanning exactly **3.50 days**; a 1-day window 29–30 days back returned **0 rows** | 1m (other intervals **not checked**) | `t, T, o, h, l, c, v, n, s, i` | `t` = candle open (ms epoch) | 1-minute history beyond ~3.5 days is not retrievable. Whether coarser intervals reach further back is **not checked** | free | verified | [P4](evidence/P4-hl-api-crosscheck.md) |
| HL | metaAndAssetCtxs | info API | live only | Snapshot; polled at 1/min for 4h03m in P9 | `coin, funding, premium, open_interest, oi_notional_usd, mark_px, oracle_px, day_ntl_vlm, is_delisted` | Read at poll time; no venue-side timestamp in the payload | 234 poll cycles, 1 `ReadTimeout` (which ended the run); a 649 s hole at 15:19–15:30Z and no data 15:30–16:54Z, attributed to host suspension (**inference**, no host logs) | free | verified | [P9](evidence/P9-live-probe.md) |
| HL | trades websocket | `wss://api.hyperliquid.xyz/ws`, `trades` channel | live only | Per fill | `coin, side ("A"/"B", aggressor), px, sz, time, hash, tid, users` | `time` = ms epoch | 19 messages in a 30 s check; longer-run reliability **not checked** | free | verified | [P9](evidence/P9-live-probe.md) |
| Lighter | /api/v1/fundings | REST | Per market, from its listing: BTC 2025-01-17 08:00Z (14,644 h), JUP 13,719 h, ENA 13,507 h, KAITO 12,954 h, ONDO 12,326 h, ETHFI 9,619 h, PONS 402 h (listed 2026-09-02) | 1h (verified). `1d` accepted but returns only the last ~2 days regardless of the range asked for. `1m`/`5m`/`15m` → HTTP 400 | `timestamp` (s), `value` (str), `rate` (str, unsigned percent, 4 dp truncated), `direction` (`long`/`short`) | `timestamp` = Unix seconds on the hour; the row stamped `T` reports the interval `[T−1h, T)` that just closed — **verified directly against the live API** (at 23:53:58Z the latest row was stamped 23:00, with no row yet for the open interval) | 0 gaps >1 h across all 7 markets from listing to now. 750-row per-call cap (confirmed); `count_back` is ignored whenever both `start`/`end` are given. `value`'s exact meaning is an **inference** (looks like `rate/100 × mark price`), not confirmed | free, no auth | verified | [P5](evidence/P5-lighter-fundings.md) |
| Lighter | /api/v1/funding-rates | REST | live only | Snapshot | `market_id, exchange, symbol, rate` — third-party reference rates across `binance, bybit, hyperliquid, lighter`, **not** Lighter's own historical rate | None in the payload; read at request time | A past `timestamp` param is accepted and silently ignored: the response was byte-for-byte identical to the no-param call (741 rows). 214 Lighter markets present, 16 negative at the time of the check | free | verified | [P6](evidence/P6-lighter-market-state.md), [P9](evidence/P9-live-probe.md) |
| Lighter | candles, markPriceCandles | REST | ≥1 year back at 1h; ≥180 days back at 1m (no truncation or error at those depths; true start **not checked**) | 1m, 1h, 1d and other documented resolutions | candles: `t,o,h,l,c,v` (base vol), `V` (quote vol), `i` (last trade id) — **no OI field**. markPriceCandles: `t,o,h,l,c,sc` | `t` = bucket open (ms epoch) | No gaps observed at the depths sampled; no systematic gap scan was run | free | verified | [P6](evidence/P6-lighter-market-state.md) |
| Lighter | orderBookDetails, orderBooks, exchangeStats, assetDetails, recentTrades | REST | live only (point-in-time snapshots; `orderBooks.created_at` is the one historical fact they carry) | Snapshot | orderBookDetails: `mark_price, index_price, open_interest, daily_*`, `funding_premium_multiplier, funding_clamp_small, funding_clamp_big, base_interest_rate`. orderBooks: `status (active/inactive), created_at`, fee/margin config. recentTrades: last ~10 trades | `created_at` = listing time (ms epoch); everything else is read at request time | **No delisting timestamp exists** — 22 of 246 markets are `status: "inactive"` with an identical field set and no `delisted_at`/`updated_at`. No historical series for any of these fields | free | verified | [P6](evidence/P6-lighter-market-state.md) |
| Lighter | /api/v1/trades | REST | not reachable | n/a | n/a | n/a | HTTP 400 `"auth query param and Authorization header are empty"` — auth required even for a public GET | free but auth-gated; no key held | unavailable | [P6](evidence/P6-lighter-market-state.md) |
| Lighter | /api/v1/exchangeMetrics, /api/v1/executeStats | REST | unknown | unknown | unknown | unknown | HTTP 400 `"invalid param"` on guessed `period`/`kind` values; valid enums not documented in the SDK. Left unclassified rather than guessed further | free | unverified | [P6](evidence/P6-lighter-market-state.md) |
| Lighter | market_stats websocket | WS (`market_stats` channel) | live only | ~1 message every 1.3 s per market (median 47 messages per market per minute); the `premium` and `current_funding_rate` fields refresh about once a minute | `current_funding_rate, funding_rate, funding_timestamp, premium, mark_price, index_price, mid_price, last_trade_price, best_bid_price, best_ask_price, open_interest, open_interest_limit, daily_base_token_volume, daily_quote_token_volume, daily_price_change/high/low, funding_clamp_big/small, base_interest_rate` | `funding_timestamp` = ms epoch of the **last completed** settlement; `premium`/`current_funding_rate` describe the interval still accruing | 1,631 snapshots over 3h52m, contiguous at ~1/min, every one with `n_msgs > 0` (no stale re-emission). 2 clean reconnects in 3h52m (~1 per 1.9 h); the recording then stopped because the recorder gated websocket writes on a REST poll that hung for 84 min — a recorder bug, not a feed failure | free, no auth | verified | [P9](evidence/P9-live-probe.md), field semantics [P7](evidence/P7-funding-formulas.md) |
| Lighter | trades websocket | WS (`trade:<market_id>` channel) | live only | Per fill | `price, size, usd_amount, is_maker_ask, ask_id, bid_id, ask_account_id, bid_account_id, tx_hash`, plus pre-trade position/margin for both sides | Per-message trade time | 5 messages in a short check; no explicit aggressor side — taker side must be derived from `is_maker_ask`. Longer-run reliability **not checked** | free | verified | [P9](evidence/P9-live-probe.md) |
| 0xArchive | Lighter funding / OI / trades / L3 | REST (`/v1/lighter/{funding,openinterest,trades,l3orderbook}/{coin}`), WS replay | funding & OI: **2025-08-25** for 6 of 7 sample coins (PONS 2026-09-02) — this is the vendor's ingest start, not the markets' listing dates | funding & OI: median 10 s. trades: per fill. L3: current snapshot only (no time param) | funding: `coin, symbol, timestamp, funding_rate` (**no `premium` field**; the WS replay payload does carry one — observed once, untested). openinterest adds `mark_price, oracle_price, index_price`. l3orderbook: `order_index, owner_account_index, side, price, remaining_size, original_size` | `timestamp` per record; `funding_rate` is a **fraction** = Lighter's native percent ÷ 100 (median ratio exactly 0.01 over n=466 joined rows) | Completeness by coin: BTC 97.4%, PONS 97.3%, ENA 96.9%, ONDO 95.5%, ETHFI 94.3%, JUP 91.6%, KAITO 84.2%. funding and OI report identical coverage per coin (one shared upstream feed, not independent). **Trades lag real time by 16.7–36 h** (observed twice). `total_records` on the coverage endpoint is unreliable (900–9,000× its own `sample_count`) and must not be used for budgeting | Free tier: data-payload calls restricted to ~the last 30 days (**inferred** from the tier description — not tested with a >30-day request). Build $49/mo (80M credits) covers a 50-market × 1-year Lighter funding+OI+trades backfill, estimated **480,000–510,000 credits** (~0.6% of quota) | verified | [P8](evidence/P8-oxarchive.md) |
| 0xArchive | HL funding / OI / trades | REST (`/v1/hyperliquid/...`) | Each market's real listing date: BTC 2023-05-20, JUP 2023-12-03, ONDO 2024-01-20, ETHFI 2024-03-21, ENA 2024-04-02, KAITO 2025-02-20, PONS 2026-08-31 | funding: median 61 s | funding: `coin, symbol, timestamp, funding_rate, premium` (HL rows **do** carry premium); trades returned immediately (no lag) | `funding_rate` is already a fraction, identical to HL's own live `funding` field (median ratio 1.0, n=466) | **100% complete with zero gaps on every data type and every one of the 7 sample coins** | as above (free tier ~30 days; Build $49/mo for history) | verified | [P8](evidence/P8-oxarchive.md) |

---

## 2. Funding semantics

### Hyperliquid

| Property | Value | Status | Evidence |
|---|---|---|---|
| Settled value | `fundingHistory.fundingRate` — one row per coin per hour, final | verified | [P4](evidence/P4-hl-api-crosscheck.md) |
| Current/running value | `funding` on `metaAndAssetCtxs` (live) and in the `asset_ctxs` archive (per minute). It is a **running estimate of the settlement that will close the current hour**, not the last settled rate: it changes within the hour on 57–87% of coin-hours (archive) and 76.2% (live), and its mean signed error against the *closing* settlement (9.3e-08 live) is ~70× smaller than against the *opening* one (6.6e-06) | verified | [P1](evidence/P1-hl-asset-ctxs.md), [P4](evidence/P4-hl-api-crosscheck.md), [P9](evidence/P9-live-probe.md) |
| Settlement timestamp | `fundingHistory` row `time` sits a few ms past an hour boundary; the row stamped `T` closes the interval `[T−1h, T)` | inference (from P4's alignment comparison, not from docs) | [P4](evidence/P4-hl-api-crosscheck.md) |
| Units | Plain dimensionless **fraction, per hour**. Interest-rate baseline `0.0001 / 8 = 0.0000125` per hour, which is exactly the value observed in both the archive and the API | verified (docs + data agree) | [P4](evidence/P4-hl-api-crosscheck.md) |
| Sign convention | Positive = longs pay shorts. Checked on data: `sign(fundingRate − 0.0000125) == sign(premium)` on **245/245** off-baseline coin-hours across 3 dates | verified | [P4](evidence/P4-hl-api-crosscheck.md) |
| Interval | 1 hour (docs, `predictedFundings.fundingIntervalHours = 1`, and the observed hourly cadence) | verified | [P4](evidence/P4-hl-api-crosscheck.md) |
| Reported precision | Up to 10 decimal places → tolerance `1e-10` | verified | [P4](evidence/P4-hl-api-crosscheck.md) |
| Formula | `F_8h = P + clamp(0.0001 − P, −0.0005, +0.0005)`; `F_hourly = clamp(F_8h / 8, −0.04, +0.04)`, with `P` = the hour's average premium as reported by `fundingHistory.premium`. **Confirmed**: reproduces 245/245 off-baseline and 494/494 total settled hours within `1e-10`, mean signed error ≈ −2e-12 | verified (the ±4%/h cap is docs-only — never exercised; largest rate seen 0.0018) | [P7](evidence/P7-funding-formulas.md) |
| Raw inputs | `premium = impact_price_difference / oracle_px`, sampled every **5 s** and averaged over the hour (docs). The 5-second samples are **not published anywhere**; the finest public premium is the archive's 1-minute snapshot | verified (that the samples are unpublished); formula for premium itself is docs-only | [P7](evidence/P7-funding-formulas.md) |
| Intra-hour state observable **live**? | Yes — `funding` and `premium` on `metaAndAssetCtxs`, at whatever cadence you poll | verified | [P9](evidence/P9-live-probe.md) |
| Intra-hour state observable **historically**? | Yes in form, no in accuracy. The archive holds a per-minute running `funding` back to 2023-05-20, and 0xArchive mirrors it at ~61 s from each market's listing — but that value **never equals** the closing settlement off-baseline: 0/64, 0/108, 0/27 archived coin-hours by date; 1/16 informative hours live (that one match is a baseline hour; no off-baseline hour ever matched); 1/5 informative hours via 0xArchive (0xArchive figures are from 2 coins, ENA and BTC, 12 market-hours total — see P8; "informative" = hours where the settled rate actually changed from the prior hour, 5 of the 12). Residual is 1e-7 to 1e-6, i.e. 2,000×–10,700× the reported tolerance. Note also: unlike P10's rebuild rule (`min_off_baseline=100`), the spec's Outcome A rule sets no minimum sample size — these 0xArchive figures (n=12, n=5) stand as-is | verified | [P4](evidence/P4-hl-api-crosscheck.md), [P9](evidence/P9-live-probe.md), [P8](evidence/P8-oxarchive.md) |
| Exact rebuild from archived inputs? | No — **not attemptable by construction**: the formula averages premium every 5 s, the archive holds one sample per minute. Attempted anyway for completeness: 0/264 off-baseline coin-hours within tolerance, absolute errors 1e-5–2e-5 | verified | [P10](evidence/P10-rebuild.md) |

### Lighter

| Property | Value | Status | Evidence |
|---|---|---|---|
| Settled value | `/api/v1/fundings` `rate` + `direction` — one row per market per hour, final | verified | [P5](evidence/P5-lighter-fundings.md) |
| Current/running value | `current_funding_rate` on the `market_stats` websocket. It is a running estimate of the **currently-accruing** interval, and its last in-hour value **exactly equals** that interval's settlement: 21/21 profiled hours, and 11/11 on the informative subset (hours where the rate actually changed) | verified | [P9](evidence/P9-live-probe.md) |
| Last-completed value | `funding_rate` + `funding_timestamp` on the same websocket = the settlement that *opened* the current hour, one interval behind `current_funding_rate` (21/21) | verified | [P9](evidence/P9-live-probe.md) |
| Settlement timestamp | `timestamp` = Unix seconds on the hour; the row stamped `T` reports `[T−1h, T)`. Checked directly against the live API | verified | [P5](evidence/P5-lighter-fundings.md), [P9](evidence/P9-live-probe.md) |
| Units | **Percent, per hour**, reported truncated (not rounded) to 4 decimal places. Baseline `0.0100 / 8 = 0.00125` is reported as `0.0012`, which is truncation, and the truncation was confirmed again by the formula check (only `trunc4` reproduces all 16 off-baseline hours exactly) | verified | [P5](evidence/P5-lighter-fundings.md), [P7](evidence/P7-funding-formulas.md) |
| Sign convention | `rate` is an **unsigned magnitude**; the sign lives in `direction`. `long` → longs pay shorts (positive), `short` → shorts pay longs (negative) — same sense as HL. Verified against market 212 (`CAP`) at a genuinely negative rate: `/fundings` `{rate: "0.0478", direction: "short"}` vs the websocket's signed `funding_rate: "-0.0478"` at the identical `funding_timestamp` | verified | [P9](evidence/P9-live-probe.md), [P5](evidence/P5-lighter-fundings.md) |
| Interval | 1 hour, per-market configurable; all sampled markets are 1 h | verified (docs + data) | [P5](evidence/P5-lighter-fundings.md), [P7](evidence/P7-funding-formulas.md) |
| Reported precision | 4 decimal places of percent → tolerance `1e-4` percent (= `1e-6` as a fraction) | verified | [P5](evidence/P5-lighter-fundings.md) |
| Formula | In percent, with `P` = the hour's **final running-average** premium: `smallClamped = P + clamp(0.01 − P, −0.05, +0.05)`; `rate_pct = trunc4( clamp(smallClamped, −4, +4) / 8 )`; then signed by `direction`. **Confirmed**: 16/16 off-baseline hours and 28/28 total reproduced **exactly to the reported 4-dp string**, mean signed error +2.4e-19. Effective `FundingPremiumMultiplier` = 1.0 (the API's integer `100` means hundredths); the three rival multiplier placements all score 0/16 | verified, **with a sample-size caveat: 28 market-hours (16 off-baseline) from a single 3h52m window on 2026-09-19, 7 markets, all settling positive.** That is the entire premium history that exists for Lighter. The 4% big clamp was never approached (largest value ~28× below it) and whether the multiplier also scales the small clamp is untestable on crypto markets | [P7](evidence/P7-funding-formulas.md) |
| Raw inputs | `premium_t` sampled once per minute at a random instant, time-weighted-averaged over the hour (docs). The individual `premium_t` samples are **never published**; only their running mean, on the `market_stats` websocket, which resets to `0.0000` at each hour boundary and whose minute-to-minute change decays as ~1/k — the signature of a cumulative mean | verified (that it is a running mean); the per-minute sampling rule is docs-only | [P7](evidence/P7-funding-formulas.md) |
| Intra-hour state observable **live**? | Yes, and richly — `premium`, `current_funding_rate` and every clamp parameter are on one unauthenticated websocket channel. `current_funding_rate` is that same formula applied to that same running premium: 100% exact from 15 min into the hour onward (1,211/1,211), 91.4% exact across all minutes | verified | [P7](evidence/P7-funding-formulas.md), [P9](evidence/P9-live-probe.md) |
| Intra-hour state observable **historically**? | **No — nowhere.** `/api/v1/fundings` rejects `1m`/`5m`/`15m` with HTTP 400. 0xArchive's Lighter `funding_rate` never moves within an hour (0/12 market-hours) — it is a ~10 s-cadence mirror of the *settled* series and carries no `premium` field. Lighter publishes no premium and no index-price history | verified | [P5](evidence/P5-lighter-fundings.md), [P8](evidence/P8-oxarchive.md), [P6](evidence/P6-lighter-market-state.md) |
| Exact rebuild from archived inputs? | No — **not attemptable, more absolutely than HL's case**: HL's input exists but is too coarse; Lighter's input does not exist at all. Confirmed live: `/v1/lighter/funding/ENA` returns keys `[coin, funding_rate, symbol, timestamp]`, no premium | verified | [P10](evidence/P10-rebuild.md) |

### Putting both venues on one basis

Common basis: **per-hour signed fraction, positive = longs pay shorts.**

| | Hyperliquid | Lighter |
|---|---|---|
| Native settled unit | fraction, per hour, already signed | percent, per hour, unsigned magnitude + `direction` |
| Conversion | use as-is | `F = (rate / 100)` if `direction == "long"` else `−(rate / 100)` |
| Interval | 1 h | 1 h |
| Settlement stamp | row `T` covers `[T−1h, T)` (inference) | row `T` covers `[T−1h, T)` (verified) |
| Reported precision on the common basis | `1e-10` | `1e-6` (4 dp of percent) — **Lighter is the binding precision when the two are differenced** |
| Payment convention | `position_size × oracle_price × funding_rate` | `−position × index_price × fundingRate` |

Both settlement stamps carry the same meaning, so hours pair one-to-one with no offset, and the
spread `F_HL − F_Lighter` is well defined at hourly resolution once Lighter is divided by 100 and
signed. Task 12's stop condition ("HL and Lighter cannot be put on the same basis") **does not
apply** — this is unit and sign conversion, not a structural mismatch
([P5](evidence/P5-lighter-fundings.md)).

---

## 3. Sample-file evidence

All five required artefacts exist, each behind a real download or response saved under
`data/phase1/` (gitignored):

- **HL `asset_ctxs` file** — `asset_ctxs/20250221.csv.lz4`, `20251205.csv.lz4`, `20260918.csv.lz4`
  (267,840 / 318,240 / 139,464 rows), plus `20260915`–`20260917` for the rebuild test:
  [P1](evidence/P1-hl-asset-ctxs.md), [P10](evidence/P10-rebuild.md)
- **HL L2 file** — `market_data/20260918/{0,1}/l2Book/[coin].lz4`, 14 coin-hours, 669–670 records
  each, first-record excerpt quoted: [P2](evidence/P2-hl-l2.md)
- **Lighter historical funding response** — full `1h` history paged for all 7 markets
  (402–14,644 rows each), plus the `1d`, `count_back`, row-cap and sub-hour probes:
  [P5](evidence/P5-lighter-fundings.md)
- **Lighter market-state response** — `data/phase1/p06/orderbookdetails.json` (ENA, market 29)
  with mark/index price, OI, daily stats and the live funding parameters, alongside saved
  request/response pairs for 13 endpoints: [P6](evidence/P6-lighter-market-state.md)
- **0xArchive coverage for the sample markets** — 14 coverage files, 7 coins × both venues, with
  earliest date, cadence, completeness and gap counts: [P8](evidence/P8-oxarchive.md)

Supporting artefacts not on the required list: HL node-fill samples
([P3](evidence/P3-hl-node-fills.md)), the 6,777-record live recording `data/phase1/p09/live.jsonl`
([P9](evidence/P9-live-probe.md)), the committed formula fixtures
`tests/fixtures/hl_formula_cases.json` and `tests/fixtures/lighter_formula_cases.json`
([P7](evidence/P7-funding-formulas.md)), and the two rebuild verdicts
`data/phase1/p10/verdict_{hl,lighter}.json` ([P10](evidence/P10-rebuild.md)).

---

## 4. Final source hierarchy

"Must be recorded going forward?" = whether the Phase 2 prospective recorder is the only way to get
the field, or is needed to cover a gap.

| Feature | Primary source | Why | Fallback-only | Must be recorded going forward? |
|---|---|---|---|---|
| Funding (settled) | **HL**: info `fundingHistory` ([P4](evidence/P4-hl-api-crosscheck.md)). **Lighter**: `/api/v1/fundings` `1h` ([P5](evidence/P5-lighter-fundings.md)) | Venue-native, free, final values, back to each market's listing; Lighter gap-free across all 7 markets (0 gaps >1 h), HL's mirror 100% complete | 0xArchive `/v1/{venue}/funding` — an independent cross-check, and a Lighter cross-check only from 2025-08-25 at 84–97% completeness ([P8](evidence/P8-oxarchive.md)) | No. Recommended anyway as a cheap live cross-check, but nothing is lost by not recording |
| Current/running funding | **HL**: `asset_ctxs` archive `funding` per minute, 2023-05-20→ ([P1](evidence/P1-hl-asset-ctxs.md)). **Lighter**: `market_stats` websocket `current_funding_rate` — **live only, no history anywhere** ([P9](evidence/P9-live-probe.md), [P8](evidence/P8-oxarchive.md)) | HL's is the only per-minute historical running value that exists; Lighter's is the only running value at all, and it is exact against settlement | HL: 0xArchive HL funding (~61 s) for days where the archive file is incomplete; live `metaAndAssetCtxs` for the last 1–2 days the archive has not uploaded | **Yes, both venues.** Lighter's is the whole of Target C's input and exists only prospectively; HL's history exists but is an approximation, so the recorder is also the only route to a high-cadence HL series |
| Premium | **HL**: `fundingHistory.premium` (hour average, settled rows) for the formula; `asset_ctxs.premium` per minute for intra-hour ([P4](evidence/P4-hl-api-crosscheck.md), [P1](evidence/P1-hl-asset-ctxs.md)). **Lighter**: `market_stats` websocket `premium` (the running hourly average) — **live only** ([P7](evidence/P7-funding-formulas.md)) | HL's hourly premium reproduces settled funding exactly (245/245); Lighter's running premium reproduces settled funding exactly (16/16) | HL's 5-second samples: none exist publicly. Lighter: 0xArchive's WS replay reportedly carries a `premium` for Lighter (observed once, **untested**) — the REST route does not | **Yes, Lighter.** No historical Lighter premium exists anywhere; HL's per-minute premium is historical but too coarse to rebuild from |
| Open interest | **HL**: `asset_ctxs.open_interest`, per minute from 2023-05-20 ([P1](evidence/P1-hl-asset-ctxs.md)). **Lighter**: 0xArchive `/v1/lighter/openinterest` (~10 s, from 2025-08-25) — **no native Lighter history exists** ([P6](evidence/P6-lighter-market-state.md), [P8](evidence/P8-oxarchive.md)) | HL's is native and complete; Lighter has none of its own — 13 endpoints checked, `open_interest` exists only as a current value | HL: 0xArchive HL openinterest. Lighter: live `orderBookDetails.open_interest` / websocket `open_interest` | **Yes, Lighter** (and 0xArchive Build tier if Lighter OI history before the recorder's start is wanted). No for HL |
| Perp price | **HL**: `asset_ctxs` `mark_px`/`mid_px`/`prev_day_px` per minute ([P1](evidence/P1-hl-asset-ctxs.md)). **Lighter**: `/api/v1/candles` and `/api/v1/markPriceCandles` (≥1 y at 1 h, ≥180 d at 1 m) ([P6](evidence/P6-lighter-market-state.md)) | Both native and historical at usable resolution | HL `candleSnapshot` (only ~3.5 days of 1 m, so fallback in name only); 0xArchive openinterest rows carry `mark_price` | No |
| Spot/index price | **HL**: `asset_ctxs.oracle_px` per minute, 2023-05-20→ ([P1](evidence/P1-hl-asset-ctxs.md)). **Lighter**: 0xArchive `/v1/lighter/openinterest` rows, which carry `index_price` and `oracle_price` from 2025-08-25 ([P8](evidence/P8-oxarchive.md)) — **no native Lighter index history** ([P6](evidence/P6-lighter-market-state.md)) | HL's oracle price is the venue's own funding input; Lighter exposes `index_price` live only, so the vendor is the only historical route | Lighter: derive from `markPriceCandles` minus a proxy — not validated, and no index-price-history endpoint was found. Live `orderBookDetails.index_price` / websocket `index_price` | **Yes, Lighter**, unless the 0xArchive index series is validated first. HL: no |
| Volume | **HL**: `asset_ctxs.day_ntl_vlm` per minute (running daily notional) ([P1](evidence/P1-hl-asset-ctxs.md)). **Lighter**: `/api/v1/candles` `v` (base) and `V` (quote) ([P6](evidence/P6-lighter-market-state.md)) | Both native and historical | HL live `metaAndAssetCtxs.day_ntl_vlm`; Lighter live `daily_base_token_volume`/`daily_quote_token_volume` on the websocket and `exchangeStats` | No |
| Signed trade flow | **HL**: `node_fills_by_block` (2025-07-27→) continued back by `node_fills` (2025-05-25→2025-07-27); `side` + `crossed` + `dir` give the aggressor ([P3](evidence/P3-hl-node-fills.md)). **Lighter**: **no usable historical source** — live `trade:<market_id>` websocket only ([P9](evidence/P9-live-probe.md)) | HL's is continuous, whole-network and cheap enough (~$27/yr); Lighter's `/api/v1/trades` needs auth and 0xArchive's Lighter fills run 16.7–36 h behind | HL: live `trades` websocket; 0xArchive HL trades (immediate). Lighter: 0xArchive `/v1/lighter/trades` — usable for research at a lag, not for anything live; `node_trades` is unusable (empty) | **Yes, Lighter.** No for HL before 2025-05-25 either — nothing exists there at all |
| Market metadata / listings | **HL**: `meta`/`metaAndAssetCtxs` (live, `is_delisted`), plus per-date coin presence in `asset_ctxs` as a point-in-time proxy ([P1](evidence/P1-hl-asset-ctxs.md), [P9](evidence/P9-live-probe.md)). **Lighter**: `/api/v1/orderBooks` `created_at` + `status` ([P6](evidence/P6-lighter-market-state.md)) | Listing dates are exact on both venues; HL listing dates are also recoverable free from the first `1d` candle | HL: first `1d` `candleSnapshot` candle per coin (used by P1 to pick dates). Lighter: last trade/candle before a market went `inactive`, as a delisting proxy — not validated | **Yes, both** — snapshot the universe on every recorder cycle. Lighter has **no delisting timestamp** at all, so only a running snapshot can date a delisting |
| Settlement timestamps | **HL**: `fundingHistory.time` (and `predictedFundings.nextFundingTime` live). **Lighter**: `/api/v1/fundings.timestamp` (and `funding_timestamp` live) | Hourly, on the hour, on both venues, known for every historical hour — so "time since / until settlement" is computable historically on both | HL: the hour grid itself (settlement is unconditionally hourly) | No |

**Prospective-only list** (nothing historical exists; the Phase 2 recorder is the only source):
Lighter running funding (`current_funding_rate`), Lighter premium, Lighter index price (unless
0xArchive's is validated), Lighter open interest before 2025-08-25 (and before today without a paid
tier), Lighter signed trade flow, Lighter delisting timestamps, and an exact-to-settlement HL
running funding series (HL's archived one exists but is an approximation).

---

## 5. Target C decision

Rule applied in order, per the spec: **A** if an archived source holds intra-hour current funding
that moves within the hour like P9's live value *and* whose last in-hour value equals the closing
settlement on ≥99% of market-hours; else **C** if P10's verdict is `pass`; else **B**.

| Venue | Outcome | Rule applied | Evidence |
|---|---|---|---|
| Hyperliquid | **Prospective only (B)** | **Step 1 (A) — fails on the second condition.** An archived intra-hour source does exist and does move within the hour like the live value: `asset_ctxs.funding`, per minute from 2023-05-20, changes within 57–87% of coin-hours (archive) against 76.2% live. But its last in-hour value equals the closing settlement on **0%** of off-baseline coin-hours at the venue's own `1e-10` precision — 0/64, 0/108, 0/27 by date; all-hours 159/358 = 44.4%, entirely from trivial baseline hours. Confirmed live at the same cadence (1/16 informative hours; residual 1e-7–1e-6, i.e. 2,000×–10,700× tolerance) and via 0xArchive's 61 s mirror (7/12 all hours, 1/5 informative — 0xArchive figures are from 2 coins, ENA and BTC, 12 market-hours total; the spec's Outcome A rule, unlike P10's `min_off_baseline=100`, sets no minimum n). Far below 99%. **Step 2 (C) — fails.** P10 verdict `not_attemptable` (`raw_verdict: "fail"`): the formula averages premium every 5 s, the archive holds 1 sample per minute, so exact rebuild is impossible by construction; the coarse attempt scored 0/264 off-baseline. **Step 3 ⇒ B.** | [P1](evidence/P1-hl-asset-ctxs.md), [P4](evidence/P4-hl-api-crosscheck.md), [P7](evidence/P7-funding-formulas.md), [P8](evidence/P8-oxarchive.md), [P9](evidence/P9-live-probe.md), [P10](evidence/P10-rebuild.md) |
| Lighter | **Prospective only (B)** | **Step 1 (A) — fails on the first condition: there is no archived source at all.** The live `current_funding_rate` would satisfy the equality test outright — it is a genuine running estimate of the accruing interval and its last in-hour value equals that interval's settlement on **21/21** profiled hours and **11/11** informative hours, exactly, not within tolerance. But Outcome A requires an *archived* source with that property and none exists: `/api/v1/fundings` rejects sub-hourly resolutions (HTTP 400), and 0xArchive's Lighter `funding_rate` never moves within an hour (0/12 market-hours) because it is a ~10 s-cadence mirror of the **settled** series, matching 0/5 informative hours. **Step 2 (C) — fails.** P10 verdict `not_attemptable` with no rebuild even run: no historical premium input exists on Lighter's API ([P6](evidence/P6-lighter-market-state.md)), on 0xArchive's REST route (no `premium` field — re-confirmed live, keys `[coin, funding_rate, symbol, timestamp]`), or anywhere else; the only Lighter premium data in existence is the 3h52m P9 recording. **Step 3 ⇒ B.** | [P5](evidence/P5-lighter-fundings.md), [P6](evidence/P6-lighter-market-state.md), [P7](evidence/P7-funding-formulas.md), [P8](evidence/P8-oxarchive.md), [P9](evidence/P9-live-probe.md), [P10](evidence/P10-rebuild.md) |

Spread version historical over: **none.** Both venues are Outcome B, so no period qualifies and
the cross-venue Target C spread is prospective-only in its entirety.

**The two failures are not the same failure**, and the distinction should survive into Phase 11.
Hyperliquid has a running value archived back to 2023 that is *close* to the settlement (1e-7–1e-6)
but never equal; if a later phase is willing to define Target C against an approximate published
value rather than an exact one, HL has 3+ years of history to work with — that would be a
redefinition of the target, not a reversal of this decision. Lighter has nothing archived at any
accuracy, and its running value, once recorded, is exact.

---

## 6. Known gaps

- **No analysed archive date exercises 2023–2024.** The `old` date used throughout (P4, P7, P10) is
  2025-02-21 — chosen because it is the first day all six non-PONS sample coins exist — so schema
  stability before 2025 rests on nothing but the first probe run's initial pass over
  `archive_start` (2023-05-20). No funding/premium/formula check in this audit exercises a
  2023–2024 date.
- **PONS contributes nothing to the old/mid dates or P10's old window.** PONS was listed
  2026-08-31/2026-09-02 (well after `old` 2025-02-21 and `mid` 2025-12-05), so P10's old-window
  rebuild test ran on 4 mid-cap sample coins, not 5 — PONS is absent from that window entirely, not
  merely thin.
- **`data/phase1/p09/live.jsonl` is the only Lighter premium history that exists.** It is
  gitignored and lives on one machine. If it is lost, Lighter's formula confirmation (P7) cannot be
  re-derived — 2026-09-19 cannot be re-recorded, since the premium field is not published anywhere
  else, live or archived. **Recommend backing it up outside git** (e.g. to cloud storage or a second
  disk) before any further work on this branch.
- **HL listing dates differ between sources and neither is a confirmed listing date.** P1's
  candle-derived dates (e.g. ETHFI 2024-03-18, BTC 2020-08-19) are unreliable — the BTC date
  predates Hyperliquid's existence, so it cannot be a real listing date and is more likely an
  artifact of how P1 derived it from the first available `1d` candle. The audit's own
  0xArchive-derived dates (ETHFI 2024-03-21, BTC 2023-05-20) are a vendor ingest start date, not a
  listing date — 0xArchive's own HL coverage note (P8) says HL history "goes back to each market's
  real listing date," but 2023-05-20 recurring as BTC's earliest date across every HL data type is
  also exactly 0xArchive's own archive-start artifact, so it is not independently confirmed either.
  **HL's `fundingHistory` start was never queried directly** for any sample coin — doing so (a free
  call) would settle this but was out of scope for this pass.

---

## Done check

| Item | Result |
|---|---|
| Every matrix row has a status and an evidence link | ✅ 18 rows, each with one of verified / unverified / unavailable and at least one evidence link; no blank cells (unknowns are written "not checked") |
| Sample-file evidence exists — HL `asset_ctxs` file | ✅ 6 real daily files downloaded ([P1](evidence/P1-hl-asset-ctxs.md), [P10](evidence/P10-rebuild.md)) |
| Sample-file evidence exists — HL L2 file | ✅ 14 coin-hour files, excerpt quoted ([P2](evidence/P2-hl-l2.md)) |
| Sample-file evidence exists — Lighter historical funding response | ✅ full `1h` history for all 7 markets + targeted calls ([P5](evidence/P5-lighter-fundings.md)) |
| Sample-file evidence exists — Lighter market-state response | ✅ `data/phase1/p06/orderbookdetails.json` + 13 endpoints classified ([P6](evidence/P6-lighter-market-state.md)) |
| Sample-file evidence exists — 0xArchive coverage for the sample markets | ✅ 14 coverage files, 7 coins × 2 venues ([P8](evidence/P8-oxarchive.md)) |
| Funding semantics written for both venues, incl. cross-venue normalisation | ✅ Section 2, both venues plus the common-basis table |
| Source hierarchy complete (source per feature, fallbacks, prospective-only list) | ✅ Section 4, all 10 features, fallbacks named, prospective-only list given |
| Target C decided per venue | ✅ Section 5 — Hyperliquid **B**, Lighter **B**, spread historical over **none** |
| Decision map filled with actual findings | ✅ [`decision_map.md`](decision_map.md), 19 rows |
| Total AWS spend recorded | ✅ **$0.01184** across 89 billed S3 operations (33 list, 28 head, 28 get), from `data/phase1/aws_ledger.jsonl`. Cap was $0.80; never approached. **Correction (2026-09-21):** the ledger priced egress at $0.09/GB, the US rate, while both buckets are in `ap-northeast-1` at **$0.114/GB** — so Phase 1's true list-price spend is **≈$0.0149** (0.1267 GB + 89 requests), not $0.01184. The constant is fixed in `src/fundr/sources/hl_archive.py`; the evidence notes record what was measured at the time and are left as they are |
| 0xArchive credits used recorded | ✅ **32 credits** — the highest `x-credits-used` header observed across P8's 27 logged calls, against a 200,000/month quota (the account's limit changed from 50,000 to 200,000 mid-probe, vendor-side, unexplained). P10 later made 1 more call and read the counter at 30; the vendor's counter is not monotonic between reads, so treat 32–33 as the total. No paid tier was used ([P8](evidence/P8-oxarchive.md), [P10](evidence/P10-rebuild.md)) |
| Full test suite | ✅ `uv run pytest -q` → **34 passed in 0.57s** (2026-09-20) |

### Known gaps carried out of Phase 1

These are real and should be read alongside the conclusions above:

- **HL archive recent days are unreliable.** 2026-09-15 held 156/1440 rows and 09-16 held 505/1440,
  neither of which was the newest file at download time; the cause is unexplained and unverified
  against any HL-side status source ([P1](evidence/P1-hl-asset-ctxs.md),
  [P10](evidence/P10-rebuild.md)).
- **No native historical open interest on Lighter** — 13 endpoints checked
  ([P6](evidence/P6-lighter-market-state.md)).
- **Lighter trade history needs auth**, and the vendor substitute lags 16.7–36 h
  ([P6](evidence/P6-lighter-market-state.md), [P8](evidence/P8-oxarchive.md)).
- **No Lighter delisting timestamps** — 22 of 246 markets are `inactive` with no date field
  ([P6](evidence/P6-lighter-market-state.md)).
- **0xArchive's free tier reaches back ~30 days**; the Build tier (~$49/mo) is needed for history,
  at an estimated 480,000–510,000 credits for 50 Lighter markets × 1 year of funding + OI + trades.
  Whether Build itself (rather than a higher add-on) unlocks calls older than 30 days is
  **inferred, not tested** ([P8](evidence/P8-oxarchive.md)).
- **Lighter's confirmed formula rests on 16 off-baseline hours** in one 3h52m window on one day,
  all settling positive; the 4% clamp and any market with a multiplier ≠ 1 are untested
  ([P7](evidence/P7-funding-formulas.md)).
- **HL's settlement-stamp convention is an inference**, not a documented fact
  ([P4](evidence/P4-hl-api-crosscheck.md)).
- **HL's exact `fundingHistory` coverage start was never queried** directly; it is taken from
  0xArchive's mirror of the same series ([P8](evidence/P8-oxarchive.md)).
- **Continuity inside the HL node-fills date ranges was not scanned**, only first/last leaf
  ([P3](evidence/P3-hl-node-fills.md)).
- **HL `k*` vs Lighter `1000*` symbols do not join**, so those markets are silently absent from any
  both-venues universe until alias logic is added ([P0](evidence/P0-sample.md)).
