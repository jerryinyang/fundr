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
    hl_formula.py      # rebuild hourly funding from premium samples
    lighter_formula.py # same; written only after P7 confirms the formula
probes/                # p01_hl_asset_ctxs.py ... p10_rebuild.py; raw output -> data/phase1/
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

## Probe market sample

Chosen by rule, fixed once at the start and recorded in `docs/phase1/evidence/P0-sample.md`:

- Markets listed on both HL and Lighter.
- Rank by HL open interest (notional) at probe time; take ranks 11–40.
- Pick 5 spread evenly across that range.
- Add BTC as a sanity control (large, liquid, well-documented).

The same 6 markets are used by every probe. Archive dates sampled: one old, one mid,
one recent date where the archive has data (chosen after listing the bucket).

## Probes

Each probe answers one question and writes one evidence note.

| # | Probe | Question | Method |
|---|---|---|---|
| P1 | HL `asset_ctxs` | Row frequency, schema, dtypes, timestamp meaning; does `funding` change within the hour; is it running or settled; is `premium` intra-hour; OI and impact bid/ask frequency; gaps; stability across dates and markets | Download 3 dates. Per market: rows per hour, distinct `funding` values per hour, per-field change frequency, gap scan |
| P2 | HL L2 archive | Timestamp resolution, snapshot vs delta, depth, market coverage, file size, extraction cost, gaps | 1 date × 2 hours × sample markets |
| P3 | HL node fills | Coverage and fields for signed trade flow in `node_fills_by_block`, `node_fills`, `node_trades` | List prefixes across date ranges; download one small file per dataset |
| P4 | HL API cross-check | Does archive `funding` match `fundingHistory` settled values; what `predictedFundings` returns; is the 5,000-candle limit real for old windows | Compare P1 dates to `fundingHistory`; request an old `candleSnapshot` window |
| P5 | Lighter `/fundings` | Coverage start per market, resolution (`1h`, `1d`), timestamp convention, 750-row cap behaviour, gaps | Page full `1h` history for sample markets |
| P6 | Lighter market state | Which historical OI, price, volume series exist natively | Walk documented endpoints (candles, market stats, others); classify each as historical or live-only |
| P7 | Lighter formula | Authoritative formula, parameter meanings, clamp and multiplier placement, what is public before settlement | Read SDK and any public source, docs, NautilusTrader integration; cross-check against P9. Nothing encoded until confirmed |
| P8 | 0xArchive coverage | Lighter OI, trades, L3, funding per sample market: start date, granularity, gaps, credit cost at research scale | Free tier; catalog/coverage calls; log credits used per call |
| P9 | Live probe | What each venue's "current funding" means intra-hour | Poll ~1/min for ~4 hours spanning at least 3 settlements: HL `metaAndAssetCtxs` + `predictedFundings`; Lighter current funding + `/funding-rates`. Afterwards compare to settled values from P4/P5 |
| P10 | Rebuild test | Can running funding be rebuilt exactly from archived raw inputs | Only per venue where P1/P6/P8 show inputs exist. 5 sample markets × 7 consecutive days. Rebuild hourly funding; compare to settled |

**Order:** P0 → P1, P4, P5, P9 (decide Target C; P9 runs in background) → P7 → P10 →
P2, P3, P6, P8 (cataloguing).

**P10 pass rule (per venue):** at least 99% of market-hours match settled funding within
absolute 1e-7, and |mean signed error| ≤ 1e-8 across all market-hours. 95–99% match is a
near-miss: report the cause, treat as not validated.

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
2. Funding semantics per venue — settled vs running, settlement timestamp, formula or raw
   inputs, whether intra-hour state is observable
3. Sample-file evidence — links to evidence notes
4. Final source hierarchy — source per feature, why, fallback-only sources, what must be
   collected prospectively
5. Target C decision per venue

## Target C decision rule (per venue)

1. Archive holds intra-hour current funding that matches what P9 observed live →
   **Historical (Outcome A)**.
2. Otherwise, raw inputs exist and P10 passes → **Reconstructed and validated (Outcome C)**.
3. Otherwise → **Prospective only (Outcome B)**.

Venues may land differently. Single-venue Target C is usable per venue. The spread version
is historical only over periods where both venues qualify.

## Decision map

`docs/phase1/decision_map.md`: each finding → consequence. Minimum rows:

| Phase 1 finding | Affects |
|---|---|
| Target C outcome per venue | Phase 2 recorder scope; Phase 4 Target C construction; Phase 11 timing |
| Lighter funding history start | Study window length for Target B |
| Settlement timing and alignment across venues | Pairing of S_t in Phase 4 |
| Intra-hour OI/price availability per venue | Which Phase 7 features are historical vs prospective-only |
| Signed trade flow source | Whether trade imbalance exists historically; whether 0xArchive paid tier is needed |
| Historical OI per venue | Whether Phase 3 universe can be point-in-time; survivorship risk flag |
| Spot/index price source | Basis feature feasibility |
| L2 archive usability | Remains optional future feature set |

## Testing

- TDD for parsers and formula code, using trimmed fixtures from real downloads.
- Formula tests rebuild known settled hours from fixture inputs.
- Network calls are not unit-tested; probes are the integration check.

## Stop conditions — pause and ask the user

- AWS spend heading past ~$1.
- 0xArchive free credits insufficient for P8.
- A finding that changes Targets A/B (e.g. Lighter history too short to study).

## User actions needed

- Create a 0xArchive free account and export `OXARCHIVE_API_KEY` (before P8).
- Approve the first billed AWS download.

## Done when

- Every source-matrix row has a status and an evidence link.
- Funding semantics written for both venues.
- Target C decided per venue by the rule above.
- Decision map filled with actual findings.
