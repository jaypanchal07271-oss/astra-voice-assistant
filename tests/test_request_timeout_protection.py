"""
Tests for Request Timeout Protection, Anti-Stuck Processing, and Stale Response Safety.
Verifies:
1. /api/chat LLM timeout handling
2. /api/chat TTS timeout handling (Fix #5 preserved)
3. /api/voice_upload transcription timeout handling (Fix #3 preserved)
4. /api/voice_upload LLM timeout handling
5. /api/voice_upload TTS timeout handling
6. core/tts.py generate_audio communicate timeout and partial file cleanup
7. static/app.js frontend timeout, AbortController, request ID stale rejection, and isProcessing recovery
"""

import os
import io
import re
import asyncio
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock
import pytest
from fastapi.testclient import TestClient

from app import app
from config import ASTRA_AUTH_TOKEN, LLM_TIMEOUT, TTS_TIMEOUT, VOICE_TRANSCRIPTION_TIMEOUT
import core.tts as tts


@pytest.fixture
def client():
    return TestClient(app)


def test_api_chat_normal_completion(client, monkeypatch):
    """Verifies that normal chat request completes successfully without timeout."""
    async def mock_brain(text, session_id="default"):
        return {"reply": "Bilkul, main theek hoon. Aap batayein?", "action": None}

    async def mock_audio(text, lang="hi-IN"):
        return "/static/audio/test_normal.mp3"

    monkeypatch.setattr("app.process_voice_command", mock_brain)
    monkeypatch.setattr("app.generate_audio", mock_audio)
    monkeypatch.setattr("app.validate_audio_file", lambda u: True)

    resp = client.post(
        "/api/chat",
        headers={"X-Astra-Token": ASTRA_AUTH_TOKEN},
        json={"text": "Hello Astra", "lang": "hi-IN", "session_id": "test_normal"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert "Bilkul, main theek hoon" in data["reply"]
    assert data["audio_url"] == "/static/audio/test_normal.mp3"
    assert data["tts_failed"] is False


def test_api_chat_llm_timeout_handled_safely(client, monkeypatch):
    """Verifies that when LLM/process_voice_command times out in /api/chat,
    it returns a safe user-facing message, does not invent fake commands, and does not leak errors."""
    async def hanging_brain(text, session_id="default"):
        # Simulate an external hang by sleeping or raising TimeoutError
        raise asyncio.TimeoutError()

    monkeypatch.setattr("app.process_voice_command", hanging_brain)
    monkeypatch.setattr("app.generate_audio", AsyncMock(return_value="/static/audio/test_to.mp3"))
    monkeypatch.setattr("app.validate_audio_file", lambda u: True)

    resp = client.post(
        "/api/chat",
        headers={"X-Astra-Token": ASTRA_AUTH_TOKEN},
        json={"text": "tell me a story", "lang": "hi-IN", "session_id": "test_llm_to"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert "too long to process" in data["reply"].lower() or "samay lag gaya" in data["reply"]
    assert "who are you" not in data["reply"].lower()
    assert data["action"]["status"] == "timeout"


def test_api_chat_tts_timeout_preserves_text_and_sets_fix5_flags(client, monkeypatch):
    """Verifies that when TTS generation times out in /api/chat,
    the text reply is preserved and Fix #5 fields (audio_url: None, tts_failed: True, tts_error: True) are returned."""
    async def mock_brain(text, session_id="default"):
        return {"reply": "Yeh text reply zaroor bachega.", "action": None}

    async def hanging_tts(text, lang="hi-IN"):
        raise TimeoutError("TTS generation timed out")

    monkeypatch.setattr("app.process_voice_command", mock_brain)
    monkeypatch.setattr("app.generate_audio", hanging_tts)

    resp = client.post(
        "/api/chat",
        headers={"X-Astra-Token": ASTRA_AUTH_TOKEN},
        json={"text": "namaste", "lang": "hi-IN", "session_id": "test_tts_to"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["reply"] == "Yeh text reply zaroor bachega."
    assert data["audio_url"] is None
    assert data["tts_failed"] is True
    assert data["tts_error"] is True


def test_voice_upload_transcription_timeout_handled_safely(client, monkeypatch):
    """Verifies that when Gemini voice transcription times out in /api/voice_upload,
    it gracefully returns a transcription_failed error and NEVER executes a fake command like 'who are you'."""
    monkeypatch.setenv("GEMINI_API_KEY", "valid_mock_key_for_test")

    # Mock genai Client to hang/timeout
    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = asyncio.TimeoutError()

    # Patch process_voice_command to verify it is NEVER called
    brain_called = []
    async def mock_brain(cmd, session_id="default"):
        brain_called.append(cmd)
        return {"reply": "Should not run", "action": None}
    monkeypatch.setattr("app.process_voice_command", mock_brain)

    fake_audio = b"\x00" * 300
    with patch("google.genai.Client", return_value=mock_client):
        resp = client.post(
            "/api/voice_upload",
            headers={"X-Astra-Token": ASTRA_AUTH_TOKEN},
            files={"audio_file": ("recording.webm", io.BytesIO(fake_audio), "audio/webm")},
            data={"session_id": "test_upload_transcribe_to", "lang": "hi-IN"}
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is False
    assert data["error"] == "transcription_failed"
    assert "couldn't understand" in data["message"].lower() or "clearly hear" in data["reply"].lower()
    # CRITICAL: Confirm brain was NOT invoked with fake command
    assert brain_called == []


def test_voice_upload_llm_timeout_handled_safely(client, monkeypatch):
    """Verifies that when LLM process_voice_command times out in /api/voice_upload,
    it returns a clean timeout response without crashing."""
    async def hanging_brain(cmd, session_id="default"):
        raise asyncio.TimeoutError()

    monkeypatch.setattr("app.process_voice_command", hanging_brain)
    monkeypatch.setattr("app.generate_audio", AsyncMock(return_value="/static/audio/test.mp3"))
    monkeypatch.setattr("app.validate_audio_file", lambda u: True)

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "Mera calculation karo"
    mock_client.models.generate_content.return_value = mock_response
    monkeypatch.setenv("GEMINI_API_KEY", "test_key")

    fake_audio = b"\x00" * 300
    with patch("google.genai.Client", return_value=mock_client):
        resp = client.post(
            "/api/voice_upload",
            headers={"X-Astra-Token": ASTRA_AUTH_TOKEN},
            files={"audio_file": ("recording.webm", io.BytesIO(fake_audio), "audio/webm")},
            data={"session_id": "test_upload_llm_to", "lang": "hi-IN"}
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert "too long to process" in data["reply"].lower() or "samay lag gaya" in data["reply"]


def test_voice_upload_tts_timeout_preserves_text_and_flags(client, monkeypatch):
    """Verifies that when TTS times out in /api/voice_upload,
    the text reply is preserved and tts_failed is flagged."""
    async def mock_brain(cmd, session_id="default"):
        return {"reply": "Voice upload response preserved text.", "action": None}

    async def hanging_tts(text, lang="hi-IN"):
        raise TimeoutError("TTS synthesis timed out")

    monkeypatch.setattr("app.process_voice_command", mock_brain)
    monkeypatch.setattr("app.generate_audio", hanging_tts)

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "Test command"
    mock_client.models.generate_content.return_value = mock_response
    monkeypatch.setenv("GEMINI_API_KEY", "test_key")

    fake_audio = b"\x00" * 300
    with patch("google.genai.Client", return_value=mock_client):
        resp = client.post(
            "/api/voice_upload",
            headers={"X-Astra-Token": ASTRA_AUTH_TOKEN},
            files={"audio_file": ("recording.webm", io.BytesIO(fake_audio), "audio/webm")},
            data={"session_id": "test_upload_tts_to", "lang": "hi-IN"}
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["reply"] == "Voice upload response preserved text."
    assert data["audio_url"] is None
    assert data["tts_failed"] is True
    assert data["tts_error"] is True


@pytest.mark.asyncio
async def test_core_tts_timeout_cleans_up_partial_file(tmp_path, monkeypatch):
    """Verifies that core.tts.generate_audio times out and removes any partial file on disk."""
    monkeypatch.setattr("core.tts.AUDIO_DIR", tmp_path)
    monkeypatch.setenv("TTS_TIMEOUT", "0.05")

    class MockCommunicate:
        def __init__(self, *args, **kwargs):
            pass
        async def save(self, filepath):
            # Create partial file
            Path(filepath).write_bytes(b"partial_bytes")
            # Hang longer than timeout
            await asyncio.sleep(0.3)

    monkeypatch.setattr("edge_tts.Communicate", MockCommunicate)

    with pytest.raises(TimeoutError):
        await tts.generate_audio("Astra test timeout cleanup", lang="hi-IN")

    # Assert that the partial file was cleaned up and does not remain on disk
    remaining_files = list(tmp_path.glob("*.mp3"))
    assert len(remaining_files) == 0


def test_frontend_app_js_timeout_and_stale_guards():
    """Inspects static/app.js to verify frontend timeout handling,
    AbortController usage, request ID stale protection, and isProcessing recovery."""
    app_js_path = Path(__file__).resolve().parent.parent / "static" / "app.js"
    assert app_js_path.exists(), "static/app.js not found"
    content = app_js_path.read_text(encoding="utf-8")

    # 1. Verify request tracking variables exist
    assert "activeRequestId" in content
    assert "activeChatAbortController" in content
    assert "activeVoiceUploadAbortController" in content
    assert "FETCH_TIMEOUT_MS" in content

    # 2. Verify AbortController and timeout timer in sendVoiceCommand
    assert "controller = new AbortController()" in content
    assert "signal: controller.signal" in content
    assert "controller.abort('timeout')" in content
    assert "didTimeout" in content

    # 3. Verify stale response check (requestId !== activeRequestId)
    assert "requestId !== activeRequestId" in content

    # 4. Verify isProcessing recovery in sendVoiceCommand catch and finally
    send_cmd_match = re.search(r'async function sendVoiceCommand[\s\S]+?function updateInterruptUI', content)
    assert send_cmd_match, "sendVoiceCommand function block not found"
    send_cmd_code = send_cmd_match.group(0)
    assert "isProcessing = false;" in send_cmd_code
    assert "currentlyProcessingTranscript = '';" in send_cmd_code
    assert "setOrbState('idle');" in send_cmd_code

    # 5. Verify sendVoiceUpload has AbortController, timeout, and state recovery
    voice_up_match = re.search(r'async function sendVoiceUpload[\s\S]+?function startListening', content)
    assert voice_up_match, "sendVoiceUpload function block not found"
    voice_up_code = voice_up_match.group(0)
    assert "activeVoiceUploadAbortController" in voice_up_code
    assert "controller = new AbortController()" in voice_up_code
    assert "signal: controller.signal" in voice_up_code
    assert "isProcessing = false;" in voice_up_code
    assert "currentlyProcessingTranscript = '';" in voice_up_code

    # 6. Verify interruptPlayback cancels in-flight controllers
    interrupt_match = re.search(r'function interruptPlayback[\s\S]+?function playAudioResponse', content)
    assert interrupt_match, "interruptPlayback function block not found"
    interrupt_code = interrupt_match.group(0)
    assert "activeChatAbortController.abort('interrupted')" in interrupt_code
    assert "activeVoiceUploadAbortController.abort('interrupted')" in interrupt_code
    assert "activeRequestId++" in interrupt_code

    # 7. Verify startListening is guarded against speaking
    assert "!isAssistantSpeaking()" in content
    assert "!isTTSPlaying" in content
    assert "!isSpeaking" in content

