"""sounddevice wrappers for playback and timestamped streaming capture.

Both `StreamRecorder` and `play_signal` try to estimate a *wall-clock* (unix
epoch, `time.time()`) timestamp for when audio actually left the speaker or
arrived at the microphone, not just when Python got around to processing a
callback. They do this using the `time_info` PortAudio gives every callback,
which reports ADC/DAC times on a steady clock -- but note that clock's
*origin is arbitrary* (on macOS Core Audio it's commonly seconds since boot,
not the unix epoch), so every conversion below anchors it to `time.time()`
via a *difference* of same-domain timestamps, never by treating a raw
PortAudio timestamp as if it were already wall-clock time. Getting this
backwards silently produces timestamps off by however long the machine has
been up, rather than a loud error -- see `StreamRecorder._callback` for the
one-line explanation of the right way round. This is what lets timestamps
computed independently on different Macs be meaningfully compared, *given*
that the Macs' wall clocks are themselves synchronized (see `timesync.py`
and the NTP assumption documented in the README).
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass

import numpy as np
import sounddevice as sd


def list_devices() -> sd.DeviceList:
    """Return the list of available audio devices (input and output)."""
    return sd.query_devices()


def format_devices() -> str:
    """Human-readable device table, same as `python -m sounddevice`."""
    return str(sd.query_devices())


@dataclass
class AudioChunk:
    """One block of captured audio with an estimated wall-clock timestamp."""

    samples: np.ndarray  # 1-D float32 mono samples
    start_sample: int    # global sample index (since stream start) of samples[0]
    start_time: float    # estimated unix timestamp of samples[0]


class StreamRecorder:
    """Continuously records mono audio, handing off timestamped chunks.

    Usage:
        rec = StreamRecorder(sample_rate=48000)
        rec.start()
        while running:
            chunk = rec.get(timeout=1.0)
            ...
        rec.stop()
    """

    # Number of leading callbacks used to estimate the wall-clock anchor
    # before settling on a stable value. A handful is enough to smooth out
    # scheduler jitter without delaying detection meaningfully.
    _CALIBRATION_CALLBACKS = 8

    def __init__(
        self,
        sample_rate: int,
        blocksize: int = 4096,
        device: int | str | None = None,
        channels: int = 1,
    ) -> None:
        self.sample_rate = sample_rate
        self.blocksize = blocksize
        self.device = device
        self.channels = channels

        self._queue: queue.Queue[AudioChunk] = queue.Queue()
        self._frames_seen = 0
        self._lock = threading.Lock()
        self._stream_start_time: float | None = None
        self._calibration_samples: list[float] = []
        self._stream: sd.InputStream | None = None

    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        now = time.time()
        adc_time = time_info.inputBufferAdcTime
        current_time = time_info.currentTime

        with self._lock:
            start_sample = self._frames_seen
            self._frames_seen += frames

            # PortAudio's stream clock (`currentTime` / `inputBufferAdcTime`)
            # has an arbitrary, implementation-defined origin -- on macOS
            # Core Audio it's typically seconds since boot, *not* the unix
            # epoch. `now` and `current_time` both mean "right now" in their
            # respective clocks, so `now - current_time` is the constant
            # offset that converts any timestamp in the PortAudio clock
            # (like `adc_time`, when this buffer was actually captured) into
            # a unix timestamp. Subtracting `adc_time` from `now` directly
            # would instead compute the wall-clock time at the PortAudio
            # clock's origin (e.g. system boot) -- wrong by however long the
            # clock has been running.
            wall_epoch_offset = now - current_time
            implied_start = wall_epoch_offset + adc_time - start_sample / self.sample_rate

            if self._stream_start_time is None:
                self._calibration_samples.append(implied_start)
                if len(self._calibration_samples) >= self._CALIBRATION_CALLBACKS:
                    self._stream_start_time = float(np.median(self._calibration_samples))
                anchor = self._stream_start_time if self._stream_start_time is not None else implied_start
            else:
                anchor = self._stream_start_time

        chunk_start_time = anchor + start_sample / self.sample_rate
        mono = indata[:, 0].copy() if indata.ndim > 1 else np.asarray(indata).copy()
        self._queue.put(
            AudioChunk(samples=mono, start_sample=start_sample, start_time=chunk_start_time)
        )

    def start(self) -> None:
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            blocksize=self.blocksize,
            device=self.device,
            channels=self.channels,
            dtype="float32",
            callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def get(self, timeout: float | None = None) -> AudioChunk:
        return self._queue.get(timeout=timeout)

    def sample_to_time(self, sample_index: int) -> float:
        """Convert a global sample index from this stream into a unix timestamp."""
        with self._lock:
            anchor = self._stream_start_time
        if anchor is None:
            # Not calibrated yet (stream just started); fall back to "now"
            # extrapolated backward, which is the best available guess.
            anchor = time.time() - sample_index / self.sample_rate
        return anchor + sample_index / self.sample_rate

    def __enter__(self) -> "StreamRecorder":
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()


def play_signal(
    signal: np.ndarray,
    sample_rate: int,
    device: int | str | None = None,
) -> float:
    """Play `signal` once (blocking) and return the estimated unix timestamp
    at which the first sample actually reached the output device (DAC)."""
    signal = np.asarray(signal, dtype=np.float32)
    n_total = len(signal)
    state: dict[str, float | int | None] = {"pos": 0, "start_time": None}

    def callback(outdata, frames, time_info, status) -> None:  # noqa: ANN001
        pos = state["pos"]
        assert isinstance(pos, int)
        chunk = signal[pos : pos + frames]
        outdata[: len(chunk), 0] = chunk
        if len(chunk) < frames:
            outdata[len(chunk) :, 0] = 0.0

        if state["start_time"] is None:
            now = time.time()
            future_offset = time_info.outputBufferDacTime - time_info.currentTime
            state["start_time"] = now + future_offset

        state["pos"] = pos + frames
        if state["pos"] >= n_total:
            raise sd.CallbackStop()

    stream = sd.OutputStream(
        samplerate=sample_rate,
        channels=1,
        dtype="float32",
        device=device,
        callback=callback,
    )
    with stream:
        while stream.active:
            time.sleep(0.005)
        # Give the last buffer time to actually drain out through the DAC.
        latency = stream.latency if isinstance(stream.latency, (int, float)) else 0.05
        time.sleep(latency + 0.05)

    start_time = state["start_time"]
    assert start_time is not None
    return float(start_time)
