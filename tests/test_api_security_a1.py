import pytest
from fastapi.testclient import TestClient
from backend.main import app
from backend.config import settings

client = TestClient(app)


def test_default_host_binding_is_loopback():
    """Validates A-1: Default HOST must be 127.0.0.1 to prevent binding to all interfaces (0.0.0.0)."""
    assert settings.HOST == "127.0.0.1"


def test_cors_trusted_origins_allowed():
    """Validates A-1: Trusted local development and desktop origins are allowed."""
    trusted_origins = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "tauri://localhost",
        "http://tauri.localhost",
        "https://tauri.localhost",
    ]
    for origin in trusted_origins:
        resp = client.get("/api/v1/health", headers={"Origin": origin})
        assert resp.status_code == 200, f"Failed for trusted origin: {origin}"


def test_untrusted_cross_origin_request_blocked():
    """
    Validates A-1: Untrusted web origins (e.g. malicious websites attempting CSRF)
    are strictly rejected with HTTP 403 Forbidden before route execution.
    """
    untrusted_origins = [
        "http://evil.com",
        "https://malicious-site.org",
        "http://localhost.evil.com",
        "http://192.168.1.100:3000",
        "http://attacker.local",
    ]
    for origin in untrusted_origins:
        # 1. State-changing / sensitive logout route
        resp_logout = client.post("/api/v1/auth/logout", headers={"Origin": origin})
        assert resp_logout.status_code == 403
        assert "Cross-origin access from this origin is forbidden" in resp_logout.json()["detail"]

        # 2. Files list route
        resp_files = client.get("/api/v1/files", headers={"Origin": origin})
        assert resp_files.status_code == 403
        assert "Cross-origin access from this origin is forbidden" in resp_files.json()["detail"]


def test_external_host_header_blocked():
    """
    Validates A-1: When bound to loopback, requests with external/spoofed Host headers
    are rejected with HTTP 403 Forbidden.
    """
    untrusted_hosts = [
        "evil.com",
        "192.168.1.200:8000",
        "attacker.org",
    ]
    for host in untrusted_hosts:
        resp = client.get("/api/v1/health", headers={"Host": host})
        assert resp.status_code == 403
        assert "Non-loopback Host header is denied" in resp.json()["detail"]


def test_api_token_authentication_when_strict_mode_enabled(monkeypatch):
    """
    Validates A-1: When REQUIRE_API_AUTH is enabled, requests require a valid
    local token via Authorization Bearer or X-API-Key.
    """
    test_token = "vayom_secure_test_token_12345"
    monkeypatch.setattr(settings, "REQUIRE_API_AUTH", True)
    monkeypatch.setattr(settings, "LOCAL_API_TOKEN", test_token)

    # 1. Missing token -> 401
    resp_no_token = client.get("/api/v1/health")
    assert resp_no_token.status_code == 401
    assert "Valid API authentication token required" in resp_no_token.json()["detail"]

    # 2. Invalid token -> 401
    resp_invalid = client.get("/api/v1/health", headers={"Authorization": "Bearer wrong_token"})
    assert resp_invalid.status_code == 401

    # 3. Valid Bearer token -> 200
    resp_bearer = client.get("/api/v1/health", headers={"Authorization": f"Bearer {test_token}"})
    assert resp_bearer.status_code == 200

    # 4. Valid X-API-Key header -> 200
    resp_api_key = client.get("/api/v1/health", headers={"X-API-Key": test_token})
    assert resp_api_key.status_code == 200
