"""audiofinder: find networked devices by emitting/listening for an audio chirp.

Package layout:
    config       - shared defaults (chirp shape, network ports, ids)
    signal_gen   - generates the reference chirp waveform
    detector     - streaming matched-filter detector for the chirp
    audio_io     - sounddevice wrappers for playback and streaming capture
    timesync     - a minimal SNTP client to report clock-sync quality
    protocol     - newline-delimited JSON message framing over TCP
    coordinator  - TCP server that collects detections/emissions from nodes
    listener     - "listen" role: records audio, detects the chirp, reports it
    sender       - "send" role: emits the chirp, optionally reports doing so
    cli          - `audiofinder` command line entry point
"""

__version__ = "0.1.0"
