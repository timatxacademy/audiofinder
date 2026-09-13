"""`audiofinder calibrate`: measure the fixed, non-acoustic delay between
playing the chirp and detecting it, so it can be canceled out via the
coordinator's `--latency-offset-ms`.

Plays the chirp out of a speaker and listens for it on a microphone on
*this* machine, repeating a few times and reporting the median delay. This
is a same-machine loopback test, not a substitute for calibrating with the
actual sender/listener hardware you deploy -- see the README's "Distance
estimate" section -- but it's a quick way to get a real, measured starting
point rather than guessing, and a same-model Mac's own speaker/mic pipeline
latency is often a reasonable proxy for what a similar machine will show.
"""

from __future__ import annotations

import queue
import statistics
import threading
import time
from dataclasses import dataclass

from .audio_io import StreamRecorder, play_signal
from .config import ChirpConfig, DetectorConfig
from .detector import ChirpDetector, StreamingChirpDetector
from .signal_gen import generate_chirp


@dataclass(frozen=True)
class CalibrationSummary:
    rounds_attempted: int
    delays_seconds: list[float]  # one entry per round that was successfully detected

    @property
    def rounds_detected(self) -> int:
        return len(self.delays_seconds)

    @property
    def median_ms(self) -> float:
        return statistics.median(self.delays_seconds) * 1000.0

    @property
    def min_ms(self) -> float:
        return min(self.delays_seconds) * 1000.0

    @property
    def max_ms(self) -> float:
        return max(self.delays_seconds) * 1000.0


def summarize_delays(rounds_attempted: int, delays_seconds: list[float]) -> CalibrationSummary:
    return CalibrationSummary(rounds_attempted=rounds_attempted, delays_seconds=delays_seconds)


def _detector_feed_loop(
    recorder: StreamRecorder,
    streaming: StreamingChirpDetector,
    stop: threading.Event,
    out_queue: "queue.Queue[tuple[float, float]]",
) -> None:
    while not stop.is_set():
        try:
            chunk = recorder.get(timeout=0.5)
        except queue.Empty:
            continue
        for det in streaming.feed(chunk.samples, chunk.start_sample):
            out_queue.put((recorder.sample_to_time(det.sample_index), det.score))


def run_local_calibration(
    input_device: int | str | None,
    output_device: int | str | None,
    chirp_cfg: ChirpConfig = ChirpConfig(),
    detector_cfg: DetectorConfig = DetectorConfig(),
    rounds: int = 5,
    interval: float = 1.5,
    blocksize: int = 4096,
    detection_timeout: float = 3.0,
) -> CalibrationSummary:
    """Play the chirp `rounds` times on `output_device` and listen for it on
    `input_device`, both on this machine, printing progress as it goes.
    """
    template = generate_chirp(chirp_cfg)
    detector = ChirpDetector(
        template=template,
        sample_rate=chirp_cfg.sample_rate,
        threshold=detector_cfg.threshold,
        # A short debounce is fine here: only one chirp plays at a time,
        # spaced `interval` seconds apart, so there's no risk of the same
        # physical chirp being double-counted the way a continuous listener
        # needs to guard against.
        debounce_seconds=0.1,
    )
    streaming = StreamingChirpDetector(detector, len(template))

    recorder = StreamRecorder(sample_rate=chirp_cfg.sample_rate, blocksize=blocksize, device=input_device)
    detections: "queue.Queue[tuple[float, float]]" = queue.Queue()
    stop = threading.Event()

    recorder.start()
    feed_thread = threading.Thread(
        target=_detector_feed_loop, args=(recorder, streaming, stop, detections), daemon=True
    )
    feed_thread.start()

    delays: list[float] = []
    try:
        time.sleep(0.5)  # let the recorder's wall-clock calibration settle first
        for i in range(rounds):
            while not detections.empty():  # drop anything stale from a previous round
                detections.get_nowait()

            emission_time: float | None = None
            got_start = threading.Event()

            def on_start(t: float) -> None:
                nonlocal emission_time
                emission_time = t
                got_start.set()

            play_signal(template, chirp_cfg.sample_rate, device=output_device, on_start=on_start)
            if not got_start.wait(timeout=1.0) or emission_time is None:
                print(f"round {i + 1}/{rounds}: couldn't determine playback time, skipping")
                continue

            try:
                detection_time, score = detections.get(timeout=detection_timeout)
            except queue.Empty:
                print(f"round {i + 1}/{rounds}: no detection within {detection_timeout:.1f}s (missed)")
                continue

            delay_ms = (detection_time - emission_time) * 1000.0
            print(f"round {i + 1}/{rounds}: delay={delay_ms:.2f} ms  score={score:.2f}")
            delays.append(detection_time - emission_time)

            if i < rounds - 1:
                time.sleep(interval)
    finally:
        stop.set()
        recorder.stop()
        feed_thread.join(timeout=2.0)

    return summarize_delays(rounds, delays)
