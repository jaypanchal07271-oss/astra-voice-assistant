"""
Automated Test Suite for Astra Progressive Web App (PWA) Shell
Verifies:
1. PWA Manifest endpoint (/manifest.json) validity, standalone display, and icon metadata.
2. Service Worker endpoint (/sw.js) root scoping and offline caching headers.
3. Index.html PWA tags (manifest link, theme color, iOS apple-mobile-web-app-capable).
4. Icon assets availability (icon.svg, icon-192.png, icon-512.png).
5. Secure session token retrieval endpoint (/api/auth/token).
6. Mobile voice upload endpoint (/api/voice_upload) authentication, execution, and transcription.
7. Mobile-responsive CSS breakpoints and iOS safe-area insets.
"""

import io
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from starlette.testclient import TestClient

from app import app
from config import ASTRA_AUTH_TOKEN, STATIC_DIR

client = TestClient(app)
AUTH_HEADERS = {"X-Astra-Token": ASTRA_AUTH_TOKEN}


def test_manifest_json_endpoint_and_schema():
    """Verifies that /manifest.json returns valid PWA manifest metadata."""
    response = client.get("/manifest.json")
    assert response.status_code == 200
    assert "application/manifest+json" in response.headers.get("content-type", "")

    data = response.json()
    assert data.get("name") == "Astra - Gemini AI Voice Assistant"
    assert data.get("short_name") == "Astra"
    assert data.get("display") == "standalone"
    assert data.get("start_url") == "/"
    assert data.get("background_color") == "#07090e"
    assert data.get("theme_color") == "#07090e"

    icons = data.get("icons", [])
    assert len(icons) >= 3
    icon_sizes = {icon.get("sizes") for icon in icons}
    assert "192x192" in icon_sizes
    assert "512x512" in icon_sizes


def test_service_worker_endpoint_and_headers():
    """Verifies that /sw.js is served with root scope permission and contains caching logic."""
    response = client.get("/sw.js")
    assert response.status_code == 200
    assert "application/javascript" in response.headers.get("content-type", "")
    assert response.headers.get("Service-Worker-Allowed") == "/"

    content = response.text
    assert ("astra-pwa-v2" in content or "astra-pwa-v1" in content)
    assert "APP_SHELL_ASSETS" in content
    assert "install" in content
    assert "activate" in content
    assert "fetch" in content


def test_index_html_pwa_meta_tags():
    """Verifies that index.html contains essential PWA and iOS standalone tags."""
    response = client.get("/")
    assert response.status_code == 200
    html = response.text

    assert '<link rel="manifest" href="/manifest.json">' in html
    assert 'name="theme-color" content="#07090e"' in html
    assert 'name="apple-mobile-web-app-capable" content="yes"' in html
    assert 'name="apple-mobile-web-app-title" content="Astra"' in html
    assert 'rel="apple-touch-icon"' in html
    assert 'viewport-fit=cover' in html


def test_pwa_icon_assets_exist():
    """Verifies that generated icon files exist and are valid."""
    icons_dir = STATIC_DIR / "icons"
    assert (icons_dir / "icon.svg").is_file()
    assert (icons_dir / "icon-192.png").is_file()
    assert (icons_dir / "icon-512.png").is_file()

    assert (icons_dir / "icon-192.png").stat().st_size > 500
    assert (icons_dir / "icon-512.png").stat().st_size > 1000


def test_auth_token_endpoint_authenticated_and_unauthenticated():
    """Verifies /api/auth/token provides secure HttpOnly / header session verification."""
    # Unauthenticated (clean client without cookies) -> 401
    clean_client = TestClient(app)
    res_unauth = clean_client.get("/api/auth/token")
    assert res_unauth.status_code == 401

    # Authenticated via Header -> 200
    res_header = clean_client.get("/api/auth/token", headers=AUTH_HEADERS)
    assert res_header.status_code == 200
    data_header = res_header.json()
    assert data_header["authenticated"] is True
    assert data_header["token"] == ASTRA_AUTH_TOKEN

    # Authenticated via HttpOnly Session Cookie -> 200
    client_with_cookie = TestClient(app, cookies={"astra_session_token": ASTRA_AUTH_TOKEN})
    res_cookie = client_with_cookie.get("/api/auth/token")
    assert res_cookie.status_code == 200
    data_cookie = res_cookie.json()
    assert data_cookie["authenticated"] is True


def test_voice_upload_endpoint_unauthenticated():
    """Verifies /api/voice_upload rejects unauthenticated requests."""
    clean_client = TestClient(app)
    fake_audio = io.BytesIO(b"RIFF" + b"\x00" * 200)
    response = clean_client.post(
        "/api/voice_upload",
        files={"audio_file": ("test.wav", fake_audio, "audio/wav")}
    )
    assert response.status_code == 401


def test_voice_upload_endpoint_authenticated_roundtrip():
    """
    Verifies /api/voice_upload processes audio input and returns
    reply, audio_url, transcript, and action details.
    """
    fake_audio = io.BytesIO(b"RIFF" + b"\x00" * 300)

    # Mock Gemini transcription, process_voice_command and generate_audio to avoid external API dependencies
    mock_genai_client = MagicMock()
    mock_model_res = MagicMock()
    mock_model_res.text = "Namaste"
    mock_genai_client.models.generate_content.return_value = mock_model_res

    with patch("google.genai.Client", return_value=mock_genai_client), \
         patch("app.process_voice_command", return_value={"reply": "Namaste! Main Astra hoon.", "action": {"status": "conversation"}}) as mock_brain, \
         patch("app.generate_audio", return_value="/static/audio/test_speech.mp3"):
        response = client.post(
            "/api/voice_upload",
            files={"audio_file": ("test.webm", fake_audio, "audio/webm")},
            data={"session_id": "test_pwa_voice", "lang": "hi-IN"},
            headers=AUTH_HEADERS
        )

        assert response.status_code == 200
        data = response.json()
        assert "reply" in data
        assert data["reply"] == "Namaste! Main Astra hoon."
        assert data["audio_url"] == "/static/audio/test_speech.mp3"
        assert "transcript" in data
        mock_brain.assert_called_once()


def test_style_css_mobile_responsive_breakpoints():
    """Verifies static/style.css contains mobile breakpoints and safe-area insets."""
    css_path = STATIC_DIR / "style.css"
    assert css_path.is_file()
    css_content = css_path.read_text(encoding="utf-8")

    assert "@media (max-width: 768px)" in css_content
    assert "@media (max-width: 480px)" in css_content
    assert "safe-area-inset-top" in css_content
    assert "safe-area-inset-bottom" in css_content
    assert "-webkit-overflow-scrolling: touch" in css_content
