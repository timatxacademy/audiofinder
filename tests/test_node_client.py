import json
import socket
import threading
import time

from audiofinder.node_client import CoordinatorClient
from audiofinder.protocol import MessageReader


def _make_fake_coordinator():
    """A tiny one-shot TCP "coordinator" for testing CoordinatorClient against
    a real socket, without depending on the real CoordinatorServer."""
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.bind(("127.0.0.1", 0))
    server_sock.listen(1)
    host, port = server_sock.getsockname()
    return server_sock, host, port


def test_connect_sends_hello_and_send_delivers_messages():
    server_sock, host, port = _make_fake_coordinator()
    try:
        client = CoordinatorClient(host, port, "mac-1", "listener")
        assert client.connect() is True

        conn, _addr = server_sock.accept()
        reader = MessageReader(conn)
        hello_msg = next(reader)
        assert hello_msg == {"type": "hello", "device_id": "mac-1", "role": "listener"}

        assert client.send({"type": "heartbeat", "device_id": "mac-1"}) is True
        hb = next(reader)
        assert hb == {"type": "heartbeat", "device_id": "mac-1"}

        client.close()
        conn.close()
    finally:
        server_sock.close()


def test_start_command_listener_invokes_callback_for_incoming_commands():
    server_sock, host, port = _make_fake_coordinator()
    try:
        client = CoordinatorClient(host, port, "mac-1", "sender")
        assert client.connect() is True
        conn, _addr = server_sock.accept()

        received = []
        event = threading.Event()

        def on_command(msg):
            received.append(msg)
            event.set()

        client.start_command_listener(on_command)

        conn.sendall((json.dumps({"type": "command", "command": "play_now"}) + "\n").encode("utf-8"))
        assert event.wait(timeout=2.0)
        assert received == [{"type": "command", "command": "play_now"}]

        client.close()
        conn.close()
    finally:
        server_sock.close()


def test_connect_failure_is_reported_not_raised():
    # Nothing listens on this port.
    client = CoordinatorClient("127.0.0.1", 1, "mac-1", "listener")
    assert client.connect(timeout=0.5) is False
    assert client.connected is False
