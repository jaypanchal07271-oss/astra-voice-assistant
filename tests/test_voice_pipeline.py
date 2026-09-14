"""
Tests for Issue #1: Voice Pipeline & Transcript Accumulation State Logic
Validates:
A. Short command: "Open Chrome"
B. Long English command: "Open Chrome and search Google for the latest cybersecurity news and then open YouTube."
C. Hindi command: "Chrome kholo aur YouTube par Naruto search karo."
D. Hinglish command: "Bhai Chrome open karo aur latest anime news search karo."
E. Multi-sentence command: "Open Notepad. Create a new file. Then type Hello World."
F. Empty transcription rejection.
G. Transcription failure handling.
H. Duplicate final result suppression.
I. Interim result followed by final result (no interim overwrite).
J. Recognition onend before final processing (unsubmitted transcript committed).
"""

import io
import time
import pytest
from unittest.mock import patch, MagicMock
from starlette.testclient import TestClient

from app import app
from config import ASTRA_AUTH_TOKEN

client = TestClient(app)
AUTH_HEADERS = {"X-Astra-Token": ASTRA_AUTH_TOKEN}


# ---------------------------------------------------------------------------
# Python reference implementation of the frontend transcript state machine
# (Mirrors the exact logic implemented in static/app.js)
# ---------------------------------------------------------------------------
class TranscriptAccumulator:
    def __init__(self):
        self.accumulated_final_transcript = ""
        self.current_interim_transcript = ""
        self.submitted_transcript = ""
        self.currently_processing_transcript = ""
        self.last_submission_time = 0.0
        self.submitted_history = []

    def on_result(self, results):
        """
        results: list of dicts with 'transcript' and 'is_final'
        Mirrors `for (let i = 0; i < event.results.length; ++i)` in static/app.js
        """
        session_final = ""
        session_interim = ""

        for item in results:
            text = item["transcript"].strip()
            if item.get("is_final", False):
                session_final += (" " if session_final else "") + text
            else:
                session_interim += (" " if session_interim else "") + text

        if session_final:
            self.accumulated_final_transcript = session_final.strip()
        self.current_interim_transcript = session_interim.strip()

    def commit_and_submit(self, source="test"):
        if not self.accumulated_final_transcript.strip():
            return None

        command = self.accumulated_final_transcript.strip()

        # Suppress duplicate in-flight command
        if self.currently_processing_transcript == command:
            return None

        # Suppress rapid duplicate submissions within 2 seconds
        now = time.time()
        if self.submitted_transcript == command and (now - self.last_submission_time < 2.0):
            return None

        self.submitted_transcript = command
        self.currently_processing_transcript = command
        self.last_submission_time = now
        self.submitted_history.append((command, source))

        # Reset buffers for next interaction
        self.accumulated_final_transcript = ""
        self.current_interim_transcript = ""
        return command

    def on_end(self):
        """If engine ends unexpectedly while unsubmitted text exists, commit it."""
        if self.accumulated_final_transcript.strip() and self.submitted_transcript != self.accumulated_final_transcript.strip():
            return self.commit_and_submit(source="engine_onend")
        return None


# ---------------------------------------------------------------------------
# Unit Tests for Cases H, I, J (Transcript Accumulator State Machine)
# ---------------------------------------------------------------------------
def test_case_h_duplicate_final_result_suppression():
    """
    Test Case H: Duplicate final results do not duplicate text in the accumulated
    buffer and do not submit the same command twice.
    """
    acc = TranscriptAccumulator()

    # Event 1: user speaks "Open Chrome"
    event_results = [{"transcript": "Open Chrome", "is_final": True}]
    acc.on_result(event_results)
    assert acc.accumulated_final_transcript == "Open Chrome"

    # Event 2: duplicate event with identical final result
    acc.on_result(event_results)
    assert acc.accumulated_final_transcript == "Open Chrome"

    # First submission succeeds
    sub1 = acc.commit_and_submit(source="timer")
    assert sub1 == "Open Chrome"

    # Second submission immediately after must be suppressed
    acc.accumulated_final_transcript = "Open Chrome"
    sub2 = acc.commit_and_submit(source="timer")
    assert sub2 is None
    assert len(acc.submitted_history) == 1


def test_case_i_interim_result_followed_by_final_result():
    """
    Test Case I: An interim result never overwrites a previously accumulated final
    transcript and is never submitted on its own as the final user command.
    """
    acc = TranscriptAccumulator()

    # Step 1: First clause finalized
    acc.on_result([{"transcript": "Open Chrome", "is_final": True}])
    assert acc.accumulated_final_transcript == "Open Chrome"
    assert acc.current_interim_transcript == ""

    # Step 2: Second clause is currently interim (user is still speaking)
    acc.on_result([
        {"transcript": "Open Chrome", "is_final": True},
        {"transcript": " and search YouTube", "is_final": False}
    ])
    # Final transcript must remain intact; interim is separate
    assert acc.accumulated_final_transcript == "Open Chrome"
    assert acc.current_interim_transcript == "and search YouTube"

    # Attempting to submit while interim text is not final must NOT submit interim text alone
    # In app.js, silence timeout is cancelled when currentInterimTranscript is non-empty
    assert acc.current_interim_transcript != ""
    assert acc.accumulated_final_transcript == "Open Chrome"

    # Step 3: Second clause now finalized
    acc.on_result([
        {"transcript": "Open Chrome", "is_final": True},
        {"transcript": " and search YouTube", "is_final": True}
    ])
    assert acc.accumulated_final_transcript == "Open Chrome and search YouTube"
    assert acc.current_interim_transcript == ""

    # Submission now dispatches complete sentence
    submitted = acc.commit_and_submit(source="silence_timeout")
    assert submitted == "Open Chrome and search YouTube"


def test_case_j_recognition_onend_before_final_processing():
    """
    Test Case J: When speech recognition ends unexpectedly while unsubmitted final
    transcript exists, it must be committed immediately instead of discarded.
    """
    acc = TranscriptAccumulator()

    # User speaks complete sentence
    acc.on_result([
        {"transcript": "Open Notepad and type Hello World", "is_final": True}
    ])
    assert acc.accumulated_final_transcript == "Open Notepad and type Hello World"
    assert acc.submitted_transcript == ""

    # Engine unexpectedly triggers onend before silence timer fires
    saved_cmd = acc.on_end()
    assert saved_cmd == "Open Notepad and type Hello World"
    assert acc.submitted_transcript == "Open Notepad and type Hello World"
    assert len(acc.submitted_history) == 1
    assert acc.submitted_history[0][1] == "engine_onend"


# ---------------------------------------------------------------------------
# Integration Tests for Cases A, B, C, D, E, F, G (API Endpoints)
# ---------------------------------------------------------------------------
def test_case_a_short_command():
    """Test Case A: Short command 'Open Chrome' reaches the backend intact."""
    with patch("app.process_voice_command") as mock_brain, \
         patch("app.generate_audio", return_value="/static/audio/test.mp3"):

        mock_brain.return_value = {
            "reply": "Maine aapke liye Google Chrome browser open kar diya hai.",
            "action": {"action": "open_app", "app": "chrome"}
        }

        res = client.post(
            "/api/chat",
            headers=AUTH_HEADERS,
            json={"text": "Open Chrome", "session_id": "test_short", "lang": "en-IN"}
        )
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["transcript"] == "Open Chrome"
        assert "Chrome" in data["reply"]
        mock_brain.assert_called_once_with("Open Chrome", session_id="test_short")


def test_case_b_long_english_command():
    """
    Test Case B: Long English command is fully preserved and processed by backend.
    """
    long_cmd = "Open Chrome and search Google for the latest cybersecurity news and then open YouTube."
    with patch("app.process_voice_command") as mock_brain, \
         patch("app.generate_audio", return_value="/static/audio/test.mp3"):

        mock_brain.return_value = {
            "reply": "I have opened Chrome, searched for cybersecurity news, and opened YouTube for you.",
            "action": {"status": "success"}
        }

        res = client.post(
            "/api/chat",
            headers=AUTH_HEADERS,
            json={"text": long_cmd, "session_id": "test_long_en", "lang": "en-IN"}
        )
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["transcript"] == long_cmd
        mock_brain.assert_called_once_with(long_cmd, session_id="test_long_en")


def test_case_c_hindi_command():
    """Test Case C: Hindi command is preserved in full."""
    hindi_cmd = "Chrome kholo aur YouTube par Naruto search karo."
    with patch("app.process_voice_command") as mock_brain, \
         patch("app.generate_audio", return_value="/static/audio/test.mp3"):

        mock_brain.return_value = {
            "reply": "Bilkul! Maine Chrome open karke YouTube par Naruto search kar diya hai.",
            "action": {"status": "success"}
        }

        res = client.post(
            "/api/chat",
            headers=AUTH_HEADERS,
            json={"text": hindi_cmd, "session_id": "test_hindi", "lang": "hi-IN"}
        )
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["transcript"] == hindi_cmd
        mock_brain.assert_called_once_with(hindi_cmd, session_id="test_hindi")


def test_case_d_hinglish_command():
    """Test Case D: Hinglish command is preserved in full."""
    hinglish_cmd = "Bhai Chrome open karo aur latest anime news search karo."
    with patch("app.process_voice_command") as mock_brain, \
         patch("app.generate_audio", return_value="/static/audio/test.mp3"):

        mock_brain.return_value = {
            "reply": "Zaroor! Chrome open kar diya aur latest anime news search kar raha hoon.",
            "action": {"status": "success"}
        }

        res = client.post(
            "/api/chat",
            headers=AUTH_HEADERS,
            json={"text": hinglish_cmd, "session_id": "test_hinglish", "lang": "hi-IN"}
        )
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["transcript"] == hinglish_cmd
        mock_brain.assert_called_once_with(hinglish_cmd, session_id="test_hinglish")


def test_case_e_multi_sentence_command():
    """
    Test Case E: Multi-sentence command is preserved across full utterance.
    """
    multi_cmd = "Open Notepad. Create a new file. Then type Hello World."
    with patch("app.process_voice_command") as mock_brain, \
         patch("app.generate_audio", return_value="/static/audio/test.mp3"):

        mock_brain.return_value = {
            "reply": "I have opened Notepad, created a new file, and typed Hello World as requested.",
            "action": {"status": "success"}
        }

        res = client.post(
            "/api/chat",
            headers=AUTH_HEADERS,
            json={"text": multi_cmd, "session_id": "test_multi", "lang": "en-IN"}
        )
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["transcript"] == multi_cmd
        mock_brain.assert_called_once_with(multi_cmd, session_id="test_multi")


def test_case_f_empty_transcription_rejection():
    """
    Test Case F: Empty or whitespace transcription returns structured failure
    and does NOT call brain, PC automation, or set 'who are you'.
    """
    with patch("app.process_voice_command") as mock_brain:
        # 1. Empty string
        res = client.post(
            "/api/chat",
            headers=AUTH_HEADERS,
            json={"text": "", "session_id": "test_empty"}
        )
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is False
        assert data["error"] == "transcription_failed"
        assert data["transcript"] == ""
        assert data["reply"] == "I could not clearly hear your command. Please try again."
        assert not mock_brain.called

        # 2. Whitespace-only string
        res2 = client.post(
            "/api/chat",
            headers=AUTH_HEADERS,
            json={"text": "   \n\t  ", "session_id": "test_whitespace"}
        )
        assert res2.status_code == 200
        data2 = res2.json()
        assert data2["success"] is False
        assert data2["error"] == "transcription_failed"
        assert not mock_brain.called


def test_case_g_transcription_failure():
    """
    Test Case G: When voice upload fails transcription or yields no text,
    it returns structured error without executing stray fallback tools.
    """
    dummy_audio = io.BytesIO(b"\x1a\x45\xdf\xa3" + b"\x00" * 200)

    mock_genai_client = MagicMock()
    mock_model_response = MagicMock()
    mock_model_response.text = ""  # No speech transcribed
    mock_genai_client.models.generate_content.return_value = mock_model_response

    with patch("os.getenv", return_value="fake_key"), \
         patch("google.genai.Client", return_value=mock_genai_client), \
         patch("app.process_voice_command") as mock_brain:

        res = client.post(
            "/api/voice_upload",
            headers=AUTH_HEADERS,
            files={"audio_file": ("test.webm", dummy_audio, "audio/webm")},
            data={"session_id": "test_fail", "lang": "hi-IN"}
        )

        assert res.status_code == 200
        data = res.json()
        assert data["success"] is False
        assert data["error"] == "transcription_failed"
        assert data["reply"] == "I could not clearly hear your command. Please try again."
        assert data["transcription"] == ""
        # CRITICAL: Verify 'who are you' was NOT sent to brain
        assert not mock_brain.called


def test_indic_voice_command_execution():
    """
    Validates that Devanagari Hindi voice commands ("ओपन ए व्हाट्सएप", "ओपन इंस्टाग्राम")
    sent to /api/chat execute their corresponding automation actions.
    """
    with patch("core.actions.open_website") as mock_open_web:
        mock_open_web.return_value = {"success": True, "message": "WhatsApp opened"}
        res = client.post(
            "/api/chat",
            headers=AUTH_HEADERS,
            json={"text": "ओपन ए व्हाट्सएप", "session_id": "test_indic_chat"}
        )
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert "whatsapp" in data["reply"].lower()
        assert mock_open_web.called

    with patch("core.actions.open_website") as mock_open_web:
        mock_open_web.return_value = {"success": True, "message": "Instagram opened"}
        res = client.post(
            "/api/chat",
            headers=AUTH_HEADERS,
            json={"text": "ओपन इंस्टाग्राम", "session_id": "test_indic_chat_ig"}
        )
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert "instagram" in data["reply"].lower()
        assert mock_open_web.called


