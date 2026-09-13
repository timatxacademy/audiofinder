"""Shared configuration and defaults.

Sender and listener nodes must agree on the chirp shape (sample rate, start/end
frequency, duration) or the matched filter on the listener side won't recognize
what the sender plays. Keep the defaults here as the single source of truth;
CLI flags let you override them, but if you override on one node you must
override the same way on every node.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass


def default_device_id() -> str:
    """A reasonably stable, human-readable identifier for this machine."""
    return socket.gethostname().split(".")[0]


@dataclass(frozen=True)
class ChirpConfig:
    """Defines the audible sweep used as the "find me" signal.

    A linear sweep is easy to generate, easy to recognize by ear while
    debugging, and gives a matched filter a sharp, well-localized
    autocorrelation peak (much better for timing than a pure tone).
    """

    sample_rate: int = 48_000
    f0: float = 500.0       # sweep start frequency, Hz
    f1: float = 8_000.0     # sweep end frequency, Hz
    duration: float = 0.5   # seconds
    amplitude: float = 0.8  # peak amplitude, 0..1
    fade_fraction: float = 0.05  # fraction of duration used for fade in/out


@dataclass(frozen=True)
class NetworkConfig:
    """Coordinator TCP endpoint that listener/sender nodes report to."""

    host: str = "0.0.0.0"
    port: int = 8765


@dataclass(frozen=True)
class DetectorConfig:
    """Matched-filter detection tuning."""

    threshold: float = 0.5      # normalized correlation score, 0..1
    debounce_seconds: float = 1.0  # ignore repeat triggers within this window
