"""
@file_name: test_websocket_circuit_breaker_gate.py
@author:
@date: 2026-07-13
@description: The WS fresh-run circuit-breaker error frame (reason→message).
"""

from backend.routes.websocket import _circuit_open_frame


def test_frame_auth():
    f = _circuit_open_frame("paused:auth")
    assert f["type"] == "error"
    assert f["error_type"] == "agent_circuit_open"
    assert f["severity"] == "fatal"
    assert f["cb_reason"] == "paused:auth"
    assert "authentication" in f["error_message"].lower()


def test_frame_quota():
    f = _circuit_open_frame("paused:quota")
    assert "quota" in f["error_message"].lower()
    assert f["error_type"] == "agent_circuit_open"


def test_frame_cooling():
    f = _circuit_open_frame("cooling")
    assert "cooling" in f["error_message"].lower()


def test_frame_none_defaults_to_cooling_copy():
    f = _circuit_open_frame(None)
    assert f["error_type"] == "agent_circuit_open"
    assert f["error_message"]
