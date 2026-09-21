from fundr.recorder import records


def test_envelope_carries_provenance_and_extras():
    rec = records.envelope("lighter_state", "lighter", 7, {"a": 1}, t_ms=1000, mono_ns=5,
                           n_msgs=47, trigger="boundary")
    assert rec["feed"] == "lighter_state" and rec["venue"] == "lighter"
    assert rec["seq"] == 7 and rec["t_ms"] == 1000 and rec["mono_ns"] == 5
    assert rec["payload"] == {"a": 1}
    assert rec["n_msgs"] == 47 and rec["trigger"] == "boundary"
    assert rec["rec_ver"] and "git_sha" in rec
    assert rec["run_id"] == records.RUN_ID


def test_gap_record_has_seq_and_reason():
    rec = records.gap("hl_state", "hl", 3, t_ms=2000, mono_ns=9, from_ms=1000, to_ms=2000,
                      reason="watchdog_timeout")
    assert rec["type"] == "gap" and rec["seq"] == 3
    assert rec["from_ms"] == 1000 and rec["to_ms"] == 2000
    assert rec["reason"] == "watchdog_timeout"
    assert rec["run_id"] == records.RUN_ID
    assert "payload" not in rec


def test_seq_is_monotonic_per_feed():
    s = records.Seq("hl_state")
    assert [s.next(), s.next(), s.next()] == [1, 2, 3]


def test_seq_is_continuous_across_gap_records():
    """A gap record consumes a sequence number like any other, so an analyst reading a run
    can tell 'a record is missing' from 'the recorder itself declared a hole here'."""
    s = records.Seq("lighter_state")
    out = [
        records.envelope("lighter_state", "lighter", s.next(), {"i": 0}, t_ms=1, mono_ns=1),
        records.gap("lighter_state", "lighter", s.next(), t_ms=2, mono_ns=2, from_ms=1,
                    to_ms=2, reason="ws_error:ConnectionClosed"),
        records.envelope("lighter_state", "lighter", s.next(), {"i": 1}, t_ms=3, mono_ns=3),
    ]
    assert [r["seq"] for r in out] == [1, 2, 3]
    assert {r["run_id"] for r in out} == {records.RUN_ID}
