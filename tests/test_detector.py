import numpy as np

from audiofinder.config import ChirpConfig
from audiofinder.detector import ChirpDetector
from audiofinder.signal_gen import generate_chirp


def _make_detector(cfg, threshold=0.5, debounce_seconds=1.0):
    template = generate_chirp(cfg)
    detector = ChirpDetector(
        template=template,
        sample_rate=cfg.sample_rate,
        threshold=threshold,
        debounce_seconds=debounce_seconds,
    )
    return detector, template


def test_detects_chirp_at_known_offset():
    rng = np.random.default_rng(0)
    cfg = ChirpConfig(sample_rate=8000, f0=200.0, f1=1000.0, duration=0.2, amplitude=0.7)
    detector, template = _make_detector(cfg)

    silence_before = 4000
    silence_after = 4000
    noise = rng.normal(0, 0.01, silence_before + len(template) + silence_after).astype(np.float32)
    buffer = noise.copy()
    buffer[silence_before : silence_before + len(template)] += template

    detections = detector.process_window(buffer, window_start_sample=0)

    assert len(detections) == 1
    det = detections[0]
    # The matched filter peak should land right where the chirp starts,
    # within a couple of samples.
    assert abs(det.sample_index - silence_before) <= 2
    assert det.score > 0.8


def test_no_false_positive_on_pure_noise():
    rng = np.random.default_rng(1)
    cfg = ChirpConfig(sample_rate=8000, f0=200.0, f1=1000.0, duration=0.2)
    detector, template = _make_detector(cfg)

    noise = rng.normal(0, 0.05, 20_000).astype(np.float32)
    detections = detector.process_window(noise, window_start_sample=0)

    assert detections == []


def test_debounce_suppresses_duplicate_trigger_across_windows():
    cfg = ChirpConfig(sample_rate=8000, f0=200.0, f1=1000.0, duration=0.2, amplitude=0.7)
    detector, template = _make_detector(cfg, debounce_seconds=1.0)

    padding = 500
    buffer = np.zeros(padding + len(template) + padding, dtype=np.float32)
    buffer[padding : padding + len(template)] = template

    first = detector.process_window(buffer, window_start_sample=0)
    assert len(first) == 1

    # Feed an overlapping window containing the *same* chirp again (as would
    # happen with overlap-save buffering); it must not be reported twice
    # within the debounce window.
    second = detector.process_window(buffer, window_start_sample=10)
    assert second == []


def test_detection_beyond_debounce_window_is_reported():
    cfg = ChirpConfig(sample_rate=8000, f0=200.0, f1=1000.0, duration=0.2, amplitude=0.7)
    detector, template = _make_detector(cfg, debounce_seconds=0.1)

    gap_samples = int(0.5 * cfg.sample_rate)  # well beyond the 0.1s debounce
    padding = 500
    buffer = np.zeros(padding + len(template) + gap_samples + len(template) + padding, dtype=np.float32)
    buffer[padding : padding + len(template)] = template
    second_start = padding + len(template) + gap_samples
    buffer[second_start : second_start + len(template)] = template

    detections = detector.process_window(buffer, window_start_sample=0)

    assert len(detections) == 2
