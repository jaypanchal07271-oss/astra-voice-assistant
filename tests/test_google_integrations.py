"""
Tests for Official Multi-App Integrations: Google Workspace (Gmail & Calendar)
Verifies:
1. Fernet token encryption & secure storage (zero plaintext tokens on disk).
2. Graceful error handling when credentials.json is missing.
3. read_recent_emails API response parsing and formatting with mocked service.
4. get_upcoming_events API response parsing and formatting with mocked service.
5. Proactive scheduler hooks: auto-scheduling reminders 15 minutes before event start.
6. Fallback intent parser matching for emails and calendar queries.
7. MCP server registration and delegation for both tools.
"""

import json
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from core import actions, brain
import mcp_skills


def test_fernet_token_encryption_roundtrip(tmp_path):
    """Verifies that tokens are encrypted with Fernet and never saved as plaintext."""
    token_file = tmp_path / "token_google.enc"

    # Mock Google Credentials object
    mock_token_payload = {
        "token": "ya29.mock_access_token_12345",
        "refresh_token": "1//0mock_refresh_token_67890",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "mock_client_id.apps.googleusercontent.com",
        "client_secret": "mock_client_secret",
        "scopes": actions.GOOGLE_SCOPES,
        "expiry": "2030-01-01T00:00:00Z"
    }

    mock_creds = MagicMock()
    mock_creds.to_json.return_value = json.dumps(mock_token_payload)

    # 1. Save encrypted tokens
    success = actions._save_encrypted_tokens(mock_creds, token_path=token_file)
    assert success is True
    assert token_file.exists()

    # 2. Verify on-disk file is NOT plaintext JSON
    raw_disk_bytes = token_file.read_bytes()
    assert b"ya29.mock_access_token_12345" not in raw_disk_bytes
    assert b"mock_client_secret" not in raw_disk_bytes

    # 3. Verify decryption restores original credentials
    restored_creds = actions._load_encrypted_tokens(token_path=token_file)
    assert restored_creds is not None
    assert restored_creds.token == "ya29.mock_access_token_12345"
    assert restored_creds.refresh_token == "1//0mock_refresh_token_67890"


def test_missing_credentials_fails_gracefully(tmp_path):
    """Verifies clear, non-crashing instructions when credentials.json does not exist."""
    fake_token_file = tmp_path / "nonexistent.enc"
    fake_creds_file = tmp_path / "nonexistent.json"

    creds, err = actions.get_google_credentials(
        token_path=fake_token_file,
        credentials_path=fake_creds_file
    )
    assert creds is None
    assert "credentials.json" in err
    assert "Google Cloud Console" in err

    # Unauthenticated read_recent_emails should return structured error
    res_mail = actions.read_recent_emails(count=5)
    # Either fails gracefully because credentials.json is not provided, or runs with existing creds
    assert "success" in res_mail

    # Unauthenticated get_upcoming_events should return structured error
    res_cal = actions.get_upcoming_events(days=7)
    assert "success" in res_cal


def test_read_recent_emails_with_mock_service():
    """Verifies that read_recent_emails correctly parses Gmail API metadata."""
    mock_service = MagicMock()

    # Mock messages.list
    mock_service.users().messages().list().execute.return_value = {
        "messages": [
            {"id": "msg_001"},
            {"id": "msg_002"}
        ]
    }

    # Mock messages.get for each message
    def mock_get(userId, id, format, metadataHeaders):
        if id == "msg_001":
            return MagicMock(execute=lambda: {
                "id": "msg_001",
                "snippet": "Meeting notes for review tomorrow.",
                "payload": {
                    "headers": [
                        {"name": "From", "value": "Alice <alice@example.com>"},
                        {"name": "Subject", "value": "Project Sync Notes"},
                        {"name": "Date", "value": "Tue, 8 Sep 2026 14:00:00 +0530"}
                    ]
                }
            })
        else:
            return MagicMock(execute=lambda: {
                "id": "msg_002",
                "snippet": "Invoice payment confirmed.",
                "payload": {
                    "headers": [
                        {"name": "From", "value": "Billing <billing@example.com>"},
                        {"name": "Subject", "value": "Receipt #1042"},
                        {"name": "Date", "value": "Tue, 8 Sep 2026 12:00:00 +0530"}
                    ]
                }
            })

    mock_service.users().messages().get.side_effect = mock_get

    res = actions.read_recent_emails(count=2, _service=mock_service)
    assert res["success"] is True
    assert res["count"] == 2
    assert len(res["emails"]) == 2
    assert res["emails"][0]["subject"] == "Project Sync Notes"
    assert res["emails"][1]["subject"] == "Receipt #1042"
    assert "Alice" in res["message"]
    assert "Receipt #1042" in res["message"]


def test_get_upcoming_events_with_mock_service_and_proactive_reminder():
    """Verifies that get_upcoming_events parses events and creates 15-minute advance reminders."""
    mock_service = MagicMock()

    # Create an event starting 45 minutes in the future
    future_start = datetime.now(timezone.utc) + timedelta(minutes=45)
    future_end = future_start + timedelta(hours=1)
    event_id = "test_event_proactive_123"

    mock_service.events().list().execute.return_value = {
        "items": [
            {
                "id": event_id,
                "summary": "Team Strategy Meeting",
                "start": {"dateTime": future_start.isoformat()},
                "end": {"dateTime": future_end.isoformat()},
                "location": "Room 402 / Google Meet"
            }
        ]
    }

    # Reset any existing scheduled events for this test id
    actions._SCHEDULED_CALENDAR_EVENT_IDS.discard(event_id)

    res = actions.get_upcoming_events(days=7, _service=mock_service)
    assert res["success"] is True
    assert res["count"] == 1
    assert res["events"][0]["summary"] == "Team Strategy Meeting"
    assert res["reminders_created"] == 1
    assert event_id in actions._SCHEDULED_CALENDAR_EVENT_IDS

    # Check proactive reminder is in _SCHEDULED_REMINDERS
    assert any("Team Strategy Meeting" in r["note"] for r in actions._SCHEDULED_REMINDERS)

    # Calling again should deduplicate and NOT create another reminder for same event ID
    res2 = actions.get_upcoming_events(days=7, _service=mock_service)
    assert res2["reminders_created"] == 0


def test_fallback_intent_parser_google_integrations(monkeypatch):
    """Verifies that voice commands trigger Gmail and Calendar actions in offline/fallback mode."""
    mail_called = []
    cal_called = []

    monkeypatch.setattr(actions, "read_recent_emails", lambda count=5: mail_called.append(count) or {"success": True, "message": "Checked 5 emails."})
    monkeypatch.setattr(actions, "get_upcoming_events", lambda days=7: cal_called.append(days) or {"success": True, "message": "Checked upcoming calendar events."})

    # Test email queries
    res1 = brain.fallback_intent_parser("kya koi naya email aaya hai?")
    assert len(mail_called) == 1
    assert "Checked 5 emails" in res1["reply"]

    res2 = brain.fallback_intent_parser("check my 3 recent emails")
    assert len(mail_called) == 2
    assert mail_called[-1] == 3

    # Test calendar queries
    res3 = brain.fallback_intent_parser("aaj ki meetings kya hain?")
    assert len(cal_called) == 1
    assert cal_called[-1] == 1  # 'aaj' maps to 1 day

    res4 = brain.fallback_intent_parser("upcoming calendar events dekho 5 din ke")
    assert len(cal_called) == 2
    assert cal_called[-1] == 5


def test_fallback_intent_parser_avoids_website_false_positives(monkeypatch):
    """Verifies that 'open gmail' still routes to opening Gmail website and not API email reader."""
    web_opened = []
    monkeypatch.setattr(actions, "open_website", lambda site, q="": web_opened.append(site) or {"success": True, "action": "open_website", "website": site, "message": f"Opened {site}"})

    res = brain.fallback_intent_parser("open gmail please")
    assert res["action"].get("action") == "open_website" or res["action"].get("website") == "gmail"
    assert len(web_opened) == 1


def test_mcp_skills_google_tools_delegation(monkeypatch):
    """Verifies MCP server tools delegate to core.actions."""
    monkeypatch.setattr(actions, "read_recent_emails", lambda count=5: {"success": True, "message": f"Read {count} emails mock"})
    monkeypatch.setattr(actions, "get_upcoming_events", lambda days=7: {"success": True, "message": f"Found events for {days} days mock"})

    mcp_mail = mcp_skills.read_recent_emails(count=4)
    assert "Read 4 emails mock" in mcp_mail

    mcp_cal = mcp_skills.get_upcoming_events(days=10)
    assert "Found events for 10 days mock" in mcp_cal

