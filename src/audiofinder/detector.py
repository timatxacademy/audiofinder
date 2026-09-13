"""Streaming matched-filter detector for the reference chirp.

The listener feeds successive, overlapping windows of recorded audio into
`ChirpDetector.process_window`. Internally this runs a normalized
cross-correlation (matched filter) against the reference chirp: the raw
correlation is divided by the local signal energy and the template energy so
the score is a value in roughly [-1, 1] regardless of input volume, similar
to a sliding Pearson correlation. A clean chirp arrival produces a sharp peak
near 1.0; a peak above `threshold` is reported as a detection.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.signal import correlate, find_peaks


@dataclass(frozen=True)
class Detection:
    """A single detected chirp arrival within one continuous audio stream."""

    sample_index: int  # global sample index (since stream start) of the peak
    score: float        # normalized correlation score, roughly 0..1


class ChirpDetector:
    def __init__(
        self,
        template: np.ndarray,
        sample_rate: int,
        threshold: float = 0.5,
        debounce_seconds: float = 1.0,
    ) -> None:
        self.template = np.asarray(template, dtype=np.float64)
        self.template_len = len(self.template)
        self.template_energy = float(np.sqrt(np.sum(self.template**2)))
        self.sample_rate = sample_rate
        self.threshold = threshold
        self.debounce_samples = int(debounce_seconds * sample_rate)
        # Global sample index of the last accepted trigger, used to suppress
        # duplicate detections of the same physical chirp across window
        # boundaries. Starts far enough negative that the very first
        # detection is never suppressed.
        self._last_trigger_sample = -(10**9)

    def process_window(self, samples: np.ndarray, window_start_sample: int) -> list[Detection]:
        """Scan one window of audio for chirp arrivals.

        Args:
            samples: 1-D array of audio samples for this window.
            window_start_sample: global sample index (since the recording
                stream started) that `samples[0]` corresponds to. Callers
                should overlap consecutive windows by at least the template
                length so a chirp straddling a window boundary is still seen
                whole at least once.

        Returns:
            Detections found in this window, already de-duplicated against
            previously accepted triggers via `debounce_seconds`.
        """
        x = np.asarray(samples, dtype=np.float64)
        if len(x) < self.template_len:
            return []

        raw = correlate(x, self.template, mode="valid")  # length len(x) - M + 1

        # Local energy of x under the template at each alignment, via a
        # cumulative-sum trick so we don't recompute the sum of squares from
        # scratch at every offset.
        squared = x**2
        cumulative = np.cumsum(np.insert(squared, 0, 0.0))
        local_energy = cumulative[self.template_len :] - cumulative[: -self.template_len]
        denom = np.sqrt(np.maximum(local_energy, 0.0)) * self.template_energy

        with np.errstate(divide="ignore", invalid="ignore"):
            score = np.where(denom > 1e-9, raw / denom, 0.0)

        peak_indices, _ = find_peaks(
            score, height=self.threshold, distance=max(self.template_len // 2, 1)
        )

        detections: list[Detection] = []
        for idx in peak_indices:
            global_sample = window_start_sample + int(idx)
            if global_sample - self._last_trigger_sample < self.debounce_samples:
                continue
            self._last_trigger_sample = global_sample
            detections.append(Detection(sample_index=global_sample, score=float(score[idx])))
        return detections


class StreamingChirpDetector:
    """Runs a `ChirpDetector` continuously over a live stream of audio
    chunks that arrive in arbitrary block sizes (as from `StreamRecorder`).

    Handles the overlap-save bookkeeping so a chirp that straddles a chunk
    boundary is still seen whole by at least one internal call to
    `process_window`: each call to `feed` appends the new chunk to a
    buffer, runs detection, then trims the buffer back down to just the
    tail (`template_len - 1` samples) needed for the next call.
    """

    def __init__(self, detector: ChirpDetector, template_len: int) -> None:
        self._detector = detector
        self._template_len = template_len
        self._buffer = np.empty(0, dtype=np.float64)
        self._buffer_start_sample = 0

    def feed(self, samples: np.ndarray, start_sample: int) -> list[Detection]:
        """Feed one chunk of audio (with its global start sample index) and
        return any detections found in it."""
        if self._buffer.size == 0:
            self._buffer_start_sample = start_sample
        self._buffer = np.concatenate([self._buffer, samples])

        detections = self._detector.process_window(self._buffer, self._buffer_start_sample)

        keep = min(self._template_len - 1, self._buffer.size)
        if keep < self._buffer.size:
            self._buffer_start_sample += self._buffer.size - keep
        self._buffer = self._buffer[self._buffer.size - keep :] if keep else np.empty(0, dtype=np.float64)

        return detections
