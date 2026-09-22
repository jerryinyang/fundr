"""The cross-venue symbol map: five hand-checked pairs and the denomination factor each leg
carries.

Hyperliquid and Lighter name the same asset differently when they re-denominate it — `kPEPE`
against `1000PEPE`. Measurement over the two venues' funding partitions found **exactly five**
recoverable pairs; after them, 129 Hyperliquid-only and 130 Lighter-only symbols remain and they
are genuine venue exclusives, not naming differences (`docs/phase3/decisions.md` D4).

**Nothing fuzzy is allowed here.** At a 0.8 similarity threshold six further candidates appear
and all six are false: `HMSTR`/`MSTR` (Hamster Kombat vs MicroStrategy), `ME`/`GME` (Magic Eden
vs GameStop), `RLB`/`RKLB` (Rollbit vs Rocket Lab), `AR`/`ARM`, `AR`/`ARC`, `COMP`/`KRCOMP`. Any
one of them silently corrupts a spread and nothing downstream would catch it. Hence a hard-coded
table.

**Denomination scaling is per venue, not per pair.** Funding *rates* are unaffected — Targets A
and B are safe — but every price, size and notional is not.

Measured 2026-09-22, Hyperliquid `hl_asset_ctxs.mark_px` (hourly median) against Lighter
`lighter_mark_candles.close`, 772 common hours over 33 days sampled 2026-05-12 -> 2026-09-18:

    kBONK /1000BONK   ratio 1.0007   (p1 0.984,  p99 1.018)
    kFLOKI/1000FLOKI  ratio 1.0001   (p1 0.985,  p99 1.014)
    kPEPE /1000PEPE   ratio 1.0006   (p1 0.984,  p99 1.016)
    kSHIB /1000SHIB   ratio 1.0004   (p1 0.987,  p99 1.015)
    NOT   /1000NOT    ratio 0.000999 (p1 0.000982, p99 0.001016)

The four `k` markets already agree: both venues quote 1000 tokens, so both legs carry 1000. The
fifth does NOT. Hyperliquid lists `NOT`, one token — not `kNOT` — against Lighter's `1000NOT`, so
the two legs differ by exactly 1000x. The Phase 3 plan states the multiplier is "1000 for the
four k/1000 pairs and 1000 for NOT/1000NOT"; the measurement says the Hyperliquid leg of
`NOT` is **1**, and this module implements the measurement.
"""

VENUES = ("hl", "lighter")

# Lighter symbol -> (Hyperliquid symbol, the HYPERLIQUID leg's size multiplier).
ALIASES: dict[str, tuple[str, int]] = {
    "1000BONK": ("kBONK", 1000),
    "1000FLOKI": ("kFLOKI", 1000),
    "1000PEPE": ("kPEPE", 1000),
    "1000SHIB": ("kSHIB", 1000),
    "1000NOT": ("NOT", 1),      # measured 1, not 1000 -- HL lists NOT, not kNOT. See above.
}

# Every Lighter symbol in the table carries its own `1000` prefix, so the Lighter leg is always
# quoted per 1000 tokens. Only the Hyperliquid leg varies, which is why ALIASES holds that one.
LIGHTER_MULTIPLIER = 1000

_HL_MULTIPLIERS = {hl: multiplier for hl, multiplier in ALIASES.values()}


def _check_venue(venue: str) -> None:
    """A mistyped venue would silently return the wrong factor, so it is an error, not a 1."""
    if venue not in VENUES:
        raise ValueError(f"unknown venue {venue!r}; expected one of {VENUES}")


def canonical(symbol: str, venue: str) -> str:
    """The one key both venues' names for an asset map to -- the Hyperliquid symbol."""
    _check_venue(venue)
    if venue == "lighter" and symbol in ALIASES:
        return ALIASES[symbol][0]
    return symbol


def size_multiplier(symbol: str, venue: str) -> int:
    """How many canonical tokens one quoted unit of this venue's market represents.

    1 for an ordinary market. Divide a price by it, multiply a size by it, to reach the canonical
    unit. Funding rates are unaffected; prices, sizes and notionals are not."""
    _check_venue(venue)
    if venue == "lighter":
        return LIGHTER_MULTIPLIER if symbol in ALIASES else 1
    return _HL_MULTIPLIERS.get(symbol, 1)


def matched_pairs(hl_symbols, lighter_symbols) -> list[tuple[str, str]]:
    """Exact symbol matches plus the five aliases, and nothing else.

    Returns `(hyperliquid_symbol, lighter_symbol)` sorted by the canonical key."""
    hl, lighter = set(hl_symbols), set(lighter_symbols)
    pairs = [(symbol, symbol) for symbol in hl & lighter]
    pairs += [(hl_symbol, lighter_symbol)
              for lighter_symbol, (hl_symbol, _) in ALIASES.items()
              if lighter_symbol in lighter and hl_symbol in hl]
    return sorted(pairs)
