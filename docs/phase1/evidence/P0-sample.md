# P0 — pick the market sample

Status: verified

## What ran
- Command: `FUNDR_DATA=/Users/jerryinyang/Trading/fundr/data/phase1 uv run python probes/p00_sample.py`
- Run date (UTC): 2026-09-19T11:14:41Z
- Markets: candidate set = markets listed on both HL and Lighter (perp, active, not delisted)
- Dates / windows sampled: single point-in-time snapshot (HL `metaAndAssetCtxs`, Lighter `/api/v1/orderBooks`)

## What came back
- Row counts / sizes: HL universe 233 markets; Lighter order books returned; candidate set (both venues, not delisted) = 95 markets
- Schema: sample rows are `{coin, rank, role, oi_notional_usd, lighter_market_id}`; roles `mid` (5), `small` (1), `control` (1)
- Excerpt (≤10 lines):
```
{'coin': 'ENA',   'rank': 11, 'role': 'mid',     'oi_notional_usd': 90301861.92,  'lighter_market_id': 29}
{'coin': 'PONS',  'rank': 18, 'role': 'mid',     'oi_notional_usd': 62232834.12,  'lighter_market_id': 231}
{'coin': 'ONDO',  'rank': 25, 'role': 'mid',     'oi_notional_usd': 37196274.52,  'lighter_market_id': 38}
{'coin': 'ETHFI', 'rank': 33, 'role': 'mid',     'oi_notional_usd': 22976784.95,  'lighter_market_id': 64}
{'coin': 'JUP',   'rank': 40, 'role': 'mid',     'oi_notional_usd': 14698414.93,  'lighter_market_id': 26}
{'coin': 'KAITO', 'rank': 80, 'role': 'small',   'oi_notional_usd': 1859275.69,   'lighter_market_id': 33}
{'coin': 'BTC',   'rank': 1,  'role': 'control', 'oi_notional_usd': 3514836197.84,'lighter_market_id': 1}
```

## Findings
- Candidate set (both venues, not delisted): 95 markets, ranked by HL OI notional (`openInterest × markPx`).
- All 7 picks resolved to a non-null `lighter_market_id`; no swaps were needed (no `--exclude` used).
- HL-only symbols (not matched on Lighter, 138 total): includes HL's `k`-prefixed meme markets (`kPEPE`, `kBONK`, `kFLOKI`, `kSHIB`, `kLUNC`, `kDOGS`, `kNEIRO`) plus tokenized-stock/FX-style tickers (`AAPL`, `AMD`, `AMZN`, `TSLA`-style, `EURUSD`, `AUDUSD`, etc.) and various other altcoins not listed on Lighter.
- Lighter-only symbols (not matched on HL, 118 total): includes `1000PEPE`, `1000BONK`, `1000FLOKI`, `1000NOT`, `1000SHIB` (Lighter's `1000`-prefix denomination), plus other altcoins not listed on HL.
- Naming mismatch confirmed: HL's `k*` (thousand-shifted) memecoins correspond to Lighter's `1000*` symbols (e.g. HL `kPEPE` ↔ Lighter `1000PEPE`). These pairs are excluded from the candidate set by the exact-symbol join and none were selected in the sample, so no alias logic was needed for this run.
- Output file `data/phase1/p00/sample.json` written with 7 rows; every later probe reads this file.

## Open issues
- HL `k*` vs Lighter `1000*` symbol naming mismatch (e.g. `kPEPE` / `1000PEPE`) means those markets are silently excluded from the both-venues candidate set. Not fixed by alias logic per brief instruction; flagged here as a known gap in cross-venue symbol matching that could matter later if these markets are needed.
- Controller ruling (Task 9 follow-up): P1 found PONS (listed ~17 days before the P1 run) missing on the archive dates it otherwise needed for intra-hour analysis. Ruling was to keep this fixed 7-coin sample everywhere (no swap) and instead re-choose P1's `old`/`mid` archive dates around the sample; PONS is present only on P1's `recent` date. See `docs/phase1/evidence/P1-hl-asset-ctxs.md` for detail.
