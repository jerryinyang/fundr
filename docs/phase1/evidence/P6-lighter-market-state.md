# P6 — Lighter market state

Status: verified

## What ran
- Command: `FUNDR_DATA=/Users/jerryinyang/Trading/fundr/data/phase1 uv run python probes/p06_lighter_market_state.py`
- Run date (UTC): 2026-09-19
- Markets: BTC (market_id 1, control), ENA (market_id 29, sample "mid" role)
- Dates / windows sampled: 1 year ago (1h candles), 180 days ago (1m candles), 90 days ago (1h candles, mid-cap), "past timestamp" = now − 1 day for `/funding-rates`

No auth header or API key was used for any call (all endpoints below are public).

## Endpoint classification

| Endpoint | Historical or live-only | Fields | Resolution | Earliest data seen |
|---|---|---|---|---|
| `/api/v1/candles` (price & volume) | **Historical** | `t,o,h,l,c,v` (base volume), `V` (quote volume), `i` (last trade id) — no OI field | 1m, 1h, 1d, etc. | Returned rows 1 year back (1h) and 180 days back (1m) without truncation or error |
| `/api/v1/markPriceCandles` (mark price) | **Historical** | `t,o,h,l,c,sc` (sample count) | same resolutions as candles | Returned rows 1 year back (1h) |
| `/api/v1/fundings` (funding rate candles) | **Historical** | `timestamp,value,rate,direction` | 1h (and others, per P5) | Returned rows 1 year back; P5 already walked this endpoint from each market's listing date to now with no gaps |
| `/api/v1/marketPriceCharts` | Live-only (fixed window) | `market_id, prices[]` | Fixed last-24h hourly, no time params accepted | n/a — only returns the trailing 24h |
| `/api/v1/orderBookDetails` | Live snapshot | mark_price, index_price, open_interest, daily_* stats, funding params — all point-in-time | n/a | n/a — single current value per field |
| `/api/v1/orderBooks` | Live snapshot (listing metadata) | `status`, `created_at`, fee/margin config | n/a | n/a |
| `/api/v1/exchangeStats` | Live snapshot | per-market `last_trade_price`, `daily_*` | n/a | n/a |
| `/api/v1/funding-rates` | **Live-only** | `market_id, exchange, symbol, rate` (external-exchange reference rates, not Lighter's own) | n/a | Same 741 rows returned with and without `timestamp` param — see below |
| `/api/v1/recentTrades` | Live snapshot (recent only) | trade_id, size, price, usd_amount, ask/bid ids | n/a | last ~10 trades only |
| `/api/v1/trades` | Blocked — auth required | — | — | HTTP 400: `"auth query param and Authorization header are empty"` (not usable unauthenticated) |
| `/api/v1/assetDetails` | Live snapshot (listing/config metadata) | asset_id, symbol, decimals, index_price, margin params | n/a | n/a |
| `/api/v1/exchangeMetrics` | Not classified | — | — | HTTP 400 `"invalid param"` — guessed `period="1d", kind="volume"` were wrong; no documented enum values found in the SDK docs to correct this without further guessing |
| `/api/v1/executeStats` | Not classified | — | — | HTTP 400 `"invalid param"` — guessed `period="1d"`; same issue as above |

Raw request/response for every row above is saved at `data/phase1/p06/<slug>.json`.

### Native historical OI
**None found among 13 endpoints checked.** `candles` (price/volume) and `markPriceCandles` (mark price) both return time-series history; neither includes an open-interest field. `orderBookDetails` and `exchangeStats` expose `open_interest` only as a single current value, with no equivalent historical/candle endpoint for it.

### Historical price and volume
Confirmed via `/api/v1/candles`: returns base volume (`v`) and quote volume (`V`) alongside OHLC, at 1h resolution 1 year back and 1m resolution 180 days back, with no truncation or error. This matches the earlier scratch dry run.

### Premium history
None found natively as a "premium" series. `/api/v1/markPriceCandles` gives historical **mark price**; `/api/v1/candles` gives historical **trade price**. A premium series (mark − index, or similar) would have to be derived by combining `markPriceCandles` with a historical index-price source — no such index-price-history endpoint was found among the endpoints checked.

### `/api/v1/funding-rates` — live-only, confirmed
The call with `timestamp=now-86400` (1 day in the past) returned a body **byte-for-byte identical** to the call with no `timestamp` param (741 rows, same values) — verified by direct comparison of the two saved JSON bodies. The `timestamp` param is accepted but ignored; this endpoint only ever returns the current snapshot. Note also: these rows are third-party reference rates (`exchange: "binance"`, etc.), not Lighter's own funding rate — Lighter's own historical funding is under `/api/v1/fundings` (confirmed historical, see above; already probed in depth by P5).

### Listing / delisting metadata
`/api/v1/orderBooks` returns, per market: `status` (`active` / `inactive`) and `created_at` (listing time, ms epoch). Checked all 246 markets: 22 are `status: "inactive"`. **No delisting timestamp field exists** — inactive markets carry the same field set as active ones (`symbol, market_id, market_type, base_asset_id, quote_asset_id, status, taker_fee, is_taker_fee_enabled, maker_fee, is_maker_fee_enabled, liquidation_fee, min_base_amount, min_quote_amount, order_quote_limit, supported_size_decimals, supported_price_decimals, supported_quote_decimals, created_at, multiplier`) — just `status` flipped to `inactive`. There is no `delisted_at`, `deactivated_at`, `updated_at`, or similar. Delisting time is not exposed by this endpoint and would have to be inferred indirectly (e.g., last trade/candle timestamp before the market went inactive).

### Representative Lighter market-state response
Saved orderBookDetails response (ENA, market_id 29): `data/phase1/p06/orderbookdetails.json`. Excerpt:
```json
{
  "symbol": "ENA", "market_id": 29, "status": "active",
  "mark_price": "0.18973", "index_price": "0.18951",
  "open_interest": 19420016.4,
  "daily_trades_count": 9016, "daily_base_token_volume": 41700757.4,
  "daily_quote_token_volume": 7490582.424896,
  "funding_premium_multiplier": 100, "funding_clamp_small": "0.0500", "funding_clamp_big": "4.0000"
}
```

## Findings
- Historical price/volume (`candles`) and historical mark price (`markPriceCandles`) exist and go back at least 1 year at 1h resolution and 180 days at 1m resolution.
- Historical funding rate exists at `/api/v1/fundings` (distinct from `/api/v1/funding-rates`), confirmed by both this probe and P5's full walk from each market's listing date.
- No native historical OI series was found on any endpoint checked.
- No native premium history series; would require deriving from `markPriceCandles` minus an index-price history (which does not appear to exist).
- `/api/v1/funding-rates` is confirmed live-only: a past `timestamp` param is accepted but silently ignored (identical response returned).
- `/api/v1/orderBooks` exposes listing time (`created_at`) and a binary `status`, but no delisting timestamp.
- `/api/v1/trades` requires auth even for a public GET; unusable without an API key.
- `/api/v1/exchangeMetrics` and `/api/v1/executeStats` both take a `period` (and `exchangeMetrics` also a `kind`) param whose valid values are not documented in the SDK docs; both calls returned HTTP 400 with guessed values and were left unclassified rather than guessing further.

## Open issues
- `exchangeMetrics` and `executeStats` remain unclassified (400 on guessed params) — would need valid enum values (from source code or trial-and-error) to determine if either exposes a historical OI or volume series distinct from `candles`/`exchangeStats`.
- No index-price-history endpoint was found, so premium history cannot be fully ruled in or out — only that no endpoint directly returning "premium" exists.

## Step 1: endpoints added / skipped
Source: `curl -sL https://raw.githubusercontent.com/elliottech/lighter-python/main/README.md | grep -E "api/v1"`

**Added** (public GET, market data, not already in `CHECKS`):
- `/api/v1/fundings` — historical funding-rate candles (distinct from live-only `/funding-rates`)
- `/api/v1/markPriceCandles` — historical mark-price candles (premium-adjacent)
- `/api/v1/marketPriceCharts` — last-24h hourly price chart per market
- `/api/v1/exchangeMetrics` — exchange-wide stats (period/kind)
- `/api/v1/executeStats` — exchange-wide execution stats (period)
- `/api/v1/assetDetails` — asset-level listing/config metadata

**Skipped:**
- All `AccountApi`, `TransactionApi`, `BridgeApi`, `ReferralApi`, `BlockApi`, `NotificationApi` endpoints, and the account-scoped `OrderApi` endpoints (`accountActiveOrders`, `accountInactiveOrders`, `accountOrders`) — account/order/transaction/auth, out of scope.
- `/api/v1/export` — requires `authorization` param (auth-gated).
- `/api/v1/orderBookOrders` — live order-book depth snapshot; no time params, not a price/OI/volume/funding/trades series, redundant with `orderBookDetails`/`recentTrades` for "is this a snapshot" purposes.
- `/api/v1/tokenlist`, `/api/v1/syntheticSpotInfo`, `/api/v1/layer1BasicInfo`, `/api/v1/systemConfig`, `/api/v1/transferFeeInfo`, `/api/v1/withdrawalDelay`, `/api/v1/announcement`, `/api/v1/livePoints/total` — general exchange/config/bridge info, not perp market OI/price/volume/funding/trades/stats.
