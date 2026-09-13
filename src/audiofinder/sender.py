"""The "send" role: emits the reference chirp, optionally reporting it."""

from __future__ import annotations

import time

from .audio_io import play_signal
from .config import ChirpConfig, default_device_id
from .node_client import CoordinatorClient
from .protocol import ROLE_SENDER
from .protocol import emission as emission_msg
from .signal_gen import generate_chirp


def run_sender(
    device_id: str | None = None,
    output_device: int | str | None = None,
    coordinator: tuple[str, int] | None = None,
    chirp_cfg: ChirpConfig = ChirpConfig(),
    repeat: int = 1,
    interval: float = 2.0,
) -> None:
    device_id = device_id or default_device_id()
    signal = generate_chirp(chirp_cfg)

    client = None
    if coordinator is not None:
        host, port = coordinator
        client = CoordinatorClient(host, port, device_id, ROLE_SENDER)
        client.connect()

    print(
        f"[{device_id}] emitting chirp "
        f"(f0={chirp_cfg.f0:.0f}Hz f1={chirp_cfg.f1:.0f}Hz dur={chirp_cfg.duration:.2f}s) "
        f"x{repeat}"
    )
    try:
        for i in range(repeat):
            start_time = play_signal(signal, chirp_cfg.sample_rate, device=output_device)
            print(f"[{device_id}] played chirp {i + 1}/{repeat} at t={start_time:.4f}")
            if client is not None:
                if not client.connected:
                    client.connect()
                client.send(emission_msg(device_id, start_time))
            if i < repeat - 1:
                time.sleep(interval)
    finally:
        if client is not None:
            client.close()
