"""Gates in scripts/daily_check.py that must fire even when health.json LOOKS fine at a
glance: a frozen (stale) file from a dead recorder, and a fresh file whose coverage is still
all-null. These are the only defence the design has, since there is no alerting -- see the
module docstring."""
import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "daily_check.py"
_spec = importlib.util.spec_from_file_location("daily_check", SCRIPT)
daily_check = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = daily_check
_spec.loader.exec_module(daily_check)


def _health(status="ok", generated_ms=1_000_000, feeds=None):
    return {"status": status, "generated_ms": generated_ms, "window_ms": 3_600_000,
            "feeds": feeds if feeds is not None else {
                "hl_state": {"coverage": 1.0, "last_write_ms": generated_ms,
                             "reconnects_window": 0, "open_gap_from_ms": None},
            }}


def test_fresh_healthy_file_passes():
    now_ms = 1_000_000 + 30_000  # 30s after generated_ms -- well within the 5-minute budget
    exit_code, notes = daily_check.evaluate(_health(), now_ms=now_ms)
    assert exit_code == 0
    assert notes == []


def test_stale_generated_ms_fails():
    now_ms = 1_000_000 + daily_check.STALE_AFTER_S * 1000 + 1000  # just past the threshold
    exit_code, notes = daily_check.evaluate(_health(), now_ms=now_ms)
    assert exit_code != 0
    assert any("STALE" in n for n in notes)


def test_fresh_file_but_stale_boundary_is_exclusive():
    # exactly at the threshold should still be considered fresh (age_s > STALE_AFTER_S, not >=)
    now_ms = 1_000_000 + daily_check.STALE_AFTER_S * 1000
    exit_code, notes = daily_check.evaluate(_health(), now_ms=now_ms)
    assert exit_code == 0
    assert not any("STALE" in n for n in notes)


def test_all_null_coverage_fails_even_when_status_is_ok():
    health = _health(feeds={
        "hl_state": {"coverage": None, "last_write_ms": None, "reconnects_window": 0, "open_gap_from_ms": None},
        "lighter_state": {"coverage": None, "last_write_ms": None, "reconnects_window": 0, "open_gap_from_ms": None},
    })
    exit_code, notes = daily_check.evaluate(health, now_ms=1_030_000)
    assert exit_code != 0
    assert any("UNKNOWN" in n for n in notes)


def test_one_real_coverage_value_is_enough_to_pass_the_unknown_gate():
    health = _health(feeds={
        "hl_state": {"coverage": 1.0, "last_write_ms": 1_000_000, "reconnects_window": 0, "open_gap_from_ms": None},
        "lighter_state": {"coverage": None, "last_write_ms": None, "reconnects_window": 0, "open_gap_from_ms": None},
    })
    exit_code, notes = daily_check.evaluate(health, now_ms=1_030_000)
    assert exit_code == 0
    assert not any("UNKNOWN" in n for n in notes)


def test_broken_status_fails_independently_of_freshness():
    exit_code, notes = daily_check.evaluate(_health(status="broken"), now_ms=1_030_000)
    assert exit_code != 0
    assert any("NOT OK" in n for n in notes)


def test_resolve_key_path_absolute_missing_raises(tmp_path):
    missing = tmp_path / "nope.pem"
    try:
        daily_check.resolve_key_path(str(missing))
        assert False, "expected SystemExit"
    except SystemExit as e:
        assert e.code == 2


def test_resolve_key_path_finds_relative_key_in_cwd(tmp_path, monkeypatch):
    key = tmp_path / "auth" / "k.pem"
    key.parent.mkdir()
    key.write_text("fake key material")
    monkeypatch.chdir(tmp_path)
    found = daily_check.resolve_key_path("auth/k.pem")
    assert found == key
