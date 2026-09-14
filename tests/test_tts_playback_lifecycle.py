"""
Tests for Astra TTS Playback Lifecycle & Audio Output Integrity.

Verifies:
1. Complete text delivered to TTS across short, medium, long, Hindi, and Hinglish queries.
2. Audio synthesis pipeline preserves full sentence length (no truncation).
3. Chat endpoint (/api/chat) delivers complete reply string to audio generator.
4. Static frontend app.js enforcement:
   - Microphone/speech recognition is NEVER started in audioPlayer.onplay.
   - isSpeaking state guards startListening().
   - audioPlayer.onpause does not prematurely restart listening or mark playback finished.
   - audioPlayer.onended cleanly cleans up state.
"""

import pytest
import os
import re
from pathlib import Path
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from app import app
from config import ASTRA_AUTH_TOKEN
from core.tts import clean_text_for_tts, generate_audio

AUTH_HEADERS = {"X-Astra-Token": ASTRA_AUTH_TOKEN}


TEST_PROMPTS = {
    "short": "Notepad khol diya gaya hai.",
    "medium": "Main aapke liye Google Chrome browser khol raha hoon jahan aap browse kar sakte hain.",
    "long": (
        "Naruto is a Japanese manga series written and illustrated by Masashi Kishimoto. "
        "It tells the story of Naruto Uzumaki, a young ninja who seeks recognition from his peers "
        "and dreams of becoming the Hokage, the leader of his village. The story is told in two parts: "
        "the first set in Naruto's pre-teen years, and the second in his teens. The series is based on "
        "two one-shot manga by Kishimoto and has received widespread critical acclaim worldwide."
    ),
    "hinglish": "Bilkul! Maine aapke liye latest Bollywood news aur tech updates check kar liye hain. Aaj ka mausam bhi kaafi accha hai.",
    "hindi": "नमस्ते, मैं आपकी आवाज सुनकर बहुत खुश हुआ। आज मैं आपकी क्या सहायता कर सकता हूँ?"
}


def test_clean_text_preserves_complete_sentences_across_all_prompts():
    """Verify that clean_text_for_tts does not truncate words across short, medium, long, hinglish, and hindi."""
    for key, prompt in TEST_PROMPTS.items():
        cleaned = clean_text_for_tts(prompt)
        prompt_words = prompt.split()
        cleaned_words = cleaned.split()
        # Word counts must match (or differ only due to punctuation/space normalization)
        assert len(cleaned_words) >= len(prompt_words) * 0.9, f"Truncation detected for {key} prompt"
        if key == "long":
            assert "Naruto Uzumaki" in cleaned
            assert "Masashi Kishimoto" in cleaned
            assert "critical acclaim worldwide" in cleaned


@pytest.mark.asyncio
async def test_generate_audio_full_synthesis_without_truncation():
    """Verify generate_audio handles long text and passes complete string to edge_tts.Communicate."""
    long_text = TEST_PROMPTS["long"]
    with patch("core.tts.edge_tts.Communicate") as mock_comm:
        mock_instance = MagicMock()
        mock_comm.return_value = mock_instance

        async def fake_save(path):
            Path(path).write_bytes(b"dummy_mp3_data_stream_for_naruto")

        mock_instance.save = fake_save

        url = await generate_audio(long_text, lang="en-US")
        assert url.startswith("/static/audio/speech_")
        assert url.endswith(".mp3")

        # Verify Communicate received the sanitized full text
        mock_comm.assert_called_once()
        args, kwargs = mock_comm.call_args
        communicated_text = args[0]
        assert "Masashi Kishimoto" in communicated_text
        assert "critical acclaim worldwide" in communicated_text

        # Clean up file
        p = Path("." + url)
        if p.exists():
            p.unlink()


def test_api_chat_delivers_full_reply_to_tts():
    """Verify /api/chat passes full brain response to generate_audio without clipping."""
    client = TestClient(app)
    expected_reply = TEST_PROMPTS["long"]

    with patch("app.process_voice_command") as mock_brain, \
         patch("app.generate_audio") as mock_tts:

        mock_brain.return_value = {
            "reply": expected_reply,
            "action": None
        }
        mock_tts.return_value = "/static/audio/speech_test123.mp3"

        response = client.post(
            "/api/chat",
            json={"text": "Tell me about Naruto", "lang": "en-US"},
            headers=AUTH_HEADERS
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["reply"] == expected_reply
        assert data["audio_url"] == "/static/audio/speech_test123.mp3"

        # Verify generate_audio received exact untruncated reply
        mock_tts.assert_called_once()
        called_reply, called_kwargs = mock_tts.call_args
        assert called_reply[0] == expected_reply


def test_frontend_app_js_tts_lifecycle_rules():
    """
    Verify static/app.js enforces:
    1. audioPlayer.onplay does NOT trigger recognition.start().
    2. isSpeaking state variable exists and is integrated into isAssistantSpeaking().
    3. startListening() checks isAssistantSpeaking().
    4. onpause does NOT treat pause as playback completion.
    5. onended resets isSpeaking to false.
    """
    app_js_path = Path(__file__).resolve().parent.parent / "static" / "app.js"
    assert app_js_path.exists(), "static/app.js must exist"
    code = app_js_path.read_text(encoding="utf-8")

    # 1. Verify audioPlayer.onplay does NOT start recognition
    onplay_match = re.search(r'audioPlayer\.onplay\s*=\s*\(\)\s*=>\s*\{([^}]+)\}', code)
    assert onplay_match, "audioPlayer.onplay handler must exist"
    onplay_body = onplay_match.group(1)
    assert "recognition.start()" not in onplay_body, (
        "audioPlayer.onplay must NEVER call recognition.start()! "
        "Starting recognition during playback creates an acoustic loopback that terminates speech."
    )

    # 2. Verify isSpeaking and isTTSPlaying state variables exist
    assert re.search(r'let\s+isSpeaking\s*=\s*(false|true);', code), "isSpeaking state variable must be declared"
    assert re.search(r'let\s+isTTSPlaying\s*=\s*(false|true);', code), "isTTSPlaying state variable must be declared"

    # 3. Verify isAssistantSpeaking checks isSpeaking and isTTSPlaying
    assert "return isTTSPlaying || isSpeaking" in code or "isSpeaking ||" in code, (
        "isAssistantSpeaking() must check isSpeaking and isTTSPlaying"
    )

    # 4. Verify startListening guards against assistant speaking
    start_listening_match = re.search(r'function startListening[^{]*\{([\s\S]*?)(function|$)', code)
    assert start_listening_match, "startListening function must exist"
    sl_body = start_listening_match.group(1)
    assert "isAssistantSpeaking()" in sl_body, (
        "startListening() must guard against assistant speaking"
    )

    # 5. Verify audioPlayer.onended resets isSpeaking and isTTSPlaying
    onended_match = re.search(r'audioPlayer\.onended\s*=\s*\(\)\s*=>\s*\{([\s\S]*?)\};', code)
    assert onended_match, "audioPlayer.onended handler must exist"
    onended_body = onended_match.group(1)
    assert "isSpeaking = false;" in onended_body, "audioPlayer.onended must set isSpeaking = false"
    assert "isTTSPlaying = false;" in onended_body, "audioPlayer.onended must set isTTSPlaying = false"

    # 6. Verify pause is not treated as completed playback
    onpause_match = re.search(r'audioPlayer\.onpause\s*=\s*\(\)\s*=>\s*\{([\s\S]*?)\};', code)
    assert onpause_match, "audioPlayer.onpause handler must exist"
    onpause_body = onpause_match.group(1)
    assert "startListening" not in onpause_body, "audioPlayer.onpause must NOT trigger startListening()"
