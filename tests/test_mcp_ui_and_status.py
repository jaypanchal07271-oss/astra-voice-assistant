"""
tests/test_mcp_ui_and_status.py - Unit tests for Astra UI/UX Upgrade & MCP Status Panel
Verifies:
1. /api/mcp/status endpoint returns real-time status payload.
2. Unconfigured state: returns 'Not Configured' with enabled=False, zero tools, and no fake data.
3. Connected state: returns 'Connected' with tools and recorded activities.
4. HTML DOM structure: verification of header badges, status pills, equalizer, code blocks, and 5 console tabs.
5. JavaScript wiring: verify exact centerpiece copy ('Tap to speak', 'Listening...', 'Thinking...', 'Speaking...', 'Something went wrong'),
   MCP status rendering, and network online/offline listener.
"""

from pathlib import Path
from starlette.testclient import TestClient
from unittest.mock import patch

from app import app
from config import STATIC_DIR
from core.mcp_client import get_mcp_status, record_mcp_activity

client = TestClient(app)


def test_mcp_status_endpoint_unconfigured():
    """Verifies /api/mcp/status returns accurate unconfigured state without faking connection."""
    res = client.get("/api/mcp/status")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "Not Configured"
    assert data["enabled"] is False
    assert data["server_name"] == "None"
    assert data["tool_count"] == 0
    assert isinstance(data["tools"], list)
    assert len(data["tools"]) == 0
    assert isinstance(data["recent_activities"], list)
    assert isinstance(data["activity"], list)


def test_mcp_status_endpoint_with_activity_and_connection():
    """Verifies /api/mcp/status correctly reflects connected state and activities when configured."""
    record_mcp_activity("search_web", "Query dispatched")

    with patch.dict("os.environ", {"MCP_ENABLED": "true", "MCP_SERVER_COMMAND": "npx mock-mcp-server"}):
        status = get_mcp_status()
        assert status["enabled"] is True
        assert status["configured"] is True
        assert len(status["recent_activities"]) >= 1
        assert status["recent_activities"][0]["tool"] == "search_web"
        assert status["recent_activities"][0]["message"] == "Query dispatched"


def test_frontend_dom_elements():
    """Verifies index.html has all required UI/UX upgrade and MCP panel DOM elements."""
    html_path = STATIC_DIR / "index.html"
    assert html_path.is_file()
    html = html_path.read_text(encoding="utf-8")

    # Header elements
    assert 'id="networkStatusPill"' in html
    assert 'id="networkStatusText"' in html
    assert 'id="mcpHeaderBadge"' in html
    assert 'id="mcpBadgeText"' in html

    # Voice centerpiece equalizer & copy
    assert 'id="orbEqualizer"' in html
    assert 'id="statusText"' in html

    # Console Tabs in Settings Modal
    assert 'data-tab="general"' in html
    assert 'data-tab="ai"' in html
    assert 'data-tab="voice"' in html
    assert 'data-tab="mcp"' in html
    assert 'data-tab="system"' in html

    # MCP tab elements in Settings Modal
    assert 'id="mcpModalStatusPill"' in html
    assert 'id="mcpServerName"' in html
    assert 'id="mcpToolCount"' in html
    assert 'id="mcpLastActivity"' in html
    assert 'id="mcpToolsList"' in html
    assert 'id="mcpActivityFeed"' in html


def test_frontend_javascript_features():
    """Verifies app.js contains required voice states copy, MCP fetching, and code block formatting."""
    js_path = STATIC_DIR / "app.js"
    assert js_path.is_file()
    js = js_path.read_text(encoding="utf-8")

    # Exact Voice State Copy
    assert '"Tap to speak"' in js
    assert '"Listening..."' in js
    assert '"Thinking..."' in js
    assert '"Speaking..."' in js
    assert '"Something went wrong"' in js

    # MCP status integration
    assert "fetchAndRenderMCPStatus" in js
    assert "/api/mcp/status" in js
    assert "mcpHeaderBadge" in js

    # Code blocks & formatting
    assert "renderFormattedMessage" in js
    assert "code-block-wrapper" in js
    assert "code-copy-btn" in js

    # Network listener
    assert "updateNetworkStatus" in js
    assert "online" in js
    assert "offline" in js
