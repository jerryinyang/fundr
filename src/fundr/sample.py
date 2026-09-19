"""Spec P0: the fixed probe market sample, chosen by rule."""
import polars as pl

MID_RANKS = (11, 18, 25, 33, 40)
SMALL_RANGE = (60, 100)
SMALL_TARGET = 80
CONTROL = "BTC"


def pick_sample(hl: pl.DataFrame, lighter_symbols: set[str], exclude: frozenset[str] = frozenset()) -> list[dict]:
    cand = (
        hl.filter(~pl.col("is_delisted") & pl.col("coin").is_in(list(lighter_symbols)))
        .sort("oi_notional_usd", descending=True)
        .with_row_index("rank", offset=1)
    )
    rows = cand.to_dicts()
    n = len(rows)
    taken: set[str] = set()

    def take(target: int, role: str) -> dict:
        # Walk from the target rank toward bigger markets until one is eligible.
        for r in range(min(target, n), 0, -1):
            row = rows[r - 1]
            if row["coin"] not in exclude and row["coin"] not in taken and row["coin"] != CONTROL:
                taken.add(row["coin"])
                return {"coin": row["coin"], "rank": r, "role": role, "oi_notional_usd": row["oi_notional_usd"]}
        raise ValueError(f"no eligible market at or above rank {target}")

    picks = [take(r, "mid") for r in MID_RANKS]
    small_target = SMALL_TARGET if n >= SMALL_TARGET else n  # n < 60 is recorded in the P0 note
    picks.append(take(small_target, "small"))
    control = next((r for r in rows if r["coin"] == CONTROL), None)
    picks.append({
        "coin": CONTROL,
        "rank": control["rank"] if control else None,
        "role": "control",
        "oi_notional_usd": control["oi_notional_usd"] if control else None,
    })
    return picks
