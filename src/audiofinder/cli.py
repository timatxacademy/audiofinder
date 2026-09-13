"""`audiofinder` command-line entry point."""

from __future__ import annotations

import click

from . import audio_io
from .config import ChirpConfig, DetectorConfig, default_device_id
from .coordinator import run_coordinator
from .listener import run_listener
from .sender import run_sender
from .timesync import query_ntp_median

_DEFAULT_CHIRP = ChirpConfig()
_DEFAULT_DETECTOR = DetectorConfig()


def _parse_device(value: str | None) -> int | str | None:
    """Accept either a sounddevice index (int) or a name substring (str)."""
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return value


def _parse_coordinator(value: str | None) -> tuple[str, int] | None:
    if not value:
        return None
    host, sep, port = value.rpartition(":")
    if not sep:
        raise click.BadParameter("expected HOST:PORT")
    return host, int(port)


def _chirp_options(f):
    f = click.option(
        "--sample-rate", type=int, default=_DEFAULT_CHIRP.sample_rate, show_default=True,
        help="Audio sample rate in Hz. Must match across all nodes.",
    )(f)
    f = click.option(
        "--f0", type=float, default=_DEFAULT_CHIRP.f0, show_default=True,
        help="Chirp sweep start frequency, Hz. Must match across all nodes.",
    )(f)
    f = click.option(
        "--f1", type=float, default=_DEFAULT_CHIRP.f1, show_default=True,
        help="Chirp sweep end frequency, Hz. Must match across all nodes.",
    )(f)
    f = click.option(
        "--duration", type=float, default=_DEFAULT_CHIRP.duration, show_default=True,
        help="Chirp duration, seconds. Must match across all nodes.",
    )(f)
    return f


def _coordinator_option(f):
    return click.option(
        "--coordinator", "coordinator", default=None, metavar="HOST:PORT",
        help="Coordinator address to report to. Omit to run standalone.",
    )(f)


@click.group()
@click.version_option()
def main() -> None:
    """audiofinder: find networked devices by emitting/listening for an audio chirp."""


@main.command("devices")
def devices_cmd() -> None:
    """List available audio input/output devices."""
    click.echo(audio_io.format_devices())


@main.command("check-sync")
@click.option("--server", default="time.apple.com", show_default=True, help="NTP server to query.")
@click.option("--samples", default=5, show_default=True, help="Number of SNTP samples to take.")
def check_sync_cmd(server: str, samples: int) -> None:
    """Query an NTP server and report this machine's clock offset/delay."""
    result = query_ntp_median(server, samples=samples)
    click.echo(f"server:            {result.server}")
    click.echo(f"clock offset:      {result.offset * 1000:+.3f} ms")
    click.echo(f"round-trip delay:  {result.round_trip_delay * 1000:.3f} ms")
    if abs(result.offset) > 0.01:
        click.echo(
            "warning: offset exceeds 10ms; cross-device detection timing will be "
            "less reliable until clocks are better synchronized.",
            err=True,
        )


@main.command("listen")
@click.option("--device-id", default=None, help="This node's identifier. Defaults to hostname.")
@click.option("--input-device", default=None, help="Input device index or name substring.")
@_coordinator_option
@_chirp_options
@click.option("--threshold", type=float, default=_DEFAULT_DETECTOR.threshold, show_default=True,
              help="Normalized correlation score (0..1) required to accept a detection.")
@click.option("--debounce", type=float, default=_DEFAULT_DETECTOR.debounce_seconds, show_default=True,
              help="Minimum seconds between accepted detections (suppresses duplicate triggers).")
@click.option("--blocksize", type=int, default=4096, show_default=True,
              help="Audio capture block size in samples.")
@click.option("--ntp-server", default=None,
              help="If set, periodically checks clock sync against this NTP server and reports it.")
@click.option("--heartbeat-interval", type=float, default=10.0, show_default=True,
              help="Seconds between heartbeat/NTP-check messages to the coordinator.")
def listen_cmd(
    device_id, input_device, coordinator, sample_rate, f0, f1, duration,
    threshold, debounce, blocksize, ntp_server, heartbeat_interval,
) -> None:
    """Record audio and report each time the reference chirp is heard."""
    chirp_cfg = ChirpConfig(sample_rate=sample_rate, f0=f0, f1=f1, duration=duration)
    detector_cfg = DetectorConfig(threshold=threshold, debounce_seconds=debounce)
    run_listener(
        device_id=device_id,
        input_device=_parse_device(input_device),
        coordinator=_parse_coordinator(coordinator),
        chirp_cfg=chirp_cfg,
        detector_cfg=detector_cfg,
        blocksize=blocksize,
        ntp_server=ntp_server,
        heartbeat_interval=heartbeat_interval,
    )


@main.command("send")
@click.option("--device-id", default=None, help="This node's identifier. Defaults to hostname.")
@click.option("--output-device", default=None, help="Output device index or name substring.")
@_coordinator_option
@_chirp_options
@click.option("--repeat", type=int, default=1, show_default=True, help="Number of times to play the chirp.")
@click.option("--interval", type=float, default=2.0, show_default=True,
              help="Seconds to wait between repeats.")
@click.option("--daemon", is_flag=True, default=False,
              help="After the initial repeats, stay connected and play again whenever the "
                   "coordinator (e.g. its web dashboard) sends a play command. Requires --coordinator.")
def send_cmd(
    device_id, output_device, coordinator, sample_rate, f0, f1, duration, repeat, interval, daemon,
) -> None:
    """Play the reference chirp so listener nodes can detect it."""
    chirp_cfg = ChirpConfig(sample_rate=sample_rate, f0=f0, f1=f1, duration=duration)
    run_sender(
        device_id=device_id,
        output_device=_parse_device(output_device),
        coordinator=_parse_coordinator(coordinator),
        chirp_cfg=chirp_cfg,
        repeat=repeat,
        interval=interval,
        daemon=daemon,
    )


@main.command("coordinator")
@click.option("--host", default="0.0.0.0", show_default=True, help="Address to bind.")
@click.option("--port", type=int, default=8765, show_default=True, help="TCP port to bind.")
@click.option("--group-window", type=float, default=2.0, show_default=True,
              help="Seconds within which detections are grouped as one chirp event.")
@click.option("--log-file", default=None, type=click.Path(dir_okay=False),
              help="Append JSONL records of all events here.")
@click.option("--web-port", type=int, default=8766, show_default=True,
              help="Port for the web dashboard.")
@click.option("--no-web", is_flag=True, default=False, help="Disable the web dashboard.")
def coordinator_cmd(
    host: str, port: int, group_window: float, log_file: str | None, web_port: int, no_web: bool
) -> None:
    """Run the coordinator that collects reports from listener/sender nodes."""
    run_coordinator(
        host=host,
        port=port,
        group_window_seconds=group_window,
        log_path=log_file,
        web_port=None if no_web else web_port,
    )


if __name__ == "__main__":
    main()
