"""
Unit and integration tests for Instagram DM automation module (core/instagram_agent.py).
Verifies rate limiting, atomic daily cap updates, audit logging, manual action detection,
DOM verification on send, unread scraping, and brain intent routing.
"""

import os
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from core import actions, brain, instagram_agent


@pytest.fixture(autouse=True)
def reset_instagram_state(tmp_path, monkeypatch):
    """Resets rate limiting timestamps, redirects usage and audit files to temporary directory."""
    instagram_agent.InstagramSession._instance = None
    with instagram_agent._RATE_LIMIT_LOCK:
        instagram_agent._LAST_DM_TIMESTAMP.clear()

    test_usage_file = tmp_path / "instagram_usage.json"
    test_audit_file = tmp_path / "instagram_audit.log"

    monkeypatch.setattr(instagram_agent, "INSTAGRAM_USAGE_FILE", test_usage_file)
    monkeypatch.setattr(instagram_agent, "INSTAGRAM_AUDIT_LOG", test_audit_file)
    monkeypatch.setattr(instagram_agent, "INSTAGRAM_RATE_LIMIT_SECONDS", 90)
    monkeypatch.setattr(instagram_agent, "INSTAGRAM_MAX_DMS_PER_DAY", 20)

    # Fast human delays for tests
    monkeypatch.setattr(instagram_agent, "human_delay", lambda min_s=0, max_s=0: None)
    monkeypatch.setattr(instagram_agent, "human_type", lambda target, text: None)

    yield {
        "usage_file": test_usage_file,
        "audit_file": test_audit_file
    }

    instagram_agent.InstagramSession._instance = None
    with instagram_agent._RATE_LIMIT_LOCK:
        instagram_agent._LAST_DM_TIMESTAMP.clear()


def test_rate_limit_per_user():
    """Verify 90s cooldown per recipient prevents rapid bursts but permits messaging other users."""
    user_a = "alex_dev"
    user_b = "sarah_code"

    # Initially allowed
    allowed, remaining = instagram_agent.check_rate_limit(user_a)
    assert allowed is True
    assert remaining == 0.0

    # Record DM to user_a
    instagram_agent.record_reply_sent(user_a)

    # Immediately checking user_a should be blocked
    allowed, remaining = instagram_agent.check_rate_limit(user_a)
    assert allowed is False
    assert remaining > 80.0

    # User_b should NOT be blocked
    allowed_b, remaining_b = instagram_agent.check_rate_limit(user_b)
    assert allowed_b is True
    assert remaining_b == 0.0


def test_daily_cap_enforcement_and_atomic_write(reset_instagram_state):
    """Verify daily cap ceiling blocks sending after 20 DMs and writes atomically to JSON."""
    usage_file = reset_instagram_state["usage_file"]
    today = instagram_agent._get_today_date_str()

    # Initially 0 count
    allowed, count, limit = instagram_agent.check_daily_cap(max_limit=20)
    assert allowed is True
    assert count == 0
    assert limit == 20

    # Increment up to 20
    for i in range(1, 21):
        new_count = instagram_agent.increment_daily_cap()
        assert new_count == i

    # Verify usage file contains valid JSON
    assert usage_file.exists()
    with open(usage_file, "r", encoding="utf-8") as f:
        data = json.load(f)
        assert data[today] == 20

    # Now daily cap must block
    allowed, count, limit = instagram_agent.check_daily_cap(max_limit=20)
    assert allowed is False
    assert count == 20

    # Direct send_instagram_dm should return daily_cap_reached
    res = instagram_agent.send_instagram_dm("john_doe", "Hello!")
    assert res["success"] is False
    assert res["status"] == "daily_cap_reached"


def test_atomic_write_uses_temp_file(tmp_path, monkeypatch):
    """Verify write_usage_data_atomic writes to .tmp and calls os.replace."""
    test_usage = tmp_path / "atomic_usage.json"
    monkeypatch.setattr(instagram_agent, "INSTAGRAM_USAGE_FILE", test_usage)

    replaced = []
    orig_replace = os.replace

    def mock_replace(src, dst):
        assert str(src).endswith(".tmp")
        assert Path(src).exists()
        replaced.append((src, dst))
        orig_replace(src, dst)

    monkeypatch.setattr(os, "replace", mock_replace)
    instagram_agent._write_usage_data_atomic({"2026-09-10": 5})

    assert len(replaced) == 1
    assert test_usage.exists()
    with open(test_usage, "r", encoding="utf-8") as f:
        assert json.load(f) == {"2026-09-10": 5}


def test_audit_logging(reset_instagram_state):
    """Verify audit entries are appended with correct metadata and statuses."""
    audit_file = reset_instagram_state["audit_file"]

    instagram_agent.log_audit_event("alice_smith", "Hey Alice, checking in!", "SUCCESS", "Bubble verified")
    instagram_agent.log_audit_event("alice_smith", "Another msg", "RATE_LIMITED", "Cooldown active")
    instagram_agent.log_audit_event("bob", "Hello", "DAILY_CAP_REACHED", "Limit 20")
    instagram_agent.log_audit_event("SYSTEM", "Direct Inbox", "MANUAL_ACTION_REQUIRED", "Login form detected")

    assert audit_file.exists()
    lines = audit_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 4
    assert "STATUS=SUCCESS | USER='alice_smith'" in lines[0]
    assert "STATUS=RATE_LIMITED | USER='alice_smith'" in lines[1]
    assert "STATUS=DAILY_CAP_REACHED | USER='bob'" in lines[2]
    assert "STATUS=MANUAL_ACTION_REQUIRED | USER='SYSTEM'" in lines[3]


def test_manual_action_required_detection():
    """Verify login page or security challenge returns manual_action_required status."""
    mock_page = MagicMock()
    mock_page.url = "https://www.instagram.com/accounts/login/?next=%2Fdirect%2F"
    mock_page.query_selector.return_value = None

    is_challenge, reason = instagram_agent.detect_login_or_challenge(mock_page)
    assert is_challenge is True
    assert "login" in reason.lower()

    # Test send_instagram_dm halts immediately when login detected
    res = instagram_agent.send_instagram_dm("charlie", "Hey there", _page=mock_page)
    assert res["success"] is False
    assert res["status"] == "manual_action_required"
    assert "manual login" in res["message"].lower() or "security verification" in res["message"].lower()


def test_send_dm_dom_verification_success(monkeypatch):
    """Verify sending succeeds when message bubble appears in the DOM."""
    mock_page = MagicMock()
    mock_page.url = "https://www.instagram.com/direct/new/"

    # Mock query selectors
    search_input = MagicMock()
    user_item = MagicMock()
    chat_btn = MagicMock()
    composer = MagicMock()
    sent_bubble = MagicMock()

    def mock_query_selector(sel):
        if "queryBox" in sel or "Search" in sel:
            return search_input
        if "david" in sel:
            return user_item
        if "Chat" in sel or "Next" in sel:
            return chat_btn
        if "textbox" in sel:
            return composer
        if "Hey David" in sel:
            return sent_bubble
        return None

    mock_page.query_selector.side_effect = mock_query_selector
    mock_page.wait_for_selector.return_value = True

    res = instagram_agent.send_instagram_dm("david", "Hey David, test message", _page=mock_page)
    assert res["success"] is True
    assert res["status"] == "sent"
    assert "Hey David, test message" in res["message"]


def test_send_dm_dom_verification_timeout(monkeypatch):
    """Verify send reports failure when message bubble never appears in the DOM."""
    mock_page = MagicMock()
    mock_page.url = "https://www.instagram.com/direct/new/"

    search_input = MagicMock()
    user_item = MagicMock()
    chat_btn = MagicMock()
    composer = MagicMock()

    def mock_query_selector(sel):
        if "queryBox" in sel or "Search" in sel:
            return search_input
        if "eva" in sel:
            return user_item
        if "Chat" in sel or "Next" in sel:
            return chat_btn
        if "textbox" in sel:
            return composer
        # Sent bubble is NEVER found
        return None

    mock_page.query_selector.side_effect = mock_query_selector
    mock_page.wait_for_selector.return_value = True

    # Speed up verification loop for test
    start_t = [0.0]
    orig_time = time.time
    def mock_time():
        start_t[0] += 5.0
        return start_t[0]

    monkeypatch.setattr(time, "time", mock_time)

    res = instagram_agent.send_instagram_dm("eva", "Hey Eva", _page=mock_page)
    assert res["success"] is False
    assert res["status"] == "failed"
    assert "bubble did not appear" in res["error"].lower()


def test_get_unread_dms_parsing():
    """Verify get_unread_dms accurately parses unread indicator rows."""
    mock_page = MagicMock()
    mock_page.url = "https://www.instagram.com/direct/inbox/"
    mock_page.query_selector.return_value = None

    row1 = MagicMock()
    def mock_row1_qs(sel):
        if "x193iq5w" in sel or "unread" in sel:
            return MagicMock()
        if "x1lliihq" in sel:
            return MagicMock(inner_text=lambda: "emma_watson")
        if "x1rg5ohu" in sel:
            return MagicMock(inner_text=lambda: "Are we meeting today?")
        return None

    row1.query_selector.side_effect = mock_row1_qs

    row2 = MagicMock()
    # row2 has no unread dot and not bold
    row2.query_selector.return_value = None

    mock_page.query_selector_all.return_value = [row1, row2]

    items = instagram_agent.get_unread_dms(max_chats=5, _page=mock_page)

    assert len(items) == 1
    assert items[0]["sender"] == "emma_watson"
    assert items[0]["snippet"] == "Are we meeting today?"
    assert items[0]["status"] == "unread"


def test_brain_intent_parser_flexible_patterns(monkeypatch):
    """Verify fallback parser handles various sentence structures and phrasings."""
    dispatched = []

    def mock_send(username, message):
        dispatched.append((username, message))
        return {"success": True, "status": "sent", "recipient": username, "message": f"Sent {message}"}

    monkeypatch.setattr(actions, "send_instagram_dm", mock_send)

    # Variation 1: "instagram par kasyap ko hello bhejo"
    res1 = brain._parse_fallback_intent("instagram par kasyap ko hello bhejo")
    assert "kasyap" in res1["reply"].lower()
    assert dispatched[-1] == ("kasyap", "hello")

    # Variation 2: "X ko Y bhejo instagram pe"
    res2 = brain._parse_fallback_intent("shivam ko good morning bhejo instagram pe")
    assert "shivam" in res2["reply"].lower()
    assert dispatched[-1] == ("shivam", "good morning")

    # Variation 3: "insta pe X ko Y send karo"
    res3 = brain._parse_fallback_intent("insta pe rahul ko call me send karo")
    assert "rahul" in res3["reply"].lower()
    assert dispatched[-1] == ("rahul", "call me")

    # Variation 4: "X ko instagram dm karo Y"
    res4 = brain._parse_fallback_intent("neha ko instagram dm karo happy birthday")
    assert "neha" in res4["reply"].lower()
    assert dispatched[-1] == ("neha", "happy birthday")

    # Variation 5: "send a dm to X on insta saying Y"
    res5 = brain._parse_fallback_intent("send a dm to alex on insta saying how are you")
    assert "alex" in res5["reply"].lower()
    assert dispatched[-1] == ("alex", "how are you")


def test_brain_intent_parser_check_unread(monkeypatch):
    """Verify unread DM voice queries route to get_instagram_unread."""
    mock_unread = MagicMock(return_value={
        "success": True,
        "action": "get_instagram_unread",
        "count": 1,
        "message": "Instagram par 1 chat me naye message hain: alex: 'Hi!'"
    })
    monkeypatch.setattr(actions, "get_instagram_unread", mock_unread)

    res = brain._parse_fallback_intent("check instagram dms")
    assert "Instagram par 1 chat" in res["reply"]

    res2 = brain._parse_fallback_intent("insta pe unread messages dekho")
    assert "Instagram par 1 chat" in res2["reply"]


def test_brain_multi_turn_pending_instagram(monkeypatch):
    """Verify clarification flow: prompt when message is missing, then send on next turn."""
    dispatched = []

    def mock_send(username, message):
        dispatched.append((username, message))
        return {"success": True, "status": "sent", "recipient": username, "message": f"Sent {message}"}

    monkeypatch.setattr(actions, "send_instagram_dm", mock_send)

    session_id = "test_multi_turn_ig"
    brain.session_manager.reset_session(session_id)

    # Turn 1: User gives user name but no message
    res1 = brain._parse_fallback_intent("instagram par kasyap ko dm bhejo", session_id=session_id)
    assert res1["action"]["status"] == "clarification_needed"
    assert "what message would you like to send to kasyap" in res1["reply"].lower()

    # Turn 2: User provides message text
    res2 = brain._parse_fallback_intent("kal milte hain shaam ko", session_id=session_id)
    assert "instagram" in res2["reply"].lower() and "kasyap" in res2["reply"].lower()
    assert dispatched[-1] == ("Kasyap", "kal milte hain shaam ko")


def test_instagram_daily_cap_direct_session(monkeypatch, tmp_path):
    """Test daily cap directly using InstagramSession instance."""
    import core.instagram_agent
    monkeypatch.setattr(core.instagram_agent, "DATA_DIR", tmp_path)
    monkeypatch.setattr(core.instagram_agent, "INSTAGRAM_MAX_DMS_PER_DAY", 20)

    session = core.instagram_agent.InstagramSession()
    session.usage_file = tmp_path / "instagram_usage.json"

    today = time.strftime("%Y-%m-%d")
    with open(session.usage_file, "w") as f:
        json.dump({today: 20}, f)

    result = session.send_instagram_dm("testuser", "Hello")
    assert result["status"] == "daily_cap_reached"


def test_instagram_rate_limit_direct_session(monkeypatch, tmp_path):
    """Test rate limit cooldown directly using InstagramSession instance."""
    import core.instagram_agent
    monkeypatch.setattr(core.instagram_agent, "DATA_DIR", tmp_path)
    monkeypatch.setattr(core.instagram_agent, "INSTAGRAM_RATE_LIMIT_SECONDS", 500)

    session = core.instagram_agent.InstagramSession()
    session.last_sent_time = time.time()

    result = session.send_instagram_dm("testuser", "Hello")
    assert result["status"] == "rate_limited"
    assert "Cooldown active" in result


@pytest.mark.asyncio
async def test_process_voice_command_hybrid_interception(monkeypatch):
    """Verify process_voice_command handles JSON and hybrid interception for YouTube, WhatsApp, and Instagram."""
    mock_chat = MagicMock()
    mock_chat.send_message.return_value = MagicMock(text='```json\n{"action": "send_instagram_message", "username": "priya", "message": "hello!"}\n```')

    dispatched = []
    monkeypatch.setattr("core.actions.send_instagram_dm", lambda username, message: dispatched.append((username, message)) or {"success": True, "status": "sent", "recipient": username, "message": f"Sent {message}"})

    with monkeypatch.context() as m:
        m.setattr("os.getenv", lambda k, d=None: "test_key" if k == "GEMINI_API_KEY" else d)
        m.setattr("core.brain.session_manager.get_or_create_chat", lambda *a, **kw: mock_chat)

        res = await brain.process_voice_command("instagram par priya ko hello bhejo", session_id="test_ig_interception")
        assert "priya" in res["reply"].lower()
        assert dispatched == [("priya", "hello!")]


def test_send_dm_route_a_primary_profile_navigation(monkeypatch):
    """Verify primary Route A navigates to user profile, clicks Message button, and cleans username."""
    mock_page = MagicMock()
    mock_page.url = "https://www.instagram.com/kavita/"

    message_btn = MagicMock()
    composer = MagicMock()
    sent_bubble = MagicMock()

    navigated_urls = []
    def mock_goto(url, **kw):
        navigated_urls.append(url)
    mock_page.goto.side_effect = mock_goto

    def mock_query_selector(sel):
        if "Message" in sel and ("button" in sel or "header" in sel):
            return message_btn
        if "textbox" in sel or "contenteditable" in sel:
            return composer
        if "Namaste" in sel:
            return sent_bubble
        return None

    mock_page.query_selector.side_effect = mock_query_selector
    mock_page.wait_for_selector.return_value = True

    res = instagram_agent.send_instagram_dm("@kavita ", "Namaste Kavita", _page=mock_page)
    assert res["success"] is True
    assert res["status"] == "sent"
    assert res["recipient"] == "kavita"  # Stripped @ and whitespace
    assert "https://www.instagram.com/kavita/" in navigated_urls
    # Route B (/direct/new/) should NOT have been visited because Route A succeeded
    assert not any("direct/new" in u for u in navigated_urls)
    assert message_btn.click.called


def test_send_dm_contenteditable_composer(monkeypatch):
    """Verify composer selector matches div[contenteditable='true'] and cleans multiple '@'."""
    mock_page = MagicMock()
    mock_page.url = "https://www.instagram.com/direct/new/"

    search_input = MagicMock()
    user_item = MagicMock()
    chat_btn = MagicMock()
    composer = MagicMock()
    sent_bubble = MagicMock()

    def mock_query_selector(sel):
        if "queryBox" in sel or "Search" in sel:
            return search_input
        if "rohit" in sel:
            return user_item
        if "Chat" in sel or "Next" in sel:
            return chat_btn
        if sel == 'div[contenteditable="true"]':
            return composer
        if "Hello Rohit" in sel:
            return sent_bubble
        return None

    mock_page.query_selector.side_effect = mock_query_selector
    mock_page.wait_for_selector.return_value = True

    res = instagram_agent.send_instagram_dm("@@rohit  ", "Hello Rohit", _page=mock_page)
    assert res["success"] is True
    assert res["recipient"] == "rohit"
    assert composer.click.called


def test_open_chat_via_search_success_with_interactive_row_and_enabled_button():
    """Verify _open_chat_via_search handles network idle, clicks interactive row, and waits for enabled Chat button."""
    mock_page = MagicMock()
    mock_search_input = MagicMock()
    mock_chat_btn = MagicMock()
    mock_chat_btn.is_enabled.return_value = True
    mock_chat_btn.get_attribute.return_value = "false"

    mock_row_loc = MagicMock()
    mock_row_loc.count.return_value = 1
    mock_page.locator.return_value = mock_row_loc

    def mock_query_selector(sel):
        if "queryBox" in sel:
            return mock_search_input
        if "Chat" in sel:
            return mock_chat_btn
        return None

    mock_page.query_selector.side_effect = mock_query_selector

    success = instagram_agent._open_chat_via_search(mock_page, "rohit_coder")

    assert success is True
    mock_page.goto.assert_called_with("https://www.instagram.com/direct/new/", wait_until="domcontentloaded", timeout=30000)
    mock_page.wait_for_load_state.assert_called_with("networkidle", timeout=4000)
    mock_row_loc.first.click.assert_called_once()
    mock_chat_btn.click.assert_called_once()


def test_open_chat_via_search_fails_gracefully_on_error():
    """Verify _open_chat_via_search safely catches errors and returns False."""
    mock_page = MagicMock()
    mock_page.goto.side_effect = Exception("Network timeout")

    success = instagram_agent._open_chat_via_search(mock_page, "rohit_coder")
    assert success is False


def test_instagram_session_open_chat_via_search():
    """Verify InstagramSession._open_chat_via_search delegates to the page helper."""
    session = instagram_agent.InstagramSession.get_instance()
    mock_page = MagicMock()
    session._page = mock_page

    with patch.object(instagram_agent, "_open_chat_via_search", return_value=True) as mock_helper:
        res = session._open_chat_via_search("test_user")
        assert res is True
        mock_helper.assert_called_once_with(mock_page, "test_user")


def test_open_chat_via_search_never_clicks_random_suggested_user():
    """Verify _open_chat_via_search rejects suggested accounts and aborts when target user is not found."""
    mock_page = MagicMock()
    mock_search_input = MagicMock()

    # Search returns 0 matching rows for "unknown_user"
    mock_row_loc = MagicMock()
    mock_row_loc.count.return_value = 0
    mock_page.locator.return_value = mock_row_loc

    # Even if suggested items or checkboxes exist in the modal, they do NOT match target user
    mock_suggested_checkbox = MagicMock()
    def mock_query_selector(sel):
        if "queryBox" in sel:
            return mock_search_input
        # Candidate selectors don't match unknown_user
        return None

    mock_page.query_selector.side_effect = mock_query_selector

    # Must return False and NEVER click a random account
    success = instagram_agent._open_chat_via_search(mock_page, "unknown_user")
    assert success is False
    assert mock_suggested_checkbox.click.call_count == 0


def test_search_instagram_user_direct_profile():
    """Verify search_instagram_user navigates to profile URL."""
    mock_page = MagicMock()
    mock_page.query_selector.return_value = None  # Not 404

    with patch.object(instagram_agent, "detect_login_or_challenge", return_value=(False, "")):
        res = instagram_agent.search_instagram_user("ananya_panday", _page=mock_page)
        assert res["success"] is True
        assert res["query"] == "ananya_panday"
        mock_page.goto.assert_called_with("https://www.instagram.com/ananya_panday/", wait_until="domcontentloaded", timeout=30000)


def test_brain_fallback_intent_parser_instagram_search():
    """Verify fallback_intent_parser identifies Instagram profile search intent."""
    with patch.object(actions, "search_instagram_user", return_value={"success": True, "message": "Profile opened."}) as mock_search:
        res = brain.fallback_intent_parser("instagram par priya search karo")
        assert res["action"]["success"] is True
        mock_search.assert_called_once_with(query="priya")



