"""Newline-delimited JSON messages exchanged between nodes and the coordinator.

The wire format is deliberately simple: each message is one JSON object
followed by "\\n", sent over a plain TCP socket. Message "type" values:

    hello      - sent once when a node connects: who it is and its role
    heartbeat  - sent periodically: liveness + current NTP sync quality
    detection  - sent by a listener when it hears the chirp
    emission   - sent by a sender when it plays the chirp
    command    - sent by the coordinator DOWN to a node (e.g. from the web
                 dashboard) asking it to do something right now, such as
                 play its chirp on demand. Only a sender node running with
                 `--daemon` acts on these.

All timestamps are unix epoch seconds (float), computed on the sending
node's own clock -- see audio_io.py and timesync.py for how those are
estimated and how trustworthy they are.
"""

from __future__ import annotations

import json
import socket
from typing import Any, Iterator

TYPE_HELLO = "hello"
TYPE_HEARTBEAT = "heartbeat"
TYPE_DETECTION = "detection"
TYPE_EMISSION = "emission"
TYPE_COMMAND = "command"

ROLE_LISTENER = "listener"
ROLE_SENDER = "sender"

COMMAND_PLAY_NOW = "play_now"


def hello(device_id: str, role: str) -> dict[str, Any]:
    return {"type": TYPE_HELLO, "device_id": device_id, "role": role}


def heartbeat(device_id: str, ntp_offset_ms: float | None = None) -> dict[str, Any]:
    return {"type": TYPE_HEARTBEAT, "device_id": device_id, "ntp_offset_ms": ntp_offset_ms}


def detection(device_id: str, timestamp: float, score: float) -> dict[str, Any]:
    return {
        "type": TYPE_DETECTION,
        "device_id": device_id,
        "timestamp": timestamp,
        "score": score,
    }


def emission(device_id: str, timestamp: float) -> dict[str, Any]:
    return {"type": TYPE_EMISSION, "device_id": device_id, "timestamp": timestamp}


def command(command: str) -> dict[str, Any]:
    return {"type": TYPE_COMMAND, "command": command}


def send_message(sock: socket.socket, message: dict[str, Any]) -> None:
    line = json.dumps(message) + "\n"
    sock.sendall(line.encode("utf-8"))


class MessageReader:
    """Buffers bytes from a socket and yields decoded JSON messages, one per line."""

    def __init__(self, sock: socket.socket, chunk_size: int = 4096) -> None:
        self._sock = sock
        self._chunk_size = chunk_size
        self._buffer = b""

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return self

    def __next__(self) -> dict[str, Any]:
        while b"\n" not in self._buffer:
            chunk = self._sock.recv(self._chunk_size)
            if not chunk:
                if self._buffer.strip():
                    line, self._buffer = self._buffer, b""
                    return json.loads(line.decode("utf-8"))
                raise StopIteration
            self._buffer += chunk
        line, self._buffer = self._buffer.split(b"\n", 1)
        return json.loads(line.decode("utf-8"))
