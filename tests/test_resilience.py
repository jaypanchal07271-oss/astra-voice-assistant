"""
Resilience & Error Recovery Test Suite for Astra Assistant
Verifies graceful degradation under adverse conditions:
1. Gemini API timeout -> Fallback intent parser with memory recovery.
2. Gemini API returning malformed tool-call JSON -> Fallback intent parser recovery.
3. Edge-TTS failure -> Chat response delivered with audio_url=None, no crash.
4. Tool function raising unexpected exception -> Safe generic response returned, /api/chat never crashes.
"""

import json
import pytest
from unittest.mock import patch, MagicMock
from starlette.testclient import TestClient

import app
import config
from core.brain import process_voice_command, fallback_intent_parser


@pytest.fixture
def client():
    return TestClient(app.app)


AUTH_HEADERS = {"X-Astra-Token": config.ASTRA_AUTH_TOKEN}


# =====================================================================
# 1. Gemini API Timeout Recovery Tests
# =====================================================================

@pytest.mark.asyncio
async def test_gemini_api_timeout_falls_back_to_intent_parser():
    """
    Simulates a Gemini API timeout during chat.send_message.
    Verifies that process_voice_command does not crash and recovers via fallback_intent_parser.
    """
    with patch("os.getenv", side_effect=lambda k, d=None: "fake_gemini_key" if k == "GEMINI_API_KEY" else d):
        with patch("core.brain.session_manager.get_or_create_chat") as mock_get_chat:
            mock_chat = MagicMock()
            mock_chat.send_message.side_effect = TimeoutError("Gemini API connection timed out after 10000ms")
            mock_get_chat.return_value = mock_chat

            result = await process_voice_command("open notepad", session_id="test_timeout_sess")

            assert isinstance(result, dict)
            assert "reply" in result
            assert len(result["reply"]) > 0
            assert "notepad" in result["reply"].lower()


def test_gemini_api_timeout_chat_endpoint_returns_200(client):
    """
    Simulates Gemini API timeout when hit via HTTP POST /api/chat.
    Confirms HTTP 200 is returned, never crashing the server.
    """
    with patch("os.getenv", side_effect=lambda k, d=None: "fake_gemini_key" if k == "GEMINI_API_KEY" else d):
        with patch("core.brain.session_manager.get_or_create_chat") as mock_get_chat:
            mock_chat = MagicMock()
            mock_chat.send_message.side_effect = TimeoutError("Deadline exceeded")
            mock_get_chat.return_value = mock_chat

            response = client.post(
                "/api/chat",
                json={"text": "Namaste", "session_id": "test_timeout_api"},
                headers=AUTH_HEADERS
            )

            assert response.status_code == 200
            data = response.json()
            assert "reply" in data
            assert len(data["reply"]) > 0
            assert "Namaste" in data["reply"] or "Astra" in data["reply"]


# =====================================================================
# 2. Gemini Malformed Tool-Call JSON Tests
# =====================================================================

@pytest.mark.asyncio
async def test_gemini_malformed_tool_call_json_falls_back():
    """
    Simulates Gemini returning malformed tool-call JSON / JSONDecodeError during AFC.
    Verifies process_voice_command recovers gracefully.
    """
    with patch("os.getenv", side_effect=lambda k, d=None: "fake_gemini_key" if k == "GEMINI_API_KEY" else d):
        with patch("core.brain.session_manager.get_or_create_chat") as mock_get_chat:
            mock_chat = MagicMock()
            mock_chat.send_message.side_effect = json.JSONDecodeError("Unterminated JSON tool argument", "{'app':", 6)
            mock_get_chat.return_value = mock_chat

            result = await process_voice_command("volume up", session_id="test_json_err_sess")

            assert isinstance(result, dict)
            assert "reply" in result
            assert "volume" in result["reply"].lower()


def test_gemini_malformed_tool_call_chat_endpoint_returns_200(client):
    """
    Simulates Gemini raising ValueError/JSONDecodeError for malformed function arguments.
    Verifies HTTP 200 returned with valid fallback response.
    """
    with patch("os.getenv", side_effect=lambda k, d=None: "fake_gemini_key" if k == "GEMINI_API_KEY" else d):
        with patch("core.brain.session_manager.get_or_create_chat") as mock_get_chat:
            mock_chat = MagicMock()
            mock_chat.send_message.side_effect = ValueError("Invalid function call schema structure")
            mock_get_chat.return_value = mock_chat

            response = client.post(
                "/api/chat",
                json={"text": "kya time ho raha hai?", "session_id": "test_malformed_json_api"},
                headers=AUTH_HEADERS
            )

            assert response.status_code == 200
            data = response.json()
            assert "reply" in data
            assert any(k in data["reply"].lower() for k in ["baje", "time", "samay"])


# =====================================================================
# 3. Edge-TTS Failure Resilience Tests
# =====================================================================

def test_edge_tts_failure_preserves_text_reply_and_returns_200(client):
    """
    Simulates an unexpected failure in edge-tts audio synthesis.
    Verifies that /api/chat still returns HTTP 200 with the full text reply,
    and sets audio_url to None without crashing.
    """
    with patch("app.generate_audio", side_effect=RuntimeError("Edge-TTS connection reset by peer")):
        response = client.post(
            "/api/chat",
            json={"text": "kaise ho?", "session_id": "test_tts_fail"},
            headers=AUTH_HEADERS
        )

        assert response.status_code == 200
        data = response.json()
        assert "reply" in data
        assert len(data["reply"]) > 0
        assert data["audio_url"] is None


def test_edge_tts_service_unavailable_exception_handled(client):
    """
    Simulates generic edge_tts exception (e.g. network offline).
    Verifies graceful degradation to text-only mode.
    """
    with patch("app.generate_audio", side_effect=Exception("Service Unavailable: Could not resolve ms-tts endpoint")):
        response = client.post(
            "/api/chat",
            json={"text": "who are you", "session_id": "test_tts_offline"},
            headers=AUTH_HEADERS
        )

        assert response.status_code == 200
        data = response.json()
        assert "Astra" in data["reply"]
        assert data["audio_url"] is None


# =====================================================================
# 4. Tool Function Raising Unexpected Exception Tests
# =====================================================================

@pytest.mark.asyncio
async def test_tool_function_raising_unexpected_exception_in_fallback():
    """
    Simulates an unexpected exception inside a tool function (e.g., actions.open_app).
    Verifies that fallback_intent_parser and process_voice_command catch it and return
    a safe generic reply, never propagating an unhandled exception.
    """
    with patch("core.brain.GEMINI_API_KEY", ""):
        with patch.dict("os.environ", {"GEMINI_API_KEY": ""}):
            with patch("core.actions.open_app", side_effect=RuntimeError("Kernel crash: missing subsystem DLL")):
                result = await process_voice_command("open chrome", session_id="test_tool_crash_fb")

                assert isinstance(result, dict)
                assert "reply" in result
                assert len(result["reply"]) > 0
                assert any(k in result["reply"].lower() for k in ["samasya", "dikkat", "takleef", "error", "kshama", "koshish", "sorry", "trouble", "issue", "problem", "unable"])
                assert result.get("action", {}).get("status") == "error"


@pytest.mark.asyncio
async def test_tool_function_raising_unexpected_exception_in_gemini_afc():
    """
    Simulates a tool raising an unexpected exception during Gemini Automatic Function Calling (AFC).
    Verifies that process_voice_command catches the exception and falls back safely.
    """
    with patch("core.brain.session_manager.get_or_create_chat") as mock_get_chat:
        mock_chat = MagicMock()
        mock_chat.send_message.side_effect = RuntimeError("Tool function threw unexpected runtime exception")
        mock_get_chat.return_value = mock_chat

        with patch("core.actions.open_app", side_effect=RuntimeError("Tool function threw unexpected runtime exception")):
            result = await process_voice_command("open chrome", session_id="test_tool_crash_afc")

            assert isinstance(result, dict)
            assert "reply" in result
            assert len(result["reply"]) > 0
            assert any(k in result["reply"].lower() for k in ["samasya", "dikkat", "takleef", "error", "kshama", "koshish", "sorry", "trouble", "issue", "problem", "unable"])
            assert result.get("action", {}).get("status") == "error"


def test_tool_function_exception_chat_endpoint_never_crashes(client):
    """
    Simulates a tool function raising an unexpected OS-level PermissionError during an HTTP chat request.
    Verifies that /api/chat handles it safely and returns HTTP 200.
    """
    with patch("core.brain.GEMINI_API_KEY", ""):
        with patch.dict("os.environ", {"GEMINI_API_KEY": ""}):
            with patch("core.actions.system_control", side_effect=PermissionError("Access denied by OS driver")):
                response = client.post(
                    "/api/chat",
                    json={"text": "lock laptop", "session_id": "test_perm_err"},
                    headers=AUTH_HEADERS
                )

                assert response.status_code == 200
                data = response.json()
                assert "reply" in data
                assert len(data["reply"]) > 0
                assert data["action"]["status"] == "error"


def test_critical_unhandled_brain_exception_handled_by_api_endpoint(client):
    """
    Simulates an unexpected catastrophic failure inside process_voice_command.
    Verifies that handle_chat catches it at the endpoint layer and returns HTTP 200
    with a safe fallback message.
    """
    with patch("app.process_voice_command", side_effect=Exception("Critical memory fault")):
        response = client.post(
            "/api/chat",
            json={"text": "test command", "session_id": "test_fatal"},
            headers=AUTH_HEADERS
        )

        assert response.status_code == 200
        data = response.json()
        assert "reply" in data
        assert any(k in data["reply"].lower() for k in ["takneeki", "kshama", "error", "sorry", "trouble", "issue", "problem", "technical"])
        assert data["action"]["status"] == "error"
