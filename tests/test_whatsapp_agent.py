"""
Tests for WhatsApp Web Automation Agent (Playwright)
Verifies:
1. Hard rate limit enforcement (max 1 reply per chat per 60 seconds).
2. Rate limit per-chat isolation (different contacts not blocked).
3. Cooldown expiration after 60 seconds.
4. Audit log file creation, entry formatting, and logging of successes and rejections.
5. DOM unread message parsing with mocked Playwright elements.
6. QR code detection handling when unauthenticated.
7. Fallback intent parser routing in core.brain.
8. MCP tools delegation in mcp_skills.
"""

import time
from unittest.mock import MagicMock
import pytest

from config import WHATSAPP_AUDIT_LOG
from core import actions, brain, whatsapp_agent
import mcp_skills


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """Clear rate limit memory between test cases."""
    with whatsapp_agent._RATE_LIMIT_LOCK:
        whatsapp_agent._LAST_REPLY_TIMESTAMP.clear()


def test_rate_limit_enforcement():
    """Verifies that the 60-second rate limit blocks immediate repeat messages to the same chat."""
    chat = "Priya Sharma"

    # First attempt should be permitted
    allowed, remaining = whatsapp_agent.check_rate_limit(chat)
    assert allowed is True
    assert remaining == 0.0

    # Record send
    whatsapp_agent.record_reply_sent(chat)

    # Immediate second attempt must be blocked
    allowed, remaining = whatsapp_agent.check_rate_limit(chat)
    assert allowed is False
    assert 55 <= remaining <= 60.0


def test_rate_limit_chat_isolation():
    """Verifies that rate limiting is isolated per contact/chat."""
    chat_a = "Alice"
    chat_b = "Bob"

    whatsapp_agent.record_reply_sent(chat_a)

    # Alice is blocked
    allowed_a, _ = whatsapp_agent.check_rate_limit(chat_a)
    assert allowed_a is False

    # Bob should remain allowed
    allowed_b, _ = whatsapp_agent.check_rate_limit(chat_b)
    assert allowed_b is True


def test_rate_limit_cooldown_expiration(monkeypatch):
    """Verifies that after 60 seconds have elapsed, sending is permitted again."""
    chat = "Charlie"
    current_time = [1000.0]

    monkeypatch.setattr(time, "time", lambda: current_time[0])

    whatsapp_agent.record_reply_sent(chat)
    allowed, _ = whatsapp_agent.check_rate_limit(chat)
    assert allowed is False

    # Fast forward 61 seconds
    current_time[0] += 61.0
    allowed, remaining = whatsapp_agent.check_rate_limit(chat)
    assert allowed is True
    assert remaining == 0.0


def test_audit_logging(tmp_path, monkeypatch):
    """Verifies that all automated actions and rejections are recorded in the audit log."""
    test_log_file = tmp_path / "whatsapp_test_audit.log"
    monkeypatch.setattr(whatsapp_agent, "WHATSAPP_AUDIT_LOG", test_log_file)

    # Log a successful send
    whatsapp_agent.log_audit_event("David", "See you tomorrow!", "SUCCESS", details="Delivered")

    # Log a rate-limited event
    whatsapp_agent.log_audit_event("David", "Are you there?", "RATE_LIMITED", details="Cooldown active")

    assert test_log_file.exists()
    content = test_log_file.read_text(encoding="utf-8")

    assert "STATUS=SUCCESS" in content
    assert "CHAT='David'" in content
    assert "PREVIEW='See you tomorrow!'" in content
    assert "STATUS=RATE_LIMITED" in content


def test_send_reply_rate_limit_blocking(monkeypatch):
    """Verifies send_reply returns rate limit error when cooldown is active."""
    chat = "Elena"
    whatsapp_agent.record_reply_sent(chat)

    res = whatsapp_agent.send_reply(chat, "Test message")
    assert res["success"] is False
    assert res.get("rate_limited") is True
    assert "Rate limit" in res["message"] or "Rate limit" in res["error"]


def test_get_unread_messages_with_mock_page():
    """Verifies DOM parsing of unread badges, contact names, and previews."""
    mock_page = MagicMock()

    # No QR code
    mock_page.query_selector.return_value = None

    # Mock two unread rows
    mock_row1 = MagicMock()
    mock_badge1 = MagicMock()
    mock_badge1.inner_text.return_value = "2"
    mock_badge1.get_attribute.return_value = "2 unread messages"
    mock_title1 = MagicMock()
    mock_title1.get_attribute.return_value = "Tech Lead"
    mock_preview1 = MagicMock()
    mock_preview1.inner_text.return_value = "Please review the PR"
    mock_time1 = MagicMock()
    mock_time1.inner_text.return_value = "11:45 AM"

    mock_row1.query_selector.side_effect = lambda sel: {
        'span[aria-label*="unread"], span[data-icon="unread-count"], [aria-label*="unread message"]': mock_badge1,
        'span[title], div[dir="auto"] span[title]': mock_title1,
        'span.selectable-text, span[dir="ltr"], div[dir="ltr"] span, p.selectable-text': mock_preview1,
        'div[class*="timestamp"], div[dir="auto"]:last-child': mock_time1
    }.get(sel)

    mock_page.query_selector_all.return_value = [mock_row1]

    items = whatsapp_agent.get_unread_messages(_page=mock_page)
    assert len(items) == 1
    assert items[0]["chat_name"] == "Tech Lead"
    assert items[0]["unread_count"] == 2
    assert items[0]["last_message"] == "Please review the PR"
    assert items[0]["timestamp"] == "11:45 AM"


def test_qr_code_detection_returns_qr_required():
    """Verifies that when WhatsApp Web shows the QR code canvas, qr_required status is returned."""
    mock_page = MagicMock()
    mock_qr = MagicMock()
    mock_page.query_selector.return_value = mock_qr  # returns element when querying canvas

    items = whatsapp_agent.get_unread_messages(_page=mock_page)
    assert len(items) == 1
    assert items[0]["status"] == "qr_required"
    assert "QR code" in items[0]["last_message"]

    res_send = whatsapp_agent.send_reply("Friend", "Hi", _page=mock_page)
    assert res_send["success"] is False
    assert res_send.get("status") == "qr_required"


def test_brain_fallback_parser_whatsapp_routing(monkeypatch):
    """Verifies that natural voice commands route to unread checker and automated reply."""
    unread_called = []
    reply_called = []

    monkeypatch.setattr(actions, "get_whatsapp_unread", lambda max_chats=5: unread_called.append(max_chats) or {"success": True, "message": "Checked 2 chats."})
    monkeypatch.setattr(actions, "send_whatsapp_reply", lambda chat_name, message: reply_called.append((chat_name, message)) or {"success": True, "message": f"Sent to {chat_name}"})

    # Test reading unread messages
    res1 = brain.fallback_intent_parser("kiska whatsapp message aaya hai?")
    assert len(unread_called) == 1
    assert "Checked 2 chats" in res1["reply"]

    res2 = brain.fallback_intent_parser("check unread whatsapp messages")
    assert len(unread_called) == 2

    # Test sending automated reply
    res3 = brain.fallback_intent_parser("reply to Rohit on whatsapp saying I will reach in 10 minutes")
    assert len(reply_called) == 1
    assert reply_called[0][0].lower() == "rohit"
    assert "10 minutes" in reply_called[0][1]

    # Test opening WhatsApp Web across multiple phrasing variants
    open_called = []
    monkeypatch.setattr(actions, "open_website", lambda website, search_query="": open_called.append(website) or {"success": True, "action": "open_website", "url": "https://web.whatsapp.com", "website": "whatsapp", "message": "Whatsapp open kar diya hai."})

    for open_cmd in ["Open WhatsApp Web", "whatsapp open karo", "whatsapp web kholo", "open whatsapp"]:
        res_open = brain.fallback_intent_parser(open_cmd)
        assert "open" in res_open["reply"].lower()
        assert res_open["action"]["success"] is True

    assert len(open_called) == 4
    assert all("whatsapp" in s for s in open_called)


def test_mcp_skills_whatsapp_tools_delegation(monkeypatch):
    """Verifies MCP server tools delegate to core.actions."""
    monkeypatch.setattr(actions, "get_whatsapp_unread", lambda max_chats=5: {"success": True, "message": "MCP unread checked"})
    monkeypatch.setattr(actions, "send_whatsapp_reply", lambda chat_name, message: {"success": True, "message": f"MCP replied to {chat_name}"})

    res_unread = mcp_skills.get_whatsapp_unread()
    assert "MCP unread checked" in res_unread

    res_reply = mcp_skills.send_whatsapp_reply(chat_name="Boss", message="Done")
    assert "MCP replied to Boss" in res_reply


def test_send_whatsapp_message_ui_full_step_execution(monkeypatch):
    """
    Verifies the complete WhatsApp UI automation sequence:
    1. Click 'New Chat'
    2. Type contact_name into search box
    3. 2.0s sleep
    4. Press Enter to open chat
    5. Type message into chat box
    6. Press Enter to send
    """
    # Fast-forward sleep in tests
    sleeps = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))

    mock_page = MagicMock()
    mock_page.query_selector.return_value = None  # Initially search_box not found

    # New Chat button mock
    mock_new_chat = MagicMock()
    # Search box mock
    mock_search_box = MagicMock()
    # Message input mock
    mock_msg_input = MagicMock()

    # Step-by-step query_selector behavior
    call_counts = {"search": 0}

    def mock_query(selector):
        if "footer" in selector or "spellcheck" in selector:
            return mock_msg_input
        if "New chat" in selector or "new-chat" in selector:
            return mock_new_chat
        if "Search" in selector or "textbox" in selector:
            call_counts["search"] += 1
            if call_counts["search"] > 1:
                return mock_search_box
            return None
        return None

    mock_page.query_selector.side_effect = mock_query

    res = whatsapp_agent.send_whatsapp_message_ui("Pranshul", "Hello from Astra!", _page=mock_page)

    assert res["success"] is True
    assert res["contact_name"] == "Pranshul"
    # Step 1: New Chat was clicked
    mock_new_chat.click.assert_called_once()
    # Step 2: Search box was filled with contact name
    mock_search_box.fill.assert_called_once_with("Pranshul")
    # Step 3: Crucial 2-second wait occurred
    assert 2.0 in sleeps
    # Step 4: Enter pressed to select contact
    assert mock_page.keyboard.press.call_count >= 2
    # Step 5: Message input was filled
    mock_msg_input.fill.assert_called_once_with("Hello from Astra!")


def test_whatsapp_missing_info_asks_clarification():
    """Verifies that giving a contact name with no message prompts: 'What message would you like to send to [Name]?'"""
    session_id = "test_missing_info_sess"
    brain.session_manager.reset_session(session_id)

    res = brain.fallback_intent_parser("Send a message to Pranshul", session_id=session_id)
    assert res["reply"] == "What message would you like to send to Pranshul?"
    assert brain.session_manager.get_pending_whatsapp(session_id) == "Pranshul"


def test_whatsapp_multiturn_flow(monkeypatch):
    """Verifies turn 1 asks for message and turn 2 dispatches message to that contact."""
    session_id = "test_multiturn_sess"
    brain.session_manager.reset_session(session_id)

    sent_messages = []
    monkeypatch.setattr(actions, "send_whatsapp_message", lambda contact_name, message, phone="": sent_messages.append((contact_name, message)) or {"success": True, "message": "Sent"})

    # Turn 1: specify contact
    res1 = brain.fallback_intent_parser("Pranshul ko WhatsApp par message bhejo", session_id=session_id)
    assert "What message would you like to send to Pranshul?" in res1["reply"]
    assert len(sent_messages) == 0

    # Turn 2: specify message
    res2 = brain.fallback_intent_parser("Kal subah 10 baje college aana", session_id=session_id)
    assert len(sent_messages) == 1
    assert sent_messages[0] == ("Pranshul", "Kal subah 10 baje college aana")
    assert "Pranshul" in res2["reply"]
    # Session state cleared after send
    assert brain.session_manager.get_pending_whatsapp(session_id) is None


@pytest.mark.asyncio
async def test_process_voice_command_structured_json_interception(monkeypatch):
    """
    Verifies that when LLM returns strict structured JSON:
    { "action": "send_whatsapp_message", "contact_name": "Pranshul", "message": "See you" }
    it is intercepted, executed via UI automation, and converted to spoken audio reply.
    """
    sent = []
    monkeypatch.setattr(actions, "send_whatsapp_message", lambda contact_name, message, phone="": sent.append((contact_name, message)) or {"success": True, "message": "Delivered"})

    mock_chat = MagicMock()
    mock_response = MagicMock()
    mock_response.text = '{\n"action": "send_whatsapp_message",\n"contact_name": "Pranshul",\n"message": "See you tomorrow"\n}'
    mock_chat.send_message.return_value = mock_response

    with monkeypatch.context() as m:
        m.setattr("os.getenv", lambda k, d=None: "fake_gemini_key" if k == "GEMINI_API_KEY" else d)
        m.setattr("core.brain.session_manager.get_or_create_chat", lambda *a, **kw: mock_chat)

        result = await brain.process_voice_command("Pranshul ko bolo See you tomorrow", session_id="test_json_interception")

        assert len(sent) == 1
        assert sent[0] == ("Pranshul", "See you tomorrow")
        assert "Pranshul" in result["reply"]
        assert "See you tomorrow" in result["reply"]


@pytest.mark.asyncio
async def test_process_voice_command_markdown_backtick_failsafe(monkeypatch):
    """
    Verifies that if the LLM accidentally sends markdown code blocks like:
    ```json
    { "action": "send_whatsapp_message", "contact_name": "Kasyap", "message": "hi" }
    ```
    the Python backend failsafe strips them cleanly and dispatches without JSONDecodeError.
    """
    sent = []
    monkeypatch.setattr(actions, "send_whatsapp_message", lambda contact_name, message, phone="": sent.append((contact_name, message)) or {"success": True, "message": "Delivered"})

    mock_chat = MagicMock()
    mock_response = MagicMock()
    mock_response.text = '```json\n{\n  "action": "send_whatsapp_message",\n  "contact_name": "Kasyap",\n  "message": "hi"\n}\n```'
    mock_chat.send_message.return_value = mock_response

    with monkeypatch.context() as m:
        m.setattr("os.getenv", lambda k, d=None: "fake_gemini_key" if k == "GEMINI_API_KEY" else d)
        m.setattr("core.brain.session_manager.get_or_create_chat", lambda *a, **kw: mock_chat)

        result = await brain.process_voice_command("Kasyap ko hi bhejo", session_id="test_failsafe_sess")

        assert len(sent) == 1
        assert sent[0] == ("Kasyap", "hi")
        assert "Kasyap" in result["reply"]
        assert "hi" in result["reply"]


def test_whatsapp_direct_command_kasyap_ko_hi_bhejo(monkeypatch):
    """
    Verifies direct commands 'Kasyap ko hi bhejo' and 'whatsapp me kasyap ko hi send karo'
    extract contact_name='kasyap' and message='hi' in fallback mode.
    """
    sent = []
    monkeypatch.setattr(actions, "send_whatsapp_message", lambda contact_name, message, phone="": sent.append((contact_name, message)) or {"success": True, "message": "Delivered"})

    res1 = brain.fallback_intent_parser("Kasyap ko hi bhejo", session_id="test_direct_1")
    assert len(sent) == 1
    assert sent[0] == ("kasyap", "hi")
    assert "kasyap" in res1["reply"].lower()

    res2 = brain.fallback_intent_parser("whatsapp me kasyap ko hi send karo", session_id="test_direct_2")
    assert len(sent) == 2
    assert sent[1] == ("kasyap", "hi")
    assert "kasyap" in res2["reply"].lower()


def test_send_whatsapp_message_ui_desktop_aborts_when_not_foreground(monkeypatch):
    """
    CRITICAL GUARD TEST:
    Verifies that when WhatsApp is NOT the active foreground window (e.g. Astra UI is open),
    send_whatsapp_message_ui aborts immediately and sends ZERO keystrokes, protecting Astra chat.
    """
    hotkey_calls = []
    press_calls = []

    mock_pyautogui = MagicMock()
    mock_pyautogui.hotkey.side_effect = lambda *a, **k: hotkey_calls.append(a)
    mock_pyautogui.press.side_effect = lambda *a, **k: press_calls.append(a)

    monkeypatch.setattr("core.whatsapp_agent.focus_whatsapp_window", lambda: False)
    monkeypatch.setattr("core.whatsapp_agent.is_whatsapp_foreground", lambda: False)
    monkeypatch.setitem(__import__("sys").modules, "pyautogui", mock_pyautogui)

    res = whatsapp_agent.send_whatsapp_message_ui("Kasyap", "hi")

    assert res["success"] is False
    assert "whatsapp" in res["error"].lower() or "active" in res["error"].lower()
    # Zero keystrokes sent - Astra UI is completely protected
    assert len(hotkey_calls) == 0
    assert len(press_calls) == 0


def test_send_whatsapp_message_ui_desktop_full_flow(monkeypatch):
    """
    Verifies the full Desktop UI automation sequence when WhatsApp is focused:
    1. focus_whatsapp_window brings window to foreground
    2. is_whatsapp_foreground returns True
    3. Ctrl+Alt+N opens New Chat
    4. Contact name is copied and pasted
    5. Crucial 2.0s wait is observed
    6. Enter is pressed to select contact
    7. 1.2s wait for chat pane
    8. Message is copied and pasted
    9. Enter is pressed to send
    """
    sleeps = []
    hotkey_calls = []
    press_calls = []
    copied = []

    mock_pyautogui = MagicMock()
    mock_pyautogui.hotkey.side_effect = lambda *a, **k: hotkey_calls.append(a)
    mock_pyautogui.press.side_effect = lambda *a, **k: press_calls.append(a)

    mock_pyperclip = MagicMock()
    mock_pyperclip.copy.side_effect = lambda text: copied.append(text)

    mock_win32gui = MagicMock()
    mock_win32gui.GetWindowText.return_value = "WhatsApp - Google Chrome"
    mock_win32gui.GetForegroundWindow.return_value = 12345

    monkeypatch.setattr("core.whatsapp_agent.focus_whatsapp_window", lambda: True)
    monkeypatch.setattr("core.whatsapp_agent.is_whatsapp_foreground", lambda: True)
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setitem(__import__("sys").modules, "pyautogui", mock_pyautogui)
    monkeypatch.setitem(__import__("sys").modules, "pyperclip", mock_pyperclip)
    monkeypatch.setitem(__import__("sys").modules, "win32gui", mock_win32gui)

    res = whatsapp_agent.send_whatsapp_message_ui("Kasyap", "hi")

    assert res["success"] is True
    assert res["contact_name"] == "Kasyap"
    # Verify New Chat shortcut (Ctrl+Alt+N for browser)
    assert ('ctrl', 'alt', 'n') in hotkey_calls
    # Verify contact and message were copied to clipboard
    assert "Kasyap" in copied
    assert "hi" in copied
    # Verify crucial 2.0s sleep and 1.2s sleep
    assert 2.0 in sleeps
    assert 1.2 in sleeps
    # Verify Enter was pressed to select contact and to send message
    assert ('enter',) in press_calls
    assert press_calls.count(('enter',)) >= 2


def test_whatsapp_send_chat_shivam_say_hello(monkeypatch):
    """
    Verifies that conversational command 'send chat shivam say hello'
    extracts contact_name='shivam' and message='hello'.
    """
    sent = []
    monkeypatch.setattr(actions, "send_whatsapp_message", lambda contact_name, message, phone="": sent.append((contact_name, message)) or {"success": True, "message": "Delivered"})

    res = brain.fallback_intent_parser("send chat shivam say hello", session_id="test_send_chat_shivam")
    assert len(sent) == 1
    assert sent[0] == ("shivam", "hello")
    assert "shivam" in res["reply"].lower()
    assert "hello" in res["reply"].lower()


def test_executor_bridge_whatsapp_tool_timeout():
    """Verifies that send_whatsapp_message receives 25s timeout instead of the 5s default."""
    from core.executor_bridge import executor_bridge, DEFAULT_TOOL_TIMEOUTS
    assert DEFAULT_TOOL_TIMEOUTS["send_whatsapp_message"] >= 20.0
    assert DEFAULT_TOOL_TIMEOUTS["send_whatsapp_reply"] >= 20.0


def test_is_whatsapp_window_multi_factor(monkeypatch):
    """Verifies multi-factor window detection across titles, badges, and process names."""
    # 1. Native WhatsApp desktop process
    monkeypatch.setattr(whatsapp_agent, "get_window_info", lambda hwnd: {
        "hwnd": hwnd, "title": "Chat with Friends", "process_name": "whatsapp.exe", "pid": 100
    })
    assert whatsapp_agent.is_whatsapp_window(101) is True

    # 2. Browser with WhatsApp tab title & unread badges
    monkeypatch.setattr(whatsapp_agent, "get_window_info", lambda hwnd: {
        "hwnd": hwnd, "title": "(4) WhatsApp - Google Chrome", "process_name": "chrome.exe", "pid": 200
    })
    assert whatsapp_agent.is_whatsapp_window(201) is True

    # 3. Non-WhatsApp window (e.g. VS Code or Astra)
    monkeypatch.setattr(whatsapp_agent, "get_window_info", lambda hwnd: {
        "hwnd": hwnd, "title": "Astra - AI Assistant", "process_name": "code.exe", "pid": 300
    })
    assert whatsapp_agent.is_whatsapp_window(301) is False
    # allow_browser_tab False
    assert whatsapp_agent.is_whatsapp_window(301, allow_browser_tab=False) is False


def test_bring_window_to_foreground_robust(monkeypatch):
    """Verifies bring_window_to_foreground handles thread input and window restoring."""
    calls = []
    mock_win32gui = MagicMock()
    mock_win32gui.IsWindow.return_value = True
    mock_win32gui.IsIconic.return_value = True
    mock_win32gui.GetForegroundWindow.side_effect = [999, 12345]

    mock_win32process = MagicMock()
    mock_win32process.GetWindowThreadProcessId.return_value = (555, 666)

    mock_win32api = MagicMock()
    mock_win32api.GetCurrentThreadId.return_value = 111

    monkeypatch.setitem(__import__("sys").modules, "win32gui", mock_win32gui)
    monkeypatch.setitem(__import__("sys").modules, "win32process", mock_win32process)
    monkeypatch.setitem(__import__("sys").modules, "win32api", mock_win32api)
    monkeypatch.setattr(whatsapp_agent, "is_whatsapp_window", lambda hwnd: True)

    res = whatsapp_agent.bring_window_to_foreground(12345)
    assert res is True
    mock_win32gui.ShowWindow.assert_called_once()
    mock_win32gui.SetForegroundWindow.assert_called_once_with(12345)


def test_whatsapp_parsing_edge_cases(monkeypatch):
    """Verifies that non-contact words ('send', 'chat') are never extracted as contact names."""
    sent = []
    monkeypatch.setattr(actions, "send_whatsapp_message", lambda contact_name, message, phone="": sent.append((contact_name, message)) or {"success": True, "message": "Sent"})

    res1 = brain.fallback_intent_parser("send message to Shivam saying meeting at 5pm", session_id="test_parse_1")
    assert len(sent) == 1
    assert sent[0][0].lower() == "shivam"
    assert sent[0][1] == "meeting at 5pm"

    res2 = brain.fallback_intent_parser("send chat rahul kal test hai", session_id="test_parse_2")
    assert len(sent) == 2
    assert sent[1][0].lower() == "rahul"
    assert sent[1][1] == "kal test hai"






