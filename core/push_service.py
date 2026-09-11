"""
core/push_service.py - Native Web Push Service with VAPID Authentication

Provides standard W3C Push API / RFC 8291 / RFC 8292 support:
- Automatic VAPID EC P-256 key pair generation and persistent storage.
- Subscription registration and persistence (data/push_subscriptions.json).
- Push notification dispatching via pywebpush with 404/410 auto-pruning.
- Broadcast push delivery for reminders and proactive alerts when PWA is closed.
"""

import json
import base64
import logging
import threading
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid

from config import (
    VAPID_KEY_FILE,
    PUSH_SUBSCRIPTIONS_FILE,
    VAPID_CLAIMS_EMAIL,
    DATA_DIR
)

logger = logging.getLogger("astra.push_service")
_file_lock = threading.Lock()


def get_or_create_vapid_keys(key_file: Optional[Path] = None) -> Tuple[str, str]:
    """
    Retrieves or generates a VAPID EC P-256 keypair.
    Returns:
        tuple (public_key_b64, private_key_pem):
        - public_key_b64: URL-safe unpadded base64 65-byte uncompressed point for the browser.
        - private_key_pem: PEM formatted private key string for pywebpush.
    """
    target_file = key_file or VAPID_KEY_FILE
    with _file_lock:
        if target_file.exists():
            try:
                data = json.loads(target_file.read_text(encoding="utf-8"))
                pub = data.get("public_key")
                priv = data.get("private_key")
                if pub and priv:
                    return pub, priv
            except Exception as e:
                logger.warning(f"Could not load existing VAPID keys: {e}. Generating new keys.")

        # Generate fresh VAPID keys
        vapid = Vapid()
        vapid.generate_keys()

        # Extract 65-byte uncompressed public key point
        raw_public = vapid.public_key.public_bytes(
            encoding=serialization.Encoding.X962,
            format=serialization.PublicFormat.UncompressedPoint
        )
        b64_pub = base64.urlsafe_b64encode(raw_public).rstrip(b"=").decode("utf-8")
        private_pem = vapid.private_pem().decode("utf-8")

        # Persist keys
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_text(
            json.dumps({"public_key": b64_pub, "private_key": private_pem}, indent=2),
            encoding="utf-8"
        )
        logger.info(f"Generated new VAPID keypair in {target_file}")
        return b64_pub, private_pem


def get_public_vapid_key(key_file: Optional[Path] = None) -> str:
    """Returns the URL-safe base64 encoded public key for browser registration."""
    pub, _ = get_or_create_vapid_keys(key_file=key_file)
    return pub


def load_subscriptions(subs_file: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Loads stored push subscriptions from JSON storage."""
    target_file = subs_file or PUSH_SUBSCRIPTIONS_FILE
    with _file_lock:
        if not target_file.exists():
            return []
        try:
            return json.loads(target_file.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"Error reading push subscriptions: {e}")
            return []


def save_subscriptions(subs: List[Dict[str, Any]], subs_file: Optional[Path] = None) -> None:
    """Saves push subscriptions to JSON storage."""
    target_file = subs_file or PUSH_SUBSCRIPTIONS_FILE
    with _file_lock:
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_text(json.dumps(subs, indent=2), encoding="utf-8")


def add_subscription(sub_data: Dict[str, Any], subs_file: Optional[Path] = None) -> Dict[str, Any]:
    """
    Registers or updates a client push subscription.
    Expected schema:
    {
        "endpoint": "https://fcm.googleapis.com/fcm/send/...",
        "keys": {
            "p256dh": "...",
            "auth": "..."
        }
    }
    """
    if not isinstance(sub_data, dict) or not sub_data.get("endpoint"):
        return {"success": False, "error": "Invalid subscription: missing endpoint."}

    keys = sub_data.get("keys", {})
    if not keys.get("p256dh") or not keys.get("auth"):
        return {"success": False, "error": "Invalid subscription: missing cryptographic keys."}

    endpoint = sub_data["endpoint"]
    subs = load_subscriptions(subs_file)

    # Update existing or append new
    updated = False
    for item in subs:
        if item.get("endpoint") == endpoint:
            item["keys"] = keys
            item["updated_at"] = sub_data.get("updated_at")
            updated = True
            break

    if not updated:
        subs.append(sub_data)

    save_subscriptions(subs, subs_file)
    logger.info(f"Registered push subscription: {endpoint[:45]}... (Total: {len(subs)})")
    return {"success": True, "count": len(subs), "updated": updated}


def remove_subscription(endpoint: str, subs_file: Optional[Path] = None) -> bool:
    """Removes a subscription by endpoint (used when client unsubscribes or endpoint expires)."""
    subs = load_subscriptions(subs_file)
    filtered = [s for s in subs if s.get("endpoint") != endpoint]
    if len(filtered) != len(subs):
        save_subscriptions(filtered, subs_file)
        logger.info(f"Pruned expired/unregistered push subscription: {endpoint[:45]}...")
        return True
    return False


def send_push_to_subscription(
    subscription_info: Dict[str, Any],
    payload: Any,
    key_file: Optional[Path] = None,
    subs_file: Optional[Path] = None
) -> Dict[str, Any]:
    """
    Sends an encrypted Web Push notification to a single client subscription.
    Automatically removes the subscription if the push service returns HTTP 404 or 410.
    """
    try:
        from pywebpush import webpush, WebPushException
        _, private_pem = get_or_create_vapid_keys(key_file=key_file)

        data_str = json.dumps(payload) if isinstance(payload, dict) else str(payload)

        claims = {"sub": VAPID_CLAIMS_EMAIL}
        response = webpush(
            subscription_info=subscription_info,
            data=data_str,
            vapid_private_key=private_pem,
            vapid_claims=claims,
            ttl=86400
        )
        status_code = getattr(response, "status_code", 200)
        return {"success": True, "status_code": status_code}

    except Exception as ex:
        # Check for WebPushException and status codes
        status_code = None
        resp = getattr(ex, "response", None)
        if resp is not None:
            status_code = getattr(resp, "status_code", None)

        if status_code in (404, 410):
            endpoint = subscription_info.get("endpoint")
            if endpoint:
                remove_subscription(endpoint, subs_file=subs_file)

        logger.warning(f"WebPush delivery failed: {ex} (Status: {status_code})")
        return {"success": False, "error": str(ex), "status_code": status_code}


def send_broadcast_push(
    title: str,
    body: str,
    url: str = "/",
    tag: str = "astra-reminder",
    actions: Optional[List[Dict[str, str]]] = None,
    key_file: Optional[Path] = None,
    subs_file: Optional[Path] = None
) -> Dict[str, Any]:
    """
    Broadcasts a push notification to all active device subscriptions.
    Default actions include a quick 'Tap to talk' trigger so the user can
    respond or launch voice commands straight from the notification shade.
    """
    if actions is None:
        actions = [
            {"action": "talk", "title": "🎙️ Tap to talk"},
            {"action": "view", "title": "Open Astra"}
        ]

    payload = {
        "title": title,
        "body": body,
        "url": url,
        "tag": tag,
        "actions": actions
    }

    subscriptions = load_subscriptions(subs_file)
    if not subscriptions:
        logger.info("No active push subscriptions to broadcast.")
        return {"sent": 0, "failed": 0, "total": 0}

    sent = 0
    failed = 0

    for sub in subscriptions:
        res = send_push_to_subscription(sub, payload, key_file=key_file, subs_file=subs_file)
        if res.get("success"):
            sent += 1
        else:
            failed += 1

    logger.info(f"Broadcast push sent: {sent} succeeded, {failed} failed out of {len(subscriptions)}.")
    return {"sent": sent, "failed": failed, "total": len(subscriptions)}

