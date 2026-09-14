import pytest
import os
from pathlib import Path
from core.tts import clean_text_for_tts, generate_audio, detect_lang_from_text
from config import TTS_RATE

def test_clean_text_basic_conversational_pacing():
    """Verifies that basic conversational sentences are preserved with clean spacing."""
    # English
    en = clean_text_for_tts("Hello, how can I help you today?")
    assert en == "Hello, how can I help you today?"

    # Hindi
    hi = clean_text_for_tts("नमस्ते, मैं आपकी कैसे मदद कर सकती हूँ?")
    assert hi == "नमस्ते, मैं आपकी कैसे मदद कर सकती हूँ?"

    # Hinglish
    hinglish = clean_text_for_tts("Bhai Chrome kholo aur YouTube pe Naruto search karo.")
    assert hinglish == "Bhai Chrome kholo aur YouTube pe Naruto search karo."

def test_clean_text_colons_semicolons_and_times():
    """Colons and semicolons convert to soft commas, while digital times (10:30) are preserved."""
    # Non-time colon becomes soft pause
    t1 = clean_text_for_tts("Sure: I will open Chrome for you.")
    assert t1 == "Sure, I will open Chrome for you."

    # Semicolon becomes soft pause
    t2 = clean_text_for_tts("I opened Notepad; you can start typing.")
    assert t2 == "I opened Notepad, you can start typing."

    # Digital times preserve colons
    t3 = clean_text_for_tts("The current time is 10:30 am.")
    assert "10:30" in t3

def test_clean_text_quotations_and_parentheses():
    """Quotes and parentheses are stripped to avoid quotation-mode/aside pauses."""
    # Double quotes stripped
    t1 = clean_text_for_tts('"Okay, done!"')
    assert t1 == "Okay, done!"

    # Contractions like don't and it's preserved
    t2 = clean_text_for_tts("don't worry, it's working fine!")
    assert "don't" in t2
    assert "it's" in t2

    # Parentheses stripped cleanly
    t3 = clean_text_for_tts("(Done) I have opened Notepad.")
    assert t3 == "Done I have opened Notepad."

def test_clean_text_ellipses_and_trailing_cadence():
    """Ellipses convert to breath pauses without leaving an unfinished trailing comma."""
    # Ellipses mid-sentence
    t1 = clean_text_for_tts("Hello... how can I help you?")
    assert t1 == "Hello, how can I help you?"

    # Trailing ellipsis resolves to period, never trailing comma
    t2 = clean_text_for_tts("Thinking...")
    assert t2 == "Thinking."
    assert not t2.endswith(",")

def test_clean_text_multiline_and_numbered_lists():
    """Numbered list items and line breaks are converted to flowing spoken pauses."""
    raw = "Here are the details:\n1. Item one\n2. Item two"
    cleaned = clean_text_for_tts(raw)
    assert "Item one" in cleaned
    assert "Item two" in cleaned
    assert not cleaned.startswith(",")

def test_clean_text_markdown_and_emojis():
    """Markdown bold, italic, code ticks, headers, and emojis are completely removed."""
    raw = "### System Status\n**All systems** `operational` 🚀! ✓"
    cleaned = clean_text_for_tts(raw)
    assert "###" not in cleaned
    assert "**" not in cleaned
    assert "`" not in cleaned
    assert "🚀" not in cleaned
    assert "✓" not in cleaned
    assert "All systems operational!" in cleaned

def test_configured_tts_rate_is_fluid():
    """Verifies that the configured default TTS rate is positive and set for natural flow."""
    assert TTS_RATE in ("+0%", "+2%", "+3%", "+4%", "+5%", "+18%")

@pytest.mark.asyncio
async def test_generate_audio_multilingual_flow():
    """Generates audio across English, Hindi, Hinglish, and short replies to ensure valid synthesis."""
    samples = [
        ("Hello, how can I help you today?", "en-IN"),
        ("नमस्ते, मैं आपकी कैसे मदद कर सकती हूँ?", "hi-IN"),
        ("Bhai Chrome kholo aur YouTube pe Naruto search karo.", "hi-IN"),
        ("Done.", "en-IN"),
        ("Kaam ho gaya.", "hi-IN")
    ]
    for text, lang in samples:
        url = await generate_audio(text, lang=lang)
        assert url.startswith("/static/audio/")
        rel_path = url.lstrip("/")
        audio_file = Path(rel_path)
        assert audio_file.exists()
        assert audio_file.stat().st_size > 500

