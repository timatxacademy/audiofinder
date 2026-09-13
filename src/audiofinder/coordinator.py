"""TCP server that collects detections/emissions from listener and sender nodes.

v1 scope: the coordinator does not compute a position. It collects
timestamped detections from every listener, groups detections that likely
came from the same physical chirp (arrival times within `group_window`
seconds of each other), and prints/logs a summary showing each listener's
arrival time relative to the earliest one in the group. That per-listener
time-of-arrival data is exactly what a future TDOA localization step would
consume -- this version just stops short of turning it into a position.

The coordinator also optionally serves a small web dashboard (see web.py)
for viewing this state and remotely triggering a connected sender's chirp,
which is why it keeps each node's live socket around, not just its last
known status.

It also gives a rough distance estimate: given synchronized clocks, the
time between a sender's emission timestamp and a listener's detection
timestamp is (to first order) how long the sound took to travel between
them, so distance ~= speed_of_sound * that delta. This is a rough estimate,
not a calibrated measurement -- see `--latency-offset-ms` below and the
README's "Known limitations" for why.
"""

from __future__ import annotations

import json
import socket
import socketserver
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from .protocol import (
    TYPE_DETECTION,
    TYPE_EMISSION,
    TYPE_HEARTBEAT,
    TYPE_HELLO,
    MessageReader,
    send_message,
)

# Speed of sound in dry air at ~20degC. Varies with temperature/humidity
# (roughly +0.6 m/s per degC), so treat distance estimates as order-of-
# magnitude unless this is tuned for actual conditions via --speed-of-sound.
DEFAULT_SPEED_OF_SOUND_M_S = 343.0

# How far back an emission can be and still be considered the cause of a
# later detection. Generous on purpose (2s of "flight time" is ~686m, far
# more than any real room/building), so this only exists to reject matching
# against a stale, unrelated emission -- not to model real acoustic range.
DEFAULT_MAX_TRAVEL_SECONDS = 2.0


@dataclass
class DetectionEvent:
    """A cluster of detections believed to come from one chirp playback."""

    last_activity: float
    detections: list[dict[str, Any]] = field(default_factory=list)

    def earliest_timestamp(self) -> float:
        return min(d["timestamp"] for d in self.detections)


class CoordinatorState:
    def __init__(
        self,
        group_window_seconds: float = 2.0,
        log_path: str | None = None,
        history_size: int = 300,
        speed_of_sound_m_s: float = DEFAULT_SPEED_OF_SOUND_M_S,
        latency_offset_seconds: float = 0.0,
    ) -> None:
        self._lock = threading.Lock()
        self.nodes: dict[str, dict[str, Any]] = {}
        self._sockets: dict[str, socket.socket] = {}
        self._open_events: list[DetectionEvent] = []
        self.group_window_seconds = group_window_seconds
        self.speed_of_sound_m_s = speed_of_sound_m_s
        # A fixed bias (measured empirically, e.g. with sender and listener
        # right next to each other) subtracted from every emission->detection
        # delta before converting to distance. Real device/OS/network latency
        # dominates that delta at close range -- see README -- and doesn't
        # scale with distance the way travel time does, so it has to be
        # calibrated out rather than estimated from physics.
        self.latency_offset_seconds = latency_offset_seconds
        self._log_file = open(log_path, "a", encoding="utf-8") if log_path else None
        # Bounded in-memory history of everything logged, for the web
        # dashboard's live feed -- separate from the (optionally unbounded)
        # JSONL file, which is for offline analysis.
        self.recent_events: deque[dict[str, Any]] = deque(maxlen=history_size)
        # Recent emissions, kept around just long enough to pair a detection
        # with whichever emission plausibly caused it.
        self._recent_emissions: deque[dict[str, Any]] = deque(maxlen=20)

    def _log(self, record: dict[str, Any]) -> None:
        record = {"logged_at": time.time(), **record}
        with self._lock:
            self.recent_events.append(record)
        if self._log_file is not None:
            self._log_file.write(json.dumps(record) + "\n")
            self._log_file.flush()

    def update_node(self, device_id: str, **fields: Any) -> None:
        with self._lock:
            entry = self.nodes.setdefault(
                device_id, {"first_seen": time.time(), "connected": False}
            )
            entry["last_seen"] = time.time()
            entry.update({k: v for k, v in fields.items() if v is not None})

    def register_socket(self, device_id: str, sock: socket.socket) -> None:
        with self._lock:
            self._sockets[device_id] = sock
            entry = self.nodes.setdefault(
                device_id, {"first_seen": time.time(), "connected": False}
            )
            entry["connected"] = True
            entry["last_seen"] = time.time()

    def unregister_socket(self, device_id: str, sock: socket.socket) -> None:
        with self._lock:
            if self._sockets.get(device_id) is sock:
                del self._sockets[device_id]
                if device_id in self.nodes:
                    self.nodes[device_id]["connected"] = False

    def send_command_to(self, device_id: str, message: dict[str, Any]) -> bool:
        """Push a message down to a specific connected node. Returns False
        if that node isn't currently connected or the send failed."""
        with self._lock:
            sock = self._sockets.get(device_id)
        if sock is None:
            return False
        try:
            send_message(sock, message)
            return True
        except OSError:
            return False

    def _find_matching_emission(self, detection_timestamp: float) -> dict[str, Any] | None:
        """The most recent emission that could plausibly have caused a
        detection at `detection_timestamp`, or None if there isn't one."""
        with self._lock:
            candidates = [e for e in self._recent_emissions if e["timestamp"] <= detection_timestamp]
        if not candidates:
            return None
        best = max(candidates, key=lambda e: e["timestamp"])
        if detection_timestamp - best["timestamp"] > DEFAULT_MAX_TRAVEL_SECONDS:
            return None
        return best

    def _estimate_distance_m(self, emission_timestamp: float, detection_timestamp: float) -> float | None:
        travel_seconds = (detection_timestamp - emission_timestamp) - self.latency_offset_seconds
        if travel_seconds < 0:
            # The latency offset overcorrected -- likely means this pair is
            # closer than the calibration sample was, or the offset is a bit
            # too high. Report "no estimate" rather than a nonsense negative
            # distance.
            return None
        return travel_seconds * self.speed_of_sound_m_s

    def record_detection(self, device_id: str, timestamp: float, score: float) -> None:
        self.update_node(device_id, role="listener")

        emission = self._find_matching_emission(timestamp)
        distance_m = (
            self._estimate_distance_m(emission["timestamp"], timestamp) if emission is not None else None
        )

        detection_record = {
            "device_id": device_id,
            "timestamp": timestamp,
            "score": score,
            "distance_m": distance_m,
        }
        with self._lock:
            target = None
            for ev in self._open_events:
                if abs(timestamp - ev.earliest_timestamp()) <= self.group_window_seconds:
                    target = ev
                    break
            if target is None:
                target = DetectionEvent(last_activity=time.time())
                self._open_events.append(target)
            target.detections.append(detection_record)
            target.last_activity = time.time()
        self._log({"type": "detection", **detection_record})
        dist_str = f"  ~{distance_m:.1f} m" if distance_m is not None else ""
        print(f"[detection] {device_id:<20} t={timestamp:.4f}  score={score:.2f}{dist_str}")

    def record_emission(self, device_id: str, timestamp: float) -> None:
        self.update_node(device_id, role="sender")
        with self._lock:
            self._recent_emissions.append({"device_id": device_id, "timestamp": timestamp})
        self._log({"type": "emission", "device_id": device_id, "timestamp": timestamp})
        print(f"[emission]  {device_id:<20} t={timestamp:.4f}")

    def flush_idle_events(self) -> None:
        """Finalize and print any event that has had no new detections for
        `group_window_seconds`."""
        now = time.time()
        finished = []
        with self._lock:
            remaining = []
            for ev in self._open_events:
                if now - ev.last_activity >= self.group_window_seconds:
                    finished.append(ev)
                else:
                    remaining.append(ev)
            self._open_events = remaining
        for ev in finished:
            self._print_event_summary(ev)

    def _print_event_summary(self, ev: DetectionEvent) -> None:
        dets = sorted(ev.detections, key=lambda d: d["timestamp"])
        earliest = dets[0]["timestamp"]
        print(f"=== event: chirp heard by {len(dets)} listener(s) ===")
        for d in dets:
            delta_ms = (d["timestamp"] - earliest) * 1000.0
            dist_str = f"  ~{d['distance_m']:.1f} m" if d.get("distance_m") is not None else ""
            print(
                f"    {d['device_id']:<20} t={d['timestamp']:.4f}  "
                f"+{delta_ms:8.2f} ms  score={d['score']:.2f}{dist_str}"
            )
        self._log({"type": "event_summary", "detections": dets})

    def snapshot(self) -> dict[str, Any]:
        """A JSON-serializable view of everything the web dashboard shows."""
        with self._lock:
            nodes = {device_id: dict(fields) for device_id, fields in self.nodes.items()}
            recent_events = list(self.recent_events)
        return {
            "server_time": time.time(),
            "group_window_seconds": self.group_window_seconds,
            "nodes": nodes,
            "recent_events": recent_events,
        }


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        state: CoordinatorState = self.server.state  # type: ignore[attr-defined]
        reader = MessageReader(self.connection)
        peer = self.client_address
        device_id = None
        try:
            for msg in reader:
                mtype = msg.get("type")
                if mtype == TYPE_HELLO:
                    device_id = msg["device_id"]
                    state.update_node(device_id, role=msg.get("role"))
                    state.register_socket(device_id, self.connection)
                    print(f"[hello]     {device_id} connected as {msg.get('role')} from {peer[0]}")
                elif mtype == TYPE_HEARTBEAT:
                    state.update_node(msg["device_id"], ntp_offset_ms=msg.get("ntp_offset_ms"))
                elif mtype == TYPE_DETECTION:
                    state.record_detection(msg["device_id"], msg["timestamp"], msg["score"])
                elif mtype == TYPE_EMISSION:
                    state.record_emission(msg["device_id"], msg["timestamp"])
                else:
                    print(f"[warn] unknown message type {mtype!r} from {peer}", file=sys.stderr)
        except (ConnectionResetError, OSError, json.JSONDecodeError, KeyError) as exc:
            print(f"[warn] connection from {peer} ended: {exc}", file=sys.stderr)
        finally:
            if device_id:
                state.unregister_socket(device_id, self.connection)
                print(f"[bye]       {device_id} disconnected")


class CoordinatorServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, host: str, port: int, state: CoordinatorState) -> None:
        self.state = state
        super().__init__((host, port), _Handler)


def run_coordinator(
    host: str = "0.0.0.0",
    port: int = 8765,
    group_window_seconds: float = 2.0,
    log_path: str | None = None,
    web_port: int | None = 8766,
    speed_of_sound_m_s: float = DEFAULT_SPEED_OF_SOUND_M_S,
    latency_offset_seconds: float = 0.0,
) -> None:
    state = CoordinatorState(
        group_window_seconds=group_window_seconds,
        log_path=log_path,
        speed_of_sound_m_s=speed_of_sound_m_s,
        latency_offset_seconds=latency_offset_seconds,
    )
    server = CoordinatorServer(host, port, state)

    stop = threading.Event()

    def idle_flusher() -> None:
        while not stop.is_set():
            time.sleep(0.5)
            state.flush_idle_events()

    threading.Thread(target=idle_flusher, daemon=True).start()

    if web_port is not None:
        from .web import run_web_server

        threading.Thread(
            target=run_web_server, args=(state, host, web_port), daemon=True
        ).start()
        display_host = "localhost" if host in ("0.0.0.0", "") else host
        print(f"audiofinder web dashboard on http://{display_host}:{web_port}")

    print(f"audiofinder coordinator listening on {host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down coordinator...")
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
