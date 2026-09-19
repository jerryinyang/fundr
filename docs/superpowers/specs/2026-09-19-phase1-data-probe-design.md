# Phase 1 — Historical Data Availability Probe: Design

Date: 2026-09-19
Parent: `docs/funding_research_design.md` (Phase 1 and "Phase 1 Deliverable")

## Goal

Establish, with real files and responses as evidence, the exact information set and
timestamp resolution available for Hyperliquid (HL) and Lighter, and decide per venue
how Target C (realized minus published/current funding) can be built. Produce a
decision map that fixes the scope of Phases 2–11.

No modelling and no large-scale extraction happen in Phase 1.

## Decisions already made

| Topic | Decision |
|---|---|
| Code lifespan | Reusable, tested source clients (`src/fundr/`), reused by Phase 2 |
| Spend | HL requester-pays S3 samples on the user's AWS account, target < $1. 0xArchive free tier only (50k credits) |
| Live observation | Short live probe (~4 hours). Permanent recorder is decided by Phase 1, built in Phase 2 |
| Reconstruction | Actually test rebuilding running funding where raw inputs exist (Outcome C) |
| Structure | Package + one probe script per question + hand-written report built from evidence notes |

## Layout

```
src/fundr/
  sources/
    hl_archive.py      # requester-pays S3: list, download, lz4 decode
    hl_api.py          # info endpoint: metaAndAssetCtxs, fundingHistory, predictedFundings, candleSnapshot
    lighter_api.py     # /api/v1/fundings, market-state endpoints, /api/v1/funding-rates, current funding
    oxarchive.py       # REST client, coverage/catalog queries, credit accounting
  funding/
    hl_formula.py      # rebuild hourly funding from premium samples; written only after P7 confirms the formula
    lighter_formula.py # same; written only after P7 confirms the formula
probes/                # p00_sample.py ... p10_rebuild.py; raw output -> data/phase1/
tests/
  fixtures/            # small trimmed samples cut from real downloads
docs/phase1/
  evidence/            # P<n>-<name>.md, one per probe
  data_audit.md        # Phase 1 deliverable
  decision_map.md      # Phase 1 findings -> Phase 2–11 consequences
```

- Raw downloads land in `data/phase1/` (gitignored by `**/data/*`).
- `.gitignore` ignores `*.csv` and `*.json` globally; add a narrow negation for
  `tests/fixtures/**` so fixtures are committed.
- Dependencies: `httpx`, `boto3`, `lz4`, `polars`, `pytest`. Python 3.13, managed by `uv`.
- Secrets (0xArchive API key) via environment variable `OXARCHIVE_API_KEY`; never committed.
  AWS via the existing default profile.

## Probe market sample (P0)

Chosen by rule, fixed once at the start and recorded in `docs/phase1/evidence/P0-sample.md`:

1. Candidate set: markets listed on **both** HL and Lighter at probe time.
2. Rank the candidate set by HL open interest in USD notional (`openInterest × markPx`
   from `metaAndAssetCtxs`) at probe time.
3. Picks: ranks 11, 18, 25, 33, 40 (mid caps) plus the market closest to rank 80 within
   ranks 60–100 (small cap). If the candidate set has fewer markets than a pick needs,
   take the lowest-ranked available and record it.
4. Add BTC as a sanity control.
5. Check each pick exists on every archive date sampled (P1) and has Lighter funding
   history over the P10 windows. If not, substitute the next rank up and record the swap.

The same 7 markets are used by every probe. Archive dates sampled: one old, one mid,
one recent date where the archive has data (chosen after listing the bucket).

## Probes

Each probe answers one question and writes one evidence note.

| # | Probe | Question | Method |
|---|---|---|---|
| P1 | HL `asset_ctxs` | Row frequency, schema, dtypes, timestamp meaning; does `funding` change within the hour; is it running or settled; is `premium` intra-hour; OI and impact bid/ask frequency; gaps; stability across dates and markets | Download 3 dates. Per market: rows per hour, distinct `funding` values per hour, per-field change frequency, gap scan. Record archive upload lag (latest available date vs today) |
| P2 | HL L2 archive | Timestamp resolution, snapshot vs delta, depth, market coverage, file size, extraction cost, gaps | 1 date × 2 hours × sample markets |
| P3 | HL node fills | Coverage and fields for signed trade flow in `node_fills_by_block`, `node_fills`, `node_trades` | List prefixes across date ranges; download one small file per dataset |
| P4 | HL API cross-check | Does archive `funding` match `fundingHistory` settled values; what `predictedFundings` returns; is the 5,000-candle limit real for old windows; funding **units, sign convention and interval** (per hour vs per 8h, fraction vs percent, positive = longs pay?) and reported precision | Compare P1 dates to `fundingHistory`; request an old `candleSnapshot` window; read docs and confirm units against values |
| P5 | Lighter `/fundings` | Coverage start per market; `1h` and `1d` resolutions; `count_back` semantics; 750-row cap behaviour; whether auth is required; timestamp convention; gaps; funding **units, sign convention, interval** and reported precision; whether any intermediate (pre-settlement) state is preserved (SoT formula item 5) | Page full `1h` history for sample markets; test `1d`, `count_back`, and an unauthenticated call; inspect all returned fields |
| P6 | Lighter market state | Which historical OI, price, volume, premium and listing/delisting series exist natively; is `/api/v1/funding-rates` live-only or historical | Walk documented endpoints (candles, market stats, `/funding-rates`, others); classify each as historical or live-only, with a request/response as evidence |
| P7 | Funding formulas (both venues) | Authoritative formula per venue, parameter meanings, clamp and multiplier placement, premium sampling rate, what is public before settlement | Lighter: SDK and any public source, docs, NautilusTrader integration. HL: official docs. Cross-check both against P9. Nothing encoded until confirmed |
| P8 | 0xArchive coverage | Per sample market on Lighter: funding (incl. any intra-hour current funding), OI, trades, L3 — start date, granularity, gaps. Replay capability. One HL coverage check (funding/OI/trades) as a possible fallback. Credit cost per call, extrapolated to research scale as a paid-tier $/month estimate | Free tier; catalog/coverage calls; try one short replay; log credits used per call |
| P9 | Live probe | What each venue's "current funding" means intra-hour; whether live endpoints exist for every field a Phase 2 recorder needs | Poll ~1/min for ~4 hours spanning at least 3 settlements. HL: `metaAndAssetCtxs` (`funding`, `premium`, `openInterest`, `markPx`, `oraclePx`, `dayNtlVlm`) and `predictedFundings`. Lighter: market stats `current_funding_rate`, `funding_rate`, `funding_timestamp`, plus `/funding-rates`, OI, index/mark price, volume. Once per venue, confirm a working live source for trade flow. Afterwards compare to settled values from P4/P5 |
| P10 | Rebuild test | Can running funding be rebuilt exactly from archived raw inputs | Only per venue where P1/P6/P8 show inputs exist. 5 mid-cap sample markets × 2 windows of 4 consecutive days (one near the P1 old date, one near the P1 recent date). Rebuild hourly funding; compare to settled |

**Order:**
1. P0.
2. P1, P4, P5, P9 (P9 runs in the background).
3. P6, P8 (catalogue all possible Target C inputs and sources before deciding).
4. P7.
5. P10.
6. P2, P3.

### P10 result rule (per venue)

- **Tolerance** = one unit in the last decimal place of settled funding as reported
  (measured in P4/P5).
- **Baseline hours** = hours where settled funding equals the interest-rate baseline or a
  clamp bound. These match trivially and are reported separately.
- **Pass** — all three hold:
  - at least 99% of all market-hours match within tolerance;
  - at least 99% of off-baseline market-hours match within tolerance, with at least 100
    off-baseline market-hours in the sample (extend the windows if fewer);
  - |mean signed error| ≤ tolerance / 10 across all market-hours.
- **Near-miss** — 95–99% on either rate: report the cause; treat as not validated.
- **Not attemptable** — archived inputs are coarser than the formula needs (e.g. formula
  averages premium sampled every few seconds, archive holds one value per minute), so exact
  rebuild is impossible by construction. Treated as not validated.

## Evidence notes

`docs/phase1/evidence/P<n>-<name>.md`, fixed sections:

1. What ran — command, run date, markets, dates sampled
2. What came back — row counts, schema, short excerpt
3. Findings — facts only
4. Status — verified / unverified / unavailable
5. Open issues

"Verified" requires a real file or response behind it. Claims from docs or third parties
stay "unverified" until checked.

## Data audit report

`docs/phase1/data_audit.md`, the five sections in the parent doc:

1. Venue/source matrix — dataset, endpoint/path, coverage start, native resolution, key
   fields, timestamp convention, missingness, access/cost, status, evidence link
2. Funding semantics per venue — settled vs running, settlement timestamp, units, sign
   convention, interval, reported precision, formula or raw inputs, whether intra-hour
   state is observable, and the normalisation needed to compare the two venues
3. Sample-file evidence — links to evidence notes
4. Final source hierarchy — source per feature, why, fallback-only sources, what must be
   collected prospectively
5. Target C decision per venue

## Target C decision rule (per venue)

1. **Historical (Outcome A)** — an archived source (HL archive, Lighter API or 0xArchive)
   holds intra-hour current funding, and on the sampled dates:
   - it changes within the hour in the same way P9 showed the live value changing; and
   - its last in-hour value equals the settled rate from P4/P5 (within tolerance) on at
     least 99% of market-hours.
   (Direct overlap with the P9 window is not required; archives lag by about a month.)
2. **Reconstructed and validated (Outcome C)** — otherwise, raw inputs exist and P10 passes.
3. **Prospective only (Outcome B)** — otherwise, including P10 near-miss or not attemptable.

Venues may land differently. Single-venue Target C is usable per venue. The spread version
is historical only over periods where both venues qualify.

## Decision map

`docs/phase1/decision_map.md`: each finding → consequence. Minimum rows:

| Phase 1 finding | Affects |
|---|---|
| Target C outcome per venue | Phase 2 recorder scope; Phase 4 Target C construction; Phase 11 timing |
| Lighter funding history start | Study window length for Target B |
| Funding units, sign, interval per venue | Normalisation of F_t before S_t is formed (Phase 4) |
| Settlement timing and alignment across venues | Pairing of S_t in Phase 4 |
| Intra-hour OI/price availability per venue | Which Phase 7 features are historical vs prospective-only |
| Volume source per venue | Volume feature (Phases 2, 3, 7) and universe segmentation |
| Premium availability per venue | Premium as a feature; Target C inputs |
| Settlement schedule known historically | Whether time since/until settlement can be computed (Phases 2, 7) |
| Signed trade flow source | Whether trade imbalance exists historically; whether 0xArchive paid tier is needed |
| Historical OI per venue | Whether Phase 3 universe can be point-in-time; survivorship risk flag |
| Listing/delisting metadata | Point-in-time universe (Phase 3); survivorship risk |
| Spot/index price source | Basis feature feasibility |
| HL archive upload lag | How much recent data must come from the recorder rather than the archive |
| Live endpoints for each recorder field | Phase 2 recorder feasibility and field list |
| 0xArchive $/month at research scale | Whether a paid tier is justified in Phase 2 |
| L2 archive usability | Remains optional future feature set |

## Testing

- TDD for parsers and formula code, using trimmed fixtures from real downloads.
- Formula code (both venues) is written only after P7 confirms the formula; its tests
  rebuild known settled hours from fixture inputs.
- Network calls are not unit-tested; probes are the integration check.

## Stop conditions — pause and ask the user

- AWS spend heading past ~$1.
- 0xArchive free credits insufficient for P8.
- A finding that changes Targets A/B (e.g. Lighter history too short to study, funding
  not comparable across venues).

## User actions needed

- Create a 0xArchive free account and export `OXARCHIVE_API_KEY` (before P8).
- Approve the first billed AWS download.

## Done when

- Every source-matrix row has a status and an evidence link.
- Sample-file evidence exists for: at least one real HL `asset_ctxs` file, at least one
  real HL L2 file, a representative Lighter historical funding response, a representative
  Lighter market-state response, and a 0xArchive coverage check for the sample markets.
- Funding semantics written for both venues, including the cross-venue normalisation.
- Final source hierarchy complete: a source for every feature, fallbacks named, and the
  prospective-only list.
- Target C decided per venue by the rule above.
- Decision map filled with actual findings.
