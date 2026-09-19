import json
from pathlib import Path

import polars as pl
import pytest

from fundr.analysis import match_stats, reported_tolerance
from fundr.funding import hl_formula

FIX = Path(__file__).parent / "fixtures"


def _cases(name):
    return pl.DataFrame(json.loads((FIX / name).read_text()))


def test_hl_baseline_and_clamp():
    out = pl.DataFrame({"p": [0.0, 0.001]}).select(hl_formula.hourly_rate(pl.col("p")))["p"]
    assert out[0] == pytest.approx(0.0000125)
    assert out[1] == pytest.approx(0.0000625)  # 0.001 + clamp(0.0001 - 0.001 → -0.0005) = 0.0005; /8


def test_hl_matches_real_settled_hours():
    df = _cases("hl_formula_cases.json")
    rebuilt = df.select(hl_formula.hourly_rate(pl.col("premium")))["premium"]
    stats = match_stats(rebuilt, df["settled"], reported_tolerance(df["settled_str"].to_list()))
    assert stats["n_match"] == stats["n"]
