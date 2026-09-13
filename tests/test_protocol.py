import socket

from audiofinder import protocol


def test_message_builders():
    h = protocol.hello("mac-1", protocol.ROLE_LISTENER)
    assert h == {"type": "hello", "device_id": "mac-1", "role": "listener"}

    d = protocol.detection("mac-1", 123.456, 0.9)
    assert d["type"] == protocol.TYPE_DETECTION
    assert d["timestamp"] == 123.456
    assert d["score"] == 0.9

    e = protocol.emission("mac-2", 999.0)
    assert e == {"type": "emission", "device_id": "mac-2", "timestamp": 999.0}

    hb = protocol.heartbeat("mac-1", ntp_offset_ms=1.5)
    assert hb == {"type": "heartbeat", "device_id": "mac-1", "ntp_offset_ms": 1.5}

    c = protocol.command(protocol.COMMAND_PLAY_NOW)
    assert c == {"type": "command", "command": "play_now"}


def test_send_and_read_multiple_messages_over_socketpair():
    a, b = socket.socketpair()
    try:
        msgs = [
            protocol.hello("mac-1", protocol.ROLE_LISTENER),
            protocol.detection("mac-1", 1.0, 0.7),
            protocol.detection("mac-1", 2.0, 0.8),
        ]
        for m in msgs:
            protocol.send_message(a, m)
        a.close()  # trigger EOF on b's read side after the buffered data

        reader = protocol.MessageReader(b)
        received = list(reader)
        assert received == msgs
    finally:
        b.close()
