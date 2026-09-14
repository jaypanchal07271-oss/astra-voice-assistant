"""
Unit tests for Fix #4: API Key Save Reliability (/api/save_key).

Verifies:
1. Valid API key saves to .env, verifies persistence, and updates os.environ.
2. Empty or whitespace-only API keys return HTTP 400 with {"success": False, "message": "API key cannot be empty"}.
3. Failed .env writes return HTTP 500 with success=False, and DO NOT update os.environ.
4. Existing environment variables in .env are preserved untouched.
5. API keys are never exposed in responses or error logs.
6. Endpoint strictly enforces authentication (HTTP 401 on missing/invalid token).
"""

import os
import pytest
from pathlib import Path
from unittest.mock import patch
from starlette.testclient import TestClient

from app import app
from config import ASTRA_AUTH_TOKEN

client = TestClient(app)
AUTH_HEADERS = {"X-Astra-Token": ASTRA_AUTH_TOKEN}


def test_save_key_rejects_unauthenticated_request():
    """Verify endpoint rejects unauthenticated requests with HTTP 401."""
    res = client.post("/api/save_key", json={"api_key": "AIzaSyTestKey123"})
    assert res.status_code == 401


def test_save_key_rejects_invalid_token():
    """Verify endpoint rejects requests with invalid token."""
    res = client.post(
        "/api/save_key",
        headers={"X-Astra-Token": "bad_token"},
        json={"api_key": "AIzaSyTestKey123"}
    )
    assert res.status_code == 401


def test_save_key_rejects_empty_and_whitespace_keys():
    """Verify whitespace-only and empty keys return HTTP 400."""
    # 1. Empty string
    res_empty = client.post(
        "/api/save_key",
        headers=AUTH_HEADERS,
        json={"api_key": ""}
    )
    assert res_empty.status_code == 400
    data_empty = res_empty.json()
    assert data_empty["success"] is False
    assert data_empty["message"] == "API key cannot be empty"

    # 2. Whitespace-only string
    res_ws = client.post(
        "/api/save_key",
        headers=AUTH_HEADERS,
        json={"api_key": "   \t\n  "}
    )
    assert res_ws.status_code == 400
    data_ws = res_ws.json()
    assert data_ws["success"] is False
    assert data_ws["message"] == "API key cannot be empty"


def test_save_key_success_flow_and_preservation(tmp_path, monkeypatch):
    """
    Verify complete success flow:
    - .env is updated
    - unrelated variables and comments are preserved
    - os.environ is updated
    - success response returned
    """
    fake_env = tmp_path / ".env"
    initial_content = (
        "# System Configuration\n"
        "HOST=127.0.0.1\n"
        "PORT=8000\n"
        "GEMINI_API_KEY=old_gemini_key\n"
        "WHATSAPP_PHONE=919876543210\n"
    )
    fake_env.write_text(initial_content, encoding="utf-8")

    # Set initial runtime state
    monkeypatch.setenv("GEMINI_API_KEY", "old_gemini_key")

    new_test_key = "AIzaSyNewSecureKey999"

    with patch("app.Path") as mock_path:
        mock_path.return_value.resolve.return_value.parent.__truediv__.return_value = fake_env
        res = client.post(
            "/api/save_key",
            headers=AUTH_HEADERS,
            json={"api_key": f"  {new_test_key}  "}
        )

    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["message"] == "API Key successfully updated and saved!"
    # Ensure raw API key is not echoed back in response
    assert new_test_key not in data.get("message", "")

    # Verify runtime environment was updated
    assert os.environ.get("GEMINI_API_KEY") == new_test_key

    # Verify .env contents
    saved_env_content = fake_env.read_text(encoding="utf-8")
    assert f"GEMINI_API_KEY={new_test_key}" in saved_env_content
    assert "old_gemini_key" not in saved_env_content
    # Verify preservation of other variables and comments
    assert "# System Configuration" in saved_env_content
    assert "HOST=127.0.0.1" in saved_env_content
    assert "PORT=8000" in saved_env_content
    assert "WHATSAPP_PHONE=919876543210" in saved_env_content


def test_save_key_creates_env_if_missing(tmp_path, monkeypatch):
    """Verify save_api_key creates .env if it does not already exist."""
    fake_env = tmp_path / ".env"
    assert not fake_env.exists()

    monkeypatch.setenv("GEMINI_API_KEY", "original_key")
    new_key = "AIzaSyBrandNewKey456"

    with patch("app.Path") as mock_path:
        mock_path.return_value.resolve.return_value.parent.__truediv__.return_value = fake_env
        res = client.post(
            "/api/save_key",
            headers=AUTH_HEADERS,
            json={"api_key": new_key}
        )

    assert res.status_code == 200
    assert fake_env.exists()
    assert f"GEMINI_API_KEY={new_key}" in fake_env.read_text(encoding="utf-8")
    assert os.environ.get("GEMINI_API_KEY") == new_key


def test_save_key_failure_does_not_update_environ(tmp_path, monkeypatch):
    """
    Verify that if writing .env fails:
    - HTTP 500 is returned
    - success=False is returned
    - os.environ is NOT updated
    - API key is not leaked in error response
    """
    original_key = "AIzaSyUntouchedOriginalKey"
    attempted_new_key = "AIzaSyFailedKeyToNeverSave"
    monkeypatch.setenv("GEMINI_API_KEY", original_key)

    fake_env = tmp_path / ".env"
    fake_env.write_text("GEMINI_API_KEY=" + original_key + "\n", encoding="utf-8")

    # Simulate filesystem / write error on .env
    def failing_write_text(*args, **kwargs):
        raise PermissionError("Disk is write-protected or read-only filesystem")

    with patch("app.Path") as mock_path:
        mock_env_obj = mock_path.return_value.resolve.return_value.parent.__truediv__.return_value
        mock_env_obj.exists.return_value = True
        mock_env_obj.read_text.return_value = fake_env.read_text()
        mock_env_obj.write_text.side_effect = failing_write_text

        res = client.post(
            "/api/save_key",
            headers=AUTH_HEADERS,
            json={"api_key": attempted_new_key}
        )

    assert res.status_code == 500
    data = res.json()
    assert data["success"] is False
    assert data["message"] == "Failed to save API key."
    # Never leak key in response
    assert attempted_new_key not in str(data)

    # CRITICAL: Runtime environment must NOT have been modified!
    assert os.environ.get("GEMINI_API_KEY") == original_key


def test_save_key_verification_failure_does_not_update_environ(tmp_path, monkeypatch):
    """
    Verify that if the file write happens but verification check fails:
    - HTTP 500 is returned
    - os.environ is NOT updated
    """
    original_key = "AIzaSyOriginalSafeKey"
    attempted_new_key = "AIzaSyCorruptKey"
    monkeypatch.setenv("GEMINI_API_KEY", original_key)

    with patch("app.Path") as mock_path:
        mock_env_obj = mock_path.return_value.resolve.return_value.parent.__truediv__.return_value
        mock_env_obj.exists.return_value = True
        # Read text initially returns original
        # After write_text, read_text returns stale/corrupt content without the key
        mock_env_obj.read_text.side_effect = [
            "GEMINI_API_KEY=" + original_key + "\n",
            "CORRUPTED_OR_STALE_CONTENT\n"
        ]
        mock_env_obj.write_text.return_value = None

        res = client.post(
            "/api/save_key",
            headers=AUTH_HEADERS,
            json={"api_key": attempted_new_key}
        )

    assert res.status_code == 500
    data = res.json()
    assert data["success"] is False
    assert data["message"] == "Failed to save API key."
    assert os.environ.get("GEMINI_API_KEY") == original_key

