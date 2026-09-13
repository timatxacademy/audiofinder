"""TCP client used by listener/sender nodes to report to the coordinator.

Connecting to a coordinator is optional: if it's unreachable, listener and
sender nodes keep working standalone (printing to their own console) rather
than failing outright, and will retry the connection on the next message.

The connection is bidirectional: nodes normally only *send* reports, but a
node can also opt in to `start_command_listener` to receive commands the
coordinator pushes down (e.g. "play your chirp now", triggered from the web
dashboard).
"""

from __future__ import annotations

import socket
import sys
import threading
from typing import Any, Callable

from .protocol import MessageReader, hello, send_message


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

    def start_command_listener(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """Spawn a background thread that reads messages the coordinator
        sends down to this node (e.g. `{"type": "command", ...}`) and calls
        `callback` for each one. Call this again after every successful
        `connect()`/reconnect -- it listens on whichever socket is current
        at the moment it's called, not on future reconnects.
        """
        with self._lock:
            sock = self._sock
        if sock is None:
            return

        def _reader() -> None:
            reader = MessageReader(sock)
            try:
                for msg in reader:
                    callback(msg)
            except OSError:
                pass
            finally:
                with self._lock:
                    if self._sock is sock:
                        self.connected = False

        threading.Thread(target=_reader, daemon=True).start()

    def close(self) -> None:
        with self._lock:
            sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        self.connected = False
