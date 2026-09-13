import json
import socket

import pytest

from audiofinder.coordinator import CoordinatorState
from audiofinder.protocol import COMMAND_PLAY_NOW
from audiofinder.protocol import command as command_msg


def test_register_and_send_command_to_connected_node():
    state = CoordinatorState()
    server_side, client_side = socket.socketpair()
    try:
        state.register_socket("mac-1", server_side)
        assert state.nodes["mac-1"]["connected"] is True

        ok = state.send_command_to("mac-1", command_msg(COMMAND_PLAY_NOW))
        assert ok is True

        received = json.loads(client_side.recv(4096).decode("utf-8").strip())
        assert received == {"type": "command", "command": COMMAND_PLAY_NOW}
    finally:
        server_side.close()
        client_side.close()


def test_send_command_to_unknown_or_disconnected_node_returns_false():
    state = CoordinatorState()
    assert state.send_command_to("nobody", command_msg(COMMAND_PLAY_NOW)) is False

    server_side, client_side = socket.socketpair()
    try:
        state.register_socket("mac-1", server_side)
        state.unregister_socket("mac-1", server_side)
        assert state.nodes["mac-1"]["connected"] is False
        assert state.send_command_to("mac-1", command_msg(COMMAND_PLAY_NOW)) is False
    finally:
        server_side.close()
        client_side.close()


def test_record_detection_and_emission_populate_snapshot():
    state = CoordinatorState(group_window_seconds=5.0)
    state.record_emission("sender-1", timestamp=100.0)
    state.record_detection("listener-1", timestamp=100.05, score=0.9)

    snap = state.snapshot()
    assert snap["nodes"]["sender-1"]["role"] == "sender"
    assert snap["nodes"]["listener-1"]["role"] == "listener"

    types = [e["type"] for e in snap["recent_events"]]
    assert types == ["emission", "detection"]


def test_flush_idle_events_produces_event_summary():
    state = CoordinatorState(group_window_seconds=0.01)
    state.record_detection("listener-1", timestamp=1.0, score=0.9)
    state.record_detection("listener-2", timestamp=1.005, score=0.8)

    import time as time_mod

    time_mod.sleep(0.05)
    state.flush_idle_events()

    summaries = [e for e in state.snapshot()["recent_events"] if e["type"] == "event_summary"]
    assert len(summaries) == 1
    assert len(summaries[0]["detections"]) == 2


def test_detection_after_emission_gets_a_distance_estimate():
    state = CoordinatorState(speed_of_sound_m_s=343.0)
    state.record_emission("sender-1", timestamp=100.0)
    state.record_detection("listener-1", timestamp=100.1, score=0.9)  # 0.1s later

    snap = state.snapshot()
    detection = next(e for e in snap["recent_events"] if e["type"] == "detection")
    assert detection["distance_m"] == pytest.approx(34.3, abs=1e-6)


def test_detection_with_no_prior_emission_has_no_distance():
    state = CoordinatorState()
    state.record_detection("listener-1", timestamp=100.0, score=0.9)

    detection = state.snapshot()["recent_events"][0]
    assert detection["distance_m"] is None


def test_detection_far_after_a_stale_emission_is_not_matched():
    state = CoordinatorState()
    state.record_emission("sender-1", timestamp=0.0)
    state.record_detection("listener-1", timestamp=100.0, score=0.9)  # 100s later, not related

    detection = next(e for e in state.snapshot()["recent_events"] if e["type"] == "detection")
    assert detection["distance_m"] is None


def test_latency_offset_is_subtracted_before_computing_distance():
    state = CoordinatorState(speed_of_sound_m_s=343.0, latency_offset_seconds=0.05)
    state.record_emission("sender-1", timestamp=100.0)
    # 0.1s raw delay - 0.05s calibrated-out latency = 0.05s of real travel time.
    state.record_detection("listener-1", timestamp=100.1, score=0.9)

    detection = next(e for e in state.snapshot()["recent_events"] if e["type"] == "detection")
    assert detection["distance_m"] == pytest.approx(17.15, abs=1e-6)


def test_latency_offset_overcorrection_yields_no_estimate_instead_of_negative():
    state = CoordinatorState(latency_offset_seconds=1.0)
    state.record_emission("sender-1", timestamp=100.0)
    state.record_detection("listener-1", timestamp=100.1, score=0.9)  # would be -0.9s of "travel"

    detection = next(e for e in state.snapshot()["recent_events"] if e["type"] == "detection")
    assert detection["distance_m"] is None
