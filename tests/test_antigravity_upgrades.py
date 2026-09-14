"""
Tests for Astra Antigravity Upgrades:
1. Mem0 Persistent Long-Term Memory Layer
2. Dynamic Model Context Protocol (MCP) Tool Bridge
3. Multi-Layered WhatsApp Automation Strategy
"""

import pytest
import asyncio
from unittest.mock import patch, MagicMock

from core import memory, mcp_client, actions, brain


# =====================================================================
# Upgrade 1: Mem0 Long-Term Memory Tests
# =====================================================================

def test_mem0_graceful_degradation_when_unconfigured(monkeypatch):
    """Verifies that Mem0 functions gracefully return empty when API key is unconfigured."""
    monkeypatch.setenv("MEM0_API_KEY", "")
    memory._MEM0_INITIALIZED = False
    memory._MEM0_CLIENT = None

    status = memory.get_memory_status()
    assert status["active"] is False
    assert status["provider"] == "local_profile_fallback"

    # Search should return empty string without error
    res = memory.search_relevant_memories("Mera favorite khana kya hai?")
    assert res == ""

    # Add interaction should not throw
    memory.add_interaction_memory_async("Mera favorite khana biryani hai", "Biryani note kar li!")


def test_mem0_search_and_formatting(monkeypatch):
    """Verifies that Mem0 search results are converted to a clean bulleted list for prompt injection."""
    mock_client = MagicMock()
    mock_client.search.return_value = [
        {"memory": "Boss prefers Hinglish over pure English."},
        {"memory": "Boss lives in Ahmedabad."},
    ]

    monkeypatch.setattr(memory, "_get_mem0_client", lambda: mock_client)

    result = memory.search_relevant_memories("Where do I live?", user_id="test_user")
    assert "- Boss prefers Hinglish over pure English." in result
    assert "- Boss lives in Ahmedabad." in result
    mock_client.search.assert_called_once_with(query="Where do I live?", user_id="test_user", limit=5)


def test_mem0_async_add_interaction(monkeypatch):
    """Verifies that interaction memory is recorded asynchronously."""
    mock_client = MagicMock()
    monkeypatch.setattr(memory, "_get_mem0_client", lambda: mock_client)

    memory.add_interaction_memory_async(
        user_text="Mera phone number 9999999999 hai",
        assistant_reply="Note kar liya Boss!",
        user_id="boss_jay"
    )

    # Allow daemon thread a brief moment to run
    import time
    time.sleep(0.3)

    assert mock_client.add.called
    args, kwargs = mock_client.add.call_args
    assert kwargs["messages"] == [
        {"role": "user", "content": "Mera phone number 9999999999 hai"},
        {"role": "assistant", "content": "Note kar liya Boss!"}
    ]
    assert kwargs["user_id"] == "boss_jay"


# =====================================================================
# Upgrade 2: Dynamic Model Context Protocol (MCP) Bridge Tests
# =====================================================================

@pytest.mark.asyncio
async def test_mcp_client_disabled_fallback(monkeypatch):
    """Verifies that MCP client returns empty collections immediately when disabled."""
    monkeypatch.setenv("MCP_ENABLED", "false")
    mcp_client._MCP_DISCOVERED = False

    tools, tool_map, schemas = await mcp_client.discover_mcp_tools()
    assert tools == []
    assert tool_map == {}
    assert schemas == []


def test_mcp_client_sync_fallback(monkeypatch):
    """Verifies sync accessor behaves identically when MCP is disabled."""
    monkeypatch.setenv("MCP_ENABLED", "false")
    mcp_client._MCP_DISCOVERED = False

    tools, tool_map, schemas = mcp_client.get_dynamic_mcp_tools_sync()
    assert tools == []
    assert tool_map == {}
    assert schemas == []


# =====================================================================
# Upgrade 3: Robust Multi-Layered WhatsApp Automation Tests
# =====================================================================

def test_whatsapp_layer1_and_2_ui_automation_success(monkeypatch):
    """Verifies Layer 1/2 UI automation path when desktop/window is focused."""
    monkeypatch.setattr(
        "core.whatsapp_agent.send_whatsapp_message_ui",
        lambda contact_name, message: {
            "success": True,
            "action": "send_whatsapp_message",
            "contact_name": contact_name,
            "message": f"Sent to {contact_name}"
        }
    )

    res = actions.send_whatsapp_message(contact_name="Kasyap", message="Kal 4 baje milte hain")
    assert res["success"] is True
    assert res["contact_name"] == "Kasyap"
    assert res.get("fallback_engaged") is not True


def test_whatsapp_layer3_playwright_fallback(monkeypatch):
    """Verifies Layer 3 Playwright fallback when Layer 1/2 desktop automation fails."""
    # UI fails (e.g. window not found or desktop not foreground)
    monkeypatch.setattr(
        "core.whatsapp_agent.send_whatsapp_message_ui",
        lambda contact_name, message: {"success": False, "error": "Window not found"}
    )
    # Playwright fallback succeeds
    monkeypatch.setattr(
        "core.whatsapp_agent.send_reply",
        lambda chat_name, message: {"success": True, "chat": chat_name}
    )

    res = actions.send_whatsapp_message(contact_name="Kasyap", message="Testing Playwright fallback")
    assert res["success"] is True
    assert "Playwright" in res["message"]


def test_whatsapp_layer4_url_fallback_when_all_fail(monkeypatch):
    """Verifies Layer 4 fail-proof URL fallback when all previous automation layers fail."""
    monkeypatch.setattr(
        "core.whatsapp_agent.send_whatsapp_message_ui",
        lambda contact_name, message: {"success": False, "error": "Desktop UI failure"}
    )
    monkeypatch.setattr(
        "core.whatsapp_agent.send_reply",
        lambda chat_name, message: {"success": False, "error": "Playwright session closed"}
    )

    with patch("core.actions._launch_browser_url") as mock_launch, \
         patch("webbrowser.open"), \
         patch("time.sleep"), \
         patch("pyautogui.press"):
        res = actions.send_whatsapp_message(contact_name="9876543210", message="Hello world")
        assert res["success"] is True
        assert res.get("fallback_engaged") is True
        assert "919876543210" in res["url"]
        assert mock_launch.called


# =====================================================================
# Integration: Brain + Memory Hook Verification
# =====================================================================

@pytest.mark.asyncio
async def test_brain_process_voice_command_invokes_memory_async(monkeypatch):
    """Verifies that brain.process_voice_command triggers async memory addition."""
    added_interactions = []

    def mock_add(user_text, assistant_reply, user_id=None):
        added_interactions.append((user_text, assistant_reply))

    monkeypatch.setattr("core.brain.add_interaction_memory_async", mock_add)

    # Trigger fallback parser through brain
    res = await brain.process_voice_command("Notepad kholo")
    assert res is not None
    assert "reply" in res
    assert len(added_interactions) == 1
    assert added_interactions[0][0] == "Notepad kholo"
    assert res["reply"] in added_interactions[0][1]
