import pytest

from audiofinder.calibrate import summarize_delays


def test_summarize_delays_computes_median_min_max():
    # 20ms, 25ms, 30ms
    summary = summarize_delays(rounds_attempted=3, delays_seconds=[0.020, 0.025, 0.030])

    assert summary.rounds_attempted == 3
    assert summary.rounds_detected == 3
    assert summary.median_ms == pytest.approx(25.0)
    assert summary.min_ms == pytest.approx(20.0)
    assert summary.max_ms == pytest.approx(30.0)


def test_summarize_delays_handles_some_missed_rounds():
    summary = summarize_delays(rounds_attempted=5, delays_seconds=[0.026, 0.027])

    assert summary.rounds_attempted == 5
    assert summary.rounds_detected == 2
    assert summary.median_ms == pytest.approx(26.5)


def test_summarize_delays_with_no_detections():
    summary = summarize_delays(rounds_attempted=5, delays_seconds=[])

    assert summary.rounds_attempted == 5
    assert summary.rounds_detected == 0
