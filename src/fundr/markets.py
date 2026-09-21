"""Which markets exist on each venue, and what the venue says about them.

Discovery lives here rather than in a script because three backfills and the QA all need it and
a script cannot import another script: `python scripts/x.py` puts `scripts/` on sys.path, not
the repo root.

The metadata is a research variable in its own right — the design names "Market metadata" a core
Phase 2 variable, Phase 3 dates listings from it and Phase 9 prices fees from it. It also carries
the per-market funding parameters, which are NOT uniform: measured 2026-09-21 over Lighter's 235
perp markets, `funding_premium_multiplier` is 100 on 137 markets, 50 on 96 and 1 on 2 (OPENAI,
ANTHROPIC); `base_interest_rate` is 0.0100 on 119, 0.0032 on 89 and 0.0000 on 27. Any
baseline-vs-off-baseline split that assumes BTC's parameters is wrong on 98 of 235 markets."""
import polars as pl

HL_SOURCE = "hl:metaAndAssetCtxs"
LIGHTER_SOURCE = "lighter:/api/v1/orderBooks+orderBookDetails"
DEFAULT_MULTIPLIER = 100          # reported in hundredths; 100 == an effective 1.0 (P7)
DEFAULT_INTEREST_PCT = 0.01       # 0.0100 %/h, the crypto baseline


def _f(x) -> float | None:
    return None if x is None else float(x)


def discover_hl_markets(hl) -> list[str]:
    """Every market name, delisted included — a delisted market's history is still history."""
    meta, _ = hl.meta_and_asset_ctxs()
    return sorted(m["name"] for m in meta["universe"])


def hl_market_metadata(hl) -> pl.DataFrame:
    meta, _ = hl.meta_and_asset_ctxs()
    return pl.DataFrame([{
        "coin": m["name"],
        "is_delisted": bool(m.get("isDelisted", False)),
        "sz_decimals": m.get("szDecimals"),
        "max_leverage": m.get("maxLeverage"),
        "margin_table_id": m.get("marginTableId"),
        "margin_mode": m.get("marginMode"),
        "only_isolated": bool(m.get("onlyIsolated", False)),
    } for m in meta["universe"]]).sort("coin")


def discover_lighter_markets(api) -> list[dict]:
    """Every perp order book, active and inactive (measured 2026-09-21: 246 books = 235 perp,
    214 active and 21 inactive, plus 11 spot). Spot markets have no funding."""
    perps = [b for b in api.order_books() if b.get("market_type", "perp") == "perp"]
    return sorted(perps, key=lambda b: b["market_id"])


def lighter_market_metadata(api) -> pl.DataFrame:
    details = {d["market_id"]: d for d in api.order_book_details_all()}
    rows = []
    for b in discover_lighter_markets(api):
        d = details.get(b["market_id"], {})
        mult = d.get("funding_premium_multiplier")
        rows.append({
            "market_id": b["market_id"],
            "symbol": b.get("symbol"),
            "status": b.get("status"),
            "market_type": b.get("market_type"),
            "created_at_ms": int(b["created_at"]) if b.get("created_at") else None,
            "funding_premium_multiplier": mult,
            "effective_multiplier": None if mult is None else mult / 100,
            "base_interest_rate_pct": _f(d.get("base_interest_rate")),
            "funding_clamp_big_pct": _f(d.get("funding_clamp_big")),
            "funding_clamp_small_pct": _f(d.get("funding_clamp_small")),
            "maker_fee": _f(b.get("maker_fee")),
            "taker_fee": _f(b.get("taker_fee")),
            "liquidation_fee": _f(b.get("liquidation_fee")),
            "min_base_amount": _f(b.get("min_base_amount")),
            "min_quote_amount": _f(b.get("min_quote_amount")),
            "supported_size_decimals": b.get("supported_size_decimals"),
            "supported_price_decimals": b.get("supported_price_decimals"),
            "is_frozen": bool(b.get("is_frozen", False)),
            # The settlement block: Lighter dates no delisting, so for an expiring or settled
            # market these four fields are the only forward-looking exit information that
            # exists. Cheap to keep, impossible to reconstruct later.
            "start_timestamp": b.get("start_timestamp"),
            "end_timestamp": b.get("end_timestamp"),
            "settlement_type": b.get("settlement_type"),
            "settlement_price": b.get("settlement_price"),
            "settlement_cap": b.get("settlement_cap"),
        })
    return (pl.DataFrame(rows)
            .with_columns(
                pl.from_epoch("created_at_ms", time_unit="ms").dt.cast_time_unit("ms")
                  .alias("listed_at"),
                (pl.col("funding_premium_multiplier") != DEFAULT_MULTIPLIER)
                  .alias("off_default_multiplier"),
                (pl.col("base_interest_rate_pct") != DEFAULT_INTEREST_PCT)
                  .alias("off_default_interest"))
            .sort("market_id"))


def funding_period_s(timestamps) -> int | None:
    """The modal gap between consecutive settlements, in seconds.

    Lighter's funding period is a per-market configuration (P5): measure it per market rather
    than assuming 1h. Measured 2026-09-21: exactly 3600 s on markets 1 and 138."""
    ts = sorted({int(t) for t in timestamps})
    if len(ts) < 3:
        return None
    gaps = [b - a for a, b in zip(ts, ts[1:])]
    return max(set(gaps), key=gaps.count)
