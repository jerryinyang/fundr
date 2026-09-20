import json
from pathlib import Path

import polars as pl
import pytest

from fundr.analysis import match_stats, reported_tolerance
from fundr.funding import hl_formula, lighter_formula

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


LIGHTER_PARAMS = dict(interest=0.0100, clamp_small=0.0500, clamp_big=4.0, multiplier=100.0)


def test_lighter_baseline():
    out = pl.DataFrame({"p": [0.0]}).select(lighter_formula.hourly_rate(pl.col("p"), **LIGHTER_PARAMS))["p"]
    # 0.01% / 8 = 0.00125%, truncated (not rounded) toward zero at 4 dp (P7: Lighter truncates
    # /fundings.rate to 4 dp) -> 0.0012, not the brief's untruncated 0.00125.
    assert out[0] == pytest.approx(0.0012, abs=1e-9)


def test_lighter_matches_real_settled_hours():
    df = _cases("lighter_formula_cases.json")
    rebuilt = df.select(lighter_formula.hourly_rate(pl.col("premium"), **LIGHTER_PARAMS))["premium"]
    stats = match_stats(rebuilt, df["settled"], reported_tolerance(df["settled_str"].to_list()))
    assert stats["n_match"] == stats["n"]


def test_lighter_big_clamp_applies_before_div8():
    # P7-confirmed ordering: clamp(smallClamped, -4, +4) / 8, NOT smallClamped / 8 then clamp.
    # p=8.05 -> smallClamped = 8.05 + clamp(0.01 - 8.05, -0.05, +0.05) = 8.05 - 0.05 = 8.0.
    # clamp-then-/8 (confirmed):    trunc4(clamp(8.0, -4, 4) / 8) = trunc4(4.0 / 8) = 0.5
    # /8-then-clamp (brief's snippet, wrong): trunc4(clamp(8.0 / 8, -4, 4)) = trunc4(1.0) = 1.0
    # The fixture never exercises the clamp (P7 "Open issues"), so this synthetic case is the
    # only regression guard for the ordering.
    out = pl.DataFrame({"p": [8.05]}).select(lighter_formula.hourly_rate(pl.col("p"), **LIGHTER_PARAMS))["p"]
    assert out[0] == pytest.approx(0.5, abs=1e-9)
