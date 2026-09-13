"""Web dashboard for the coordinator: live node/event view + remote triggers.

This is a small Flask app bound to the coordinator's in-memory `CoordinatorState`.
It's meant for LAN use alongside the app, not for exposing to the internet:
there's no authentication, and it runs Flask's built-in development server
(fine for a handful of local clients polling once a second; not a
general-purpose production web server).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from flask import Flask, Response, jsonify

from .protocol import COMMAND_PLAY_NOW
from .protocol import command as command_msg

if TYPE_CHECKING:
    from .coordinator import CoordinatorState


def create_app(state: "CoordinatorState") -> Flask:
    app = Flask(__name__)
    # Keep Flask's default logging quiet for routine polling requests; real
    # errors still surface via exceptions/500s.
    app.logger.disabled = True

    @app.get("/")
    def index() -> Response:
        return Response(_DASHBOARD_HTML, mimetype="text/html")

    @app.get("/api/status")
    def status() -> Response:
        return jsonify(state.snapshot())

    @app.post("/api/nodes/<device_id>/play")
    def play_now(device_id: str) -> tuple[Response, int] | Response:
        ok = state.send_command_to(device_id, command_msg(COMMAND_PLAY_NOW))
        if not ok:
            return jsonify(
                {"ok": False, "error": f"{device_id} is not currently connected"}
            ), 409
        return jsonify({"ok": True})

    return app


def run_web_server(state: "CoordinatorState", host: str, port: int) -> None:
    app = create_app(state)
    # use_reloader must stay off: the reloader tries to re-exec the process,
    # which breaks when Flask isn't running on the main thread (it's started
    # from a background thread by the coordinator).
    app.run(host=host, port=port, threaded=True, use_reloader=False, debug=False)


_DASHBOARD_HTML: str = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>audiofinder</title>
<style>
  :root {
    color-scheme: light dark;
    --bg: #f7f7f8;
    --panel: #ffffff;
    --border: #e2e2e6;
    --text: #1c1c1f;
    --muted: #6b6b74;
    --accent: #2f6feb;
    --good: #1f9254;
    --warn: #b3841f;
    --bad: #c0392b;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #17181c;
      --panel: #202126;
      --border: #33343a;
      --text: #eceef0;
      --muted: #9a9aa2;
      --accent: #6ea3ff;
      --good: #3ecf82;
      --warn: #e0b23e;
      --bad: #e0685c;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    padding-block: 24px;
    padding-inline: 20px;
    background: var(--bg);
    color: var(--text);
    font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  }
  .wrap { max-width: 960px; margin: 0 auto; }
  header { display: flex; align-items: baseline; justify-content: space-between; flex-wrap: wrap; gap: 8px; margin-bottom: 20px; }
  h1 { font-size: 20px; margin: 0; }
  .subtitle { color: var(--muted); font-size: 13px; }
  #conn-indicator { font-size: 12px; color: var(--muted); }
  #conn-indicator.live { color: var(--good); }
  #conn-indicator.stale { color: var(--bad); }
  .panel {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px;
    margin-bottom: 20px;
  }
  .panel h2 { font-size: 14px; margin: 0 0 12px 0; color: var(--muted); text-transform: uppercase; letter-spacing: .04em; }
  table { width: 100%; border-collapse: collapse; }
  th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--border); font-size: 13px; }
  th { color: var(--muted); font-weight: 500; }
  tr:last-child td { border-bottom: none; }
  .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }
  .dot.on { background: var(--good); }
  .dot.off { background: var(--muted); }
  .role-listener { color: var(--accent); }
  .role-sender { color: var(--warn); }
  button {
    font: inherit;
    background: var(--accent);
    color: white;
    border: none;
    border-radius: 6px;
    padding: 5px 10px;
    cursor: pointer;
  }
  button:disabled { opacity: .4; cursor: default; }
  button:hover:not(:disabled) { filter: brightness(1.08); }
  .empty { color: var(--muted); font-style: italic; padding: 8px 0; }
  #events { max-height: 420px; overflow-y: auto; }
  .event { display: flex; gap: 10px; padding: 6px 0; border-bottom: 1px solid var(--border); font-size: 13px; }
  .event:last-child { border-bottom: none; }
  .event time { color: var(--muted); flex: 0 0 90px; font-variant-numeric: tabular-nums; }
  .event .icon { flex: 0 0 20px; }
  .toast { font-size: 12px; color: var(--muted); margin-left: 8px; }
  code { background: var(--border); padding: 1px 5px; border-radius: 4px; font-size: 12px; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div>
      <h1>audiofinder</h1>
      <div class="subtitle">coordinator dashboard</div>
    </div>
    <div id="conn-indicator">connecting&hellip;</div>
  </header>

  <div class="panel">
    <h2>Nodes</h2>
    <table id="nodes-table">
      <thead>
        <tr><th>Device</th><th>Role</th><th>Status</th><th>Last seen</th><th>NTP offset</th><th></th></tr>
      </thead>
      <tbody id="nodes-body"></tbody>
    </table>
    <div id="nodes-empty" class="empty" hidden>No nodes have connected yet. Run <code>audiofinder listen</code> or <code>audiofinder send</code> with <code>--coordinator</code> pointed here.</div>
  </div>

  <div class="panel">
    <h2>Live feed</h2>
    <div id="events"></div>
    <div id="events-empty" class="empty" hidden>No detections or emissions yet.</div>
  </div>
</div>

<script>
const nodesBody = document.getElementById('nodes-body');
const nodesEmpty = document.getElementById('nodes-empty');
const eventsEl = document.getElementById('events');
const eventsEmpty = document.getElementById('events-empty');
const connIndicator = document.getElementById('conn-indicator');

function fmtAgo(seconds) {
  if (seconds < 1.5) return 'just now';
  if (seconds < 60) return Math.round(seconds) + 's ago';
  if (seconds < 3600) return Math.round(seconds / 60) + 'm ago';
  return Math.round(seconds / 3600) + 'h ago';
}

function fmtClock(unixTime) {
  const d = new Date(unixTime * 1000);
  return d.toLocaleTimeString([], {hour12: false}) + '.' + String(d.getMilliseconds()).padStart(3, '0');
}

async function triggerPlay(deviceId, btn) {
  btn.disabled = true;
  const original = btn.textContent;
  btn.textContent = 'Sending...';
  try {
    const res = await fetch(`/api/nodes/${encodeURIComponent(deviceId)}/play`, {method: 'POST'});
    const body = await res.json();
    btn.textContent = body.ok ? 'Sent!' : 'Failed';
  } catch (e) {
    btn.textContent = 'Failed';
  }
  setTimeout(() => { btn.textContent = original; btn.disabled = false; }, 1500);
}

function renderNodes(nodes, serverTime) {
  const ids = Object.keys(nodes).sort();
  nodesEmpty.hidden = ids.length > 0;
  nodesBody.innerHTML = '';
  for (const id of ids) {
    const n = nodes[id];
    const tr = document.createElement('tr');

    const role = n.role || 'unknown';
    const roleClass = role === 'listener' ? 'role-listener' : (role === 'sender' ? 'role-sender' : '');

    const ago = serverTime - (n.last_seen || serverTime);
    const offsetText = (n.ntp_offset_ms === undefined || n.ntp_offset_ms === null)
      ? '&mdash;'
      : `${n.ntp_offset_ms.toFixed(1)} ms`;

    let actionHtml = '';
    if (role === 'sender') {
      const disabled = n.connected ? '' : 'disabled';
      actionHtml = `<button data-device="${id}" ${disabled}>Play now</button>`;
    }

    tr.innerHTML = `
      <td>${id}</td>
      <td class="${roleClass}">${role}</td>
      <td><span class="dot ${n.connected ? 'on' : 'off'}"></span>${n.connected ? 'connected' : 'offline'}</td>
      <td>${fmtAgo(ago)}</td>
      <td>${offsetText}</td>
      <td>${actionHtml}</td>
    `;
    nodesBody.appendChild(tr);
  }
  nodesBody.querySelectorAll('button[data-device]').forEach(btn => {
    btn.addEventListener('click', () => triggerPlay(btn.dataset.device, btn));
  });
}

function describeEvent(ev) {
  if (ev.type === 'detection') {
    return {icon: '🎤', text: `<strong>${ev.device_id}</strong> heard the chirp (score ${ev.score.toFixed(2)})`};
  }
  if (ev.type === 'emission') {
    return {icon: '🔊', text: `<strong>${ev.device_id}</strong> played the chirp`};
  }
  if (ev.type === 'event_summary') {
    const n = ev.detections.length;
    const earliest = Math.min(...ev.detections.map(d => d.timestamp));
    const latest = Math.max(...ev.detections.map(d => d.timestamp));
    const spreadMs = ((latest - earliest) * 1000).toFixed(1);
    return {icon: '📊', text: `event: <strong>${n}</strong> listener(s) heard one chirp, spread ${spreadMs} ms`};
  }
  return {icon: '•', text: ev.type};
}

function renderEvents(events) {
  eventsEmpty.hidden = events.length > 0;
  eventsEl.innerHTML = '';
  for (const ev of events.slice().reverse()) {
    const {icon, text} = describeEvent(ev);
    const row = document.createElement('div');
    row.className = 'event';
    row.innerHTML = `<time>${fmtClock(ev.logged_at)}</time><span class="icon">${icon}</span><span>${text}</span>`;
    eventsEl.appendChild(row);
  }
}

async function poll() {
  try {
    const res = await fetch('/api/status');
    const data = await res.json();
    renderNodes(data.nodes, data.server_time);
    renderEvents(data.recent_events);
    connIndicator.textContent = 'live';
    connIndicator.className = 'live';
  } catch (e) {
    connIndicator.textContent = 'connection lost, retrying…';
    connIndicator.className = 'stale';
  }
}

poll();
setInterval(poll, 1500);
</script>
</body>
</html>
"""
