# audiofinder

Find networked devices by having them emit and listen for a short audio
chirp. Every listening node timestamps what it hears using its own,
NTP-synchronized clock; a coordinator collects those timestamped reports so
you can see which devices heard a given chirp and how their arrival times
relate to each other.

**v1 scope:** detection + timestamps, plus a rough distance estimate derived
from them. Listener nodes report *when* they heard the chirp; the
coordinator groups near-simultaneous detections into "events" and shows
each listener's arrival time relative to the others, and (given a matching
emission report) an estimated sender<->listener distance from the travel
time. It does **not** yet compute a full physical *position* from multiple
listeners' distances (that's a natural next step — see
[Roadmap](#roadmap) — and the per-listener arrival-time data this version
produces is exactly what that would need).

## How it works

1. A **sender** node plays a short frequency-swept "chirp" out of its
   speaker (`audiofinder send`).
2. **Listener** nodes continuously record audio and run a streaming matched
   filter (normalized cross-correlation) against the same chirp waveform
   (`audiofinder listen`). When the correlation score crosses a threshold,
   that's a detection.
3. Each listener converts the detection's sample offset into a wall-clock
   (unix epoch) timestamp, using its own system clock. This is where time
   sync matters: **the whole point of comparing timestamps across machines
   requires those machines' clocks to already agree**, ideally to well
   under the ~1-2ms scale of interesting acoustic timing differences at
   room scale.
4. Listeners (and senders) report to a **coordinator** (`audiofinder
   coordinator`) over a plain TCP/JSON connection. The coordinator groups
   detections that happen close together in time into one "event" and
   prints/logs a table of who heard it and when, relative to the earliest.
   It also matches each detection to the emission that most plausibly
   caused it and estimates the distance sound traveled between them (see
   [Distance estimate](#distance-estimate)).
5. The coordinator also serves a small **web dashboard** showing connected
   nodes and a live feed of detections/emissions/distance estimates, with a
   button to remotely tell a given sender to play its chirp right now.

```
   sender (plays chirp) ---- audio through the air ---->  listener A (mic)
                                                     \---> listener B (mic)
                                                      \--> listener C (mic)

   listener A, B, C  ----TCP/JSON detections---->  coordinator (collects, groups, logs)
                                                        |
                                          web dashboard (browser, http://<coordinator>:8766)
```

## Requirements

- macOS (developed/tested for this; the audio and networking stack are
  cross-platform but v1 is scoped to Mac as requested).
- Python 3.10+.
- All devices on the same LAN (wifi or ethernet), reachable on the
  coordinator's TCP port.
- **Time sync**: this app assumes the network already keeps clocks in sync
  via NTP — which macOS does by default (System Settings > General > Date &
  Time > "Set time and date automatically" uses `time.apple.com`). For
  tighter sync on a LAN:
  - Point every Mac at the *same* nearby NTP source (e.g. a local router or
    a Raspberry Pi running `chrony`) rather than a public pool server, to
    minimize path asymmetry.
  - For sub-millisecond accuracy, run a PTP daemon (e.g. `ptpd` via
    Homebrew) on all nodes — v1 doesn't implement PTP itself, it just
    assumes *something* is keeping wall clocks aligned and gives you a tool
    (`audiofinder check-sync`) to verify how well that's working.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

`sounddevice` ships its own PortAudio binary on macOS via pip, so no
Homebrew install should be required. If audio device access fails, install
PortAudio manually as a fallback: `brew install portaudio`.

The first time you run anything that opens the microphone, macOS will
prompt for microphone permission for your terminal app — allow it.

## Usage

Check available audio devices on a given Mac:

```bash
audiofinder devices
```

Check clock sync quality against an NTP server (default `time.apple.com`):

```bash
audiofinder check-sync --server time.apple.com
```

Start the coordinator (run this once, anywhere reachable on the LAN):

```bash
audiofinder coordinator --port 8765 --log-file events.jsonl
```

On each Mac with a microphone, start a listener, pointing it at the
coordinator:

```bash
audiofinder listen --coordinator 192.168.1.50:8765 --ntp-server 192.168.1.1
```

From the device you want to be "found", play the chirp:

```bash
audiofinder send --coordinator 192.168.1.50:8765 --repeat 3 --interval 2
```

Listener nodes print each detection locally too, so you can run
`audiofinder listen` standalone (no `--coordinator`) just to test that a
given Mac can hear the chirp at all.

## Web dashboard

The coordinator serves a web dashboard alongside its TCP port (default
`http://<coordinator-host>:8766`, right next to the TCP port 8765 nodes
report to). Open it in a browser to see:

- **Nodes**: every listener/sender that has ever connected, its role,
  live connected/offline status, how long since it was last heard from,
  and its most recently reported NTP offset.
- **Live feed**: detections, emissions, and grouped event summaries, as
  they happen (polls every 1.5s).
- **Play now**: a button next to any currently-connected sender that's
  running in `--daemon` mode (see below) — click it to make that Mac play
  its chirp immediately, without walking over to it or opening a terminal
  on it.

To make a sender remotely triggerable from the dashboard, run it with
`--daemon`:

```bash
audiofinder send --coordinator 192.168.1.50:8765 --daemon --repeat 0
```

(`--repeat 0` skips the immediate play on startup so it just sits waiting
for the dashboard's "Play now" button; drop it to also play immediately.)

Disable the dashboard with `audiofinder coordinator --no-web`, or move it
to a different port with `--web-port`.

**No authentication.** This is meant for a trusted LAN, not the open
internet — it also runs Flask's built-in development server, which is
fine for a handful of people polling a dashboard on your network but isn't
a general-purpose production web server.

**Important:** the chirp shape (`--sample-rate`, `--f0`, `--f1`,
`--duration`) must match between the sender and every listener — a listener
can only recognize a chirp it has the same template for. Stick to the
defaults unless you have a reason to change them, and if you do, change
them identically everywhere.

### Tuning detection

- `--threshold` (default `0.5`): normalized correlation score in `0..1`
  required to accept a detection. Lower it if real chirps are being missed
  (check the score printed on near-misses by temporarily lowering
  threshold); raise it if you get false triggers from ambient noise.
- `--debounce` (default `1.0`s): minimum time between accepted detections
  on one listener, to avoid one physical chirp being reported multiple
  times as its correlation peak decays.

## Distance estimate

Whenever a detection can be matched to the emission that plausibly caused
it, the coordinator estimates the distance sound traveled between them:
`distance = speed_of_sound * (detection_timestamp - emission_timestamp)`.
It's printed alongside each detection and event summary, and shown in the
web dashboard's live feed.

**This is a rough estimate, not a calibrated measurement**, because that
timestamp delta isn't pure travel time — it also includes:

- Real acoustic travel time (what we actually want): ~2.9 ms per meter.
- Non-acoustic latency: audio buffering/driver latency on both ends, and
  any residual error in this app's own DAC/ADC-to-wall-clock estimate (see
  `audio_io.py`). In testing on this project, this alone was **~20-100 ms**
  — i.e. equivalent to 7-35 *meters* of falsely-implied distance — even
  between a laptop's own built-in speaker and mic a few centimeters apart.
- Clock offset between sender and listener, if they're different machines
  (see [Requirements](#requirements) on NTP/PTP).

Because the non-acoustic latency is roughly constant regardless of real
distance, it can be calibrated out: run a sender and listener right next
to each other (true distance ~0), note the delay the coordinator reports
for that detection, and pass it as `--latency-offset-ms` when starting the
coordinator for real use:

```bash
audiofinder coordinator --latency-offset-ms 45.2
```

`--speed-of-sound` (default `343` m/s, dry air at ~20°C) is also available
if you want to account for temperature (roughly +0.6 m/s per °C).

Even calibrated, treat the result as order-of-magnitude: it doesn't account
for the sound taking a longer, reflected path than the direct line between
sender and listener, and a single distance doesn't tell you a *direction* —
for that you'd need multiple listeners at known positions and the TDOA
localization step described in [Roadmap](#roadmap).

## Project layout

```
src/audiofinder/
  config.py        shared defaults (chirp shape, network port, device id)
  signal_gen.py     builds the reference chirp waveform
  detector.py       streaming matched-filter chirp detector
  audio_io.py       sounddevice wrappers; estimates wall-clock timestamps
  timesync.py       minimal SNTP client, for reporting clock-sync quality
  protocol.py       newline-delimited JSON message format
  node_client.py    TCP client used by listener/sender to reach the coordinator
  coordinator.py    TCP server that collects and groups reports
  web.py            web dashboard (Flask) served alongside the coordinator
  listener.py       "listen" role
  sender.py         "send" role
  cli.py            `audiofinder` command line entry point
tests/              unit tests (signal/detector/protocol/timesync/coordinator/web), no hardware required
```

## Known limitations (v1)

- **No position estimate**, only a per-listener distance estimate (see
  above) — turning several of those into an actual (x, y) fix is future
  work.
- **Distance estimates need calibration** (`--latency-offset-ms`) to be
  meaningful at all — see [Distance estimate](#distance-estimate). Without
  it, non-acoustic latency can dominate the number entirely, especially at
  short range.
- **Timestamp accuracy** depends on (a) OS-level NTP sync between machines,
  which is typically 1-10ms on a LAN, and (b) this app's own estimate of
  when a sample actually left the speaker / arrived at the mic, derived
  from PortAudio's reported ADC/DAC times. Good enough to see coarse
  ordering and rough timing; not yet calibrated for sub-millisecond
  precision.
- **One chirp shape at a time** — there's no per-device signal ID, so this
  version can tell you *that* a chirp was heard, not distinguish between
  multiple simultaneously-emitting senders. Fine for finding one device at
  a time.
- Detection runs against a fixed threshold; no adaptive noise-floor
  tracking yet.

## Roadmap

- TDOA multilateration: given known listener positions and enough
  detections of the same event, estimate the emitting device's location.
- Per-sender signal IDs (e.g. distinct chirp codes or spread-spectrum
  codes) to disambiguate multiple simultaneous senders.
- Optional PTP-based sync built into the app for sub-millisecond accuracy
  without relying on external daemons.
- mDNS-based auto-discovery of the coordinator instead of hardcoding
  `HOST:PORT`.
- Authentication on the web dashboard/API, and a production-grade WSGI
  server, if this ever needs to run somewhere less trusted than a home/lab
  LAN.

## Tests

```bash
pytest
```

Tests cover chirp generation/detection (synthetic audio, no hardware
needed), the JSON protocol framing (including coordinator→node commands),
the SNTP offset/delay math, coordinator state/event-grouping logic, and the
web dashboard's API endpoints (via Flask's test client).
