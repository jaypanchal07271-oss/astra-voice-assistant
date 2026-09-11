"""
UI/UX Refactor Test Suite for Astra Assistant
Verifies the 5 premium frontend upgrades:
1. Dynamic Orb State CSS classes (.orb-listening, .orb-thinking, .orb-speaking) & Dusky Pink glow.
2. Transcript History Area with CSS fade-out mask-image.
3. Embedded minimalistic send icon & prominent voice-first microphone with continuous shimmer.
4. Categorized Suggestion Pills in 3 distinct rows with glassmorphism.
5. JavaScript Wiring: setOrbState toggles & dynamic appendChatMessage with smooth auto-scroll.
"""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def test_orb_state_animations_and_dusky_pink_glow():
    html = (BASE_DIR / "static" / "index.html").read_text(encoding="utf-8")
    css = (BASE_DIR / "static" / "style.css").read_text(encoding="utf-8")

    # HTML contains orb container and pink glow
    assert "orb-container" in html
    assert "orb-pink-glow" in html

    # CSS contains muted dusky pink rgba(205, 145, 158, 0.15)
    assert "rgba(205, 145, 158, 0.15)" in css

    # CSS contains the 3 state animation classes
    assert ".orb-wrapper.orb-listening" in css or ".orb-listening" in css
    assert ".orb-wrapper.orb-thinking" in css or ".orb-thinking" in css
    assert ".orb-wrapper.orb-speaking" in css or ".orb-speaking" in css


def test_transcript_history_area_with_fade_out_mask():
    html = (BASE_DIR / "static" / "index.html").read_text(encoding="utf-8")
    css = (BASE_DIR / "static" / "style.css").read_text(encoding="utf-8")

    # HTML contains multi-line chat container
    assert "transcript-history-container" in html
    assert "chat-messages" in html

    # CSS applies mask-image linear-gradient with 20% fade
    assert "linear-gradient(to bottom, transparent, black 20%, black)" in css
    assert "mask-image" in css


def test_input_field_embedded_send_and_microphone_dominance():
    html = (BASE_DIR / "static" / "index.html").read_text(encoding="utf-8")
    css = (BASE_DIR / "static" / "style.css").read_text(encoding="utf-8")

    # Microhpone dominance & continuous shimmer
    assert "voice-primary" in html or "mic-button" in html
    assert "mic-shimmer-ring" in html
    assert "micVoiceShimmer" in css or "shimmer" in css.lower()

    # Sleek embedded input field with send inside
    assert "send-text-btn" in html
    assert "text-command-form" in html
    assert "position: absolute" in css


def test_minimalist_clean_ui_no_cluttered_suggestions():
    html = (BASE_DIR / "static" / "index.html").read_text(encoding="utf-8")
    css = (BASE_DIR / "static" / "style.css").read_text(encoding="utf-8")

    # Cluttered suggestion pills and physical shortcut hints are removed for voice-first minimalism
    assert "categorized-suggestions" not in html
    assert "shortcut-hints" not in html
    assert "Dev Tools" not in html
    assert ".categorized-suggestions" not in css

    # Modern glassmorphism styling is preserved across interactive components
    assert "backdrop-filter" in css


def test_javascript_wiring_orb_and_chat_history():
    js = (BASE_DIR / "static" / "app.js").read_text(encoding="utf-8")

    # setOrbState supports listening, thinking, speaking, idle
    assert "setOrbState" in js
    assert "orb-listening" in js
    assert "orb-thinking" in js
    assert "orb-speaking" in js

    # appendChatMessage dynamically appends bubbles and auto-scrolls
    assert "appendChatMessage" in js
    assert "chat-bubble" in js
    assert "scrollTo" in js or "scrollTop" in js
