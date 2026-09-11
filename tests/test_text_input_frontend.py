import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

def test_index_html_contains_text_input_and_send_button():
    html_path = BASE_DIR / "static" / "index.html"
    assert html_path.exists(), "index.html should exist"
    content = html_path.read_text(encoding="utf-8")

    assert 'id="textCommandForm"' in content
    assert 'id="textCommandInput"' in content
    assert 'id="sendTextBtn"' in content
    assert 'class="text-input-container"' in content
    assert 'class="text-command-input"' in content
    assert 'class="send-text-btn"' in content

def test_style_css_contains_text_input_styling():
    css_path = BASE_DIR / "static" / "style.css"
    assert css_path.exists(), "style.css should exist"
    content = css_path.read_text(encoding="utf-8")

    assert ".text-input-container" in content
    assert ".text-command-form" in content
    assert ".text-command-input" in content
    assert ".send-text-btn" in content

def test_app_js_handles_text_input_and_send_event():
    js_path = BASE_DIR / "static" / "app.js"
    assert js_path.exists(), "app.js should exist"
    content = js_path.read_text(encoding="utf-8")

    assert "textCommandInput" in content
    assert "sendTextBtn" in content
    assert "handleTextCommandSubmit" in content
    assert "sendVoiceCommand" in content
    assert "Enter" in content
