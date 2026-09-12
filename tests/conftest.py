import os
import pytest

@pytest.fixture(autouse=True)
def isolate_test_environment(monkeypatch):
    """
    Ensures unit tests do not make unintended live external LLM calls by default.
    Tests specifically targeting OpenRouter or Gemini override these keys via patch.dict.
    """
    # Isolate live OpenRouter calls during standard test suites
    monkeypatch.setenv("OPENROUTER_API_KEY", "")

