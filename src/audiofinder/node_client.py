"""TCP client used by listener/sender nodes to report to the coordinator.

Connecting to a coordinator is optional: if it's unreachable, listener and
sender nodes keep working standalone (printing to their own console) rather
than failing outright, and will retry the connection on the next message.
"""

from __future__ import annotations

import socket
import sys
import threading
from typing import Any

from .protocol import hello, send_message


class CoordinatorClient:
    def __init__(self, host: str, port: int, device_id: str, role: str) -> None:
        self.host = host
        self.port = port
        self.device_id = device_id
        self.role = role
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()
        self.connected = False

    def connect(self, timeout: float = 5.0) -> bool:
        try:
            sock = socket.create_connection((self.host, self.port), timeout=timeout)
            sock.settimeout(None)
            send_message(sock, hello(self.device_id, self.role))
        except OSError as exc:
            print(f"[warn] could not connect to coordinator {self.host}:{self.port}: {exc}", file=sys.stderr)
            self.connected = False
            return False
        with self._lock:
            self._sock = sock
        self.connected = True
        return True

    def send(self, message: dict[str, Any]) -> bool:
        with self._lock:
            sock = self._sock
        if not self.connected or sock is None:
            return False
        try:
            send_message(sock, message)
            return True
        except OSError as exc:
            print(f"[warn] lost connection to coordinator: {exc}", file=sys.stderr)
            self.connected = False
            return False

    def close(self) -> None:
        with self._lock:
            sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        self.connected = False
