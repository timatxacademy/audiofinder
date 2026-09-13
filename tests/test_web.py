import socket

from audiofinder.coordinator import CoordinatorState
from audiofinder.web import create_app


def test_index_serves_html():
    state = CoordinatorState()
    client = create_app(state).test_client()

    resp = client.get("/")

    assert resp.status_code == 200
    assert b"audiofinder" in resp.data
    assert resp.mimetype == "text/html"


def test_status_endpoint_reflects_state():
    state = CoordinatorState()
    state.record_emission("sender-1", timestamp=100.0)
    client = create_app(state).test_client()

    resp = client.get("/api/status")
    body = resp.get_json()

    assert resp.status_code == 200
    assert "sender-1" in body["nodes"]
    assert body["nodes"]["sender-1"]["role"] == "sender"
    assert body["recent_events"][0]["type"] == "emission"


def test_play_now_succeeds_for_connected_node():
    state = CoordinatorState()
    server_side, client_side = socket.socketpair()
    try:
        state.register_socket("sender-1", server_side)
        client = create_app(state).test_client()

        resp = client.post("/api/nodes/sender-1/play")

        assert resp.status_code == 200
        assert resp.get_json() == {"ok": True}
        assert client_side.recv(4096)  # the command was actually written to the socket
    finally:
        server_side.close()
        client_side.close()


def test_play_now_fails_for_unknown_node():
    state = CoordinatorState()
    client = create_app(state).test_client()

    resp = client.post("/api/nodes/nobody/play")

    assert resp.status_code == 409
    assert resp.get_json()["ok"] is False
