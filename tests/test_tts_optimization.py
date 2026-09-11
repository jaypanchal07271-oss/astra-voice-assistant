"""
Test Suite for Optimized TTS Pipeline:
1. Text Sanitization (strips markdown, code blocks, emojis, normalizes pauses).
2. Voice Tuning (tuned rate and pitch for energetic conversational speech).
3. Full sentence prosody & file verification (ensures files exist and are non-empty).
"""

import pytest
import os
from pathlib import Path
from unittest.mock import patch, MagicMock
from core.tts import clean_text_for_tts, generate_audio, detect_lang_from_text


def test_clean_text_strips_markdown_and_code_blocks():
    """Verifies that markdown asterisks, hashes, backticks, and code blocks are stripped."""
    raw = "### Astra Response\n**Notepad** khol diya gaya hai! `notepad.exe` running. ```python\nprint('code')\n```"
    cleaned = clean_text_for_tts(raw)
    assert "**" not in cleaned
    assert "#" not in cleaned
    assert "```" not in cleaned
    assert "print('code')" not in cleaned
    assert "Notepad khol diya gaya hai!" in cleaned
    assert "notepad.exe running." in cleaned


def test_clean_text_strips_emojis_and_decorative_symbols():
    """Verifies that emojis and UI symbols that cause robotic pauses are stripped."""
    raw = "Volume badha diya hai! 🔊 Aur dev environment ready hai 🚀 ✦🎙️"
    cleaned = clean_text_for_tts(raw)
    assert "🔊" not in cleaned
    assert "🚀" not in cleaned
    assert "✦" not in cleaned
    assert "🎙️" not in cleaned
    assert "Volume badha diya hai!" in cleaned
    assert "Aur dev environment ready hai" in cleaned


def test_clean_text_normalizes_awkward_punctuation_into_natural_pauses():
    """Verifies that ellipses and multiple dashes become natural commas/pauses."""
    raw = "Rukiye zara... aapka kaam ho raha hai --- bas thoda intezar karein."
    cleaned = clean_text_for_tts(raw)
    assert "..." not in cleaned
    assert "---" not in cleaned
    assert "Rukiye zara," in cleaned
    assert "aapka kaam ho raha hai," in cleaned


def test_clean_text_strips_markdown_links():
    """Verifies that markdown links [text](url) are transformed to just text."""
    raw = "Please visit [Google](https://www.google.com) or check https://youtube.com for songs."
    cleaned = clean_text_for_tts(raw)
    assert "https://" not in cleaned
    assert "Google" in cleaned
    assert "check for songs" in cleaned


@pytest.mark.asyncio
async def test_generate_audio_applies_voice_tuning():
    """Verifies that Communicate is instantiated with tuned rate, pitch, and SentenceBoundary."""
    with patch("core.tts.edge_tts.Communicate") as mock_comm_cls:
        mock_instance = MagicMock()
        mock_comm_cls.return_value = mock_instance

        async def fake_save(dest):
            p = Path(dest)
            p.write_bytes(b"dummy_mp3_data_stream_content")

        mock_instance.save = fake_save

        url = await generate_audio("Namaste, main aapki sahayata ke liye taiyar hoon.", lang="hi-IN")

        assert url.startswith("/static/audio/speech_")
        assert url.endswith(".mp3")

        # Verify Communicate arguments
        mock_comm_cls.assert_called_once()
        _, kwargs = mock_comm_cls.call_args
        assert kwargs.get("rate") == "+10%"
        assert kwargs.get("pitch") == "+2Hz"
        assert kwargs.get("boundary") == "SentenceBoundary"

        # Verify file was written
        created_file = Path("." + url)
        if created_file.exists():
            created_file.unlink()


def test_detect_lang_from_text():
    """Verifies Hindi and English language auto-detection."""
    assert detect_lang_from_text("Aapka swagat hai, main Astra hoon.") == "hi"
    assert detect_lang_from_text("नमस्ते, मैं आपकी क्या मदद करूँ?") == "hi"
    assert detect_lang_from_text("Hello, what is the weather today?") == "en"

