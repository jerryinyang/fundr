from datetime import datetime, timedelta

import polars as pl
import pytest

from fundr import premium
from fundr.funding import lighter_formula

HOUR = timedelta(hours=1)
T0 = datetime(2026, 1, 1)

# Three market shapes, straight off `markets/venue=lighter`: crypto, an equity/RWA market on the
# halved clamp and the lower base rate, and a market with no base rate at all.
CRYPTO = {"symbol": "BTC", "base_interest_rate_pct": 0.01, "funding_clamp_small_pct": 0.05,
          "funding_clamp_big_pct": 4.0, "funding_premium_multiplier": 100}
EQUITY = {"symbol": "TSLA", "base_interest_rate_pct": 0.0032, "funding_clamp_small_pct": 0.05,
          "funding_clamp_big_pct": 4.0, "funding_premium_multiplier": 50}
NO_INTEREST = {"symbol": "OPENAI", "base_interest_rate_pct": 0.0, "funding_clamp_small_pct": 0.05,
               "funding_clamp_big_pct": 4.0, "funding_premium_multiplier": 1}


def _params(*markets) -> pl.DataFrame:
    return pl.DataFrame(list(markets), schema={
        "symbol": pl.String, "base_interest_rate_pct": pl.Float64,
        "funding_clamp_small_pct": pl.Float64, "funding_clamp_big_pct": pl.Float64,
        "funding_premium_multiplier": pl.Int64})


def _settle(premia_pct, market, *, clamp_pct=None) -> list[float]:
    """The venue's confirmed forward formula, run on `market`'s own parameters.

    `clamp_pct` overrides the clamp the forward run uses, which is how the crypto-calibrated
    bound is fed to a multiplier-50 market deliberately."""
    clamp = clamp_pct if clamp_pct is not None else premium.effective_clamp_small_pct(
        market["funding_clamp_small_pct"], market["funding_premium_multiplier"])
    return (pl.DataFrame({"p": premia_pct})
            .select(lighter_formula.hourly_rate(
                pl.col("p"), market["base_interest_rate_pct"], clamp,
                market["funding_clamp_big_pct"], market["funding_premium_multiplier"]))
            .to_series().to_list())


def _rates(rows) -> pl.DataFrame:
    """`rows` is (symbol, hour offset, settled rate in PERCENT) -- percent is what the venue
    publishes; the frame carries it on the common basis, as a signed fraction."""
    return pl.DataFrame(
        {"symbol": [r[0] for r in rows],
         "hour": [T0 + r[1] * HOUR for r in rows],
         "signed_rate_fraction": [r[2] / 100 for r in rows]},
        schema={"symbol": pl.String, "hour": pl.Datetime("ms"),
                "signed_rate_fraction": pl.Float64})


def _invert(premia_pct, market, **kwargs) -> pl.DataFrame:
    rates = _rates([(market["symbol"], i, r)
                    for i, r in enumerate(_settle(premia_pct, market, **kwargs))])
    return premium.lighter_premium(rates, _params(market))


# --- the round trip: the inverse of the confirmed forward formula ----------------------------

@pytest.mark.parametrize("market", [CRYPTO, EQUITY, NO_INTEREST],
                         ids=lambda m: f"mult{m['funding_premium_multiplier']}")
@pytest.mark.parametrize("p", [0.30, 0.0800, -0.0405, -0.20, -1.10])
def test_round_trip_returns_the_premium_within_the_truncation_width(market, p):
    """Settle a known premium, invert it, and it comes back within Lighter's own truncation.

    The venue truncates the settled rate toward zero at four decimals of percent, so eight
    hours of premium land in one 8e-4-percent bucket. That width is the whole of the inversion's
    error and it is not reducible -- the information was discarded at settlement."""
    out = _invert([p], market)
    point_pct = out["lighter_premium_point"].item() * 100
    assert out["premium_is_point_identified"].item() is True
    assert abs(point_pct - p) < premium.TRUNCATION_WIDTH_PCT


def test_the_point_estimate_brackets_the_true_premium_from_the_correct_side():
    """Truncation is toward zero, so a positive rate understates and a negative one overstates."""
    assert 0 <= 0.30 - _invert([0.30], CRYPTO)["lighter_premium_point"].item() * 100
    assert 0 >= -0.30 - _invert([-0.30], CRYPTO)["lighter_premium_point"].item() * 100


# --- each market's own parameters, never BTC's ------------------------------------------------

def test_a_market_inverts_with_its_own_base_rate_not_btcs():
    """0.0004 %/h is TSLA's baseline (0.0032/8) and is off-baseline for BTC (0.01/8 -> 0.0012).

    Read with BTC's parameters the TSLA hour would be point-identified and 0.0008 percent of
    premium would be invented for it."""
    out = premium.lighter_premium(_rates([("BTC", 0, 0.0004), ("TSLA", 0, 0.0004)]),
                                  _params(CRYPTO, EQUITY))
    by_symbol = {r["symbol"]: r for r in out.to_dicts()}
    assert by_symbol["TSLA"]["premium_is_point_identified"] is False
    assert by_symbol["TSLA"]["lighter_premium_point"] is None
    assert by_symbol["BTC"]["premium_is_point_identified"] is True


def test_a_multiplier_50_market_is_not_inverted_with_the_crypto_clamp():
    """The one calibration finding that survived F6: the effective clamp scales with the
    multiplier, so an equity market's bound is 0.025, not crypto's 0.05.

    Both markets settle the same rate here. Reading the equity hour with the crypto clamp would
    move its premium by exactly 0.025 percent -- silently, and on 96 of Lighter's markets."""
    out = premium.lighter_premium(_rates([("BTC", 0, 0.05), ("TSLA", 0, 0.05)]),
                                  _params(CRYPTO, EQUITY))
    by_symbol = {r["symbol"]: r for r in out.to_dicts()}
    assert by_symbol["BTC"]["funding_clamp_small_eff_pct"] == 0.05
    assert by_symbol["TSLA"]["funding_clamp_small_eff_pct"] == 0.025
    gap = (by_symbol["BTC"]["lighter_premium_point"]
           - by_symbol["TSLA"]["lighter_premium_point"]) * 100
    assert gap == pytest.approx(0.025)


def test_a_multiplier_50_market_fed_the_crypto_clamp_does_not_round_trip():
    """The failure this module exists to prevent, run forwards: settle TSLA's premium under the
    crypto-calibrated 0.05 and the inversion misses by the difference between the two clamps."""
    out = _invert([0.30], EQUITY, clamp_pct=CRYPTO["funding_clamp_small_pct"])
    error = abs(out["lighter_premium_point"].item() * 100 - 0.30)
    assert error > premium.TRUNCATION_WIDTH_PCT
    assert error == pytest.approx(0.025, abs=premium.TRUNCATION_WIDTH_PCT)


def test_the_effective_clamp_scales_with_the_multiplier():
    assert premium.effective_clamp_small_pct(0.05, 100) == 0.05
    assert premium.effective_clamp_small_pct(0.05, 50) == 0.025
    assert premium.effective_clamp_small_pct(0.05, 1) == pytest.approx(0.0005)


# --- the dead zone --------------------------------------------------------------------------

def test_a_baseline_hour_returns_an_interval_and_no_point():
    out = premium.lighter_premium(_rates([("BTC", 0, 0.0012)]), _params(CRYPTO))
    row = out.to_dicts()[0]
    assert row["premium_is_point_identified"] is False
    assert row["lighter_premium_point"] is None
    assert row["lighter_premium_lo"] * 100 == pytest.approx(-0.04, abs=1e-3)
    assert row["lighter_premium_hi"] * 100 == pytest.approx(0.06, abs=1e-3)


@pytest.mark.parametrize("p", [-0.0404, -0.04, -0.0001, 0.0, 0.03, 0.06, 0.0603])
def test_the_dead_zone_interval_contains_every_premium_that_settles_there(p):
    """The bound has to hold at the edges, including the truncation slack outside the band
    proper: a premium up to 8e-4 above `interest + clamp` still settles at the baseline rate."""
    out = _invert([p], CRYPTO)
    assert out["premium_is_point_identified"].item() is False
    assert out["lighter_premium_lo"].item() * 100 <= p <= out["lighter_premium_hi"].item() * 100


def test_the_baseline_is_matched_by_tolerance_not_by_float_equality():
    """The baseline computed from `0.01 / 8` lands one ulp above the double the venue's own
    `"0.0012"` parses to -- but only on a frame long enough to take polars' vectorised path,
    which is every real one. Exact equality then calls all 621,772 baseline hours off-baseline
    and invents a premium for each."""
    interest = pl.DataFrame({"i": [0.01] * 4})
    baseline = interest.select(lighter_formula.hourly_rate(
        pl.col("i"), pl.col("i"), 0.05, 4.0, 100)).to_series().to_list()
    assert baseline[0] != 0.0012
    assert abs(baseline[0] - 0.0012) < premium.BASELINE_TOLERANCE_PCT

    out = premium.lighter_premium(_rates([("BTC", i, 0.0012) for i in range(4)]), _params(CRYPTO))
    assert out["premium_is_point_identified"].to_list() == [False] * 4


def test_a_zero_base_rate_market_has_its_dead_zone_at_zero():
    out = premium.lighter_premium(_rates([("OPENAI", 0, 0.0)]), _params(NO_INTEREST))
    row = out.to_dicts()[0]
    assert row["premium_is_point_identified"] is False
    assert row["lighter_premium_lo"] * 100 == pytest.approx(-0.0005)


def test_exactly_one_of_point_and_interval_is_present_on_every_row():
    out = premium.lighter_premium(_rates([("BTC", 0, 0.0012), ("BTC", 1, 0.05), ("BTC", 2, -0.2)]),
                                  _params(CRYPTO))
    assert out["premium_is_point_identified"].to_list() == [False, True, True]
    assert out["lighter_premium_point"].is_null().to_list() == [True, False, False]
    assert out["lighter_premium_lo"].is_null().to_list() == [False, True, True]
    assert out["lighter_premium_hi"].is_null().to_list() == [False, True, True]


# --- what the inversion is, and is not --------------------------------------------------------

def test_the_inversion_is_a_monotone_relabelling_and_adds_no_information():
    """The redundancy, asserted rather than documented. `P` is strictly increasing in the
    settled rate across both branches, so it cannot change any rank -- which is why the Spearman
    correlation of the inverted premium against Hyperliquid's observed premium is identical to
    that of `signed_rate_fraction` itself (0.6233 on both, measured). Nothing here is recovered
    data: it is the rate on the premium's scale."""
    rates_pct = [-1.10, -0.20, -0.0405, -0.0001, 0.0001, 0.0011, 0.0012, 0.0013, 0.05, 0.30]
    out = premium.lighter_premium(
        _rates([("BTC", i, r) for i, r in enumerate(rates_pct)]), _params(CRYPTO))
    points = out.filter("premium_is_point_identified").sort("signed_rate_fraction")
    assert points.height == len(rates_pct) - 1  # every rate but the baseline 0.0012
    assert points["lighter_premium_point"].is_sorted(descending=False)
    assert points["lighter_premium_point"].diff().drop_nulls().min() > 0


def test_the_premium_is_emitted_on_the_common_basis_not_in_percent():
    """Both venues' premia land in the same frame, so both are per-hour signed fractions.
    Lighter's formula runs in percent; the emitted column does not, or the Phase 4 spread of the
    two premia is a 100x units bug of exactly the kind Task 1 exists to prevent."""
    out = premium.lighter_premium(_rates([("BTC", 0, 0.05)]), _params(CRYPTO))
    assert out["lighter_premium_point"].item() == pytest.approx(0.45 / 100)


# --- the guards -------------------------------------------------------------------------------

def test_an_unparameterised_market_raises_rather_than_borrowing_parameters():
    with pytest.raises(ValueError, match="TSLA"):
        premium.lighter_premium(_rates([("BTC", 0, 0.05), ("TSLA", 0, 0.05)]), _params(CRYPTO))


@pytest.mark.parametrize("column", ["base_interest_rate_pct", "funding_clamp_small_pct",
                                    "funding_premium_multiplier"])
def test_params_without_every_parameter_raise(column):
    with pytest.raises(ValueError, match=column):
        premium.lighter_premium(_rates([("BTC", 0, 0.05)]), _params(CRYPTO).drop(column))


def test_a_null_parameter_raises():
    params = _params(CRYPTO).with_columns(
        pl.lit(None, pl.Int64).alias("funding_premium_multiplier"))
    with pytest.raises(ValueError, match="funding_premium_multiplier"):
        premium.lighter_premium(_rates([("BTC", 0, 0.05)]), params)


def test_duplicate_parameter_rows_raise():
    with pytest.raises(ValueError, match="BTC"):
        premium.lighter_premium(_rates([("BTC", 0, 0.05)]), _params(CRYPTO, CRYPTO))


def test_lighters_raw_percent_rate_is_rejected():
    """The units bug Phase 1 named the most likely silent one in this phase, caught by Task 1's
    assert rather than inverted into a premium 100x too large."""
    raw = _rates([("BTC", i, 0.0012) for i in range(16)]).rename({"signed_rate_fraction": "rate"})
    raw = raw.with_columns(pl.col("rate") * 100)
    with pytest.raises(ValueError, match="lattice"):
        premium.lighter_premium(raw, _params(CRYPTO))


def test_a_frame_without_the_signed_fraction_raises():
    with pytest.raises(ValueError, match="signed_rate_fraction"):
        premium.lighter_premium(_rates([("BTC", 0, 0.05)]).rename(
            {"signed_rate_fraction": "spread"}), _params(CRYPTO))


def test_rates_carrying_their_own_parameter_columns_raise():
    rates = _rates([("BTC", 0, 0.05)]).with_columns(pl.lit(0.05).alias("funding_clamp_small_pct"))
    with pytest.raises(ValueError, match="funding_clamp_small_pct"):
        premium.lighter_premium(rates, _params(CRYPTO))


# --- Hyperliquid's observed premium -----------------------------------------------------------

def test_hl_premium_is_a_passthrough():
    df = pl.DataFrame({"coin": ["BTC"], "premium": [-0.000123]})
    assert premium.hl_premium(df)["hl_premium"].item() == -0.000123


def test_hl_premium_raises_on_a_missing_value():
    """Hyperliquid publishes the premium on 100% of rows. A null means the frame is wrong, not
    that the venue was quiet."""
    df = pl.DataFrame({"coin": ["BTC", "ETH"], "premium": [-0.000123, None]})
    with pytest.raises(ValueError, match="null"):
        premium.hl_premium(df)


def test_hl_premium_raises_when_the_column_is_absent():
    with pytest.raises(ValueError, match="premium"):
        premium.hl_premium(pl.DataFrame({"coin": ["BTC"]}))
