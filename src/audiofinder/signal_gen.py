"""Generates the reference chirp waveform used as the find-me signal."""

from __future__ import annotations

import numpy as np
from scipy.signal import chirp as scipy_chirp
from scipy.signal.windows import tukey

from .config import ChirpConfig


def generate_chirp(cfg: ChirpConfig = ChirpConfig()) -> np.ndarray:
    """Build a linear-sweep chirp as a float32 array in [-amplitude, amplitude].

    A Tukey (tapered cosine) window is applied so the sweep fades in/out
    smoothly instead of clicking at the edges, which would otherwise smear
    energy across the whole spectrum and hurt the matched filter.
    """
    n_samples = int(round(cfg.sample_rate * cfg.duration))
    t = np.linspace(0, cfg.duration, n_samples, endpoint=False)
    sweep = scipy_chirp(t, f0=cfg.f0, f1=cfg.f1, t1=cfg.duration, method="linear")

    alpha = min(max(cfg.fade_fraction * 2, 0.0), 1.0)
    window = tukey(n_samples, alpha=alpha)

    signal = (sweep * window * cfg.amplitude).astype(np.float32)
    return signal
