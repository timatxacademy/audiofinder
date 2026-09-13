import numpy as np

from audiofinder.config import ChirpConfig
from audiofinder.signal_gen import generate_chirp


def test_generate_chirp_shape_and_length():
    cfg = ChirpConfig(sample_rate=8000, f0=200.0, f1=1000.0, duration=0.25, amplitude=0.5)
    sig = generate_chirp(cfg)

    assert sig.dtype == np.float32
    assert len(sig) == int(cfg.sample_rate * cfg.duration)
    assert np.max(np.abs(sig)) <= cfg.amplitude + 1e-6


def test_generate_chirp_fades_edges_toward_zero():
    cfg = ChirpConfig(sample_rate=8000, f0=200.0, f1=1000.0, duration=0.25, amplitude=1.0)
    sig = generate_chirp(cfg)

    # The Tukey window should taper the very first/last samples close to
    # zero, unlike an un-windowed sweep which starts at sin(0)=0 anyway but
    # would otherwise click without a taper mid-window.
    assert abs(sig[0]) < 0.05
    assert abs(sig[-1]) < 0.05


def test_generate_chirp_is_deterministic():
    cfg = ChirpConfig()
    a = generate_chirp(cfg)
    b = generate_chirp(cfg)
    np.testing.assert_array_equal(a, b)
