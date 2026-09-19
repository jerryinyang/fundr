import polars as pl

from fundr.sample import pick_sample


def _hl(n):
    coins = ["BTC"] + [f"C{i}" for i in range(2, n + 1)]
    return pl.DataFrame({
        "coin": coins,
        "is_delisted": [False] * n,
        "oi_notional_usd": [float(n - i) for i in range(n)],
    })


def test_picks_ranks_and_control():
    hl = _hl(120)
    sample = pick_sample(hl, set(hl["coin"]))
    by_role = {}
    for s in sample:
        by_role.setdefault(s["role"], []).append(s["rank"])
    assert by_role["mid"] == [11, 18, 25, 33, 40]
    assert by_role["small"] == [80]
    assert by_role["control"] == [1]


def test_only_markets_on_both_venues_and_not_delisted():
    hl = _hl(120).with_columns(pl.when(pl.col("coin") == "C3").then(True).otherwise(False).alias("is_delisted"))
    lighter = set(hl["coin"]) - {"C2"}
    sample = pick_sample(hl, lighter)
    assert all(s["coin"] not in {"C2", "C3"} for s in sample)


def test_short_candidate_list_takes_lowest_available():
    hl = _hl(50)
    small = [s for s in pick_sample(hl, set(hl["coin"])) if s["role"] == "small"]
    assert small[0]["rank"] == 50


def test_exclude_moves_to_next_bigger_market():
    hl = _hl(120)
    first = pick_sample(hl, set(hl["coin"]))
    rank11 = next(s["coin"] for s in first if s["rank"] == 11)
    again = pick_sample(hl, set(hl["coin"]), exclude=frozenset({rank11}))
    mids = [s["rank"] for s in again if s["role"] == "mid"]
    assert mids == [10, 18, 25, 33, 40]
