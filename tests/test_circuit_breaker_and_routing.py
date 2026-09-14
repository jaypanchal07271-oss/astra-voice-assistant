import sys
import time
import socket
import pytest
from unittest.mock import patch, MagicMock
from core import brain


def test_openrouter_availability_and_circuit_breaker():
    """Verify circuit breaker trips and blocks openrouter calls."""
    brain.reset_openrouter_circuit_breaker()

    with patch.dict("os.environ", {"OPENROUTER_API_KEY": ""}):
        assert not brain.is_openrouter_available()

    with patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-valid-key"}):
        assert brain.is_openrouter_available()

        # Trip circuit breaker
        brain.trip_openrouter_circuit_breaker("Rate limit 429 reached", duration=10.0)
        assert not brain.is_openrouter_available()

        # Reset
        brain.reset_openrouter_circuit_breaker()
        assert brain.is_openrouter_available()


@pytest.mark.asyncio
async def test_primary_provider_gemini_bypasses_openrouter():
    """When PRIMARY_PROVIDER=gemini, OpenRouter is not invoked even if key is present."""
    brain.reset_openrouter_circuit_breaker()
    session_id = "test_primary_gemini"
    brain.set_active_session(session_id)

    with patch("core.brain._process_via_openrouter") as mock_openrouter, \
         patch("core.brain.fallback_intent_parser", return_value={"reply": "Offline fallback reply", "action": None}), \
         patch.dict("os.environ", {
             "PRIMARY_PROVIDER": "gemini",
             "OPENROUTER_API_KEY": "sk-or-dummy-key",
             "GEMINI_API_KEY": ""  # triggers fallback without touching OpenRouter
         }):

        res = await brain.process_voice_command("hello test", session_id=session_id)
        assert not mock_openrouter.called
        assert res["reply"] == "Offline fallback reply"


@pytest.mark.asyncio
async def test_openrouter_429_trips_circuit_breaker():
    """When OpenRouter raises 429 rate limit, circuit breaker is tripped for 3600s."""
    brain.reset_openrouter_circuit_breaker()
    session_id = "test_or_429"
    brain.set_active_session(session_id)

    mock_429_error = Exception("Error code: 429 - {'error': {'message': 'Rate limit exceeded: free-models-per-day'}}")

    with patch("core.brain._process_via_openrouter", side_effect=mock_429_error) as mock_openrouter, \
         patch("core.brain.fallback_intent_parser", return_value={"reply": "Fallback time 12:00", "action": None}), \
         patch.dict("os.environ", {
             "PRIMARY_PROVIDER": "openrouter",
             "OPENROUTER_API_KEY": "sk-or-rate-limited-key",
             "GEMINI_API_KEY": ""
         }):

        # First call triggers 429 and trips circuit breaker
        res1 = await brain.process_voice_command("time kya hua", session_id=session_id)
        assert mock_openrouter.call_count == 1
        assert not brain.is_openrouter_available()

        # Second call should immediately skip OpenRouter without invoking it
        res2 = await brain.process_voice_command("time kya hua", session_id=session_id)
        assert mock_openrouter.call_count == 1  # Still 1, did not call mock again!

    brain.reset_openrouter_circuit_breaker()


def test_windows_proactor_winerror_10054_handled():
    """Verify patched call_connection_lost handles ConnectionResetError gracefully without raising."""
    import run  # ensure patch is active

    class DummyTransport:
        def __init__(self):
            self._called_connection_lost = False
            self._protocol = MagicMock()
            self._sock = MagicMock()
            self._server = MagicMock()
            # Simulate Windows 10054 on shutdown
            self._sock.fileno.return_value = 1
            self._sock.shutdown.side_effect = ConnectionResetError(10054, "An existing connection was forcibly closed by the remote host")

    transport = DummyTransport()
    import asyncio.proactor_events
    # Calling the patched method should not raise ConnectionResetError
    asyncio.proactor_events._ProactorBasePipeTransport._call_connection_lost(transport, None)
    assert transport._called_connection_lost is True
    assert transport._sock is None


def test_dynamic_device_and_datetime_injection():
    """Verify system instruction replaces {current_datetime} and {user_device} dynamically."""
    from core.brain import get_effective_system_instruction, session_manager

    sid = "sess_device_test"
    session_manager.set_user_data(sid, "device", "Mobile (Smartphone)")

    instruction = get_effective_system_instruction("kya chal raha hai", session_id=sid)
    assert "User's Device: Mobile (Smartphone)" in instruction
    assert "{current_datetime}" not in instruction
    assert "{{current_datetime}}" not in instruction
    assert "{user_device}" not in instruction
    assert "{{user_device}}" not in instruction


def test_gemini_circuit_breaker_functionality():
    """Verify Gemini circuit breaker trips on 429 and parses retryDelay."""
    brain.reset_gemini_circuit_breaker()

    with patch.dict("os.environ", {"GEMINI_API_KEY": ""}):
        assert not brain.is_gemini_available()

    with patch.dict("os.environ", {"GEMINI_API_KEY": "AIzaSyTestKey"}):
        assert brain.is_gemini_available()

        # Trip circuit breaker with retryDelay string
        err_msg = "429 RESOURCE_EXHAUSTED: [{'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '8s'}]"
        brain.trip_gemini_circuit_breaker(err_msg, duration=30.0)
        assert not brain.is_gemini_available()

        # Reset
        brain.reset_gemini_circuit_breaker()
        assert brain.is_gemini_available()


@pytest.mark.asyncio
async def test_gemini_429_trips_breaker_and_fast_fallback_action():
    """When Gemini returns 429 quota error, circuit breaker trips and direct action executes immediately."""
    brain.reset_gemini_circuit_breaker()
    session_id = "test_gemini_429_fast"
    brain.set_active_session(session_id)

    mock_429 = Exception("429 RESOURCE_EXHAUSTED: retryDelay: 8s")

    with patch("core.brain.session_manager.get_or_create_chat") as mock_chat_factory, \
         patch.dict("os.environ", {
             "PRIMARY_PROVIDER": "gemini",
             "GEMINI_API_KEY": "AIzaSyTestKey",
             "OPENROUTER_API_KEY": ""
         }):

        mock_chat = MagicMock()
        mock_chat.send_message.side_effect = mock_429
        mock_chat_factory.return_value = mock_chat

        # Command: "Open YouTube"
        res = await brain.process_voice_command("Open YouTube", session_id=session_id)

        # 1. Circuit breaker must be tripped
        assert not brain.is_gemini_available()
        # 2. Action must be successfully executed via fast deterministic parser without hanging
        assert res["action"] is not None
        assert res["action"]["success"] is True
        assert res["action"]["action"] == "open_website"
        assert "YouTube" in res["reply"]

    brain.reset_gemini_circuit_breaker()


def test_get_ai_provider_status():
    """Verify get_ai_provider_status tracks Gemini and OpenRouter availability and degraded mode."""
    brain.reset_gemini_circuit_breaker()
    brain.reset_openrouter_circuit_breaker()

    with patch.dict("os.environ", {"GEMINI_API_KEY": "AIzaSyValidKey", "OPENROUTER_API_KEY": "sk-or-valid"}):
        status = brain.get_ai_provider_status()
        assert status["status"] == "ready"
        assert not status["degraded_mode"]
        assert status["gemini_available"]
        assert status["openrouter_available"]

        # Trip both
        brain.trip_gemini_circuit_breaker("429 RESOURCE_EXHAUSTED", duration=60.0)
        brain.trip_openrouter_circuit_breaker("Rate limit 429", duration=60.0)
        status_degraded = brain.get_ai_provider_status()
        assert status_degraded["status"] == "degraded"
        assert status_degraded["degraded_mode"] is True

    brain.reset_gemini_circuit_breaker()
    brain.reset_openrouter_circuit_breaker()


@pytest.mark.asyncio
async def test_honest_fallback_on_total_quota_exhaustion_general_query():
    """When all Gemini models and OpenRouter are exhausted/unavailable, general query receives honest quota reply."""
    brain.reset_gemini_circuit_breaker()
    brain.reset_openrouter_circuit_breaker()
    session_id = "test_honest_quota_reply"
    brain.set_active_session(session_id)

    mock_429 = Exception("429 RESOURCE_EXHAUSTED: quota exceeded 20 requests per day")

    with patch("core.brain.session_manager.get_or_create_chat") as mock_chat_factory, \
         patch("core.actions.search_web_for_answer", return_value={"success": False, "error": "Search unavailable"}), \
         patch.dict("os.environ", {
             "PRIMARY_PROVIDER": "gemini",
             "GEMINI_API_KEY": "AIzaSyTestKey",
             "OPENROUTER_API_KEY": ""
         }):

        mock_chat = MagicMock()
        mock_chat.send_message.side_effect = mock_429
        mock_chat_factory.return_value = mock_chat

        # General knowledge query (not a local PC command like 'Notepad kholo')
        res = await brain.process_voice_command("who is albert einstein", session_id=session_id)

        # Must not be a generic canned reply pretending nothing is wrong
        assert "quota" in res["reply"].lower()
        assert res.get("degraded_mode") is True
        assert res.get("ai_status") == "degraded"

    brain.reset_gemini_circuit_breaker()
    brain.reset_openrouter_circuit_breaker()


