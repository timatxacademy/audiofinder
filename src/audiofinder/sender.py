"""The "send" role: emits the reference chirp, optionally reporting it.

Normally a sender just plays the chirp `repeat` times and exits. With
`daemon=True` it instead stays connected to the coordinator afterward and
plays the chirp again whenever it receives a "play_now" command -- this is
what lets the web dashboard trigger a chirp on a specific Mac on demand.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from .audio_io import play_signal
from .config import ChirpConfig, default_device_id
from .node_client import CoordinatorClient
from .protocol import COMMAND_PLAY_NOW, ROLE_SENDER, TYPE_COMMAND
from .protocol import emission as emission_msg
from .signal_gen import generate_chirp


def run_sender(
    device_id: str | None = None,
    output_device: int | str | None = None,
    coordinator: tuple[str, int] | None = None,
    chirp_cfg: ChirpConfig = ChirpConfig(),
    repeat: int = 1,
    interval: float = 2.0,
    daemon: bool = False,
) -> None:
    device_id = device_id or default_device_id()
    signal = generate_chirp(chirp_cfg)

    if daemon and coordinator is None:
        raise SystemExit("--daemon requires --coordinator: it waits for play commands from there")

    client = None
    if coordinator is not None:
        host, port = coordinator
        client = CoordinatorClient(host, port, device_id, ROLE_SENDER)
        client.connect()

    play_lock = threading.Lock()

    def play_once(reason: str) -> None:
        with play_lock:
            start_time = play_signal(signal, chirp_cfg.sample_rate, device=output_device)
            print(f"[{device_id}] played chirp ({reason}) at t={start_time:.4f}")
            if client is not None:
                if not client.connected:
                    client.connect()
                client.send(emission_msg(device_id, start_time))

    def on_command(msg: dict[str, Any]) -> None:
        if msg.get("type") == TYPE_COMMAND and msg.get("command") == COMMAND_PLAY_NOW:
            play_once("remote command")

    print(
        f"[{device_id}] emitting chirp "
        f"(f0={chirp_cfg.f0:.0f}Hz f1={chirp_cfg.f1:.0f}Hz dur={chirp_cfg.duration:.2f}s) "
        f"x{repeat}"
    )
    try:
        for i in range(repeat):
            play_once(f"{i + 1}/{repeat}")
            if i < repeat - 1:
                time.sleep(interval)

        if daemon:
            assert client is not None
            client.start_command_listener(on_command)
            print(f"[{device_id}] daemon mode: waiting for remote play commands (Ctrl-C to stop)...")
            while True:
                if not client.connected:
                    if client.connect():
                        client.start_command_listener(on_command)
                time.sleep(1.0)
    except KeyboardInterrupt:
        print(f"\n[{device_id}] stopping sender...")
    finally:
        if client is not None:
            client.close()
