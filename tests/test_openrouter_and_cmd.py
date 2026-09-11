import pytest
import os
import json
from unittest.mock import patch, MagicMock
from core import actions
from core import brain


def test_execute_cmd_command_security_guardrails():
    """Verify dangerous CMD commands are strictly blocked by guardrails."""
    dangerous = [
        "format C:",
        "del /s /q C:\\*",
        "del /f important.txt",
        "rmdir /s folder",
        "rd /s /q temp",
        "shutdown /s /t 0",
        "reg delete HKLM\\Software",
        "diskpart"
    ]
    for cmd in dangerous:
        res = actions.execute_cmd_command(cmd)
        assert res["success"] is False
        assert "Security Guardrail" in res["message"]


def test_execute_cmd_command_empty():
    """Verify empty or whitespace command is rejected."""
    res = actions.execute_cmd_command("   ")
    assert res["success"] is False
    assert "empty" in res["message"]


def test_execute_cmd_command_success():
    """Verify benign diagnostic command succeeds and returns output."""
    res = actions.execute_cmd_command("echo HelloAstra")
    assert res["success"] is True
    assert "HelloAstra" in res["output"]


def test_load_user_profile():
    """Verify user_profile.json loads correctly with Boss persona."""
    profile = brain.load_user_profile()
    assert profile["call_name"] == "Boss"
    assert profile["name"] == "Jay Panchal"
    assert profile["city"] == "Ahmedabad"


@pytest.mark.asyncio
async def test_process_voice_command_openrouter_success():
    """Verify OpenRouter routing when OPENROUTER_API_KEY is configured."""
    session_id = "test_or_session"
    brain.set_active_session(session_id)

    mock_client = MagicMock()
    mock_choice = MagicMock()
    mock_msg = MagicMock()
    mock_msg.content = "Python version 3.12 chal raha hai Boss!"
    mock_msg.tool_calls = None
    mock_choice.message = mock_msg
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_client.chat.completions.create.return_value = mock_response

    with patch("core.brain._get_openrouter_client", return_value=mock_client), \
         patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-test-key", "OPENROUTER_MODEL": "test-model"}):

        res = await brain.process_voice_command("python check karo", session_id=session_id)

        assert "Boss" in res["reply"]
        assert "3.12" in res["reply"]
        assert mock_client.chat.completions.create.called


@pytest.mark.asyncio
async def test_process_voice_command_openrouter_fallback_on_error():
    """Verify fallback to offline engine when OpenRouter errors out and no Gemini key is set."""
    session_id = "test_or_fallback"
    brain.set_active_session(session_id)

    with patch("core.brain._process_via_openrouter", side_effect=RuntimeError("OpenRouter API error")), \
         patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-bad-key", "GEMINI_API_KEY": ""}):

        # When falling back without Gemini key, should use fallback_intent_parser
        res = await brain.process_voice_command("time kya hua", session_id=session_id)

        assert any(w in res["reply"].lower() for w in ["baj", "samay", "time", "date"])

