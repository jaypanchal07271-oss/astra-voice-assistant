"""
Test Suite for Fix #3: Voice Transcription Failure and Prevention of Fake Commands.

Covers:
Test 1: Normal voice ("Open Chrome" -> succeeds, processed normally, no regression).
Test 2: Empty transcription ("" -> no AI command processing, no PC action, proper error response).
Test 3: Whitespace transcription ("   " -> treated as empty, no command execution).
Test 4: Transcription exception (Gemini throws exception -> fails safely, no fake command, no PC action).
Test 5: Very short valid transcription ("Hi" -> treated as valid input, not rejected).
Test 6: Normal text chat (/api/chat works as expected).
Test 7: Mobile voice upload pipeline validation (empty vs valid).
Test 8: Frontend app.js contract validation (no 'who are you' fallback, proper failure handling).
"""

import io
import pytest
from unittest.mock import patch, MagicMock
from starlette.testclient import TestClient
from pathlib import Path
import re

from app import app
from config import ASTRA_AUTH_TOKEN
from core.brain import process_voice_command, fallback_intent_parser

client = TestClient(app)
AUTH_HEADERS = {"X-Astra-Token": ASTRA_AUTH_TOKEN}
DUMMY_AUDIO = io.BytesIO(b"\x1a\x45\xdf\xa3" + b"\x00" * 200)


def test_1_normal_voice_command():
    """Test 1: Normal voice command is processed normally without regression."""
    dummy_audio = io.BytesIO(b"\x1a\x45\xdf\xa3" + b"\x01" * 200)
    mock_genai_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "Open Chrome"
    mock_genai_client.models.generate_content.return_value = mock_response

    with patch("os.getenv", return_value="test_key"), \
         patch("google.genai.Client", return_value=mock_genai_client), \
         patch("app.process_voice_command") as mock_brain, \
         patch("app.generate_audio", return_value="/static/audio/test_normal.mp3"):

        mock_brain.return_value = {
            "reply": "Opening Google Chrome for you.",
            "action": {"action": "open_app", "app": "chrome", "success": True}
        }

        res = client.post(
            "/api/voice_upload",
            headers=AUTH_HEADERS,
            files={"audio_file": ("test.webm", dummy_audio, "audio/webm")},
            data={"session_id": "test_normal", "lang": "en-US"}
        )

        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["transcript"] == "Open Chrome"
        assert data["reply"] == "Opening Google Chrome for you."
        mock_brain.assert_called_once_with("Open Chrome", session_id="test_normal")


def test_2_empty_transcription():
    """Test 2: Empty transcription does NOT process command, execute PC action, or use fake fallback."""
    dummy_audio = io.BytesIO(b"\x1a\x45\xdf\xa3" + b"\x00" * 200)
    mock_genai_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = ""
    mock_genai_client.models.generate_content.return_value = mock_response

    with patch("os.getenv", return_value="test_key"), \
         patch("google.genai.Client", return_value=mock_genai_client), \
         patch("app.process_voice_command") as mock_brain, \
         patch("core.actions.open_app") as mock_open_app:

        res = client.post(
            "/api/voice_upload",
            headers=AUTH_HEADERS,
            files={"audio_file": ("test.webm", dummy_audio, "audio/webm")},
            data={"session_id": "test_empty", "lang": "hi-IN"}
        )

        assert res.status_code == 200
        data = res.json()
        assert data["success"] is False
        assert data["error"] == "transcription_failed"
        assert "message" in data
        assert "couldn't understand" in data["message"].lower() or "could not understand" in data["message"].lower()
        assert not mock_brain.called
        assert not mock_open_app.called


def test_3_whitespace_transcription():
    """Test 3: Whitespace-only transcription is treated as empty and does not execute commands."""
    dummy_audio = io.BytesIO(b"\x1a\x45\xdf\xa3" + b"\x00" * 200)
    mock_genai_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "   \n\t  "
    mock_genai_client.models.generate_content.return_value = mock_response

    with patch("os.getenv", return_value="test_key"), \
         patch("google.genai.Client", return_value=mock_genai_client), \
         patch("app.process_voice_command") as mock_brain, \
         patch("core.actions.open_app") as mock_open_app:

        res = client.post(
            "/api/voice_upload",
            headers=AUTH_HEADERS,
            files={"audio_file": ("test.webm", dummy_audio, "audio/webm")},
            data={"session_id": "test_ws", "lang": "hi-IN"}
        )

        assert res.status_code == 200
        data = res.json()
        assert data["success"] is False
        assert data["error"] == "transcription_failed"
        assert not mock_brain.called
        assert not mock_open_app.called


def test_4_transcription_exception_handling():
    """Test 4: When transcription service throws an exception, fail safely without fake command or PC action."""
    dummy_audio = io.BytesIO(b"\x1a\x45\xdf\xa3" + b"\x00" * 200)
    mock_genai_client = MagicMock()
    mock_genai_client.models.generate_content.side_effect = RuntimeError("Quota exceeded or network timeout")

    with patch("os.getenv", return_value="test_key"), \
         patch("google.genai.Client", return_value=mock_genai_client), \
         patch("app.process_voice_command") as mock_brain, \
         patch("core.actions.open_app") as mock_open_app:

        res = client.post(
            "/api/voice_upload",
            headers=AUTH_HEADERS,
            files={"audio_file": ("test.webm", dummy_audio, "audio/webm")},
            data={"session_id": "test_exception", "lang": "hi-IN"}
        )

        assert res.status_code == 200
        data = res.json()
        assert data["success"] is False
        assert data["error"] == "transcription_failed"
        assert "Internal processing error" not in data.get("detail", "") or data["detail"] == "Voice transcription failed or no speech detected"
        # No internal stack trace exposed
        assert "Quota exceeded" not in str(data)
        assert not mock_brain.called
        assert not mock_open_app.called


def test_5_very_short_valid_transcription():
    """Test 5: Very short valid words (e.g. 'Hi') must be preserved and not rejected."""
    dummy_audio = io.BytesIO(b"\x1a\x45\xdf\xa3" + b"\x01" * 200)
    mock_genai_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "Hi"
    mock_genai_client.models.generate_content.return_value = mock_response

    with patch("os.getenv", return_value="test_key"), \
         patch("google.genai.Client", return_value=mock_genai_client), \
         patch("app.process_voice_command") as mock_brain, \
         patch("app.generate_audio", return_value="/static/audio/test_short.mp3"):

        mock_brain.return_value = {
            "reply": "Hello! How can I help you?",
            "action": None
        }

        res = client.post(
            "/api/voice_upload",
            headers=AUTH_HEADERS,
            files={"audio_file": ("test.webm", dummy_audio, "audio/webm")},
            data={"session_id": "test_hi", "lang": "en-US"}
        )

        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["transcript"] == "Hi"
        mock_brain.assert_called_once_with("Hi", session_id="test_hi")


def test_6_normal_text_chat_still_works():
    """Test 6: Verify /api/chat behaves correctly with valid and empty text commands."""
    with patch("app.process_voice_command") as mock_brain, \
         patch("app.generate_audio", return_value="/static/audio/chat.mp3"):

        mock_brain.return_value = {
            "reply": "Notepad khol diya gaya hai.",
            "action": {"action": "open_app", "app": "notepad"}
        }

        # Valid text command
        res = client.post(
            "/api/chat",
            headers=AUTH_HEADERS,
            json={"text": "Open Notepad", "session_id": "test_chat_ok"}
        )
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["reply"] == "Notepad khol diya gaya hai."

        # Empty text command
        res_empty = client.post(
            "/api/chat",
            headers=AUTH_HEADERS,
            json={"text": "   ", "session_id": "test_chat_empty"}
        )
        assert res_empty.status_code == 200
        data_empty = res_empty.json()
        assert data_empty["success"] is False
        assert data_empty["error"] == "transcription_failed"


@pytest.mark.asyncio
async def test_7_brain_layer_rejects_empty_inputs_safely():
    """Test 7: Direct calls to process_voice_command and fallback_intent_parser reject empty input."""
    # process_voice_command
    res1 = await process_voice_command("")
    assert res1["action"] is None
    assert "reply" in res1

    res2 = await process_voice_command("    ")
    assert res2["action"] is None

    # fallback_intent_parser
    res3 = fallback_intent_parser("")
    assert res3["action"] is None

    res4 = fallback_intent_parser("    ")
    assert res4["action"] is None


def test_8_static_code_ensures_no_who_are_you_fallback_in_transcription_path():
    """Test 8: Verify static source code never assigns 'who are you' on transcription failure."""
    app_py_path = Path(__file__).resolve().parent.parent / "app.py"
    app_py = app_py_path.read_text(encoding="utf-8")

    # Ensure no pattern like 'if not transcription: transcription = "who are you"' exists
    assert 'transcription = "who are you"' not in app_py
    assert "transcription = 'who are you'" not in app_py
    assert "transcription='who are you'" not in app_py

    # Verify static/app.js handles data.success === false
    app_js_path = Path(__file__).resolve().parent.parent / "static" / "app.js"
    app_js = app_js_path.read_text(encoding="utf-8")

    assert "data.success === false" in app_js
    assert "Sorry, I couldn't understand that. Please try again." in app_js

