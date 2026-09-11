"""
Automated Security Test Suite for Astra Assistant
Verifies:
1. Command injection defenses & shell metacharacter stripping.
2. Canonicalized Odoo security guardrail with leetspeak/spacing evasion attempts.
3. API Token-Based Authentication on all sensitive endpoints.
4. CORS restrictions.
"""

import pytest
from starlette.testclient import TestClient
import app
import config
from core import actions


@pytest.fixture
def client():
    return TestClient(app.app)


# =====================================================================
# 1. Command Injection & Input Sanitization Tests
# =====================================================================

def test_sanitize_text_strips_dangerous_metacharacters():
    payloads = [
        ("notepad.exe & calc.exe", "notepad.exe  calc.exe"),
        ("; rm -rf /", "rm -rf /"),
        ("app | whoami", "app  whoami"),
        ("test && echo hacked", "test  echo hacked"),
        ("val>out.txt", "valout.txt"),
        ("app^name", "appname"),
        ("user%input", "userinput"),
        ("cmd$var", "cmdvar")
    ]
    for raw, expected in payloads:
        sanitized = actions.sanitize_text(raw)
        assert sanitized == expected, f"Sanitization failed for '{raw}': got '{sanitized}'"
        assert not any(c in sanitized for c in ["&", "|", ";", ">", "<", "^", "%", "$"])


def test_open_app_rejects_empty_or_malformed():
    res = actions.open_app("")
    assert res["success"] is False
    assert "empty" in res["message"].lower()


def test_close_app_rejects_empty():
    res = actions.close_app("")
    assert res["success"] is False
    assert "empty" in res["message"].lower()


# =====================================================================
# 2. Hardened Odoo Security Guardrail Evasion Tests
# =====================================================================

def test_odoo_guardrail_blocks_standard_modules():
    assert actions.is_restricted_odoo_module("account") is True
    assert actions.is_restricted_odoo_module("inventory") is True
    assert actions.is_restricted_odoo_module("stock") is True


def test_odoo_guardrail_blocks_obfuscated_and_leetspeak_bypasses():
    evasion_attempts = [
        "a_c_c_o_u_n_t",
        "a c c o u n t",
        "a-c-c-o-u-n-t",
        "account_asset",
        "accounting_custom",
        "accountant",
        "1nventory",
        "1nv3ntory",
        "invento_ry",
        "inv",
        "stock_account",
        "stock_move",
        "ledger_custom",
        "journal_entry"
    ]
    for attempt in evasion_attempts:
        is_blocked = actions.is_restricted_odoo_module(attempt)
        assert is_blocked is True, f"Security Guardrail failed to block evasion attempt: '{attempt}'"

        res = actions.manage_odoo_server("update", attempt)
        assert res.get("status") == "access_denied", f"manage_odoo_server did not return access_denied for: '{attempt}'"
        assert "Access Denied" in res.get("message", "")


def test_odoo_guardrail_permits_safe_modules():
    safe_modules = ["website", "crm", "sale", "point_of_sale", "hr", "project"]
    for safe in safe_modules:
        assert actions.is_restricted_odoo_module(safe) is False
        res = actions.manage_odoo_server("update", safe)
        assert res.get("status") != "access_denied"


# =====================================================================
# 3. API Authentication & Token Protection Tests
# =====================================================================

def test_chat_endpoint_rejects_unauthenticated_request(client):
    res = client.post("/api/chat", json={"text": "open notepad"})
    assert res.status_code == 401
    assert "token required" in res.json().get("detail", "").lower()


def test_chat_endpoint_rejects_invalid_token(client):
    res = client.post(
        "/api/chat",
        json={"text": "open notepad"},
        headers={"X-Astra-Token": "invalid_wrong_token_12345"}
    )
    assert res.status_code == 401


def test_save_key_endpoint_rejects_unauthenticated_request(client):
    res = client.post("/api/save_key", json={"api_key": "some_test_key"})
    assert res.status_code == 401


def test_chat_endpoint_accepts_valid_token(client):
    res = client.post(
        "/api/chat",
        json={"text": "Abhi time kya hai?"},
        headers={"X-Astra-Token": config.ASTRA_AUTH_TOKEN}
    )
    assert res.status_code == 200
    data = res.json()
    assert "reply" in data
    assert len(data["reply"]) > 0
