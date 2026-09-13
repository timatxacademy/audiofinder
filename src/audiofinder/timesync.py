"""A minimal SNTP client used to *report* clock-sync quality.

This project assumes the network already keeps every Mac's wall clock in
sync via NTP (macOS does this by default; see README for tightening it up,
e.g. with a local NTP server or `chrony`/`ptpd` for PTP). This module does
not discipline the system clock itself -- it just measures this machine's
offset and round-trip delay against a reference NTP server, so listeners and
the coordinator can log how trustworthy their timestamps currently are.

Implements the client half of SNTP (RFC 4330), which is sufficient for a
one-shot offset/delay measurement.
"""

from __future__ import annotations

import socket
import statistics
import struct
import time
from dataclasses import dataclass

# Seconds between the NTP epoch (1900-01-01) and the Unix epoch (1970-01-01).
_NTP_EPOCH_DELTA = 2_208_988_800


@dataclass(frozen=True)
class NtpResult:
    server: str
    offset: float             # seconds to add to the local clock to match the server
    round_trip_delay: float   # seconds


def _from_ntp_timestamp(raw: int) -> float:
    seconds = (raw >> 32) - _NTP_EPOCH_DELTA
    fraction = (raw & 0xFFFFFFFF) / 2**32
    return seconds + fraction


def query_ntp(server: str = "time.apple.com", port: int = 123, timeout: float = 2.0) -> NtpResult:
    """Perform one SNTP round-trip and estimate this machine's clock offset.

    Raises OSError/socket.timeout if the server is unreachable.
    """
    packet = bytearray(48)
    packet[0] = 0x1B  # LI=0 (no warning), VN=3, Mode=3 (client)

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        t1 = time.time()
        sock.sendto(bytes(packet), (server, port))
        data, _addr = sock.recvfrom(48)
        t4 = time.time()

    if len(data) < 48:
        raise OSError(f"NTP response from {server} too short ({len(data)} bytes)")

    recv_ts_raw, trans_ts_raw = struct.unpack("!QQ", data[32:48])
    t2 = _from_ntp_timestamp(recv_ts_raw)
    t3 = _from_ntp_timestamp(trans_ts_raw)

    offset = ((t2 - t1) + (t3 - t4)) / 2.0
    delay = (t4 - t1) - (t3 - t2)
    return NtpResult(server=server, offset=offset, round_trip_delay=delay)


def query_ntp_median(
    server: str = "time.apple.com",
    port: int = 123,
    timeout: float = 2.0,
    samples: int = 5,
) -> NtpResult:
    """Take several SNTP samples and return one with the median offset.

    A handful of samples and taking the median is a cheap way to reject the
    occasional outlier round trip without implementing full NTP clock
    filtering.
    """
    results = []
    for _ in range(max(samples, 1)):
        try:
            results.append(query_ntp(server, port, timeout))
        except OSError:
            continue
    if not results:
        raise OSError(f"no successful NTP responses from {server}")

    offsets = [r.offset for r in results]
    median_offset = statistics.median(offsets)
    # Report the sample whose offset is closest to the median, so delay is
    # reported alongside a real, consistent measurement.
    best = min(results, key=lambda r: abs(r.offset - median_offset))
    return best
