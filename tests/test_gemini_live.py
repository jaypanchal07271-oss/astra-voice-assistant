"""
Unit and Integration Tests for Gemini Live API (Bidirectional Streaming Session).
Validates:
1. Tool calling under Live engine: "notepad kholo" triggers open_application("notepad").
2. Tool calling under Live engine: "WhatsApp message" triggers send_whatsapp_message().
3. USE_GEMINI_LIVE feature flag properly toggles between Live session and standard engine.
4. Fallback resilience when Live connection fails or raises exceptions.
5. WebSocket /ws/live endpoint streams frames and executes actions.
"""

import pytest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
from starlette.testclient import TestClient

from app import app
from config import ASTRA_AUTH_TOKEN
from core import brain, actions

client = TestClient(app)
AUTH_HEADERS = {"X-Astra-Token": ASTRA_AUTH_TOKEN}


class MockAsyncLiveSession:
    """Mock for google.genai.live.AsyncSession."""
    def __init__(self, messages=None):
        self.messages = messages or []
        self.sent_content = []
        self.sent_tool_responses = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

    async def send_client_content(self, turns, turn_complete=True):
        self.sent_content.append((turns, turn_complete))

    async def send_tool_response(self, function_responses):
        self.sent_tool_responses.append(function_responses)

    async def receive(self):
        for msg in self.messages:
            yield msg


@pytest.mark.asyncio
async def test_live_session_triggers_open_app(monkeypatch):
    """
    Confirms that under the Gemini Live engine, when the model yields
    a tool_call for open_application("notepad"), it executes open_application
    and dispatches open_app.
    """
    opened_apps = []
    monkeypatch.setattr(actions, "open_app", lambda app_name: opened_apps.append(app_name) or {"success": True, "app": app_name, "message": f"{app_name} opened"})
    monkeypatch.setattr(brain, "dispatch_pc_tool_sync", lambda tool_name, params: opened_apps.append(params.get("app_name")) or {"success": True, "app": params.get("app_name"), "message": f"{params.get('app_name')} opened"} if tool_name == "open_app" else {"success": True})

    # Prepare mock live server messages
    fc = MagicMock()
    fc.name = "open_application"
    fc.args = {"app_name": "notepad"}
    fc.id = "call_open_1"

    tool_msg = MagicMock()
    tool_msg.tool_call = MagicMock(function_calls=[fc])
    tool_msg.server_content = None

    part = MagicMock()
    part.text = "Bilkul! Maine aapke liye Notepad open kar diya hai."
    part.inline_data = None

    content_msg = MagicMock()
    content_msg.tool_call = None
    content_msg.server_content = MagicMock(model_turn=MagicMock(parts=[part]))

    mock_session = MockAsyncLiveSession(messages=[tool_msg, content_msg])

    mock_genai_client = MagicMock()
    mock_genai_client.aio.live.connect.return_value = mock_session

    text_chunks = []
    with patch("google.genai.Client", return_value=mock_genai_client), \
         patch.dict("os.environ", {"GEMINI_API_KEY": "fake_test_key", "USE_GEMINI_LIVE": "true"}):

        # Note: bypass deterministic fast-check to test model live streaming
        with patch("core.brain.fallback_intent_parser", return_value=None):
            res = await brain.run_live_session(
                user_text="notepad kholo",
                session_id="test_live_np",
                on_text_chunk=lambda chunk: text_chunks.append(chunk)
            )

    assert "notepad" in opened_apps
    assert len(mock_session.sent_tool_responses) == 1
    assert "Notepad open kar diya hai" in res["reply"]
    assert "Notepad open kar diya hai" in "".join(text_chunks)
    assert res["action"] is not None


@pytest.mark.asyncio
async def test_live_session_triggers_send_whatsapp_message(monkeypatch):
    """
    Confirms that under the Gemini Live engine, when the model yields
    a tool_call for send_whatsapp_message, it executes send_whatsapp_message.
    """
    sent_messages = []
    monkeypatch.setattr(actions, "send_whatsapp_message", lambda contact_name, message, phone="": sent_messages.append((contact_name, message)) or {"success": True, "message": f"Sent to {contact_name}"})

    fc = MagicMock()
    fc.name = "send_whatsapp_message"
    fc.args = {"contact_name": "Aarti School", "message": "hello from live session"}
    fc.id = "call_wa_1"

    tool_msg = MagicMock()
    tool_msg.tool_call = MagicMock(function_calls=[fc])
    tool_msg.server_content = None

    part = MagicMock()
    part.text = "Aarti School ko WhatsApp par message bhej diya hai."
    part.inline_data = None

    content_msg = MagicMock()
    content_msg.tool_call = None
    content_msg.server_content = MagicMock(model_turn=MagicMock(parts=[part]))

    mock_session = MockAsyncLiveSession(messages=[tool_msg, content_msg])

    mock_genai_client = MagicMock()
    mock_genai_client.aio.live.connect.return_value = mock_session

    with patch("google.genai.Client", return_value=mock_genai_client), \
         patch.dict("os.environ", {"GEMINI_API_KEY": "fake_test_key", "USE_GEMINI_LIVE": "true"}):

        with patch("core.brain.fallback_intent_parser", return_value=None):
            res = await brain.run_live_session(
                user_text="WhatsApp par Aarti School ko message bhejo hello",
                session_id="test_live_wa"
            )

    assert len(sent_messages) == 1
    assert sent_messages[0][0] == "Aarti School"
    assert sent_messages[0][1] == "hello from live session"
    assert len(mock_session.sent_tool_responses) == 1
    assert "Aarti School" in res["reply"]


@pytest.mark.asyncio
async def test_use_gemini_live_feature_flag_toggle():
    """
    Tests that USE_GEMINI_LIVE=true triggers run_live_session,
    and USE_GEMINI_LIVE=false uses the standard request-response engine.
    """
    with patch("core.brain.run_live_session", new_callable=AsyncMock) as mock_live, \
         patch("core.brain._process_voice_command_core", new_callable=AsyncMock) as mock_core:

        mock_live.return_value = {"reply": "Live reply", "action": None}
        mock_core.return_value = {"reply": "Standard reply", "action": None}

        # 1. Feature Flag = false -> uses standard core
        with patch.dict("os.environ", {"USE_GEMINI_LIVE": "false"}):
            res_std = await brain.process_voice_command("test command", session_id="s1")
            assert mock_core.called
            assert not mock_live.called
            assert res_std["reply"] == "Standard reply"

        mock_core.reset_mock()
        mock_live.reset_mock()

        # 2. Feature Flag = true -> uses Live session
        with patch.dict("os.environ", {"USE_GEMINI_LIVE": "true"}):
            res_live = await brain.process_voice_command("test command", session_id="s2")
            assert mock_live.called
            assert not mock_core.called
            assert res_live["reply"] == "Live reply"


@pytest.mark.asyncio
async def test_live_session_resilient_fallback_on_error():
    """
    Tests that if run_live_session encounters a connection or API error,
    it gracefully falls back to the standard engine without crashing.
    """
    mock_genai_client = MagicMock()
    mock_genai_client.aio.live.connect.side_effect = Exception("WebSocket 1006 connection closed abnormally")

    with patch("google.genai.Client", return_value=mock_genai_client), \
         patch("core.brain._process_voice_command_core", new_callable=AsyncMock) as mock_core, \
         patch("core.brain.fallback_intent_parser", return_value=None), \
         patch.dict("os.environ", {"GEMINI_API_KEY": "fake_test_key", "USE_GEMINI_LIVE": "true"}):

        mock_core.return_value = {"reply": "Fallback handled", "action": None}
        res = await brain.run_live_session("random query", session_id="test_live_err")

        assert mock_core.called
        assert res["reply"] == "Fallback handled"


def test_websocket_live_endpoint_authentication():
    """
    Verifies that the /ws/live endpoint requires first-frame authentication.
    """
    # 1. Reject connection with invalid auth token
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/live") as ws:
            ws.send_json({"type": "auth", "token": "invalid_wrong_token"})
            ws.receive_json()

    # 2. Accept connection with valid first-frame token
    with client.websocket_connect("/ws/live") as ws:
        ws.send_json({"type": "auth", "token": ASTRA_AUTH_TOKEN})
        auth_resp = ws.receive_json()
        assert auth_resp.get("type") == "authenticated"

        # Send a command
        ws.send_json({"text": "kaise ho", "session_id": "ws_test", "lang": "hi-IN"})

        # Read frames until 'done'
        received_types = []
        while True:
            frame = ws.receive_json()
            received_types.append(frame.get("type"))
            if frame.get("type") == "done":
                assert "reply" in frame
                break

        assert "start" in received_types
        assert "done" in received_types

