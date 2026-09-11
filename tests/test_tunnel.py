"""
test_tunnel.py - Test suite for Cloudflare Tunnel integration, CORS, and security.
"""

import os
import re
import pytest
from fastapi.testclient import TestClient
from app import app
import config
from start_tunnel import (
    TRYCLOUDFLARE_REGEX,
    extract_tunnel_url_from_line,
    find_cloudflared_executable,
    start_cloudflared_tunnel,
    stop_cloudflared_tunnel
)

client = TestClient(app)


def test_trycloudflare_regex():
    """Verify regex correctly parses trycloudflare.com URLs from log formats."""
    line1 = "2026-09-09T09:40:00Z INF |  https://sweet-apples-123.trycloudflare.com  |"
    line2 = "Your quick Tunnel has been created! Visit it at: https://cool-breeze-456.trycloudflare.com"
    line3 = "https://alpha-beta-gamma.trycloudflare.com"
    line4 = "Just a regular log line without url"
    line5 = "https://example.com/not-a-tunnel"

    assert extract_tunnel_url_from_line(line1) == "https://sweet-apples-123.trycloudflare.com"
    assert extract_tunnel_url_from_line(line2) == "https://cool-breeze-456.trycloudflare.com"
    assert extract_tunnel_url_from_line(line3) == "https://alpha-beta-gamma.trycloudflare.com"
    assert extract_tunnel_url_from_line(line4) is None
    assert extract_tunnel_url_from_line(line5) is None


def test_add_cors_origin():
    """Verify dynamic addition of origins to config.CORS_ORIGINS."""
    test_origin = "https://dynamically-added-tunnel.trycloudflare.com"
    initial_len = len(config.CORS_ORIGINS)

    # Adding new origin
    config.add_cors_origin(test_origin)
    assert test_origin in config.CORS_ORIGINS

    # Adding duplicate origin should not duplicate
    config.add_cors_origin(test_origin)
    assert config.CORS_ORIGINS.count(test_origin) == 1

    # Empty string should not be added
    config.add_cors_origin("")
    assert "" not in config.CORS_ORIGINS


def test_cors_preflight_for_tunnel_origin():
    """Verify CORSMiddleware allows trycloudflare.com origins via allow_origin_regex."""
    tunnel_origin = "https://test-mobile-access.trycloudflare.com"

    # Send preflight OPTIONS request
    response = client.options(
        "/api/chat",
        headers={
            "Origin": tunnel_origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type, X-Astra-Token",
        }
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == tunnel_origin

    # Verify disallowed origin does not get CORS permission
    bad_origin = "https://malicious-site.example.org"
    bad_response = client.options(
        "/api/chat",
        headers={
            "Origin": bad_origin,
            "Access-Control-Request-Method": "POST",
        }
    )
    assert bad_response.headers.get("access-control-allow-origin") is None


def test_security_auth_mandatory_on_tunnel_origin():
    """
    Critical Security Test: Verify that requests originating from a Cloudflare Tunnel
    STILL strictly require ASTRA_AUTH_TOKEN authentication.
    """
    tunnel_origin = "https://public-tunnel.trycloudflare.com"

    # 1. Unauthenticated request to /api/chat from tunnel origin must return 401
    unauth_resp = client.post(
        "/api/chat",
        json={"text": "Open notepad"},
        headers={"Origin": tunnel_origin}
    )
    assert unauth_resp.status_code == 401
    assert "Unauthorized" in unauth_resp.json().get("detail", "")

    # 2. Request with invalid token from tunnel origin must return 401
    invalid_resp = client.post(
        "/api/chat",
        json={"text": "Open notepad"},
        headers={
            "Origin": tunnel_origin,
            "X-Astra-Token": "invalid_fake_token_12345"
        }
    )
    assert invalid_resp.status_code == 401

    # 3. Request with valid token from tunnel origin must be accepted (not 401)
    valid_resp = client.post(
        "/api/chat",
        json={"text": ""},
        headers={
            "Origin": tunnel_origin,
            "X-Astra-Token": config.ASTRA_AUTH_TOKEN
        }
    )
    assert valid_resp.status_code == 200

    # 4. Same test on other sensitive endpoints
    logs_unauth = client.get("/api/logs/recent", headers={"Origin": tunnel_origin})
    assert logs_unauth.status_code == 401

    key_unauth = client.post("/api/save_key", json={"api_key": "test"}, headers={"Origin": tunnel_origin})
    assert key_unauth.status_code == 401


def test_start_tunnel_missing_binary(monkeypatch):
    """Verify start_cloudflared_tunnel handles missing executable gracefully without crashing."""
    # Monkeypatch find_cloudflared_executable to return None
    monkeypatch.setattr("start_tunnel.find_cloudflared_executable", lambda: None)

    proc, url = start_cloudflared_tunnel(port=8000, timeout=1.0)
    assert proc is None
    assert url is None

