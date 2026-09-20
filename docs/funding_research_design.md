# Cross-Venue Funding Dynamics in Mid- and Small-Cap Perpetuals

## Research Statement

Funding rates on mid- and small-cap perpetual futures may contain predictable short-horizon dynamics that are not fully captured by their current level. This project investigates whether observable market state can predict the **next change in funding**, with particular emphasis on the **cross-venue funding spread between Hyperliquid and Lighter**.

The research deliberately avoids treating a venue's mechanically calculated current funding rate as a conventional forecast. Instead, it asks whether information available at time $t$—including open interest, changes in open interest, perp–spot basis, recent returns, trading volume, trade flow, funding history, and time relative to settlement—contains information about subsequent funding.

The initial scope is narrow: perpetual markets outside the top 10 by open interest, where funding can be relatively large and persistent and where liquidity constraints may create stronger funding dynamics. Full L2 order-book data is excluded from the initial study and can be introduced later as an incremental feature set.

The project combines:

1. **Historical research:** use currently available historical funding and market data to test whether future funding changes and cross-venue spread changes are predictable.
2. **Historical-data probe:** verify exactly what the available archives preserve, especially whether intrahour funding state can be reconstructed.
3. **Prospective collection if required:** if historical archives do not preserve the intrahour published/current funding state, record it going forward.

The ultimate practical question is whether predictable funding dynamics can support a delta-neutral cross-venue strategy that remains viable after trading fees, slippage, funding, and margin costs.

---

## Testable Hypotheses

### H1 — Funding-rate continuation

The current funding regime contains information about the subsequent funding rate.

In particular, changes in recent funding, open interest, basis, returns, volume, and trade flow should explain some of the variation in the next funding rate beyond a naive historical baseline.

The strength and persistence of this relationship may differ systematically with market size and liquidity.

### H2 — Observable market state predicts funding changes

The change in funding between consecutive funding intervals is predictable from observable market state.

Candidate predictors include:

- Open interest level
- Change in open interest
- Perp–spot basis
- Recent returns
- Trading volume
- Buy/sell trade imbalance
- Recent funding rates
- Time since and time until funding settlement

The hypothesis is not that every feature is individually predictive, but that their joint information content improves out-of-sample forecasts relative to simple baselines.

### H3 — Cross-venue funding spread dynamics are more predictable

The subsequent change in the Hyperliquid–Lighter funding spread is more predictable than the corresponding funding change on either venue individually.

The intuition is that common market-wide funding movements may partially cancel when the two venues are compared, leaving venue-specific dislocations and relative positioning effects that may be easier to model.

### H4 — Predictability is economically meaningful

Any statistical predictability in funding changes or cross-venue spread changes survives realistic implementation costs, including:

- Trading fees
- Bid/ask spread
- Slippage
- Thin-book execution constraints
- Funding paid/received on both legs
- Margin and capital requirements
- Position limits

A signal that is statistically significant but economically untradeable does not constitute a successful trading result.

---

## Targets

The research uses three targets, each answering a slightly different question.

### Target A — Funding-Rate Continuation

For each venue and funding interval:

$$
Y_t^{venue} = F_{t+1}^{venue} - F_t^{venue}
$$

where $F_t$ is the funding rate associated with the current/completed interval and $F_{t+1}$ is the subsequent funding rate.

#### Research question

> Can the current market state predict how funding will change in the next interval?

This is the simplest historical target and provides the baseline for the rest of the research.

---

### Target B — Cross-Venue Funding Convergence

Define the cross-venue funding spread:

$$
S_t = F_t^{HL} - F_t^{Lighter}
$$

Then define the target as:

$$
Y_t^{spread} = S_{t+1} - S_t
$$

or equivalently:

$$
Y_t^{spread} = (F_{t+1}^{HL} - F_{t+1}^{Lighter}) - (F_t^{HL} - F_t^{Lighter})
$$

#### Research question

> Can the current state of the two markets predict the subsequent movement of their funding spread?

This is the primary historical target because it maps most directly to the intended delta-neutral cross-venue strategy.

---

### Target C — Prospective Published-Funding Adjustment

Once live data collection begins, record the published/current funding estimate throughout each funding interval.

For each timestamp $t$:

$$
Y_t^{adj} = F_{realized,t+1} - F_{published,t}
$$

where:

- $F_{published,t}$ is the funding rate displayed/currently available from the venue at time $t$
- $F_{realized,t+1}$ is the funding rate ultimately realized at settlement

#### Research question

> Given exactly what was observable at time $t$, can we predict how the eventual funding rate will differ from the currently published/current rate?

If historical intrahour snapshots are available, Target C can be reconstructed retrospectively. Otherwise it is a prospective target.

---

## Roadmap

### Phase 1 — Historical Data Availability Probei

**This phase blocks the rest of the research pipeline. Do not freeze the historical target design until the probe has been run.**

The goal is not merely to confirm that a historical funding endpoint exists. The goal is to establish the exact **information set and timestamp resolution** available at each venue.

#### 1. Hyperliquid — primary historical sources

Hyperliquid's official historical-data documentation identifies a public S3 archive:

- `s3://hyperliquid-archive/asset_ctxs/[date].csv.lz4`
- `s3://hyperliquid-archive/market_data/[date]/[hour]/l2Book/[coin].lz4`

The archive is requester-pays, uploaded approximately monthly, and the documentation explicitly warns that updates may be delayed and data may be missing.

Hyperliquid also documents historical node data:

- `s3://hl-mainnet-node-data/node_fills_by_block`
- older `node_fills`
- older `node_trades`

These are the preferred historical sources for signed/fill-level flow where their coverage is usable.

Official source:

- Hyperliquid Historical Data: https://hyperliquid.gitbook.io/hyperliquid-docs/historical-data

##### `asset_ctxs` — what must be verified

Community documentation has reported fields resembling:

- `time`
- `coin`
- `funding`
- `open_interest`
- `prev_day_px`
- `day_ntl_vlm`
- `premium`
- `oracle_px`
- `mark_px`
- `mid_px`
- `impact_bid_px`
- `impact_ask_px`

**These fields and their semantics are not to be treated as established until a real archive file is inspected.**

The probe must establish:

- Exact row frequency
- Exact schema and data types
- Timestamp semantics
- Whether `funding` changes within the funding interval
- Whether `funding` is a running/current value or only a settled value
- Whether `premium` is intrahour
- Intrahour OI availability
- Availability and frequency of impact bid/ask
- Missing-data patterns
- Whether row frequency is stable across dates and assets

This is the critical test for historical reconstruction of Target C.

##### Hyperliquid L2

Inspect at least one historical `market_data/.../l2Book/...lz4` file and record:

- Timestamp resolution
- Snapshot/delta structure
- Depth
- Symbol coverage
- File size / practical extraction cost
- Missingness

Full L2 remains out of the initial feature set. The purpose of this inspection is to establish availability and optional future use, not to expand the initial model.

##### Hyperliquid candles — important constraint

The official `candleSnapshot` API exposes only the most recent **5,000 candles per request/history window**. This means it is not a suitable source for building a long historical 1-minute/3-minute/5-minute price dataset by repeatedly requesting arbitrary old windows.

Approximate lookback represented by 5,000 candles:

- 1m: ~3.5 days
- 3m: ~10.4 days
- 5m: ~17.4 days
- 15m: ~52 days
- 1h: ~208 days

Therefore, for older historical prices/volume, prefer the S3 archive where available, a verified third-party historical source, or another archival source discovered during Phase 1.

Official API source:

- Hyperliquid Info API: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint

---

#### 2. Hyperliquid — API cross-checks

The official API exposes:

- `fundingHistory` for historical realized funding
- `predictedFundings` for current predicted funding across supported venues

The important distinction is that these endpoints do **not** by themselves prove that historical intrahour snapshots of the published/current funding estimate are available.

Official source:

- Hyperliquid Perpetuals API: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/perpetuals

---

#### 3. Lighter — official historical funding

The first-choice historical funding source should be Lighter's own public API:

`GET /api/v1/fundings`

The official Lighter Python SDK documents:

- `market_id`
- `resolution`
- `start_timestamp`
- `end_timestamp`
- `count_back`
- maximum **750 fundings per call**
- public/no authorization required

The documented historical resolutions are `1h` and `1d`.

This endpoint is the primary source for **settled historical funding**.

Official SDK/API reference:

- https://github.com/elliottech/lighter-python
- https://github.com/elliottech/lighter-python/blob/main/docs/CandlestickApi.md

##### Do not confuse `/fundings` with `/funding-rates`

`GET /api/v1/funding-rates` is a different surface. Current public documentation and SDK references show it as a cross-venue/current funding feed with rows identified by `market_id`, `exchange`, `symbol`, and `rate`.

For this research:

- `/api/v1/fundings` = historical funding series
- `/api/v1/funding-rates` = current funding/comparison feed

The latter should not be assumed to provide historical intrahour snapshots.

---

#### 4. Lighter — funding mechanics

The funding formula itself must be verified against the current implementation before being used to reconstruct any historical running funding state.

Candidate parameters reported in current third-party documentation include:

- `SmallClamp` = 0.05%
- `BigClamp` = 4%
- `InterestRate` = 0.01%
- 60 premium samples per hour
- a `FundingPremiumMultiplier` / premium scaling term

However, the exact formula and especially the placement of the premium multiplier/clamp operation must be checked against the current implementation. A probable documentation typo has been reported around the multiplication order.

**Do not encode these parameters into the research pipeline during Phase 1 merely because they appear in a secondary description.**

The probe should determine:

1. the authoritative current formula;
2. the exact meaning of each parameter;
3. whether the displayed/current funding is a running estimate;
4. what intermediate state is publicly observable before settlement;
5. whether the historical API preserves that intermediate state.

A useful independent integration reference is NautilusTrader, which distinguishes Lighter's live `current_funding_rate` (upcoming estimate) from the completed `funding_rate` and `funding_timestamp` fields.

Reference:

- https://nautilustrader.io/docs/nightly/integrations/lighter/

---

#### 5. Lighter — historical market-state alternatives

The official Lighter API should be preferred for venue-native fields such as:

- funding
- OI
- prices/market state
- other documented market statistics

For richer historical microstructure, **0xArchive is a secondary/fallback source**, not an assumption that the official API is insufficient.

0xArchive currently advertises:

- Hyperliquid and Lighter historical data
- REST queries
- WebSocket replay
- Parquet exports
- funding
- open interest
- trades
- order books
- Lighter L3 order-book history

Its current public pricing page shows:

- Free: 50k credits
- Build: \$49/month
- Pro: \$199/month
- Scale: \$799/month

Current source:

- https://0xarchive.io/
- https://docs.0xarchive.io/

The current SDK documentation indicates Lighter L3 history from approximately March 2026 onward and Lighter market data coverage that varies by dataset/market. Do **not** hard-code claims such as “all Lighter trades begin in August 2025” without checking the actual market/data catalog. Coverage should be recorded per dataset and instrument during the probe.

0xArchive should therefore be evaluated for:

- Lighter OI history
- Lighter trade/fill history
- Lighter L3 history
- replay capability
- timestamp granularity
- coverage by market
- gaps
- cost at the expected research scale

---

#### 6. The key Phase 1 decision

For **both venues**, answer:

> **Can we observe what the funding rate would have been at time $t$, before the funding interval had finished?**

There are three possible outcomes:

##### Outcome A — historical intrahour running funding exists

Target C can be constructed historically.

##### Outcome B — only settled funding exists historically

Targets A and B proceed historically; Target C becomes prospective.

##### Outcome C — the venue exposes enough raw intermediate inputs to reconstruct the running funding exactly

Target C may be historically reconstructable, but only after validating the reconstruction against known settled funding across a sample of markets and dates.

This third case is important. Reconstructability is not the same thing as simply having premium/OI/price data.

---

### Phase 2 — Historical Data Collection

Only after Phase 1 establishes the source hierarchy and timestamp semantics, build the historical dataset.

#### Core variables

Collect, where available:

- Funding rates
- Perpetual prices
- Spot/index prices
- Open interest
- Trading volume
- Historical trades or aggregated flow
- Premium
- Market metadata
- Funding timestamps

#### Source hierarchy

Prefer, in order:

1. **Venue-native official data**
2. **Official venue historical archive**
3. **Verified third-party archival source**
4. **Prospective recording for information that cannot be reconstructed historically**

Keep a source/provenance field for every dataset so later results can distinguish native observations from reconstructed or vendor-supplied data.

#### Prospective collection

If historical intrahour funding is unavailable, begin prospective collection at a fixed interval, ideally every 1–5 minutes:

- Current/published funding
- Premium
- Open interest
- Perp price
- Spot/index price
- Volume
- Trade flow
- Timestamp
- Time to settlement

The prospective recorder should be treated as a permanent research dataset.

---

### Phase 3 — Universe Construction

Define the research universe using market size/liquidity rather than selecting assets after observing their performance.

Primary universe:

> Perpetual markets outside the top 10 by open interest.

Record market-level characteristics so results can later be segmented by:

- Open interest
- Trading volume
- Funding magnitude
- Liquidity
- Venue availability

Avoid survivorship bias by defining the universe using information that would have been available at each historical date where possible.

---

### Phase 4 — Construct the Targets

Create:

1. **Target A:** next funding-rate change for each venue.
2. **Target B:** next change in the Hyperliquid–Lighter funding spread.
3. **Target C:** realized-minus-published/current funding adjustment.

The implementation of Target C depends on Phase 1:

- **Historical intrahour estimate available:** construct retrospectively.
- **Exact reconstruction from raw inputs validated:** construct retrospectively.
- **Neither available:** collect the published/current funding prospectively.

Ensure every feature is timestamped so that only information available before the target period enters the model.

---

### Phase 5 — Exploratory Data Analysis

Before fitting complex models, establish the basic properties of the targets.

Investigate:

- Distribution of funding changes
- Autocorrelation
- Persistence
- Cross-venue correlation
- Mean reversion
- Relationship with OI changes
- Relationship with basis
- Relationship with trade flow
- Relationship with market size
- Relationship with volatility
- Time-of-day / settlement effects

For the cross-venue spread, estimate its empirical half-life where appropriate.

---

### Phase 6 — Baseline Models

Establish simple benchmarks before using machine learning.

Candidate baselines:

- Zero-change forecast
- Historical mean change
- Last funding change
- Recent funding trend
- Simple autoregression
- Linear regression

The purpose is to establish how much predictive power exists before introducing more flexible models.

---

### Phase 7 — Feature-Based Forecasting

Introduce the observable market-state features:

- OI level
- ΔOI
- Basis
- Returns
- Volume
- Trade imbalance
- Recent funding
- Volatility
- Time since settlement
- Time until settlement

Start with interpretable models, then test tree-based models such as LightGBM/XGBoost.

Evaluate models strictly out-of-sample using walk-forward or expanding-window validation.

---

### Phase 8 — Compare Individual vs. Cross-Venue Prediction

Directly test H3.

Compare:

- Hyperliquid funding-change predictability
- Lighter funding-change predictability
- Cross-venue funding-spread predictability

Use identical evaluation methodology and comparable feature sets.

The key question is whether modelling the relative funding relationship provides incremental predictive power over modelling each venue separately.

---

### Phase 9 — Trading Simulation

Translate the forecast into a simple delta-neutral strategy.

Example structure:

- Forecast next-period funding spread.
- Enter the relative position when expected spread movement exceeds a threshold.
- Long the venue expected to provide the more favorable funding exposure.
- Short the other venue to reduce directional price exposure.
- Size according to forecast magnitude/confidence and estimated spread half-life.

Initially keep the execution logic deliberately simple.

Apply realistic:

- Fees
- Slippage
- Bid/ask assumptions
- Funding payments
- Margin requirements
- Position limits
- Liquidity constraints

---

### Phase 10 — Robustness Tests

Test whether results survive changes in:

- Market universe
- OI thresholds
- Signal thresholds
- Holding periods
- Training windows
- Model specifications
- Transaction-cost assumptions
- Market regimes
- Venue combinations

Pay particular attention to whether results are driven by a small number of extreme funding events.

---

### Phase 11 — Intrahour Funding Study

If Target C was not historically reconstructable, use the prospective dataset once enough observations have accumulated.

If Target C was historically reconstructable, use the historical data first and continue collecting prospectively to extend the sample and validate that the relationship persists out of sample.

Use the exact information available at each timestamp to predict the remaining adjustment in funding.

This enables the strongest version of the original information-set question:

> **Does intrahour market information predict the eventual adjustment from the funding rate currently available from the venue?**

Compare this result with the purely historical Targets A and B.

---

## Phase 1 Deliverable

Before any modelling or large-scale historical extraction, produce a short data-audit report containing:

1. **Venue/source matrix**
   - Dataset
   - Endpoint/path
   - Historical start/coverage
   - Native resolution
   - Key fields
   - Timestamp convention
   - Missingness
   - Access/cost
   - Status: verified / unverified / unavailable

2. **Funding semantics**
   - Settled vs current/running
   - Settlement timestamp
   - Formula or raw inputs
   - Whether intrahour state is observable

3. **Sample-file evidence**
   - At least one real Hyperliquid `asset_ctxs` file
   - At least one real Hyperliquid L2 file
   - Representative Lighter historical funding response
   - Representative Lighter market-state response
   - 0xArchive coverage check for at least a few target markets

4. **Final source hierarchy**
   - What will be used for each feature
   - Why
   - What is fallback-only
   - What must be collected prospectively

5. **Decision on Target C**
   - Historical
   - Reconstructed and validated
   - Or prospective only

No model development should begin until this deliverable is complete.

---

## Final Research Question

The project ultimately asks:

> **Can observable market state predict subsequent funding-rate dynamics—and particularly changes in the cross-venue funding spread—well enough to create a statistically robust and economically viable delta-neutral trading strategy in mid- and small-cap perpetual markets?**

A secondary prospective question is:

> **Can intrahour market information predict the eventual adjustment from the funding rate currently available from a venue?**

---

## Key Sources

### Hyperliquid

- Historical data: https://hyperliquid.gitbook.io/hyperliquid-docs/historical-data
- Info API: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint
- Perpetuals API: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/perpetuals

### Lighter

- Official Python SDK: https://github.com/elliottech/lighter-python
- Historical funding endpoint documentation: https://github.com/elliottech/lighter-python/blob/main/docs/CandlestickApi.md
- NautilusTrader integration reference: https://nautilustrader.io/docs/nightly/integrations/lighter/

### 0xArchive

- Main site/pricing: https://0xarchive.io/
- Developer documentation: https://docs.0xarchive.io/
- TypeScript SDK / coverage notes: https://github.com/0xArchiveIO/sdk-typescript
