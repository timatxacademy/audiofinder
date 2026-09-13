"""Tests for the SNTP offset/delay math, without touching the network or the
real `socket`/`time` modules (we patch only the names imported into
`audiofinder.timesync`, so nothing outside this test is affected)."""

import socket
import struct

import pytest

from audiofinder import timesync

_NTP_EPOCH_DELTA = 2_208_988_800


def _to_ntp_raw(t: float) -> int:
    seconds = int(t) + _NTP_EPOCH_DELTA
    fraction = int((t - int(t)) * (2**32))
    return (seconds << 32) | fraction


class _FakeSocket:
    def __init__(self, response: bytes):
        self._response = response

    def settimeout(self, timeout):
        pass

    def sendto(self, data, addr):
        pass

    def recvfrom(self, bufsize):
        return self._response, ("server", 123)

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class _FakeSocketModule:
    AF_INET = socket.AF_INET
    SOCK_DGRAM = socket.SOCK_DGRAM

    def __init__(self, fake_sock):
        self._fake_sock = fake_sock

    def socket(self, *args, **kwargs):
        return self._fake_sock


class _FakeTimeModule:
    def __init__(self, values):
        self._values = iter(values)

    def time(self):
        return next(self._values)


def _build_response(t2: float, t3: float) -> bytes:
    packet = bytearray(48)
    packet[0] = 0x1B
    struct.pack_into("!Q", packet, 32, _to_ntp_raw(t2))
    struct.pack_into("!Q", packet, 40, _to_ntp_raw(t3))
    return bytes(packet)


def test_query_ntp_offset_and_delay(monkeypatch):
    # Client sends at t1, server receives at t2 and transmits at t3, client
    # receives at t4.
    t1, t2, t3, t4 = 100.0, 105.0, 105.1, 100.3

    monkeypatch.setattr(timesync, "socket", _FakeSocketModule(_FakeSocket(_build_response(t2, t3))))
    monkeypatch.setattr(timesync, "time", _FakeTimeModule([t1, t4]))

    result = timesync.query_ntp("fake-server")

    expected_offset = ((t2 - t1) + (t3 - t4)) / 2.0
    expected_delay = (t4 - t1) - (t3 - t2)

    assert result.server == "fake-server"
    assert result.offset == pytest.approx(expected_offset, abs=1e-6)
    assert result.round_trip_delay == pytest.approx(expected_delay, abs=1e-6)


def test_query_ntp_short_response_raises(monkeypatch):
    monkeypatch.setattr(timesync, "socket", _FakeSocketModule(_FakeSocket(b"\x00" * 10)))
    monkeypatch.setattr(timesync, "time", _FakeTimeModule([0.0, 0.0]))

    with pytest.raises(OSError):
        timesync.query_ntp("fake-server")


def test_query_ntp_median_picks_sample_closest_to_median(monkeypatch):
    results = iter(
        [
            timesync.NtpResult(server="s", offset=0.010, round_trip_delay=0.001),
            timesync.NtpResult(server="s", offset=0.011, round_trip_delay=0.001),
            timesync.NtpResult(server="s", offset=0.500, round_trip_delay=0.001),  # outlier
        ]
    )
    monkeypatch.setattr(timesync, "query_ntp", lambda *a, **k: next(results))

    result = timesync.query_ntp_median("s", samples=3)

    assert result.offset in (0.010, 0.011)
