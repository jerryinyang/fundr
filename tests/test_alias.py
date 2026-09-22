import pytest

from fundr import alias

# The six candidates fuzzy matching returns at a 0.8 similarity threshold. All six are FALSE
# pairs (HMSTR is Hamster Kombat, MSTR is MicroStrategy; ME is Magic Eden, GME is GameStop) and
# none may ever appear in a matched pair.
FUZZY_FALSE_PAIRS = [("HMSTR", "MSTR"), ("ME", "GME"), ("RLB", "RKLB"),
                     ("AR", "ARM"), ("AR", "ARC"), ("COMP", "KRCOMP")]


def test_alias_table_has_exactly_five_entries():
    assert set(alias.ALIASES) == {"1000BONK", "1000FLOKI", "1000PEPE", "1000SHIB", "1000NOT"}
    assert {k: v[0] for k, v in alias.ALIASES.items()} == {
        "1000BONK": "kBONK", "1000FLOKI": "kFLOKI", "1000PEPE": "kPEPE",
        "1000SHIB": "kSHIB", "1000NOT": "NOT"}


def test_canonical_maps_both_venues_to_one_key():
    assert alias.canonical("1000PEPE", "lighter") == alias.canonical("kPEPE", "hl")
    assert alias.canonical("1000NOT", "lighter") == alias.canonical("NOT", "hl")
    assert alias.canonical("BTC", "hl") == alias.canonical("BTC", "lighter") == "BTC"
    # A Lighter-only `1000X` with no Hyperliquid counterpart is not an alias and is left alone.
    assert alias.canonical("1000TOSHI", "lighter") == "1000TOSHI"


def test_size_multiplier_agrees_on_the_k_pairs():
    # Both venues quote these in 1000 tokens, so both carry the same factor.
    assert alias.size_multiplier("1000PEPE", "lighter") == 1000
    assert alias.size_multiplier("kPEPE", "hl") == 1000
    for hl_symbol, lighter_symbol in (("kBONK", "1000BONK"), ("kFLOKI", "1000FLOKI"),
                                      ("kSHIB", "1000SHIB")):
        assert alias.size_multiplier(hl_symbol, "hl") == 1000
        assert alias.size_multiplier(lighter_symbol, "lighter") == 1000


def test_size_multiplier_differs_across_venues_on_NOT():
    # Measured 2026-09-22: HL mark / Lighter mark = 0.000999 over 772 common hours. Hyperliquid
    # lists NOT (one token), Lighter lists 1000NOT. The plan said 1000 for both; that is wrong.
    assert alias.size_multiplier("NOT", "hl") == 1
    assert alias.size_multiplier("1000NOT", "lighter") == 1000


def test_size_multiplier_is_one_for_an_ordinary_symbol():
    assert alias.size_multiplier("BTC", "hl") == 1
    assert alias.size_multiplier("BTC", "lighter") == 1
    assert alias.size_multiplier("HYPE", "hl") == 1


def test_matched_pairs_is_exact_matches_plus_the_five_aliases():
    hl = ["BTC", "ETH", "kPEPE", "kBONK", "NOT", "HYPE"]
    lighter = ["BTC", "ETH", "1000PEPE", "1000BONK", "1000NOT", "SPX"]
    # Sorted by canonical key, which is the Hyperliquid symbol.
    assert alias.matched_pairs(hl, lighter) == [
        ("BTC", "BTC"), ("ETH", "ETH"), ("NOT", "1000NOT"),
        ("kBONK", "1000BONK"), ("kPEPE", "1000PEPE")]


def test_matched_pairs_admits_no_fuzzy_candidate():
    for hl_symbol, lighter_symbol in FUZZY_FALSE_PAIRS:
        assert alias.matched_pairs([hl_symbol], [lighter_symbol]) == []
        assert alias.matched_pairs([lighter_symbol], [hl_symbol]) == []


def test_matched_pairs_needs_both_legs_present():
    assert alias.matched_pairs(["kPEPE"], ["BTC"]) == []
    assert alias.matched_pairs(["BTC"], ["1000PEPE"]) == []


def test_matched_pairs_over_the_real_partition_lists_returns_105():
    from fundr import dataset
    if not (dataset.root() / "hl_funding").exists():
        pytest.skip("Phase 2b partitions not on disk")
    from scripts.qa_backfill import _load
    pairs = alias.matched_pairs(list(_load("hl_funding", "coin")),
                                list(_load("lighter_funding", "market_id")))
    assert len(pairs) == 105
    assert len({hl for hl, _ in pairs}) == len({li for _, li in pairs}) == 105
