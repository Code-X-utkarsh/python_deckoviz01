"""
End-to-end tests for the 6-digit device linking system.

Tests cover:
  - POST /device/code      (code generation)
  - POST /device/link       (linking a device)
  - GET  /device/{id}/status (polling fallback)
  - POST /device/token/refresh (token refresh)
  - WS   /ws/device/{id}    (WebSocket notifications)
  - Rate limiting
  - Edge cases (expired, invalid, duplicate codes)

Run:
  cd api
  python -m pytest tests/test_device_link.py -v
"""

import sys
import os
import time
import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Ensure the api/ directory is importable
# ---------------------------------------------------------------------------
API_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if API_DIR not in sys.path:
    sys.path.insert(0, API_DIR)


# ---------------------------------------------------------------------------
# Fake Redis that stores everything in-memory (no real server needed)
# ---------------------------------------------------------------------------

class FakeRedis:
    """Minimal in-memory Redis stand-in for testing."""

    def __init__(self):
        self._store: dict = {}
        self._ttls: dict = {}
        self._sets: dict = {}

    # -- string commands ---------------------------------------------------

    def set(self, key, value, ex=None):
        self._store[key] = value
        if ex:
            self._ttls[key] = time.time() + ex

    def get(self, key):
        if key in self._ttls and time.time() > self._ttls[key]:
            self.delete(key)
            return None
        return self._store.get(key)

    def delete(self, *keys):
        for key in keys:
            self._store.pop(key, None)
            self._ttls.pop(key, None)

    def exists(self, key):
        if key in self._ttls and time.time() > self._ttls[key]:
            self.delete(key)
            return False
        return key in self._store

    def ttl(self, key):
        if key not in self._ttls:
            return -1
        remaining = int(self._ttls[key] - time.time())
        if remaining <= 0:
            self.delete(key)
            return -2
        return remaining

    def expire(self, key, seconds):
        if key in self._store:
            self._ttls[key] = time.time() + seconds

    def incr(self, key):
        val = int(self._store.get(key, 0)) + 1
        self._store[key] = str(val)
        return val

    # -- set commands ------------------------------------------------------

    def sadd(self, key, *members):
        if key not in self._sets:
            self._sets[key] = set()
        self._sets[key].update(members)

    def srem(self, key, *members):
        if key in self._sets:
            self._sets[key] -= set(members)

    def scard(self, key):
        return len(self._sets.get(key, set()))

    def smembers(self, key):
        return self._sets.get(key, set())


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_fake_redis = FakeRedis()


@pytest.fixture(autouse=True)
def _reset_redis():
    """Reset the shared fake Redis before each test."""
    _fake_redis._store.clear()
    _fake_redis._ttls.clear()
    _fake_redis._sets.clear()
    yield


@pytest.fixture()
def client():
    """
    Create a TestClient with the fake Redis injected so we don't need a
    running Redis server.
    """
    with patch("databases.configs.get_redis_client", return_value=_fake_redis):
        from main import app  # noqa: import inside patch scope
        with TestClient(app) as c:
            yield c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TEST_USER_PAYLOAD = {"sub": "test-user-123", "email": "test@example.com"}


def _make_jwt() -> str:
    """Create a valid JWT for testing using the project's own helper."""
    from utils.token import create_access_token
    return create_access_token(_TEST_USER_PAYLOAD)


# ---------------------------------------------------------------------------
# POST /device/code
# ---------------------------------------------------------------------------

class TestGenerateCode:
    """Tests for the code-generation endpoint."""

    def test_generate_code_success(self, client: TestClient):
        resp = client.post("/device/code", json={"device_name": "Living Room TV"})
        assert resp.status_code == 200
        data = resp.json()
        assert "device_id" in data
        assert "code" in data
        assert len(data["code"]) == 6
        assert data["code"].isdigit()
        assert data["expires_in"] == 600

    def test_generate_code_no_body(self, client: TestClient):
        """device_name is optional — an empty body should still work."""
        resp = client.post("/device/code", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["code"]) == 6

    def test_generate_code_unique(self, client: TestClient):
        """Two consecutive calls should produce different codes."""
        r1 = client.post("/device/code", json={})
        r2 = client.post("/device/code", json={})
        assert r1.status_code == 200
        assert r2.status_code == 200
        # Different device_ids at minimum
        assert r1.json()["device_id"] != r2.json()["device_id"]

    def test_generate_code_rate_limit(self, client: TestClient):
        """After 5 rapid calls from the same IP, the 6th should be 429."""
        for _ in range(5):
            resp = client.post("/device/code", json={})
            assert resp.status_code == 200

        resp = client.post("/device/code", json={})
        assert resp.status_code == 429


# ---------------------------------------------------------------------------
# POST /device/link
# ---------------------------------------------------------------------------

class TestLinkDevice:
    """Tests for the device-linking endpoint."""

    def test_link_success(self, client: TestClient):
        # Step 1 — generate code
        gen = client.post("/device/code", json={})
        code = gen.json()["code"]
        device_id = gen.json()["device_id"]

        # Step 2 — link
        token = _make_jwt()
        resp = client.post(
            f"/device/link?token={token}",
            json={"code": code},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["device_id"] == device_id
        assert "room_id" in body

    def test_link_with_custom_room_id(self, client: TestClient):
        gen = client.post("/device/code", json={})
        code = gen.json()["code"]

        token = _make_jwt()
        resp = client.post(
            f"/device/link?token={token}",
            json={"code": code, "room_id": "my-room-42"},
        )
        assert resp.status_code == 200
        assert resp.json()["room_id"] == "my-room-42"

    def test_link_invalid_code(self, client: TestClient):
        token = _make_jwt()
        resp = client.post(
            f"/device/link?token={token}",
            json={"code": "000000"},
        )
        assert resp.status_code == 400

    def test_link_code_already_used(self, client: TestClient):
        gen = client.post("/device/code", json={})
        code = gen.json()["code"]

        token = _make_jwt()
        # First link — should succeed
        r1 = client.post(f"/device/link?token={token}", json={"code": code})
        assert r1.status_code == 200

        # Second link with same code — should fail
        r2 = client.post(f"/device/link?token={token}", json={"code": code})
        assert r2.status_code == 400

    def test_link_bad_code_format(self, client: TestClient):
        """Non-6-digit codes should be rejected by Pydantic validation."""
        token = _make_jwt()
        resp = client.post(
            f"/device/link?token={token}",
            json={"code": "abc"},
        )
        assert resp.status_code == 422  # Pydantic validation error

    def test_link_no_auth(self, client: TestClient):
        """Missing token should result in an error."""
        gen = client.post("/device/code", json={})
        code = gen.json()["code"]
        resp = client.post("/device/link", json={"code": code})
        # The endpoint requires ?token=..., so missing it gives 422 or 401
        assert resp.status_code in (401, 422)


# ---------------------------------------------------------------------------
# GET /device/{device_id}/status
# ---------------------------------------------------------------------------

class TestDeviceStatus:
    """Tests for the polling-fallback status endpoint."""

    def test_status_pending(self, client: TestClient):
        gen = client.post("/device/code", json={})
        device_id = gen.json()["device_id"]

        resp = client.get(f"/device/{device_id}/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "pending"
        assert body["code"] is not None
        assert body["room_id"] is None

    def test_status_linked(self, client: TestClient):
        gen = client.post("/device/code", json={})
        device_id = gen.json()["device_id"]
        code = gen.json()["code"]

        token = _make_jwt()
        client.post(f"/device/link?token={token}", json={"code": code})

        resp = client.get(f"/device/{device_id}/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "linked"
        assert body["room_id"] is not None
        assert body["token"] is not None
        assert body["refresh_token"] is not None
        assert body["ws_url"] is not None

    def test_status_expired(self, client: TestClient):
        """A non-existent device_id should return status='expired'."""
        resp = client.get(f"/device/{uuid.uuid4()}/status")
        assert resp.status_code == 200
        assert resp.json()["status"] == "expired"


# ---------------------------------------------------------------------------
# POST /device/token/refresh
# ---------------------------------------------------------------------------

class TestTokenRefresh:
    """Tests for the token-refresh endpoint."""

    def test_refresh_success(self, client: TestClient):
        # Generate & link to get tokens
        gen = client.post("/device/code", json={})
        code = gen.json()["code"]
        device_id = gen.json()["device_id"]
        token = _make_jwt()
        client.post(f"/device/link?token={token}", json={"code": code})

        # Get refresh_token from status
        status_resp = client.get(f"/device/{device_id}/status")
        refresh_token = status_resp.json()["refresh_token"]

        # Refresh
        resp = client.post(
            "/device/token/refresh",
            json={"refresh_token": refresh_token},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert body["token_type"] == "bearer"

    def test_refresh_invalid_token(self, client: TestClient):
        resp = client.post(
            "/device/token/refresh",
            json={"refresh_token": "invalid.jwt.token"},
        )
        assert resp.status_code == 401

    def test_refresh_with_access_token(self, client: TestClient):
        """An access token (not a refresh token) should be rejected."""
        access = _make_jwt()
        resp = client.post(
            "/device/token/refresh",
            json={"refresh_token": access},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# WS /ws/device/{device_id}
# ---------------------------------------------------------------------------

class TestWebSocket:
    """Tests for the device-linking WebSocket endpoint."""

    def test_ws_waiting_event(self, client: TestClient):
        gen = client.post("/device/code", json={})
        device_id = gen.json()["device_id"]

        with client.websocket_connect(f"/ws/device/{device_id}") as ws:
            data = ws.receive_json()
            assert data["type"] == "waiting"
            assert "code" in data
            assert "device_id" in data
            assert "expires_in" in data

    def test_ws_ping_pong(self, client: TestClient):
        gen = client.post("/device/code", json={})
        device_id = gen.json()["device_id"]

        with client.websocket_connect(f"/ws/device/{device_id}") as ws:
            _ = ws.receive_json()  # consume the "waiting" event
            ws.send_json({"type": "ping"})
            pong = ws.receive_json()
            assert pong["type"] == "pong"
            assert "timestamp" in pong

    def test_ws_check_status_pending(self, client: TestClient):
        gen = client.post("/device/code", json={})
        device_id = gen.json()["device_id"]

        with client.websocket_connect(f"/ws/device/{device_id}") as ws:
            _ = ws.receive_json()  # consume "waiting"
            ws.send_json({"type": "check_status"})
            status_msg = ws.receive_json()
            assert status_msg["type"] == "waiting"
            assert "code" in status_msg

    def test_ws_invalid_device(self, client: TestClient):
        """Connecting with a bogus device_id should return error + close."""
        with client.websocket_connect(f"/ws/device/{uuid.uuid4()}") as ws:
            data = ws.receive_json()
            assert data["type"] == "error"

    def test_ws_invalid_json(self, client: TestClient):
        gen = client.post("/device/code", json={})
        device_id = gen.json()["device_id"]

        with client.websocket_connect(f"/ws/device/{device_id}") as ws:
            _ = ws.receive_json()  # consume "waiting"
            ws.send_text("not valid json {{")
            err = ws.receive_json()
            assert err["type"] == "error"


# ---------------------------------------------------------------------------
# Token expiration
# ---------------------------------------------------------------------------

class TestAccessTokenExpiry:
    """Verify that access tokens now carry an ``exp`` claim."""

    def test_access_token_has_exp(self):
        from utils.token import create_access_token
        from jose import jwt
        from utils.settings import SECRET_KEY, JWT_HASH_ALGORITHM

        token = create_access_token({"sub": "user1"})
        payload = jwt.decode(token, SECRET_KEY, algorithms=[JWT_HASH_ALGORITHM])
        assert "exp" in payload

    def test_refresh_token_has_exp(self):
        from utils.token import create_refresh_token
        from jose import jwt
        from utils.settings import SECRET_KEY, JWT_HASH_ALGORITHM

        token = create_refresh_token({"sub": "user1"})
        payload = jwt.decode(token, SECRET_KEY, algorithms=[JWT_HASH_ALGORITHM])
        assert "exp" in payload
        assert payload.get("token_type") == "refresh"


# ---------------------------------------------------------------------------
# DeviceLinkRedisManager unit tests
# ---------------------------------------------------------------------------

class TestDeviceLinkRedisManager:
    """Unit tests for the Redis manager (no HTTP involved)."""

    def _manager(self):
        from utils.device_link_redis import DeviceLinkRedisManager
        return DeviceLinkRedisManager(_fake_redis)

    def test_generate_code_format(self):
        mgr = self._manager()
        code = mgr.generate_code(str(uuid.uuid4()))
        assert len(code) == 6 and code.isdigit()

    def test_get_device_for_code(self):
        mgr = self._manager()
        device_id = str(uuid.uuid4())
        code = mgr.generate_code(device_id)
        data = mgr.get_device_for_code(code)
        assert data is not None
        assert data["device_id"] == device_id

    def test_get_device_status(self):
        mgr = self._manager()
        device_id = str(uuid.uuid4())
        mgr.generate_code(device_id, device_name="TestTV")
        status = mgr.get_device_status(device_id)
        assert status is not None
        assert status["status"] == "pending"
        assert status["device_name"] == "TestTV"

    def test_link_device(self):
        mgr = self._manager()
        device_id = str(uuid.uuid4())
        code = mgr.generate_code(device_id)
        result = mgr.link_device(code, "user-42")
        assert result["status"] == "linked"
        assert result["device_id"] == device_id
        assert result["user_id"] == "user-42"
        assert result["room_id"].startswith("room-")

    def test_link_device_invalid_code(self):
        mgr = self._manager()
        with pytest.raises(ValueError, match="Invalid or expired"):
            mgr.link_device("000000", "user-42")

    def test_link_device_already_linked(self):
        mgr = self._manager()
        device_id = str(uuid.uuid4())
        code = mgr.generate_code(device_id)
        mgr.link_device(code, "user-42")
        # Code is deleted after linking, so a second attempt gives "Invalid or expired"
        with pytest.raises(ValueError):
            mgr.link_device(code, "user-99")

    def test_rate_limit(self):
        mgr = self._manager()
        for i in range(5):
            assert mgr.check_rate_limit("test-ip", 5) is True
        assert mgr.check_rate_limit("test-ip", 5) is False

    def test_invalidate_pending_device(self):
        mgr = self._manager()
        device_id = str(uuid.uuid4())
        code = mgr.generate_code(device_id)
        mgr.invalidate_device(device_id)
        assert mgr.get_device_status(device_id) is None
        assert mgr.get_device_for_code(code) is None

    def test_cleanup_stale_active_codes(self):
        mgr = self._manager()
        # Manually add an orphaned code to the SET
        _fake_redis.sadd(mgr.ACTIVE_CODES_KEY, "orphan1", "orphan2")
        removed = mgr.cleanup_stale_active_codes()
        assert removed == 2
        assert _fake_redis.scard(mgr.ACTIVE_CODES_KEY) == 0


