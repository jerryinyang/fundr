# Phase 2 → Phases 3–11 Handoff

Audience: whoever plans or executes Phase 3 onward, with no memory of Phase 2. Read this end to
end and you can plan Phase 3 without opening anything else; each claim links the note to open
when you need the workings.

Parent research design: [`../funding_research_design.md`](../funding_research_design.md).
Phase 1's handoff still applies in full and is not restated here:
[`../phase1/handoff.md`](../phase1/handoff.md). Phase 2's own deliverables are
[`datasets.md`](datasets.md) (what is on disk and how to load it),
[`backfill_coverage.md`](backfill_coverage.md) (how completely it was collected),
[`dependency_map.md`](dependency_map.md) (what each phase needs and from where),
[`recorder_runbook.md`](recorder_runbook.md) and [`day1_validation.md`](day1_validation.md)
(the live recorder). Generated measurements live under `data/phase2/qa/` and are uncommitted by
design — regenerate rather than trust a stale copy.

---

## 1. Where the research stands

Phase 2 collected the history. Targets A and B can now be built without waiting for anything.

- **Both target inputs are on disk and gap-free where it matters.** `hl_funding` (234 markets,
  4,676,365 rows, from 2023-05-12) and `lighter_funding` (235 markets, 1,585,687 rows, from
  2025-01-17). Across all 100 matched symbols, inside the window where both venues are live,
  there are **zero internal hourly gaps on either side** — 1,202,030 HL rows and 1,005,950
  Lighter rows, no holes.
- **Settlement alignment was re-tested, not assumed.** Phase 1 could only *infer* that HL's row
  stamped `T` closes the hour ending at `T`. The lag scan in `scripts/qa_backfill.py` re-tested
  it against Lighter's directly-verified convention: lag 0 wins, verdict **ALIGNED**. Target B's
  spread is not an hour out of place.
- **Target C still needs the recorder**, exactly as Phase 1 said. Nothing in Phase 2 changed
  that, and nothing will — the published-versus-settled gap was never archived by anyone.
- **The universe question is now decided** (§2) and the cross-venue overlap is now measured
  (§3), which between them tell Phase 3 how large its Target B universe really is.

---

## 2. The universe decision: two universes, one intersection

**The problem.** `dependency_map.md` used to record Phase 3's point-in-time market size as
"collected", while `datasets.md` item 6 recorded Lighter open interest as recorder-only with no
history. Both cannot be true. They were describing different venues.

The facts behind them are not in dispute:

| Venue | Open-interest history | From |
|---|---|---|
| Hyperliquid | **Real, per minute** — `hl_asset_ctxs.open_interest`, 1,218 days | 2023-05-20 |
| Lighter | **None, anywhere.** 0xArchive from 2025-08-25 at 84–97% completeness behind a paid tier is the only thing that exists, and it was never validated | — |

**The decision.** Phase 3 builds **two universes**, not one:

1. **Target A (per-venue)** ranks each venue by the size measure that venue actually has.
   Hyperliquid by real open-interest notional (`open_interest × mark_px` from `hl_asset_ctxs`).
   Lighter by **trailing quote volume** (`lighter_candles.quote_volume`) as a size *proxy*.
2. **Target B (cross-venue)** uses **only the intersection** of the two — an asset must be in
   both venues' universes, and must have been live on both venues in the same hour.

**Why the proxy is acceptable.** The rule is "outside the top 10 by open interest". It excludes
a handful of obvious giants; it does not need to rank positions 40 and 41 correctly. Trailing
volume and open interest disagree at the margin and agree completely about which markets are
the largest, which is the only judgement the rule asks for. Record the proxy in results as a
proxy; do not describe Lighter's universe as open-interest-ranked.

**Three consequences Phase 3 must handle.**

- **The Lighter proxy has 17 blind markets.** `lighter_candles` covers 218 of the 235 markets
  with funding history. The 17 without candles — AAOI, ARM, AVGO, AXTI, BE, BYD, GME, KIOXIA,
  KORU, NOK, POPMART, QCOM, QNT, SOXS, TTWO, USDHKD, WDC — are all equities/FX, all
  Lighter-exclusive, and **none of them is a matched pair**. So this dents Target A's Lighter
  universe and leaves Target B's intersection untouched.
- ~~**The proxy starts eight days late.**~~ **Retired 2026-09-22.** This was an artifact of the
  candle paging bug, not of Lighter's history. After the fix and re-collection, `lighter_candles`
  begins **2025-01-17 08:00** — the same hour Lighter funding begins — so there is no unrankable
  first week. The same fix took Lighter-unrankable pair-hours from 242,460 (24.78%) to **2,323
  (0.24%)**. See the resolved block under `lighter_candles` in [`datasets.md`](datasets.md).
- **Ranking is point-in-time or it is nothing.** Rank per hour from that hour's own recorded
  size, never from a rank measured today. §3's measurements were done this way and the code in
  `scripts/cross_venue_overlap.py` shows the shape.

**One thing the design does not settle, and Phase 3 must.** "Top 10 by open interest" does not
say *top 10 of what pool* — every market on the venue, or only the markets in the universe
being built. §3 measures both; they differ by about 10,000 pair-hours. Phase 3 should pick one,
write it down, and apply it consistently.

---

## 3. The cross-venue overlap Target B actually has

Measured by `scripts/cross_venue_overlap.py`; full report at
`data/phase2/qa/cross_venue_overlap.md` (regenerate it, do not cite a stale copy).

> **Updated 2026-09-22 (Phase 3, Task 4).** Every figure in this section now **includes the five
> denomination-alias pairs** — `kBONK`/`1000BONK`, `kFLOKI`/`1000FLOKI`, `kPEPE`/`1000PEPE`,
> `kSHIB`/`1000SHIB`, `NOT`/`1000NOT` — matched through `fundr.alias`. The matched count is
> **105**, not 100, and the concurrent intersection is **60,951 pair-hours larger**. The
> denomination factor is **per venue leg, not per pair**: the four `k` markets carry 1000 on both
> legs, but Hyperliquid lists `NOT` (one token) against Lighter's `1000NOT`. Funding rates are
> unaffected; prices, sizes and notionals are not.

**The matched symbols counted in `datasets.md` item 1 are a *lifetime* count** — they say the
symbol exists on both venues at some point, not that it was ever live on both at once. Target B
only exists in the concurrent intersection. That intersection is:

- **105 of 105** matched pairs have at least one concurrent hour, so the lifetime count happens
  not to overstate the pair count here.
- **102 pairs** have ≥ 720 concurrent hours (30 days); **99 pairs** have ≥ 2,160 (90 days).
- **1,039,523 concurrent pair-hours** in total; median 9,882 hours per pair.
- The window runs **2025-01-17 08:00Z → 2026-09-21 21:00Z**, and every one of its 14,702 hours
  has at least one live pair.

**But the window does not start wide — it grows.** This is the part a naive "we have 105 pairs
since 2025-01" assumption gets wrong:

| Milestone | First reached |
|---|---|
| ≥ 10 pairs live | 2025-01-17 08:00Z (the first hour) |
| ≥ 25 pairs live | 2025-02-24 21:00Z |
| ≥ 50 pairs live | 2025-07-29 18:00Z |
| ≥ 75 pairs live | 2025-09-24 16:00Z |
| peak | 101 pairs |

Median live pairs per month: 14 in 2025-01, 39 in 2025-05, 70 in 2025-09, 89 in 2026-01, 101 in
2026-09. **A cross-sectional model trained on the whole window is trained on a cross-section
that grows sevenfold through it.** Either weight by it, or start the panel later and say so.

**Top 20 pairs by concurrent hours** (`density` = shared hours ÷ span; 1.0 means no holes):

| symbol | shared_hours | first_shared | last_shared | density |
|---|---|---|---|---|
| SOL | 14702 | 2025-01-17 08:00 | 2026-09-21 21:00 | 1.0 |
| DOGE | 14702 | 2025-01-17 08:00 | 2026-09-21 21:00 | 1.0 |
| TAO | 14702 | 2025-01-17 08:00 | 2026-09-21 21:00 | 1.0 |
| WLD | 14702 | 2025-01-17 08:00 | 2026-09-21 21:00 | 1.0 |
| NEAR | 14702 | 2025-01-17 08:00 | 2026-09-21 21:00 | 1.0 |
| LINK | 14702 | 2025-01-17 08:00 | 2026-09-21 21:00 | 1.0 |
| ETH | 14702 | 2025-01-17 08:00 | 2026-09-21 21:00 | 1.0 |
| AVAX | 14702 | 2025-01-17 08:00 | 2026-09-21 21:00 | 1.0 |
| XRP | 14702 | 2025-01-17 08:00 | 2026-09-21 21:00 | 1.0 |
| BTC | 14702 | 2025-01-17 08:00 | 2026-09-21 21:00 | 1.0 |
| DOT | 14702 | 2025-01-17 08:00 | 2026-09-21 21:00 | 1.0 |
| WIF | 14702 | 2025-01-17 08:00 | 2026-09-21 21:00 | 1.0 |
| kPEPE | 14702 | 2025-01-17 08:00 | 2026-09-21 21:00 | 1.0 |
| POL | 14702 | 2025-01-17 08:00 | 2026-09-21 21:00 | 1.0 |
| TRUMP | 14380 | 2025-01-30 18:00 | 2026-09-21 21:00 | 1.0 |
| kSHIB | 14352 | 2025-01-31 22:00 | 2026-09-21 21:00 | 1.0 |
| SUI | 14352 | 2025-01-31 22:00 | 2026-09-21 21:00 | 1.0 |
| kBONK | 14352 | 2025-01-31 22:00 | 2026-09-21 21:00 | 1.0 |
| kFLOKI | 14352 | 2025-01-31 22:00 | 2026-09-21 21:00 | 1.0 |
| POPCAT | 13996 | 2025-02-15 18:00 | 2026-09-21 21:00 | 1.0 |

Every pair in the top 20 has density 1.0 — their concurrent history has no holes in it. Symbols
are the canonical (Hyperliquid) name, so `kPEPE` here is Lighter's `1000PEPE`.

### How much survives the top-10 exclusion

Ranked per hour on HL open-interest notional, point-in-time:

| Pool the top 10 is drawn from | Pairs never in the top 10 | Pairs always in it | Surviving pair-hours |
|---|---|---|---|
| Every HL market live that hour | **66** of 105 | 5 | 899,694 of 1,039,523 (87%) |
| The matched pairs only | **66** of 105 | 5 | 894,323 of 1,039,523 (86%) |

The five always-excluded pairs are the same under both readings: **BTC, ETH, SOL, XRP, HYPE**.
The rest of the difference is churn — 28–29 pairs cross in and out of the top 10 for under half
their hours, so **membership must be evaluated per hour, not once per symbol.** Pairs that spend
a large minority of their time in the top 10 (ZEC 0.94, LIT 0.73, PUMP 0.65, ASTER 0.58,
FARTCOIN 0.47, DOGE 0.39, kPEPE 0.30) are exactly where a once-per-symbol shortcut would do the
most damage.

**Planning number: roughly 66 pairs are in the Target B universe unconditionally — the same
count under both pools now — ~86% of concurrent pair-hours survive the exclusion, and about 95
pairs are in it for at least part of their history.**

Caveat on that number: 18,066 of the 1,039,523 pair-hours (1.7%) have no HL open-interest row to
rank against, because of archive short days (§4). They are counted as surviving. Treating them
the other way moves the totals by under two percent.

---

## 4. The 2024 archive hole cannot affect Target B

`hl_asset_ctxs` has 131 of 1,218 days below 0.99 completeness, the largest caveat in
`datasets.md`. It does not reach Target B, and the reason is worth stating precisely because
the obvious version of the claim is slightly wrong.

- **121 of the 131 short days fall in 2023-05-20 → 2024-12-14 — entirely before Lighter's first
  settled hour on 2025-01-17.** The big 2024 cluster is outside the overlap window altogether,
  so it cannot touch a single Target B observation. This is the important half of the claim.
- **Ten short days do fall inside the window**: 2025-06-08, 2025-07-19, 2026-05-30, 2026-08-08,
  2026-08-09, 2026-08-27, 2026-08-30, 2026-09-15, 2026-09-16, 2026-09-18. Several are severe
  (2026-09-15 at 0.108). **`2026-07-08` is missing from the archive entirely** and is also
  inside the window.
- **None of that reaches the targets, because the targets do not come from the archive.**
  Target A and Target B are built from `hl_funding` and `lighter_funding`, which are venue API
  pulls, not S3 archive pulls. Both were confirmed gap-free across all 100 matched symbols
  inside the window (§1).
- **What those eleven days *do* reach is the universe ranking and the HL-side features**, both
  of which read `hl_asset_ctxs`. The measured cost is the 17,156 unrankable pair-hours in §3 —
  1.8% of the intersection.

So: say "the archive hole is outside Target B's window and the targets do not depend on the
archive anyway", not "the archive is clean inside the window".

> **Related correction, 2026-09-22.** `qa_backfill.py`'s vendor note and this session's own
> Phase 4 framing both described Hyperliquid's `0.0000125` as a **floor** that **censors** the
> settled rate. Measured over the 978,572 concurrent pair-hours: the rate sits exactly on it
> 59.86% of the time but is **strictly below it 34.00%** of the time and **negative 23.98%** of
> the time — rates pass straight through. It is a *plateau* produced by the **inner** clamp
> cancelling the premium inside a band, not a floor, and **nothing is censored**: the settled
> rate is fully observed and HL publishes its premium on 100% of rows. Separately, HL's **outer
> ±0.04 clamp never binds** — 0 of 978,572 hours, largest observed magnitude 2.26e-2; Lighter's
> binds on 142 hours (0.0145%). Phase 4 should not carry a censoring correction for this.

---

## 5. Open items

Two pieces of work are genuinely unfinished. Neither blocks Phase 3.

### 5.1 The 24-hour archive re-run has not happened

`scripts/backfill_hl_archive.py` quarantines any day whose recorded completeness is below 0.99
and re-fetches it on the next run. **Whether the recent 2026 short days fill in on a later
re-run has never been tested** — it needs a re-run at least 24 hours after 2026-09-22, and no
such run has been made. If HL backfills its own archive with a lag, those days heal for free; if
it does not, they are permanent and the §3 caveat is permanent with them.

- **This is billed.** Requester-pays S3, roughly $0.13 per full re-sweep of the 131 quarantined
  days. A second consecutive sweep risks tripping the $1.20 budget on the third attempt. Check
  the budget in `data/phase2/aws_ledger.jsonl` before running.
- `2023-05-20` is marked `PERMANENT_SHORT` and will never improve — the archive's first file
  genuinely begins at 02:50:04Z. Do not chase it.
- Command: `FUNDR_DATA=data/phase2 uv run python scripts/backfill_hl_archive.py` — the
  `FUNDR_DATA` variable must be set or the script refuses to start.

### 5.2 The Frankfurt recorder has no IAM role, so nothing reaches S3

The recorder runs on `i-02348231aa3d6e4bf` in `eu-central-1` and is recording to local disk, but
**the recording exists in exactly one place**. Until the role is attached, the runbook's
done-when clause ("`health.json` is readable from S3 without logging into the instance") is
unmet and port 22 stays open. This needs IAM permissions the current credentials do not have —
an account administrator has to do it. Full steps in
[`recorder_runbook.md`](recorder_runbook.md) § *To finish the S3 setup*; in short:

1. Create role `fundr-recorder-role` (trust `ec2.amazonaws.com`) with
   `AmazonSSMManagedInstanceCore` plus an inline policy scoped to the one bucket
   `fundr-recorder-<AWS_ACCOUNT_ID>-us-east-1` and **nothing else** — this is the only credential on
   the instance.
2. Create the matching instance profile and associate it with the instance. No restart needed.
3. Set `FUNDR_BUCKET` in `/etc/fundr/recorder.env` and restart; the next timer firing backfills
   everything still on disk, idempotently.

**Risk while this is open:** a single-instance failure loses Target C's history back to the
Frankfurt cutover. Target C is the only thing that cannot be re-collected later.

---

## 6. What Phase 3 should do first, in order

1. **Build the alias table — it is a 5-row lookup, not a project.**

   > **Corrected 2026-09-22.** This item originally said the 269 unmatched symbol-instances were
   > "dominated by denomination prefixes" and that every recovered pair adds Target B data, with
   > 100 as "a floor, not a ceiling". **That was wrong**, inherited from `datasets.md` item 1 and
   > repeated here without checking. Measured: exactly **5** recoverable denomination pairs exist
   > — `kBONK`/`kFLOKI`/`kPEPE`/`kSHIB` ↔ `1000*`, and `NOT` ↔ `1000NOT` — worth **+60,951
   > concurrent pair-hours (+6.2%)**. After aliasing them, 129 HL-only and 130 Lighter-only
   > symbols remain and they are **genuine venue exclusives**: Lighter lists equities, FX and
   > commodities (AAPL, AMZN, ASML, EURUSD, BRENTOIL…), Hyperliquid lists older alts (ACE, ALGO,
   > APE, ATOM…). Fuzzy matching at 0.8 similarity returns 6 further candidates and **all 6 are
   > false** (`HMSTR`/`MSTR`, `ME`/`GME`, `RLB`/`RKLB`, `AR`/`ARM`, `AR`/`ARC`, `COMP`/`KRCOMP`).
   > So 100 pairs is very nearly the ceiling, not a floor, and there is no aggressiveness dial to
   > tune. Ship the 5 as a literal map; do not build a matcher.
2. **Implement the two universes from §2**, ranked per hour, and write down which pool the top
   10 is drawn from.
3. **Re-run `scripts/cross_venue_overlap.py` after the alias table lands** — every number in §3
   is conditional on exact-symbol matching and will change.
4. **Do the archive re-run (§5.1)** once, cheaply, to learn whether the 2026 short days are
   permanent. It changes a caveat, not a target.
5. **Chase the IAM role (§5.2)** in parallel — it is somebody else's approval, not your work.

---

## Where to look for more

| Question | Document |
|---|---|
| What is on disk, how to load it, what will bite | [`datasets.md`](datasets.md) |
| How completely it was collected, and the worst markets | [`backfill_coverage.md`](backfill_coverage.md) |
| Which phase needs what, and what waits on the recorder | [`dependency_map.md`](dependency_map.md) |
| Running, checking and fixing the recorder | [`recorder_runbook.md`](recorder_runbook.md) |
| Whether the recorder's first day was any good | [`day1_validation.md`](day1_validation.md) |
| Funding semantics, venue quirks, Target C's impossibility | [`../phase1/handoff.md`](../phase1/handoff.md) |
| Regenerating §3's numbers | `scripts/cross_venue_overlap.py` |
| Regenerating coverage and the alignment verdict | `scripts/qa_backfill.py` |
