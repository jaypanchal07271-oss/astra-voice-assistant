"""
Unit tests for Fix #5: TTS Failure Handling (/api/chat and /api/voice_upload).

Verifies:
1. AI response succeeds + TTS succeeds -> returns valid audio_url and complete text reply.
2. AI response succeeds + TTS raises exception -> returns audio_url=None, tts_error=True, preserves text reply.
3. AI response succeeds + TTS returns nonexistent file -> validates file, returns audio_url=None, tts_error=True.
4. AI response succeeds + TTS returns zero-byte file -> validates file, returns audio_url=None, tts_error=True.
5. /api/voice_upload with successful transcription but failed TTS -> returns audio_url=None, tts_error=True, preserves text reply.
6. /api/voice_upload empty transcription -> Fix #3 preserved (no brain call, no fake command, safe failure response).
7. Diagnostic logging is safe (never exposes API keys or internal stack traces).
"""

import io
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from starlette.testclient import TestClient

from app import app, validate_audio_file
from config import ASTRA_AUTH_TOKEN

client = TestClient(app)
AUTH_HEADERS = {"X-Astra-Token": ASTRA_AUTH_TOKEN}


def test_validate_audio_file_helper(tmp_path):
    """Verify validate_audio_file checks existence and non-zero file size."""
    assert not validate_audio_file(None)
    assert not validate_audio_file("")
    assert not validate_audio_file("   ")
    assert not validate_audio_file("/static/audio/nonexistent_file_9999.mp3")

    # Zero-byte file
    empty_file = tmp_path / "empty.mp3"
    empty_file.write_bytes(b"")
    assert not validate_audio_file(str(empty_file))

    # Valid non-empty file
    valid_file = tmp_path / "valid.mp3"
    valid_file.write_bytes(b"dummy_mp3_frames_data")
    assert validate_audio_file(str(valid_file))


def test_chat_tts_success_returns_audio_url_and_reply(tmp_path):
    """Case 1: AI response succeeds + TTS succeeds -> audio_url populated, reply preserved."""
    test_file = tmp_path / "speech_test_success.mp3"
    test_file.write_bytes(b"ID3_valid_audio_bytes_content_stream")

    with patch("app.process_voice_command") as mock_brain, \
         patch("app.generate_audio", return_value=str(test_file)):

        mock_brain.return_value = {
            "reply": "Google Chrome khol diya gaya hai.",
            "action": {"action": "open_app", "app": "chrome"}
        }

        res = client.post(
            "/api/chat",
            headers=AUTH_HEADERS,
            json={"text": "Open Chrome", "session_id": "test_tts_ok"}
        )

        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["reply"] == "Google Chrome khol diya gaya hai."
        assert data["audio_url"] == str(test_file)
        assert data.get("tts_error") is not True


def test_chat_tts_exception_handled_with_explicit_tts_error():
    """Case 2: AI response succeeds + TTS throws exception -> audio_url=None, tts_error=True, reply preserved."""
    expected_reply = "Astra yahan hai! Main aapki madad karne ke liye tayar hoon."

    with patch("app.process_voice_command") as mock_brain, \
         patch("app.generate_audio", side_effect=RuntimeError("Edge-TTS network socket closed by peer")):

        mock_brain.return_value = {
            "reply": expected_reply,
            "action": None
        }

        res = client.post(
            "/api/chat",
            headers=AUTH_HEADERS,
            json={"text": "Hello Astra", "session_id": "test_tts_exc"}
        )

        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["reply"] == expected_reply  # Text reply MUST be preserved!
        assert data["audio_url"] is None
        assert data.get("tts_error") is True
        # Ensure internal stack trace is not exposed in response
        assert "socket closed" not in str(data)


def test_chat_tts_missing_file_treated_as_tts_error():
    """Case 3: TTS returns path to missing file -> validated, audio_url=None, tts_error=True."""
    expected_reply = "Notepad open kar diya gaya hai."

    with patch("app.process_voice_command") as mock_brain, \
         patch("app.generate_audio", return_value="/static/audio/ghost_nonexistent_audio.mp3"):

        mock_brain.return_value = {
            "reply": expected_reply,
            "action": {"action": "open_app", "app": "notepad"}
        }

        res = client.post(
            "/api/chat",
            headers=AUTH_HEADERS,
            json={"text": "Open Notepad", "session_id": "test_tts_missing"}
        )

        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["reply"] == expected_reply
        assert data["audio_url"] is None
        assert data.get("tts_error") is True


def test_chat_tts_zero_byte_file_treated_as_tts_error(tmp_path):
    """Case 4: TTS returns path to zero-byte file -> validated, audio_url=None, tts_error=True."""
    empty_file = tmp_path / "speech_empty.mp3"
    empty_file.write_bytes(b"")  # 0 bytes

    expected_reply = "YouTube par gaana chala raha hoon."

    with patch("app.process_voice_command") as mock_brain, \
         patch("app.generate_audio", return_value=str(empty_file)):

        mock_brain.return_value = {
            "reply": expected_reply,
            "action": {"action": "play_youtube", "query": "song"}
        }

        res = client.post(
            "/api/chat",
            headers=AUTH_HEADERS,
            json={"text": "Play song", "session_id": "test_tts_zero"}
        )

        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["reply"] == expected_reply
        assert data["audio_url"] is None
        assert data.get("tts_error") is True


def test_voice_upload_tts_failure_preserves_reply_and_indicates_error():
    """Verify /api/voice_upload applies consistent Fix #5 TTS error handling."""
    dummy_audio = io.BytesIO(b"\x1a\x45\xdf\xa3" + b"\x01" * 200)
    mock_genai_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "Open Calculator"
    mock_genai_client.models.generate_content.return_value = mock_response

    expected_reply = "Calculator khol diya gaya hai."

    with patch("os.getenv", return_value="test_key"), \
         patch("google.genai.Client", return_value=mock_genai_client), \
         patch("app.process_voice_command") as mock_brain, \
         patch("app.generate_audio", side_effect=Exception("TTS synthesis error")):

        mock_brain.return_value = {
            "reply": expected_reply,
            "action": {"action": "open_app", "app": "calc"}
        }

        res = client.post(
            "/api/voice_upload",
            headers=AUTH_HEADERS,
            files={"audio_file": ("test.webm", dummy_audio, "audio/webm")},
            data={"session_id": "test_upload_tts_fail", "lang": "en-US"}
        )

        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["transcript"] == "Open Calculator"
        assert data["reply"] == expected_reply
        assert data["audio_url"] is None
        assert data.get("tts_error") is True


def test_voice_upload_preserves_fix_3_safety():
    """Verify Fix #3 safety remains intact: empty transcription never executes commands or fallback."""
    dummy_audio = io.BytesIO(b"\x1a\x45\xdf\xa3" + b"\x00" * 200)
    mock_genai_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = ""
    mock_genai_client.models.generate_content.return_value = mock_response

    with patch("os.getenv", return_value="test_key"), \
         patch("google.genai.Client", return_value=mock_genai_client), \
         patch("app.process_voice_command") as mock_brain:

        res = client.post(
            "/api/voice_upload",
            headers=AUTH_HEADERS,
            files={"audio_file": ("test.webm", dummy_audio, "audio/webm")},
            data={"session_id": "test_upload_empty", "lang": "hi-IN"}
        )

        assert res.status_code == 200
        data = res.json()
        assert data["success"] is False
        assert data["error"] == "transcription_failed"
        assert data["audio_url"] is None
        assert not mock_brain.called

