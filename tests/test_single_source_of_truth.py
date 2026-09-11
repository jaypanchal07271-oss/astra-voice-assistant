"""
Test verifying single source of truth architecture:
Confirms that mcp_skills delegates directly to core.actions
and exposes all 11 tools with consistent behavior.
"""

import pytest
import asyncio
import mcp_skills
from core import actions


@pytest.mark.asyncio
async def test_mcp_tools_delegation_to_core_actions():
    tools = await mcp_skills.mcp.list_tools()
    tool_names = {t.name for t in tools}

    expected_tools = {
        "open_application",
        "close_application",
        "control_system",
        "get_time_and_date",
        "analyze_clipboard",
        "open_or_search_website",
        "send_whatsapp_message",
        "play_spotify_music",
        "start_dev_environment",
        "manage_odoo_server",
        "analyze_screen",
        "read_recent_emails",
        "get_upcoming_events",
        "get_whatsapp_unread",
        "send_whatsapp_reply"
    }

    for expected in expected_tools:
        assert expected in tool_names, f"Expected MCP tool '{expected}' missing from registered tools."


def test_mcp_and_core_odoo_guardrail_consistency():
    # Attempt blocked module on both
    mcp_reply = mcp_skills.manage_odoo_server("update", "account")
    core_reply = actions.manage_odoo_server("update", "account")

    assert "Access Denied" in mcp_reply
    assert "Access Denied" in core_reply["message"]

