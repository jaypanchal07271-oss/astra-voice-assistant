import os
import pytest

from pathlib import Path
from config import AUDIO_DIR

@pytest.fixture(autouse=True)
def isolate_test_environment(monkeypatch):
    """
    Ensures unit tests do not make unintended live external LLM calls by default.
    Tests specifically targeting OpenRouter or Gemini override these keys via patch.dict.
    """
    # Isolate live OpenRouter calls during standard test suites
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("PRIMARY_PROVIDER", "auto")

    # Reset AI provider circuit breakers between tests for clean test isolation
    try:
        from core import brain
        brain.reset_openrouter_circuit_breaker()
        brain.reset_gemini_circuit_breaker()
    except Exception:
        pass

    # Ensure test fixtures exist for unit tests that mock generate_audio return paths
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    test_mock_files = [
        "speech_test123.mp3",
        "test_speech.mp3",
        "test_reply.mp3",
        "test_normal.mp3",
        "test_short.mp3",
        "chat.mp3",
        "test.mp3"
    ]
    for fname in test_mock_files:
        fpath = AUDIO_DIR / fname
        if not fpath.exists():
            fpath.write_bytes(b"dummy_test_audio_stream_data")

