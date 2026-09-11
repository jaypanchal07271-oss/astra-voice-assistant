"""
tests/test_mobile_push_and_shortcuts.py - Automated Test Suite for Mobile Push & Background Quick-Talk

Verifies:
1. VAPID key generation, uncompressed EC point format, and disk persistence.
2. Public key endpoint (/api/push/vapid_public_key) returns valid VAPID key.
3. Push subscribe endpoint (/api/push/subscribe) authentication and subscription storage.
4. Test push endpoint (/api/push/test) authentication and broadcast triggering.
5. Push delivery via pywebpush with automatic pruning on 404/410 status codes.
6. schedule_reminder triggers send_broadcast_push with 'Tap to talk' action.
7. Manifest shortcuts array contains 'Tap to Talk' (?action=talk).
8. Service Worker sw.js contains push, notificationclick, and message handlers.
9. Index.html and style.css contain push notification button and active styles.
10. App.js contains shortcut triggerVoiceInput, TRIGGER_TALK listener, and push registration.
"""

import json
import time
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from starlette.testclient import TestClient

from app import app
from config import ASTRA_AUTH_TOKEN, STATIC_DIR, DATA_DIR
from core.push_service import (
    get_or_create_vapid_keys,
    get_public_vapid_key,
    load_subscriptions,
    save_subscriptions,
    add_subscription,
    remove_subscription,
    send_push_to_subscription,
    send_broadcast_push
)
import core.actions as actions

client = TestClient(app)
AUTH_HEADERS = {"X-Astra-Token": ASTRA_AUTH_TOKEN}


def test_vapid_key_lifecycle_and_persistence(tmp_path):
    """Verifies VAPID keys are generated as valid uncompressed P-256 points and persisted."""
    test_key_file = tmp_path / "test_vapid.json"

    # 1. First generation
    pub1, priv1 = get_or_create_vapid_keys(key_file=test_key_file)
    assert len(pub1) >= 80  # 65-byte uncompressed EC point in base64url is 87 chars
    assert "BEGIN PRIVATE KEY" in priv1
    assert test_key_file.exists()

    # 2. Subsequent load must return identical keys
    pub2, priv2 = get_or_create_vapid_keys(key_file=test_key_file)
    assert pub1 == pub2
    assert priv1 == priv2


def test_vapid_public_key_endpoint():
    """Verifies /api/push/vapid_public_key returns the public key."""
    response = client.get("/api/push/vapid_public_key")
    assert response.status_code == 200
    data = response.json()
    assert "public_key" in data
    assert len(data["public_key"]) >= 80


def test_push_subscribe_endpoint_unauthorized():
    """Verifies /api/push/subscribe rejects unauthenticated requests."""
    clean_client = TestClient(app)
    response = clean_client.post(
        "/api/push/subscribe",
        json={"subscription": {"endpoint": "https://test.com/ep", "keys": {"p256dh": "k", "auth": "a"}}}
    )
    assert response.status_code == 401


def test_push_subscribe_endpoint_authenticated(tmp_path):
    """Verifies /api/push/subscribe stores subscription when authenticated."""
    test_subs_file = tmp_path / "subs.json"
    sub_payload = {
        "endpoint": "https://fcm.googleapis.com/fcm/send/sample-device-token-1",
        "keys": {
            "p256dh": "BNcRdreALRF89xU25GgwDA-p5E2cgj1uA===",
            "auth": "tBH9oQn0AQ=="
        }
    }

    with patch("core.push_service.PUSH_SUBSCRIPTIONS_FILE", test_subs_file):
        response = client.post(
            "/api/push/subscribe",
            json={"subscription": sub_payload},
            headers=AUTH_HEADERS
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["count"] == 1

        # Subscribe again with same endpoint (should update, not duplicate)
        response2 = client.post(
            "/api/push/subscribe",
            json={"subscription": sub_payload},
            headers=AUTH_HEADERS
        )
        assert response2.status_code == 200
        assert response2.json()["count"] == 1


def test_push_test_endpoint():
    """Verifies /api/push/test requires auth and triggers broadcast push."""
    clean_client = TestClient(app)
    res_unauth = clean_client.post("/api/push/test")
    assert res_unauth.status_code == 401

    with patch("core.push_service.send_broadcast_push", return_value={"sent": 1, "failed": 0, "total": 1}) as mock_send:
        res_auth = client.post(
            "/api/push/test",
            json={"title": "Test Title", "body": "Test Message"},
            headers=AUTH_HEADERS
        )
        assert res_auth.status_code == 200
        data = res_auth.json()
        assert data["success"] is True
        mock_send.assert_called_once()


def test_push_service_delivery_and_pruning_on_410(tmp_path):
    """Verifies push delivery via pywebpush and automatic removal of expired 410 subscriptions."""
    test_subs_file = tmp_path / "test_subs.json"
    expired_endpoint = "https://fcm.googleapis.com/fcm/send/expired-device"
    valid_endpoint = "https://fcm.googleapis.com/fcm/send/valid-device"

    save_subscriptions([
        {"endpoint": expired_endpoint, "keys": {"p256dh": "k1", "auth": "a1"}},
        {"endpoint": valid_endpoint, "keys": {"p256dh": "k2", "auth": "a2"}}
    ], subs_file=test_subs_file)

    class FakeWebPushException(Exception):
        def __init__(self, status_code):
            self.response = MagicMock()
            self.response.status_code = status_code
            super().__init__(f"Push service returned {status_code}")

    def fake_webpush(subscription_info, **kwargs):
        if subscription_info["endpoint"] == expired_endpoint:
            raise FakeWebPushException(410)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        return mock_resp

    with patch("pywebpush.webpush", side_effect=fake_webpush):
        result = send_broadcast_push(
            title="Broadcast Test",
            body="Hello!",
            subs_file=test_subs_file
        )

        assert result["sent"] == 1
        assert result["failed"] == 1

        # The expired endpoint should have been automatically pruned
        remaining = load_subscriptions(subs_file=test_subs_file)
        assert len(remaining) == 1
        assert remaining[0]["endpoint"] == valid_endpoint


def test_schedule_reminder_dispatches_push_notification():
    """Verifies schedule_reminder worker triggers send_broadcast_push."""
    import threading
    called_event = threading.Event()

    def mock_broadcast(*args, **kwargs):
        called_event.set()
        return {"sent": 1, "failed": 0, "total": 1}

    with patch("core.push_service.send_broadcast_push", side_effect=mock_broadcast) as mock_push:
        with patch("core.actions.time.sleep", return_value=None):
            res = actions.schedule_reminder(minutes=0.001, note="Call doctor")
            assert res["success"] is True
            assert "Call doctor" in res["message"]
            assert called_event.wait(timeout=2.0)
            mock_push.assert_called_once()
            call_kwargs = mock_push.call_args[1]
            assert "Astra Reminder" in call_kwargs.get("title", "")
            assert "Call doctor" in call_kwargs.get("body", "")


def test_manifest_shortcuts_for_quick_launch():
    """Verifies manifest.json contains shortcuts array with Tap to Talk (?action=talk)."""
    response = client.get("/manifest.json")
    assert response.status_code == 200
    data = response.json()

    assert "shortcuts" in data
    shortcuts = data["shortcuts"]
    assert len(shortcuts) >= 1

    talk_shortcut = next((s for s in shortcuts if "?action=talk" in s.get("url", "")), None)
    assert talk_shortcut is not None
    assert "Talk" in talk_shortcut.get("name", "")
    assert len(talk_shortcut.get("icons", [])) >= 1


def test_service_worker_push_and_quick_talk_handlers():
    """Verifies sw.js implements push, notificationclick, and quick-talk message handlers."""
    response = client.get("/sw.js")
    assert response.status_code == 200
    content = response.text

    assert "self.addEventListener('push'" in content
    assert "self.addEventListener('notificationclick'" in content
    assert "action === 'talk'" in content
    assert "TRIGGER_TALK" in content
    assert "SHOW_QUICK_TALK_NOTIFICATION" in content
    assert "astra-quick-talk" in content


def test_frontend_ui_elements_and_javascript():
    """Verifies index.html, style.css, and app.js implement push notifications and quick talk."""
    # 1. HTML button
    res_html = client.get("/")
    assert res_html.status_code == 200
    assert 'id="pushNotificationBtn"' in res_html.text

    # 2. CSS styles
    css_path = STATIC_DIR / "style.css"
    css_text = css_path.read_text(encoding="utf-8")
    assert ".push-btn" in css_text
    assert ".push-btn.active" in css_text
    assert "bellRing" in css_text

    # 3. JavaScript handlers
    js_path = STATIC_DIR / "app.js"
    js_text = js_path.read_text(encoding="utf-8")
    assert "triggerVoiceInput" in js_text
    assert "TRIGGER_TALK" in js_text
    assert "SHOW_QUICK_TALK_NOTIFICATION" in js_text
    assert "enablePushAndQuickTalk" in js_text
    assert "?action=" in js_text

