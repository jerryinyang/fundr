import json
from pathlib import Path

from fundr.recorder.config import Clock
from fundr.recorder.health import Health

HOUR = 3_600_000
MIN = 60_000
START = HOUR * 100


def _health(start_ms: int = START):
    """A clock the test moves forward by hand. `state["wall"]` is the only time source, and
    no test ever moves it backwards — `Health` clamps it anyway, but a test that rewound it
    would be measuring the clamp instead of the window."""
    state = {"wall": start_ms}
    return Health(Clock(now_ms=lambda: state["wall"], mono_ns=lambda: 0)), state


def _minutely(h, state, feed, counts: dict[str, int], n_minutes: int, *,
              per_hour: int = 60, every_min: int = 1):
    """Feed `counts[market]` records, one every `every_min` minutes, moving the clock as
    reality would: sixty records a minute apart take fifty-nine minutes, not zero."""
    for m in counts:
        h.expect(feed, m, per_hour)
    for i in range(n_minutes):
        if i:
            state["wall"] += every_min * MIN
        for m, n in counts.items():
            if i < n:
                h.record(feed, m, state["wall"])


def test_a_full_trailing_hour_of_full_coverage_is_ok():
    h, state = _health()
    _minutely(h, state, "hl_state", {"BTC": 60, "ETH": 60}, 60)   # 60 records over 59 min
    state["wall"] += 1_000
    assert h.status(state["wall"]) == "ok"


def test_ten_minutes_into_the_hour_with_ten_records_is_ok():
    """The regression test for the calendar-hour bug: 10 of 60 expected is 100% of the
    10 minutes that have actually elapsed, not 17% of an hour that has not."""
    h, state = _health()
    _minutely(h, state, "hl_state", {"BTC": 10}, 10)              # minutes 0..9
    state["wall"] = START + 10 * MIN
    assert h.status(state["wall"]) == "ok"


def test_a_market_seen_seconds_ago_is_not_judged_yet():
    h, state = _health()
    h.expect("lighter_state", "NEWCOIN", 60)          # just listed, nothing received yet
    state["wall"] = START + 30_000
    h.expect("lighter_state", "BTC", 60)
    h.record("lighter_state", "BTC", state["wall"])   # another market keeps the feed alive
    assert h.status(state["wall"]) == "ok"            # 30 s earns 0.5 expected: too young


def test_one_market_below_95_percent_is_degraded():
    h, state = _health()
    _minutely(h, state, "hl_state", {"BTC": 60, "ETH": 40}, 60)   # ETH silent after min 39
    state["wall"] += 1_000
    assert h.status(state["wall"]) == "degraded"


def test_feed_below_50_percent_is_broken():
    h, state = _health()
    _minutely(h, state, "lighter_state", {"BTC": 10}, 10, every_min=4)  # 10 over 36 min
    state["wall"] = START + 40 * MIN
    assert h.status(state["wall"]) == "broken"


def test_long_open_gap_is_broken_and_short_one_degraded():
    h, state = _health()
    _minutely(h, state, "hl_state", {"BTC": 60}, 60)
    state["wall"] += 1_000
    now = state["wall"]
    h.gap_open("hl_state", now - 10 * MIN)
    assert h.status(now) == "degraded"
    h.gap_open("hl_state", now - 40 * MIN)
    assert h.status(now) == "broken"


def test_a_gap_under_five_minutes_old_is_still_ok():
    """Sixth-round spec deviation fix: `status()` used to degrade on ANY open gap regardless of
    age, so a gap that opens and closes within seconds -- one bad poll cycle -- could still read
    `degraded` for a whole health-write interval, costing a day of the seven-day gate for a blip
    the spec explicitly calls `ok` ("no open gap older than 5 minutes")."""
    h, state = _health()
    _minutely(h, state, "hl_state", {"BTC": 60}, 60)
    state["wall"] += 1_000
    now = state["wall"]
    h.gap_open("hl_state", now - 30_000)   # 30s old: well under the 5-minute "ok" tolerance
    assert h.status(now) == "ok"
    h.gap_open("hl_state", now - 5 * MIN - 1_000)   # just over 5 minutes: no longer ok
    assert h.status(now) == "degraded"


def test_no_write_for_five_minutes_is_broken():
    h, state = _health()
    _minutely(h, state, "hl_state", {"BTC": 30}, 30)
    state["wall"] += 6 * MIN
    assert h.status(state["wall"]) == "broken"


def test_many_reconnects_drop_out_of_ok():
    h, state = _health()
    _minutely(h, state, "lighter_state", {"BTC": 60}, 60)
    state["wall"] += 1_000
    for _ in range(5):
        h.reconnect("lighter_state")
    assert h.status(state["wall"]) != "ok"


def test_counters_survive_an_hour_boundary_and_a_late_record():
    """Three feeds share one Health. The old calendar-hour roll cleared every counter when
    the clock crossed HH:00, and a record whose t_ms sat just before the boundary rolled it
    a second time. A trailing window must do neither."""
    h, state = _health(START + 50 * MIN)                     # 50 minutes into hour 100
    _minutely(h, state, "lighter_state", {"BTC": 20}, 20)    # crosses into hour 101
    state["wall"] += 1_000
    now = state["wall"]
    assert h.status(now) == "ok"
    before = h.snapshot(now)["feeds"]["lighter_state"]["markets"]["BTC"]["received_window"]
    h.record("lighter_state", "BTC", now - 2 * MIN)   # late arrival, timestamped in the past
    after = h.snapshot(now)["feeds"]["lighter_state"]["markets"]["BTC"]
    assert after["received_window"] == before + 1
    assert h.status(now) == "ok"


def test_expect_markets_replaces_the_active_set():
    h, state = _health()
    h.expect_markets("lighter_state", ["BTC", "ETH"], 60)
    assert set(h.snapshot(START)["feeds"]["lighter_state"]["markets"]) == {"BTC", "ETH"}
    h.expect_markets("lighter_state", ["BTC", "SOL"], 60)   # ETH delisted, SOL listed
    assert set(h.snapshot(START)["feeds"]["lighter_state"]["markets"]) == {"BTC", "SOL"}


def test_registered_cadence_feed_not_stale_at_its_own_period():
    """universe writes once every 300 s by design; a flat 300 s stale threshold would call
    it broken by construction, between every single sweep. Registering its cadence must
    give it headroom: not stale 301 s after its last write."""
    h, state = _health()
    h.expect("universe", "sweep", 12)
    h.set_cadence("universe", 300)
    h.record("universe", "sweep", state["wall"])
    state["wall"] += 301_000
    assert h.status(state["wall"]) == "ok"


def test_registered_cadence_feed_broken_after_three_missed_cycles():
    h, state = _health()
    h.expect("universe", "sweep", 12)
    h.set_cadence("universe", 300)
    h.record("universe", "sweep", state["wall"])
    state["wall"] += 3 * 300_000 + 1_000   # just past 3x cadence
    assert h.status(state["wall"]) == "broken"


def test_registered_cadence_feed_still_floors_at_five_minutes():
    """A 60 s-cadence feed's 3x-cadence threshold (180 s) is below the 300 s floor, so it
    must still go broken at the existing 300 s default, not sooner. `n=1` keeps coverage
    below MIN_JUDGEABLE for this short a window, so only staleness is under test."""
    h, state = _health()
    h.expect("hl_state", "BTC", 1)
    h.set_cadence("hl_state", 60)
    h.record("hl_state", "BTC", state["wall"])
    state["wall"] += 200_000               # past 3x cadence (180 s) but under the 300 s floor
    assert h.status(state["wall"]) == "ok"
    state["wall"] += 101_000               # now past the 300 s floor
    assert h.status(state["wall"]) == "broken"


def test_unregistered_feed_keeps_the_flat_five_minute_default():
    h, state = _health()
    _minutely(h, state, "hl_state", {"BTC": 30}, 30)
    state["wall"] += 6 * MIN
    assert h.status(state["wall"]) == "broken"


def test_snapshot_written_atomically(tmp_path: Path):
    h, state = _health()
    h.expect("hl_state", "BTC", 60)
    h.record("hl_state", "BTC", state["wall"])
    out = tmp_path / "health.json"
    h.write(out, state["wall"])
    body = json.loads(out.read_text())
    assert body["status"] in {"ok", "degraded", "broken"}
    assert body["feeds"]["hl_state"]["markets"]["BTC"]["received_window"] == 1
    assert body["window_ms"] == HOUR
    assert not list(tmp_path.glob("*.tmp"))
