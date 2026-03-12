# Device Linking System — 6-Digit Code TV Login

## Overview

A 6-digit device linking system for TV login, similar to Netflix / YouTube TV login. This replaces the QR code scanning flow with a simpler code-entry flow that works well with TV remote controls.

---

## Architecture

```
┌─────────┐     POST /device/code      ┌──────────┐
│  TV App │ ──────────────────────────▶ │ FastAPI  │
│         │ ◀────── {code, device_id}   │ Backend  │
│         │                             │ (Port    │
│         │     WS /ws/device/{id}      │  8080)   │
│         │ ═══════════════════════════ │          │
│         │ ◀── {type: "waiting"}       │          │
│         │                             │          │
│         │                             │          │
│         │ ◀── {type: "device_linked", │          │
│         │      access_token,          │          │
│         │      refresh_token,         │          │
│         │      ws_url, room_id}       │          │
│         │                             │          │
│         │     POST /device/token/     │          │
│         │       refresh               │          │
│         │ ──────────────────────────▶ │          │
│         │ ◀── {access_token}          │          │
└─────────┘                             │          │
                                        │          │
┌─────────┐     POST /device/link       │          │
│ Mobile  │ ──────────────────────────▶ │          │
│  App    │     {code: "123456"}        │          │  ┌─────────┐
│         │ ◀── {success, room_id}      │          │──│  Redis   │
└─────────┘                             └──────────┘  └─────────┘
```

All state is stored in **Redis** with TTL-based auto-expiration — no PostgreSQL migrations required. This matches the existing QR pairing architecture.

---

## API Endpoints

### 1. Generate Device Code (TV)

```
POST /device/code
```

**Authentication:** None (called by TV before user is authenticated)

**Request Body:**
```json
{
  "device_name": "Living Room TV"
}
```

**Response (200):**
```json
{
  "device_id": "550e8400-e29b-41d4-a716-446655440000",
  "code": "482931",
  "expires_in": 600
}
```

**Errors:**
- `429` — Rate limited (max 5 requests per IP per 60 seconds)
- `503` — Code generation failed (collision exhaustion)

---

### 2. Link Device (Mobile — Authenticated)

```
POST /device/link?token=<JWT_TOKEN>
```

**Authentication:** JWT token via query parameter (same pattern as existing `/qr/pair`)

**Request Body:**
```json
{
  "code": "482931",
  "room_id": "room-abc123"
}
```

**Response (200):**
```json
{
  "success": true,
  "device_id": "550e8400-e29b-41d4-a716-446655440000",
  "room_id": "room-abc123",
  "message": "Device 550e8400-... linked successfully."
}
```

**Errors:**
- `400` — Invalid/expired code, or code already used
- `429` — Rate limited (max 10 attempts per user per 60 seconds)

---

### 3. Check Device Status (TV — Polling Fallback)

```
GET /device/{device_id}/status
```

**Authentication:** None

**Response (200):**
```json
{
  "status": "linked",
  "room_id": "room-abc123",
  "code": null,
  "token": "eyJ...",
  "refresh_token": "eyJ...",
  "ws_url": "/ws/tv?room=room-abc123&device_id=..."
}
```

---

### 4. Refresh Access Token (TV)

```
POST /device/token/refresh
```

**Authentication:** None (the refresh token itself proves identity)

**Request Body:**
```json
{
  "refresh_token": "eyJ..."
}
```

**Response (200):**
```json
{
  "access_token": "eyJ...",
  "token_type": "bearer"
}
```

**Errors:**
- `401` — Invalid, expired, or non-refresh token

This endpoint allows the TV to stay authenticated long-term without requiring the user to re-enter a 6-digit code every time the access token expires.

---

### 5. WebSocket — TV Linking Notification

```
WS /ws/device/{device_id}
```

**Events received by TV:**

| Event Type | When | Payload |
|---|---|---|
| `waiting` | On connect | `{type, message, code, device_id, expires_in, timestamp}` |
| `device_linked` | When mobile links | `{type, device_id, room_id, access_token, refresh_token, ws_url, timestamp}` |
| `expired` | When code expires | `{type, message, timestamp}` |
| `pong` | After ping | `{type, timestamp}` |

**Messages TV can send:**

| Message Type | Purpose |
|---|---|
| `{"type": "ping"}` | Keepalive |
| `{"type": "check_status"}` | Manual status check |

---

## Full Flow

### TV Login Flow

```
1. TV calls POST /device/code
   → Receives {device_id, code: "482931", expires_in: 600}

2. TV displays "482931" on screen

3. TV connects to WS /ws/device/{device_id}

4. TV waits for linking event...

5. When mobile links:
   → TV receives {type: "device_linked", access_token, refresh_token, ws_url, room_id}

6. TV stores access_token and refresh_token locally

7. TV disconnects from linking WS (/ws/device/{device_id})

8. TV connects to /ws/tv?room={room_id}&device_id={device_id}
   (use ws_url from the event)

9. When access_token expires, TV calls POST /device/token/refresh
   with the stored refresh_token to get a fresh access_token
```

### Mobile Linking Flow

```
1. User opens mobile app (already authenticated)

2. User navigates to "Link TV" screen

3. User enters 6-digit code shown on TV

4. Mobile calls POST /device/link?token={jwt}
   with body: {code: "482931"}

5. Backend validates code, links device

6. Backend pushes device_linked event via WebSocket to TV

7. Mobile receives success response

8. Mobile can now connect to /ws/mobile?room={room_id}
```

---

## Redis Key Structure

| Key Pattern | Value | TTL |
|---|---|---|
| `devicelink:code:{code}` | `{device_id, created_at, status}` | 600s (10 min) |
| `devicelink:device:{device_id}` | `{code, user_id, room_id, status, ...}` | 600s → 1800s |
| `devicelink:rate:{identifier}` | Integer counter | 60s |
| `devicelink:active_codes` | SET of active codes | — |

---

## Security

- **Code expiration:** Codes auto-expire after 10 minutes via Redis TTL
- **One-time use:** Code is deleted from Redis immediately after linking
- **Rate limiting:** 5 code generations per IP per 60s; 10 link attempts per user per 60s
- **Brute-force protection:** With only 10 attempts allowed per minute against 900,000 possible codes, brute-force is infeasible
- **Collision avoidance:** Code generation retries up to 5 times on collision

---

## Files

### New Files Created

| File | Purpose |
|---|---|
| `api/utils/token.py` | Added `create_refresh_token()` and `verify_refresh_token()` functions |
| `api/utils/device_link_redis.py` | Redis manager for device linking (like `qr_redis.py`) |
| `api/schemas/device_link.py` | Pydantic request/response schemas |
| `api/routers/device_link.py` | FastAPI router with REST + WebSocket endpoints |
| `api/tests/test_device_link.py` | End-to-end test script |
| `DEVICE_LINKING.md` | This documentation file |

### Modified Files

| File | Change |
|---|---|
| `api/main.py` | Added `device_link` router import and registration |

### No Migration Files

No Django migration is needed. The system stores all ephemeral state in Redis with TTL-based auto-expiration, matching the existing QR pairing architecture (`QRRedisManager`). The `active_codes` SET is self-cleaning via lazy purge in `generate_code()` — when the SET grows beyond 100 members, orphaned entries (whose TTL-expired Redis keys no longer exist) are automatically removed. This avoids adding a Celery task dependency to the FastAPI service, which doesn't run Celery.

---

## Post-Link Room Connection

After the TV receives the `device_linked` event, it needs to transition from the one-time device linking WebSocket to the persistent room-based WebSocket used for content streaming. Here is the exact flow:

1. **TV receives `device_linked`** — The event includes `access_token`, `refresh_token`, `room_id`, and `ws_url`.

2. **TV stores tokens** — Save both `access_token` and `refresh_token` to local storage / secure preferences.

3. **TV disconnects from `/ws/device/{device_id}`** — The linking WebSocket is no longer needed.

4. **TV connects to the room WebSocket** — Use the `ws_url` from the event (e.g. `/ws/tv?room={room_id}&device_id={device_id}`). This is the same endpoint already used by the QR flow in `api/routers/qr_code_redis.py` (line 163: `@router.websocket("/ws/tv")`).

5. **Mobile connects to its room WebSocket** — The mobile app connects to `/ws/mobile?room={room_id}&token={access_token}` (same as the existing QR pairing flow).

6. **Normal room-based communication begins** — Both TV and mobile are in the same room, and the standard curation/content streaming WebSocket protocol takes over.

7. **Token refresh** — When the TV's `access_token` expires, it calls `POST /device/token/refresh` with the stored `refresh_token` to obtain a new `access_token`. The refresh token is long-lived (controlled by `JWT_REFRESH_TOKEN_EXPIRE_DAYS` env var), so the TV stays logged in for months without requiring a new 6-digit code.

---

## Testing

### Prerequisites

1. Redis must be running (check `REDIS_HOST`, `REDIS_PORT` in `.env`)
2. FastAPI server running on port 8080
3. A valid JWT token (obtain from Django auth service on port 8000)

### Get a JWT Token

```bash
curl -X POST http://localhost:8000/auth/login/ \
  -H "Content-Type: application/json" \
  -d '{"email": "your@email.com", "password": "yourpassword"}'
```

### Run the Full Test

```bash
cd api
python -m pytest tests/test_device_link.py -v
```

### Manual Testing with curl

**Step 1 — Generate code (TV):**
```bash
curl -X POST http://localhost:8080/device/code \
  -H "Content-Type: application/json" \
  -d '{"device_name": "Living Room TV"}'
```

**Step 2 — Link device (Mobile):**
```bash
curl -X POST "http://localhost:8080/device/link?token=YOUR_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"code": "XXXXXX"}'
```

**Step 3 — Check status (TV fallback):**
```bash
curl http://localhost:8080/device/DEVICE_ID/status
```

### WebSocket Testing with wscat

```bash
# Install wscat
npm install -g wscat

# Connect as TV
wscat -c ws://localhost:8080/ws/device/DEVICE_ID_HERE

# Send ping
{"type": "ping"}

# Send status check
{"type": "check_status"}
```

### Test with Docker

```bash
docker-compose up --build
# In another terminal:
python -m pytest api/tests/test_device_link.py -v
```

---

## Edge Cases Handled

| Scenario | Behavior |
|---|---|
| Code expires | Redis TTL deletes the code; TV gets `expired` status |
| WebSocket disconnects | Connection cleaned up; TV can still poll via REST |
| Invalid code entered | Returns 400 with descriptive error message |
| Code already used | Returns 400 "Code has already been used" |
| Rate limit exceeded | Returns 429 with retry message |
| Code collision | Auto-retries up to 5 times; returns 503 if all fail |
| TV reconnects after disconnect | Can reconnect to same device WS if code still valid |

