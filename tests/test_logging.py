"""
Tests for Structured JSON Logging & Observability Engine
Verifies:
1. Every tool call logs structured JSON with timestamp, tool_name, params, success, and latency_ms.
2. Sensitive parameters (phone numbers, WhatsApp messages, reminders) are automatically redacted.
3. Execution latency is captured accurately in milliseconds.
4. TimedRotatingFileHandler is configured with daily rotation and 30-day retention.
5. GET /api/logs/recent endpoint requires authentication (401 without token).
6. GET /api/logs/recent endpoint returns recent log entries as JSON array when authenticated.
"""

import json
import logging
from logging.handlers import TimedRotatingFileHandler
from unittest.mock import MagicMock
import pytest
from fastapi.testclient import TestClient

from config import LOG_FILE, ASTRA_AUTH_TOKEN
from core import actions
from core.logger import get_logger, redact_params, get_recent_logs
from app import app


@pytest.fixture
def client():
    return TestClient(app)


def test_tool_call_logs_structured_json():
    """Verifies that executing an action writes valid JSON with required observability fields."""
    # Execute an action
    res = actions.get_time_and_date()
    assert res["success"] is True

    # Read recent logs
    logs = get_recent_logs(limit=10)
    assert len(logs) > 0

    # Find the get_time_and_date log entry
    tool_entry = None
    for entry in logs:
        if entry.get("tool_name") == "get_time_and_date":
            tool_entry = entry
            break

    assert tool_entry is not None, "get_time_and_date log entry was not found in astra.log"
    assert "timestamp" in tool_entry
    assert "latency_ms" in tool_entry
    assert isinstance(tool_entry["latency_ms"], (int, float))
    assert tool_entry["latency_ms"] >= 0
    assert tool_entry["success"] is True
    assert "params" in tool_entry


def test_sensitive_parameter_redaction(monkeypatch):
    """Verifies that phone numbers and message bodies are strictly redacted from logs."""
    # Mock webbrowser.open to prevent launching actual browser during test
    monkeypatch.setattr("webbrowser.open", lambda url: True)

    secret_phone = "+919876543210"
    secret_message = "My super confidential banking password"

    actions.send_whatsapp(phone=secret_phone, message=secret_message)

    # Read raw log file text
    assert LOG_FILE.exists()
    log_text = LOG_FILE.read_text(encoding="utf-8")

    # Plaintext phone and message MUST NOT be logged
    assert secret_phone not in log_text
    assert secret_message not in log_text

    # Verify redaction token is present in the latest send_whatsapp entry
    logs = get_recent_logs(limit=5)
    whatsapp_entry = next((e for e in logs if e.get("tool_name") == "send_whatsapp"), None)
    assert whatsapp_entry is not None
    assert whatsapp_entry["params"]["phone"] == "[REDACTED_PHONE]"
    assert whatsapp_entry["params"]["message"] == "[REDACTED_MESSAGE]"


def test_redact_params_utility():
    """Unit test for parameter redaction helper on various sensitive keys and patterns."""
    raw_params = {
        "app_name": "notepad",
        "phone": "+14155552671",
        "message": "Meet me at the station",
        "note": "Call doctor at 9876543210",
        "count": 5
    }

    safe = redact_params(raw_params)
    assert safe["app_name"] == "notepad"
    assert safe["phone"] == "[REDACTED_PHONE]"
    assert safe["message"] == "[REDACTED_MESSAGE]"
    assert safe["note"] == "[REDACTED_MESSAGE]"
    assert safe["count"] == 5


def test_daily_rotation_handler_configuration():
    """Verifies that the logger is equipped with TimedRotatingFileHandler configured for midnight rotation."""
    logger = get_logger("astra.actions")
    rotating_handlers = [h for h in logger.handlers if isinstance(h, TimedRotatingFileHandler)]

    assert len(rotating_handlers) >= 1, "TimedRotatingFileHandler not attached to logger"
    handler = rotating_handlers[0]
    assert handler.when == "MIDNIGHT"
    assert handler.interval == 86400 or handler.interval == 1  # 1 day in seconds or daily interval
    assert handler.backupCount == 30


def test_recent_logs_endpoint_unauthenticated(client):
    """GET /api/logs/recent must return 401 when called without security token."""
    response = client.get("/api/logs/recent")
    assert response.status_code == 401
    assert "Unauthorized" in response.text


def test_recent_logs_endpoint_authenticated(client):
    """GET /api/logs/recent returns valid JSON log entries when called with valid token."""
    headers = {"X-Astra-Token": ASTRA_AUTH_TOKEN}
    response = client.get("/api/logs/recent?limit=10", headers=headers)
    assert response.status_code == 200

    data = response.json()
    assert data["success"] is True
    assert "count" in data
    assert "logs" in data
    assert isinstance(data["logs"], list)
    assert len(data["logs"]) <= 10

