"""`audiofinder` command-line entry point."""

from __future__ import annotations

import click

from . import audio_io
from .calibrate import run_local_calibration
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
@click.option("--speed-of-sound", type=float, default=343.0, show_default=True,
              help="Speed of sound in m/s, used to convert emission->detection delay into a "
                   "distance estimate. Adjust for temperature (~+0.6 m/s per degC above 20degC).")
@click.option("--latency-offset-ms", type=float, default=0.0, show_default=True,
              help="Fixed delay (ms) to subtract from every emission->detection delay before "
                   "converting to distance, to cancel out non-acoustic latency (audio buffering, "
                   "device DSP, etc). See `audiofinder calibrate` to measure a real value for "
                   "this instead of guessing.")
def coordinator_cmd(
    host: str,
    port: int,
    group_window: float,
    log_file: str | None,
    web_port: int,
    no_web: bool,
    speed_of_sound: float,
    latency_offset_ms: float,
) -> None:
    """Run the coordinator that collects reports from listener/sender nodes."""
    run_coordinator(
        host=host,
        port=port,
        group_window_seconds=group_window,
        log_path=log_file,
        web_port=None if no_web else web_port,
        speed_of_sound_m_s=speed_of_sound,
        latency_offset_seconds=latency_offset_ms / 1000.0,
    )


@main.command("calibrate")
@click.option("--input-device", default=None, help="Input (mic) device index or name substring.")
@click.option("--output-device", default=None, help="Output (speaker) device index or name substring.")
@_chirp_options
@click.option("--threshold", type=float, default=_DEFAULT_DETECTOR.threshold, show_default=True,
              help="Normalized correlation score (0..1) required to accept a detection.")
@click.option("--rounds", type=int, default=5, show_default=True,
              help="Number of chirps to play and average the delay over.")
@click.option("--interval", type=float, default=1.5, show_default=True,
              help="Seconds to wait between rounds.")
@click.option("--timeout", "detection_timeout", type=float, default=3.0, show_default=True,
              help="Seconds to wait for a detection before counting a round as missed.")
def calibrate_cmd(
    input_device, output_device, sample_rate, f0, f1, duration, threshold, rounds, interval, detection_timeout,
) -> None:
    """Measure this machine's own speaker->mic delay, for --latency-offset-ms.

    Plays the chirp out of a speaker and listens for it on a microphone --
    by default this machine's own built-in ones, right next to each other,
    which is a same-machine loopback test rather than a substitute for
    calibrating with your actual deployed hardware. Still, a real measured
    number beats a guess: run this a few times, note the median delay it
    reports, and pass it to `audiofinder coordinator --latency-offset-ms`.
    """
    chirp_cfg = ChirpConfig(sample_rate=sample_rate, f0=f0, f1=f1, duration=duration)
    detector_cfg = DetectorConfig(threshold=threshold)
    summary = run_local_calibration(
        input_device=_parse_device(input_device),
        output_device=_parse_device(output_device),
        chirp_cfg=chirp_cfg,
        detector_cfg=detector_cfg,
        rounds=rounds,
        interval=interval,
        detection_timeout=detection_timeout,
    )
    click.echo("")
    if summary.rounds_detected == 0:
        click.echo(
            f"0/{summary.rounds_attempted} rounds detected -- couldn't calibrate.\n"
            f"Most likely --threshold {threshold} is too strict for this mic/speaker pair "
            "and placement -- try again with a lower value (e.g. --threshold 0.3), or "
            "confirm --input-device/--output-device are the ones you expect "
            "(`audiofinder devices` lists them).",
            err=True,
        )
        raise SystemExit(1)

    click.echo(f"{summary.rounds_detected}/{summary.rounds_attempted} rounds detected")
    click.echo(
        f"delay: median={summary.median_ms:.2f} ms  min={summary.min_ms:.2f} ms  max={summary.max_ms:.2f} ms"
    )
    if summary.rounds_detected < summary.rounds_attempted:
        click.echo(
            "note: some rounds were missed -- that's normal over the air; the median "
            "of the successful ones is still a reasonable estimate.",
        )
    click.echo(f"\nSuggested: audiofinder coordinator --latency-offset-ms {summary.median_ms:.1f}")


if __name__ == "__main__":
    main()
