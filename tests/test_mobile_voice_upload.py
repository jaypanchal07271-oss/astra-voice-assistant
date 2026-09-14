import io
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from starlette.testclient import TestClient

from app import app
from config import ASTRA_AUTH_TOKEN
from core.brain import session_manager

client = TestClient(app)
AUTH_HEADERS = {"X-Astra-Token": ASTRA_AUTH_TOKEN}


def test_voice_upload_rejects_empty_or_tiny_payload():
    """Audio payload under 100 bytes is rejected without attempting transcription or stray action."""
    tiny_audio = io.BytesIO(b"RIFF" + b"\x00" * 20)  # 24 bytes (<100 bytes)
    response = client.post(
        "/api/voice_upload",
        headers=AUTH_HEADERS,
        files={"audio_file": ("test.webm", tiny_audio, "audio/webm")},
        data={"session_id": "test_sess_tiny", "lang": "hi-IN"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is False
    assert data["error"] == "transcription_failed"
    assert "Empty or corrupt audio payload" in data.get("detail", "")
    assert data["reply"] == "I could not clearly hear your command. Please try again."
    assert data["transcription"] == ""
    assert data["audio_url"] is None


def test_voice_upload_handles_empty_transcription_gracefully():
    """
    When Gemini returns an empty transcription (silence/noise), Astra must return
    a polite error response and NEVER execute a fallback command like 'who are you'.
    """
    dummy_audio = io.BytesIO(b"\x1a\x45\xdf\xa3" + b"\x00" * 200)  # > 100 bytes

    # Mock Google GenAI client returning empty text
    mock_genai_client = MagicMock()
    mock_model_response = MagicMock()
    mock_model_response.text = ""
    mock_genai_client.models.generate_content.return_value = mock_model_response

    with patch("os.getenv", return_value="fake_gemini_key"), \
         patch("google.genai.Client", return_value=mock_genai_client), \
         patch("app.process_voice_command") as mock_process_cmd:

        response = client.post(
            "/api/voice_upload",
            headers=AUTH_HEADERS,
            files={"audio_file": ("recording.webm", dummy_audio, "audio/webm;codecs=opus")},
            data={"session_id": "test_sess_empty_stt", "lang": "hi-IN"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert data["error"] == "transcription_failed"
        assert data["reply"] == "I could not clearly hear your command. Please try again."
        assert data["transcription"] == ""
        # CRITICAL: ensure process_voice_command was NOT called with 'who are you'
        assert not mock_process_cmd.called


def test_voice_upload_successful_transcription_and_mime_normalization():
    """
    Valid mobile audio is normalized to clean MIME (e.g. audio/webm, audio/mp4, audio/ogg)
    and transcribed using the complete-sentence transcription prompt.
    """
    long_command = "Open Chrome and search YouTube for Jujutsu Kaisen Season 3"
    dummy_audio = io.BytesIO(b"\x1a\x45\xdf\xa3" + b"\x01" * 350)  # > 100 bytes

    mock_genai_client = MagicMock()
    mock_model_response = MagicMock()
    mock_model_response.text = long_command
    mock_genai_client.models.generate_content.return_value = mock_model_response

    async def fake_brain_process(cmd, session_id=None):
        return {
            "reply": f"Maine Chrome open kar diya aur YouTube par {cmd} search kar raha hoon.",
            "action": {"action": "open_app", "app": "chrome"},
            "intent": "open_app"
        }

    with patch("os.getenv", return_value="fake_gemini_key"), \
         patch("google.genai.Client", return_value=mock_genai_client), \
         patch("app.process_voice_command", side_effect=fake_brain_process), \
         patch("app.generate_audio", return_value="/static/audio/test_reply.mp3"):

        response = client.post(
            "/api/voice_upload",
            headers={
                **AUTH_HEADERS,
                "User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Mobile Safari/537.36"
            },
            files={"audio_file": ("recording_12345.webm", dummy_audio, "audio/webm;codecs=opus")},
            data={"session_id": "test_sess_mobile", "lang": "hi-IN"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["transcription"] == long_command
        assert data["transcript"] == long_command
        assert "Chrome" in data["reply"]
        assert data["audio_url"] == "/static/audio/test_reply.mp3"

        # Verify device was tagged as Mobile (Smartphone)
        user_device = session_manager.get_user_data("test_sess_mobile", "device")
        assert user_device == "Mobile (Smartphone)"

        # Verify prompt instructed Gemini to capture the ENTIRE audio recording
        call_args = mock_genai_client.models.generate_content.call_args
        contents = call_args.kwargs.get("contents", [])
        audio_part = contents[0]
        prompt_used = contents[1]
        assert audio_part.inline_data.mime_type == "audio/webm"
        assert "Transcribe the ENTIRE audio recording accurately into text" in prompt_used
        assert "Do not summarize. Do not truncate" in prompt_used


def test_start_listening_routes_mobile_device_to_media_recorder():
    """
    Verifies that startListening checks isMobileDevice() before native SpeechRecognition,
    routing mobile devices directly to MediaRecorder + Gemini STT.
    """
    from pathlib import Path
    import re

    app_js_path = Path(__file__).resolve().parent.parent / "static" / "app.js"
    assert app_js_path.exists(), "static/app.js must exist"
    code = app_js_path.read_text(encoding="utf-8")

    fn_match = re.search(r'function startListening[^{]*\{([\s\S]*?)(?:function stopListening|$)', code)
    assert fn_match, "startListening function must exist"
    fn_body = fn_match.group(1)

    # 1. Verifies isMobileDevice() check exists
    pos_mobile_check = fn_body.find("if (isMobileDevice())")
    assert pos_mobile_check != -1, "startListening must check isMobileDevice()"

    # 2. Verifies startMobileRecording() is called inside that check
    pos_mobile_record = fn_body.find("startMobileRecording();", pos_mobile_check)
    assert pos_mobile_record != -1, "startListening must call startMobileRecording() for mobile"

    # 3. Verifies early return so desktop SpeechRecognition is never touched on mobile
    pos_return = fn_body.find("return;", pos_mobile_record)
    assert pos_return != -1, "startListening must return early after calling startMobileRecording()"

    # 4. Verifies desktop SpeechRecognition check is placed AFTER the mobile check
    pos_speech_rec = fn_body.find("const SpeechRecognition", pos_return)
    assert pos_speech_rec != -1, "desktop SpeechRecognition fallback must occur after mobile routing"


def test_merge_transcripts_deduplication_and_containment():
    """
    Verifies mergeTranscripts properly deduplicates re-emitted sentences,
    handles full containment, word overlaps, and identical repetitions.
    """
    import subprocess
    from pathlib import Path

    app_js_path = Path(__file__).resolve().parent.parent / "static" / "app.js"
    assert app_js_path.exists(), "static/app.js must exist"

    # Pass path as forward slashes so Node.js handles it cross-platform
    js_path_str = str(app_js_path).replace("\\", "/")

    node_script = f"""
    const fs = require('fs');
    const code = fs.readFileSync('{js_path_str}', 'utf8');
    const normMatch = code.match(/function normalizeTranscript[\\s\\S]*?function mergeTranscripts[\\s\\S]*?\\n\\}}/);
    if (!normMatch) {{
        console.error("Functions not found");
        process.exit(1);
    }}
    eval(normMatch[0]);

    const tests = [
        // Re-emitted sub-phrase (user bug: 'Open WhatsApp and send a message' followed by 'Open WhatsApp')
        [mergeTranscripts('Open WhatsApp and send a message', 'Open WhatsApp'), 'Open WhatsApp and send a message'],
        // Growing transcript (existing sub-phrase of incoming)
        [mergeTranscripts('Open WhatsApp', 'Open WhatsApp and send a message'), 'Open WhatsApp and send a message'],
        // Word-level suffix/prefix overlap
        [mergeTranscripts('Open WhatsApp', 'WhatsApp and send'), 'Open WhatsApp and send'],
        // Hinglish multi-word overlap
        [mergeTranscripts('kal meeting hai', 'meeting hai 4 baje'), 'kal meeting hai 4 baje'],
        // Exact identical repetition
        [mergeTranscripts('Hello Astra', 'Hello Astra'), 'Hello Astra'],
        // Empty existing
        [mergeTranscripts('', 'Hello Astra'), 'Hello Astra'],
        // Empty incoming
        [mergeTranscripts('Hello Astra', ''), 'Hello Astra']
    ];

    for (const [actual, expected] of tests) {{
        if (actual !== expected) {{
            console.error(`Mismatch: got "${{actual}}", expected "${{expected}}"`);
            process.exit(2);
        }}
    }}
    console.log("ALL_PASSED");
    """

    res = subprocess.run(["node", "-e", node_script], capture_output=True, text=True)
    assert res.returncode == 0, f"mergeTranscripts test failed: {res.stderr or res.stdout}"
    assert "ALL_PASSED" in res.stdout


def test_mobile_voice_upload_transcription_status_feedback():
    """
    Verifies that stopMobileRecording informs the user that transcription and thinking
    with Gemini is occurring.
    """
    from pathlib import Path
    import re

    app_js_path = Path(__file__).resolve().parent.parent / "static" / "app.js"
    code = app_js_path.read_text(encoding="utf-8")

    fn_match = re.search(r'function stopMobileRecording[^{]*\{([\s\S]*?)(?:async function sendVoiceUpload|$)', code)
    assert fn_match, "stopMobileRecording function must exist"
    fn_body = fn_match.group(1)

    assert "Transcribing & thinking with Gemini" in fn_body

