"""The "listen" role: records audio, detects the reference chirp, reports it."""

from __future__ import annotations

import sys
import threading
import time

import numpy as np

from .audio_io import StreamRecorder
from .config import ChirpConfig, DetectorConfig, default_device_id
from .detector import ChirpDetector
from .node_client import CoordinatorClient
from .protocol import ROLE_LISTENER
from .protocol import detection as detection_msg
from .protocol import heartbeat as heartbeat_msg
from .signal_gen import generate_chirp
from .timesync import query_ntp_median


def _heartbeat_loop(
    stop: threading.Event,
    client: CoordinatorClient | None,
    device_id: str,
    ntp_server: str | None,
    interval: float,
) -> None:
    while not stop.is_set():
        offset_ms = None
        if ntp_server:
            try:
                result = query_ntp_median(ntp_server)
                offset_ms = result.offset * 1000.0
            except OSError as exc:
                print(f"[warn] NTP query to {ntp_server} failed: {exc}", file=sys.stderr)
        if client is not None:
            if not client.connected:
                client.connect()
            client.send(heartbeat_msg(device_id, ntp_offset_ms=offset_ms))
        stop.wait(interval)


def run_listener(
    device_id: str | None = None,
    input_device: int | str | None = None,
    coordinator: tuple[str, int] | None = None,
    chirp_cfg: ChirpConfig = ChirpConfig(),
    detector_cfg: DetectorConfig = DetectorConfig(),
    blocksize: int = 4096,
    ntp_server: str | None = None,
    heartbeat_interval: float = 10.0,
) -> None:
    device_id = device_id or default_device_id()
    template = generate_chirp(chirp_cfg)
    template_len = len(template)
    detector = ChirpDetector(
        template=template,
        sample_rate=chirp_cfg.sample_rate,
        threshold=detector_cfg.threshold,
        debounce_seconds=detector_cfg.debounce_seconds,
    )

    client = None
    if coordinator is not None:
        host, port = coordinator
        client = CoordinatorClient(host, port, device_id, ROLE_LISTENER)
        client.connect()

    stop = threading.Event()
    hb_thread = None
    if client is not None or ntp_server:
        hb_thread = threading.Thread(
            target=_heartbeat_loop,
            args=(stop, client, device_id, ntp_server, heartbeat_interval),
            daemon=True,
        )
        hb_thread.start()

    recorder = StreamRecorder(sample_rate=chirp_cfg.sample_rate, blocksize=blocksize, device=input_device)
    recorder.start()
    print(
        f"[{device_id}] listening for chirp "
        f"(f0={chirp_cfg.f0:.0f}Hz f1={chirp_cfg.f1:.0f}Hz dur={chirp_cfg.duration:.2f}s, "
        f"threshold={detector_cfg.threshold})... Ctrl-C to stop."
    )

    # Overlap-save buffering: keep the tail of the previous window (at least
    # template_len - 1 samples) so a chirp that straddles a chunk boundary
    # is still seen whole by at least one call to process_window.
    buffer = np.empty(0, dtype=np.float32)
    buffer_start_sample = 0

    try:
        while True:
            chunk = recorder.get(timeout=5.0)
            if buffer.size == 0:
                buffer_start_sample = chunk.start_sample
            buffer = np.concatenate([buffer, chunk.samples])

            detections = detector.process_window(buffer, buffer_start_sample)
            for det in detections:
                timestamp = recorder.sample_to_time(det.sample_index)
                print(f"[{device_id}] DETECTED chirp  t={timestamp:.4f}  score={det.score:.2f}")
                if client is not None:
                    if not client.connected:
                        client.connect()
                    client.send(detection_msg(device_id, timestamp, det.score))

            # Trim buffer, keeping only the tail needed for overlap next time.
            keep = min(template_len - 1, buffer.size)
            if keep < buffer.size:
                buffer_start_sample += buffer.size - keep
            buffer = buffer[buffer.size - keep :] if keep else np.empty(0, dtype=np.float32)
    except KeyboardInterrupt:
        print(f"\n[{device_id}] stopping listener...")
    finally:
        stop.set()
        recorder.stop()
        if client is not None:
            client.close()
