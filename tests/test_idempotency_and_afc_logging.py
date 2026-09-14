"""
Unit tests for duplicate invocation suppression and Gemini AFC tool logging.
Verifies that:
1. Idempotency cache in core/actions.py prevents opening duplicate tabs or apps within 3 seconds.
2. Tool call counters in core/brain.py track and log invocations per session.
3. Fallback interceptor in core/brain.py is suppressed when AFC already called a tool.
4. Frontend static/app.js guards SpeechRecognition against multiple initializations.
"""

import time
import pytest
from unittest.mock import patch, MagicMock
from core import actions
from core import brain


@pytest.fixture(autouse=True)
def setup_teardown():
    actions.clear_idempotency_cache()
    brain.reset_session_tool_counts("test_session")
    yield
    actions.clear_idempotency_cache()
    brain.reset_session_tool_counts("test_session")


def test_open_website_idempotency_suppresses_duplicate_calls():
    with patch.object(actions, "_launch_browser_url", return_value=True) as mock_launch:
        # First call should succeed and launch browser
        res1 = actions.open_website("youtube")
        assert res1.get("success") is True
        assert res1.get("duplicate_suppressed") is not True
        assert mock_launch.call_count == 1

        # Second identical call within 3 seconds should be suppressed
        res2 = actions.open_website("youtube")
        assert res2.get("success") is True
        assert res2.get("duplicate_suppressed") is True
        assert mock_launch.call_count == 1  # Still 1! Not 2!

        # Different target should NOT be suppressed
        res3 = actions.open_website("google")
        assert res3.get("success") is True
        assert res3.get("duplicate_suppressed") is not True
        assert mock_launch.call_count == 2


def test_open_app_idempotency_suppresses_duplicate_calls():
    with patch("subprocess.Popen") as mock_popen, \
         patch("shutil.which", return_value=r"C:\Windows\notepad.exe"):
        # First call
        res1 = actions.open_app("notepad")
        assert res1.get("success") is True
        assert res1.get("duplicate_suppressed") is not True
        assert mock_popen.call_count == 1

        # Second identical call within 3 seconds
        res2 = actions.open_app("notepad")
        assert res2.get("success") is True
        assert res2.get("duplicate_suppressed") is True
        assert mock_popen.call_count == 1  # Suppressed!


def test_play_youtube_video_idempotency_suppresses_duplicate_calls():
    with patch.object(actions, "_launch_browser_url", return_value=True) as mock_launch, \
         patch.object(actions, "fetch_youtube_first_video_url", return_value="https://www.youtube.com/watch?v=mock123"):
        # First call
        res1 = actions.play_youtube_video("specialz song")
        assert res1.get("success") is True
        assert res1.get("duplicate_suppressed") is not True
        assert mock_launch.call_count == 1

        # Second call within 3 seconds
        res2 = actions.play_youtube_video("specialz song")
        assert res2.get("success") is True
        assert res2.get("duplicate_suppressed") is True
        assert mock_launch.call_count == 1


def test_search_instagram_user_idempotency_suppresses_duplicate_calls():
    with patch.object(actions, "_launch_browser_url", return_value=True) as mock_launch:
        # First call
        res1 = actions.search_instagram_user("elonmusk")
        assert res1.get("success") is True
        assert res1.get("duplicate_suppressed") is not True
        assert mock_launch.call_count == 1

        # Second call within 3 seconds (e.g. AFC + hybrid interception)
        res2 = actions.search_instagram_user("elonmusk")
        assert res2.get("success") is True
        assert res2.get("duplicate_suppressed") is True
        assert mock_launch.call_count == 1  # Suppressed! Still 1!

        # Third call (e.g. open_website nudging instagram)
        res3 = actions.search_instagram_user("@elonmusk")
        assert res3.get("success") is True
        assert res3.get("duplicate_suppressed") is True
        assert mock_launch.call_count == 1  # Suppressed! Still 1!

        # Different user should NOT be suppressed
        res4 = actions.search_instagram_user("zuck")
        assert res4.get("success") is True
        assert res4.get("duplicate_suppressed") is not True
        assert mock_launch.call_count == 2


def test_idempotency_cache_expires_after_window():
    with patch.object(actions, "_launch_browser_url", return_value=True) as mock_launch:
        # First call
        res1 = actions.open_website("youtube")
        assert res1.get("success") is True
        assert mock_launch.call_count == 1

        # Artificially age the cache entry beyond 3 seconds
        key = "open_website:youtube"
        with actions._IDEMPOTENCY_LOCK:
            if key in actions._IDEMPOTENCY_CACHE:
                old_ts, old_res = actions._IDEMPOTENCY_CACHE[key]
                actions._IDEMPOTENCY_CACHE[key] = (old_ts - 5.0, old_res)

        # Call after expiry should run again
        res2 = actions.open_website("youtube")
        assert res2.get("success") is True
        assert res2.get("duplicate_suppressed") is not True
        assert mock_launch.call_count == 2


def test_brain_tool_invocation_counter_and_session_isolation():
    brain.set_active_session("session_a")
    brain.reset_session_tool_counts("session_a")
    brain.set_active_session("session_b")
    brain.reset_session_tool_counts("session_b")

    brain.set_active_session("session_a")
    with patch.object(actions, "get_time_and_date", return_value={"message": "12:00 PM", "iso": "2026-09-11T12:00:00"}):
        brain.get_time_and_date()
        brain.get_time_and_date()

    brain.set_active_session("session_b")
    with patch.object(actions, "get_time_and_date", return_value={"message": "12:00 PM", "iso": "2026-09-11T12:00:00"}):
        brain.get_time_and_date()

    assert brain.get_session_tool_call_count("session_a", "get_time_and_date") == 2
    assert brain.get_session_tool_call_count("session_b", "get_time_and_date") == 1
    assert brain.get_session_tool_call_count("session_a") == 2
    assert brain.get_session_tool_call_count("session_b") == 1


@pytest.mark.asyncio
async def test_process_voice_command_skips_fallback_interceptor_when_afc_invoked():
    session_id = "test_afc_skip"
    brain.set_active_session(session_id)
    brain.reset_session_tool_counts(session_id)

    # Simulate Gemini AFC having executed open_or_search_website during chat.send_message
    mock_chat = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "YouTube open kar diya hai."

    def fake_send_message(text):
        # AFC invokes tool function inside Python
        brain.set_active_session(session_id)
        brain._log_tool_invocation("open_or_search_website", {"website": "youtube"})
        return mock_response

    mock_chat.send_message.side_effect = fake_send_message

    with patch("core.brain.session_manager.get_or_create_chat", return_value=mock_chat), \
         patch("core.brain.dispatch_action_safe") as mock_dispatch, \
         patch("core.brain.GEMINI_API_KEY", "test_key"), \
         patch.dict("os.environ", {"GEMINI_API_KEY": "test_key", "OPENROUTER_API_KEY": ""}):

        result = await brain.process_voice_command("youtube kholo", session_id=session_id)

        # Gemini AFC was executed, so fallback interceptor dispatch_action_safe MUST NOT BE CALLED
        assert mock_dispatch.call_count == 0
        assert "YouTube open kar diya hai." in result["reply"]


def test_app_js_has_domcontentloaded_recognition_guard():
    with open("static/app.js", "r", encoding="utf-8") as f:
        content = f.read()

    # Verify that window.addEventListener('DOMContentLoaded' contains 'if (!recognition)'
    dom_idx = content.find("window.addEventListener('DOMContentLoaded'")
    assert dom_idx != -1, "DOMContentLoaded listener not found in app.js"

    slice_after_dom = content[dom_idx:dom_idx + 300]
    assert "if (!recognition)" in slice_after_dom, "DOMContentLoaded listener lacks `if (!recognition)` guard!"


def test_tool_call_deduplication_set_suppresses_same_turn_duplicate():
    """Verify check_and_record_executed_action suppresses duplicates within the same session turn."""
    sess = "test_dedup_turn"
    brain.reset_executed_actions(sess)

    # First execution should not be duplicate
    is_dup1 = brain.check_and_record_executed_action(sess, "open_website", {"website": "youtube"})
    assert is_dup1 is False

    # Second identical execution in the same turn MUST be detected as duplicate
    is_dup2 = brain.check_and_record_executed_action(sess, "open_website", {"website": "youtube"})
    assert is_dup2 is True

    # Different arguments should NOT be duplicate
    is_dup3 = brain.check_and_record_executed_action(sess, "open_website", {"website": "whatsapp"})
    assert is_dup3 is False

    # After resetting for the next turn, the action can run again
    brain.reset_executed_actions(sess)
    is_dup4 = brain.check_and_record_executed_action(sess, "open_website", {"website": "youtube"})
    assert is_dup4 is False


def test_dispatch_action_safe_deduplication():
    """Verify dispatch_action_safe returns duplicate_suppressed when called with identical arguments."""
    sess = "test_dispatch_dedup"
    brain.set_active_session(sess)
    brain.reset_executed_actions(sess)

    with patch.object(actions, "open_website", return_value={"success": True, "message": "Opened"}) as mock_open:
        res1 = brain.dispatch_action_safe("open_website", {"website": "youtube"})
        assert res1.get("success") is True
        assert res1.get("duplicate_suppressed") is not True
        assert mock_open.call_count == 1

        # Second identical call in same turn
        res2 = brain.dispatch_action_safe("open_website", {"website": "youtube"})
        assert res2.get("success") is True
        assert res2.get("duplicate_suppressed") is True
        assert mock_open.call_count == 1  # Not called again!


@pytest.mark.asyncio
async def test_process_voice_command_structured_json_open_website_no_fallthrough():
    """Verify structured JSON action 'open_or_search_website' halts immediately without falling through."""
    session_id = "test_no_fallthrough_site"
    brain.set_active_session(session_id)
    brain.reset_session_tool_counts(session_id)

    mock_chat = MagicMock()
    mock_response = MagicMock()
    # Gemini outputs structured JSON
    mock_response.text = '```json\n{"action": "open_or_search_website", "website": "youtube"}\n```'
    mock_chat.send_message.return_value = mock_response

    dispatched = []
    def fake_dispatch(tool_name, params=None):
        dispatched.append((tool_name, params))
        return {"success": True, "message": "YouTube open kar diya hai."}

    with patch("core.brain.session_manager.get_or_create_chat", return_value=mock_chat), \
         patch("core.brain.dispatch_action_safe", side_effect=fake_dispatch), \
         patch.dict("os.environ", {"GEMINI_API_KEY": "test_key", "OPENROUTER_API_KEY": ""}):

        res = await brain.process_voice_command("open youtube", session_id=session_id)

        assert "YouTube open kar diya hai." in res["reply"]
        # Exactly ONE tool dispatch occurred (no fall-through to regex safeguards)
        assert len(dispatched) == 1
        assert dispatched[0][0] == "open_website"
        assert dispatched[0][1]["website"] == "youtube"


@pytest.mark.asyncio
async def test_process_voice_command_structured_json_open_application_no_fallthrough():
    """Verify structured JSON action 'open_application' halts immediately without falling through."""
    session_id = "test_no_fallthrough_app"
    brain.set_active_session(session_id)
    brain.reset_session_tool_counts(session_id)

    mock_chat = MagicMock()
    mock_response = MagicMock()
    mock_response.text = '```json\n{"action": "open_application", "app_name": "notepad"}\n```'
    mock_chat.send_message.return_value = mock_response

    dispatched = []
    def fake_dispatch(tool_name, params=None):
        dispatched.append((tool_name, params))
        return {"success": True, "message": "Notepad open kar diya hai."}

    with patch("core.brain.session_manager.get_or_create_chat", return_value=mock_chat), \
         patch("core.brain.dispatch_action_safe", side_effect=fake_dispatch), \
         patch.dict("os.environ", {"GEMINI_API_KEY": "test_key", "OPENROUTER_API_KEY": ""}):

        res = await brain.process_voice_command("notepad kholo", session_id=session_id)

        assert "Notepad open kar diya hai." in res["reply"]
        assert len(dispatched) == 1
        assert dispatched[0][0] == "open_app"
        assert dispatched[0][1]["app_name"] == "notepad"


