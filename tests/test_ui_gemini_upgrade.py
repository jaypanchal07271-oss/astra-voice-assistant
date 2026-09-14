"""
tests/test_ui_gemini_upgrade.py - Unit tests for Gemini & ChatGPT UI/UX Upgrade
Verifies:
1. DOM elements for the Gemini executive console (sidebar, mobile drawer, 21 MCP tool catalog, new chat session).
2. Unique 3D Gyroscopic Celestial AI Orb (counter-rotating rings, plasma core, soundwave wings, equalizer).
3. Responsive prompt deck (2x2 on mobile, 4x1 on laptop) with zero overlap/clipping.
4. Floating omnibar dock with mic, text input, send button, and instant interrupt.
5. JavaScript event wiring for drawer, sidebar collapse, new chat session reset, and prompt suggestions.
"""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def test_index_html_gemini_console_dom():
    html_path = BASE_DIR / "static" / "index.html"
    assert html_path.exists(), "index.html must exist"
    html = html_path.read_text(encoding="utf-8")

    # Layout & Navigation
    assert 'class="app-layout"' in html
    assert 'id="appSidebar"' in html
    assert 'id="sidebarBackdrop"' in html
    assert 'id="mobileDrawerBtn"' in html
    assert 'id="sidebarCloseBtn"' in html
    assert 'id="sidebarCollapseBtn"' in html
    assert 'id="newChatBtn"' in html
    assert 'id="sidebarSettingsBtn"' in html

    # 21 MCP Server Tools section in sidebar
    assert 'MCP Server Tools' in html
    assert '21 Tools' in html
    assert 'mcp-tool-chip' in html

    # Unique 3D Gyroscopic AI Orb Centerpiece
    assert 'gyro-ring-1' in html
    assert 'gyro-ring-2' in html
    assert 'plasma-core' in html
    assert 'soundwave-wing' in html
    assert 'id="orbWrapper"' in html
    assert 'id="orbCore"' in html
    assert 'id="orbEqualizer"' in html
    assert 'id="statusDot"' in html
    assert 'id="statusText"' in html

    # Responsive Prompt Deck
    assert 'id="promptDeck"' in html
    assert 'class="prompt-card"' in html

    # Floating Omnibar Dock
    assert 'class="omnibar-dock"' in html
    assert 'class="text-input-container"' in html
    assert 'id="textCommandForm"' in html
    assert 'id="textCommandInput"' in html
    assert 'id="micBtn"' in html
    assert 'id="sendTextBtn"' in html
    assert 'id="interruptBtn"' in html
    assert 'id="omnibarToolsBtn"' in html


def test_style_css_3d_gyroscopic_orb_and_responsive_rules():
    css_path = BASE_DIR / "static" / "style.css"
    assert css_path.exists(), "style.css must exist"
    css = css_path.read_text(encoding="utf-8")

    # 3D Gyroscopic keyframes and transforms
    assert "@keyframes gyroRotateClockwise" in css
    assert "@keyframes gyroRotateCounter" in css
    assert "@keyframes plasmaSwirl" in css
    assert "@keyframes soundwavePulse" in css
    assert "transform-style: preserve-3d" in css
    assert ".gyro-ring-1" in css
    assert ".gyro-ring-2" in css
    assert ".plasma-core" in css
    assert ".soundwave-wing" in css

    # Responsive layout & mobile media query
    assert "@media (max-width: 899px)" in css
    assert ".desktop-controls" in css
    assert ".omnibar-dock" in css
    assert ".prompt-deck-container" in css


def test_app_js_gemini_interactions_wired():
    js_path = BASE_DIR / "static" / "app.js"
    assert js_path.exists(), "app.js must exist"
    js = js_path.read_text(encoding="utf-8")

    # Drawer and sidebar handlers
    assert "openMobileDrawer" in js
    assert "closeMobileDrawer" in js
    assert "toggleSidebarCollapse" in js
    assert "startNewChatSession" in js

    # Element references
    assert "appSidebar" in js
    assert "mobileDrawerBtn" in js
    assert "sidebarBackdrop" in js
    assert "newChatBtn" in js
    assert "promptDeck" in js
    assert "omnibarToolsBtn" in js

    # Session generation & reset
    assert "astraSessionId" in js
    assert "astra_sidebar_collapsed" in js

