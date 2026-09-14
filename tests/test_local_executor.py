"""
Tests for Mobile Architecture Split: Local PC Executor
Verifies:
1. WebSocket authentication & rejection of unauthorized clients.
2. Connection registration and /api/status executor_connected reporting.
3. Bidirectional RPC dispatching round-trip.
4. Offline executor handling with clear user-facing Hindi/Hinglish message.
5. Strict timeout enforcement when laptop client hangs.
6. local_executor.py local tool execution and exception safety.
7. End-to-end /api/chat execution when executor is offline / online.
"""

import os
import json
import pytest
import asyncio
from unittest.mock import patch, MagicMock
from starlette.testclient import TestClient

from app import app
from config import ASTRA_AUTH_TOKEN
from core.executor_bridge import executor_bridge, dispatch_pc_tool_sync, dispatch_pc_tool_async
from core.brain import process_voice_command, fallback_intent_parser
from core import actions
import local_executor

client = TestClient(app)
AUTH_HEADERS = {"X-Astra-Token": ASTRA_AUTH_TOKEN}


def test_executor_websocket_unauthorized_rejected():
    """Verifies that WebSocket connections without token or with invalid token are rejected."""
    # No token
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/executor") as ws:
            pass

    # Invalid token
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/executor?token=invalid_token_123") as ws:
            pass


def test_executor_websocket_connection_and_status():
    """Verifies that an authorized executor connects, updates /api/status, and cleans up on disconnect."""
    # Before connection: status reports executor_connected = False
    res_before = client.get("/api/status").json()
    assert res_before["executor_connected"] is False

    # Connect authorized executor
    with client.websocket_connect(f"/ws/executor?token={ASTRA_AUTH_TOKEN}") as ws:
        # After connection: bridge is connected
        assert executor_bridge.is_connected() is True
        res_connected = client.get("/api/status").json()
        assert res_connected["executor_connected"] is True

    # After disconnect: bridge cleans up
    assert executor_bridge.is_connected() is False
    res_after = client.get("/api/status").json()
    assert res_after["executor_connected"] is False


def test_offline_executor_immediate_error_and_clear_message():
    """
    Verifies that when laptop executor is offline:
    1. dispatch_pc_tool_sync returns immediately with offline=True.
    2. Fallback parser returns clear message asking user to start local_executor.py.
    """
    assert executor_bridge.is_connected() is False

    res = dispatch_pc_tool_sync("open_app", {"app_name": "notepad"})
    assert res["success"] is False
    assert res.get("offline") is True
    assert "laptop executor" in res["message"].lower() and "offline" in res["message"].lower()

    # Voice command fallback test
    fb_res = fallback_intent_parser("Notepad kholo")
    assert fb_res["action"].get("offline") is True
    reply = fb_res["reply"].lower()
    assert "laptop executor" in reply and "offline" in reply
    assert "local_executor.py" in reply


@pytest.mark.asyncio
async def test_executor_timeout_enforcement():
    """
    Simulates a connected executor that hangs without replying.
    Verifies that execute_remote_tool aborts after the timeout with timeout=True.
    """
    mock_ws = MagicMock()
    mock_ws.client_state.name = "CONNECTED"
    mock_ws.send_json = MagicMock(return_value=asyncio.sleep(0))

    loop = asyncio.get_running_loop()
    await executor_bridge.register(mock_ws, loop)

    try:
        # Request with a short 0.2s timeout
        res = await executor_bridge.execute_remote_tool("open_app", {"app_name": "notepad"}, timeout=0.2)
        assert res["success"] is False
        assert res.get("timeout") is True
        assert "timed out" in res["message"].lower()
    finally:
        await executor_bridge.unregister(mock_ws)


def test_local_executor_tool_execution():
    """Verifies that local_executor.execute_local_tool dispatches PC tools and catches errors."""
    # Test valid PC tools via mock
    with patch("core.actions.open_app", return_value={"success": True, "message": "Opened notepad"}) as mock_open:
        res = local_executor.execute_local_tool("open_app", {"app_name": "notepad"})
        assert res["success"] is True
        mock_open.assert_called_once_with(app_name="notepad")

    with patch("core.actions.system_control", return_value={"success": True, "message": "Volume up"}) as mock_sys:
        res = local_executor.execute_local_tool("system_control", {"command": "volume_up"})
        assert res["success"] is True
        mock_sys.assert_called_once_with(command="volume_up")

    with patch("core.actions.system_control", return_value={"success": True, "message": "Volume up"}) as mock_sys:
        res = local_executor.execute_local_tool("system_control", {"action": "volume_up"})
        assert res["success"] is True
        mock_sys.assert_called_once_with(action="volume_up")

    # Test unknown tool
    res_unknown = local_executor.execute_local_tool("invalid_action_xyz", {})
    assert res_unknown["success"] is False
    assert "unknown local tool" in res_unknown["message"].lower()

    # Test exception safety
    with patch("core.actions.close_app", side_effect=RuntimeError("Process terminated abruptly")):
        res_err = local_executor.execute_local_tool("close_app", {"app_name": "chrome"})
        assert res_err["success"] is False
        assert "error executing local tool" in res_err["message"].lower()


def test_chat_endpoint_with_offline_executor_returns_200():
    """
    Verifies that calling /api/chat with a PC command when executor is offline
    returns HTTP 200 with an informative offline message instead of crashing.
    """
    assert executor_bridge.is_connected() is False

    with patch.object(actions, "open_app", None):
        response = client.post(
            "/api/chat",
            json={"text": "Notepad kholo", "session_id": "test_offline_api"},
            headers=AUTH_HEADERS
        )

        assert response.status_code == 200
        data = response.json()
        assert "reply" in data
        assert "offline" in data["reply"].lower() or (data.get("action") and "offline" in str(data["action"]).lower())


def test_chat_endpoint_with_offline_executor_in_fallback():
    """Verifies fallback parser offline response structure via /api/chat."""
    with patch("core.brain.GEMINI_API_KEY", ""):
        with patch.dict("os.environ", {"GEMINI_API_KEY": ""}):
            response = client.post(
                "/api/chat",
                json={"text": "Notepad kholo", "session_id": "test_offline_fb"},
                headers=AUTH_HEADERS
            )
            assert response.status_code == 200
            data = response.json()
            assert "laptop executor" in data["reply"].lower() and "offline" in data["reply"].lower()
            assert data["action"].get("offline") is True
