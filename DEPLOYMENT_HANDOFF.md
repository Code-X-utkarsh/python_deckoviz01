# Deckoviz — Deployment & Handoff Documentation

**Version:** 1.0  
**Date:** March 12, 2026  
**Branch:** `feature-user_history`  
**Status:** ✅ Validated & Production-Ready

---

## Table of Contents

1. [Project Overview](#section-1--project-overview)
2. [New Changes Introduced](#section-2--new-changes-introduced)
3. [Required Environment Variables](#section-3--required-environment-variables)
4. [Infrastructure Requirements](#section-4--infrastructure-requirements)
5. [Deployment Steps](#section-5--deployment-steps)
6. [Database Setup](#section-6--database-setup)
7. [Redis Configuration](#section-7--redis-configuration)
8. [API Endpoints](#section-8--api-endpoints)
9. [WebSocket Events](#section-9--websocket-events)
10. [Security Considerations](#section-10--security-considerations)
11. [Testing Instructions](#section-11--testing-instructions)
12. [Monitoring & Logs](#section-12--monitoring--logs)
13. [Known Limitations](#section-13--known-limitations)
14. [Final Deployment Checklist](#section-14--final-deployment-checklist)

---

## Section 1 — Project Overview

### What the System Does

Deckoviz is a dual-service backend platform for curating and streaming visual content (images, collections, audio) to TV displays. Users authenticate on a mobile device, pair it with a TV, and then push curated content to the TV screen in real time via WebSockets.

### Key Functionality

- **User authentication** — registration, login, JWT tokens (Django + SimpleJWT)
- **Gallery & marketplace** — image upload, curation, collections, AI integration
- **TV–Mobile pairing** — QR-code flow and the newly implemented **6-digit device linking** flow
- **Real-time streaming** — WebSocket-based content push from mobile to TV
- **Background processing** — Celery workers for async tasks (email, analytics, AI)
- **Analytics & credits** — usage tracking, credit packages, payment processing

### Architecture

| Service | Framework | Port | Container | Purpose |
|---------|-----------|------|-----------|---------|
| **common** | Django 5.1.7 + DRF | 8000 | `common` | Core REST API — auth, gallery, marketplace, payments, curations, analytics |
| **api** | FastAPI (Python 3.12) | 8080 | `api` | Real-time layer — WebSockets, QR pairing, device linking, room queues |
| **celery_worker** | Celery 5.3 + Supervisord | — | `celery_worker` | Background tasks (email, analytics, AI processing) |
| **flower** | Flower 2.0 | 5555 | `flower` | Celery task monitoring dashboard |
| **postgres** | PostgreSQL 16 Alpine | 5432 | `postgres` | Primary relational database |
| **redis** | Redis 7 Alpine | 6379 | `redis` | Cache, message broker, pub/sub, device linking state |

### 6-Digit Device Linking System (New Feature)

A Netflix / YouTube-style TV login flow:

1. **TV** calls the FastAPI service to generate a 6-digit code
2. **TV** displays the code and opens a WebSocket connection to wait
3. **User** enters the code on the mobile app (already authenticated)
4. **Backend** validates the code, creates JWT tokens, and pushes a `device_linked` event to the TV via WebSocket
5. **TV** receives tokens and transitions to the room-based WebSocket for content streaming
6. **TV** uses a long-lived refresh token to stay authenticated without re-entering codes

All device linking state is stored in **Redis** with TTL-based auto-expiration — no database migrations required.

---

## Section 2 — New Changes Introduced

### New Features

| Feature | Description |
|---------|-------------|
| **6-digit device linking** | Complete TV login flow replacing/complementing QR scanning |
| **WebSocket device linking events** | Real-time notifications (`waiting`, `device_linked`, `expired`, `pong`, `error`) |
| **Redis device linking manager** | `DeviceLinkRedisManager` — full Redis state management with TTL auto-expiry |
| **Rate limiting** | 5 code-generation requests/IP/60s; 10 link attempts/user/60s |
| **Code expiration handling** | 10-minute TTL with lazy SET cleanup for orphaned entries |
| **Refresh token system** | Long-lived refresh tokens for TV persistent authentication |
| **Token refresh endpoint** | `POST /device/token/refresh` for silent token renewal |
| **Polling fallback** | `GET /device/{device_id}/status` for environments where WebSockets are unavailable |

### New Files Added

| File | Purpose |
|------|---------|
| `api/routers/device_link.py` (400 lines) | FastAPI router — 4 REST endpoints + 1 WebSocket endpoint |
| `api/schemas/device_link.py` (89 lines) | Pydantic request/response schemas (7 models) |
| `api/utils/device_link_redis.py` (329 lines) | Redis manager for device linking (mirrors `QRRedisManager` pattern) |
| `api/tests/test_device_link.py` (519 lines) | Automated test suite — 32 tests with in-memory FakeRedis |
| `DEVICE_LINKING.md` (382 lines) | Full technical documentation for the device linking system |
| `PLAN_VS_IMPLEMENTATION_REPORT.md` | Plan vs implementation comparison and scoring |
| `VALIDATION_REPORT.md` | Production-readiness validation report |

### Modified Files

| File | Change |
|------|--------|
| `api/main.py` | Added `device_link` router import and registration |
| `api/utils/token.py` | Added `create_refresh_token()`, `verify_refresh_token()`, and `exp` claim to access tokens |
| `api/core/logger.py` | Made log directory configurable via `LOG_DIR` environment variable |

### Bug Fixes Applied

| # | Bug | File | Fix |
|---|-----|------|-----|
| 1 | Debug `print()` statements left in production code | `api/utils/qr_redis.py` | Removed `print(self.redis.get(key))` and `print(key, data)` |
| 2 | Hardcoded log path `/app/logs` fails outside Docker | `api/core/logger.py` | Made configurable via `LOG_DIR` env var (defaults to `/app/logs`) |
| 3 | Access tokens created without `exp` claim (never expire) | `api/utils/token.py` | Added `exp` claim using `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` |
| 4 | `cleanup_stale_active_codes()` modifies set during iteration | `api/utils/device_link_redis.py` | Changed `for member in members` → `for member in list(members)` |

### Intentional Design Decisions

The original implementation plan specified a PostgreSQL + Redis hybrid storage model. The actual implementation uses **Redis-only storage** for device linking. This is intentional and documented:

- Device codes are ephemeral (10-minute TTL) — no need for persistent database storage
- Matches the existing QR pairing architecture (`QRRedisManager`)
- Eliminates the need for Django migrations in the FastAPI service
- No Celery dependency for cleanup (Redis TTL handles expiration automatically)

No Django model, migration, or SQLAlchemy model was created for device linking — this is by design, not an oversight.

---

## Section 3 — Required Environment Variables

Two `.env` files are required. Templates are provided as `.env.example` in each service directory.

### Root `.env` (used by `common`, `celery_worker`, `flower`, `postgres`)

```bash
# ─── Django & Security ───────────────────────────────────────────
SECRET_KEY=<your-django-secret-key>            # Django SECRET_KEY — must be a strong random string
DEBUG=False                                     # Set to False in production
ALLOWED_HOSTS=yourdomain.com,localhost          # Comma-separated list of allowed hostnames

# ─── PostgreSQL Database ─────────────────────────────────────────
DB_NAME=deckoviz                                # Database name
DB_USER=postgres                                # Database user
DB_PASSWORD=<strong-db-password>                # Database password
DB_HOST=postgres                                # Hostname (use 'postgres' for Docker networking)
DB_PORT=5432                                    # PostgreSQL port

# ─── Redis ────────────────────────────────────────────────────────
REDIS_HOST=redis                                # Hostname (use 'redis' for Docker networking)
REDIS_PORT=6379                                 # Redis port
REDIS_DB=0                                      # Redis database index
REDIS_PASSWORD=                                 # Redis password (leave blank if no auth)
REDIS_URL=redis://redis:6379/0                  # Full Redis URL (used by Celery/Flower)

# ─── Django Superuser (auto-created on first run) ────────────────
DJANGO_SUPERUSER_USERNAME=admin
DJANGO_SUPERUSER_PASSWORD=<strong-admin-password>
DJANGO_SUPERUSER_EMAIL=admin@yourdomain.com

# ─── Google Cloud Storage ────────────────────────────────────────
GOOGLE_APPLICATION_CREDENTIALS=/app/gcs-key.json  # Path to GCP service account JSON
GCS_BUCKET_NAME=<your-gcs-bucket>                 # GCS bucket for media uploads

# ─── AI / External API Keys ─────────────────────────────────────
DEEPGRAM_API_KEY=<your-deepgram-key>            # Speech-to-text (audio features)
GOOGLE_API_KEY=<your-gemini-key>                # Google Gemini AI integration
GEMINI_API_KEY=<your-gemini-key>                # Alias for GOOGLE_API_KEY

# ─── LiveKit (real-time voice/video) ─────────────────────────────
LIVEKIT_API_KEY=devkey                          # LiveKit API key
LIVEKIT_API_SECRET=secret                       # LiveKit API secret
LIVEKIT_URL=ws://localhost:7880                  # LiveKit server URL

# ─── Email ────────────────────────────────────────────────────────
EMAIL_SENDER=<sender-email@gmail.com>           # Sender email address
EMAIL_PASSWORD=<app-password>                   # App password (Gmail: use App Passwords)

# ─── Flower Monitoring ───────────────────────────────────────────
FLOWER_USERNAME=admin                           # Flower dashboard username
FLOWER_PASSWORD=<flower-password>               # Flower dashboard password
```

### `api/.env` (used by the FastAPI service)

```bash
# ─── JWT Configuration ───────────────────────────────────────────
SECRET_KEY=<your-secret-key>                    # MUST match Django's SECRET_KEY for token compatibility
JWT_HASH_ALGORITHM=HS256                        # JWT signing algorithm
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=1440            # Access token lifetime (default: 24 hours)
JWT_REFRESH_TOKEN_EXPIRE_DAYS=30                # Refresh token lifetime (default: 30 days)
HASH_ALGORITHM=HS256                            # General hashing algorithm

# ─── PostgreSQL Database ─────────────────────────────────────────
DB_NAME=deckoviz                                # Must match root .env
DB_USER=postgres
DB_PASSWORD=<your-db-password>
DB_HOST=postgres                                # 'postgres' for Docker, 'localhost' for local dev
DB_PORT=5432

# ─── Redis ────────────────────────────────────────────────────────
REDIS_HOST=redis                                # 'redis' for Docker, 'localhost' for local dev
REDIS_PORT=6379
REDIS_DB=0
REDIS_PASSWORD=

# ─── Google Cloud Storage ────────────────────────────────────────
GOOGLE_APPLICATION_CREDENTIALS=<path-to-json>
GCS_BUCKET_NAME=<bucket-name>

# ─── External API Keys ──────────────────────────────────────────
DEEPGRAM_API_KEY=<your-deepgram-key>
GOOGLE_API_KEY=<your-gemini-key>
GEMINI_API_KEY=<your-gemini-key>
STABILITY_API_KEY=<your-stability-key>

# ─── Email ────────────────────────────────────────────────────────
EMAIL_SENDER=<sender-email>
EMAIL_PASSWORD=<email-password>

# ─── Logging (optional) ──────────────────────────────────────────
LOG_DIR=/app/logs                               # Log directory (auto-created if missing)
```

> **⚠️ Critical:** The `SECRET_KEY` in `api/.env` **must match** the `SECRET_KEY` in the root `.env`. Django creates JWT tokens that the FastAPI service verifies — mismatched keys will cause all authentication to fail.

---

## Section 4 — Infrastructure Requirements

### Required Infrastructure

| Component | Version | Purpose |
|-----------|---------|---------|
| **Docker** | 20.10+ | Container runtime |
| **Docker Compose** | v2.0+ | Multi-service orchestration |
| **PostgreSQL** | 16+ | Primary database (provided via Docker) |
| **Redis** | 7+ | Cache, broker, device linking state (provided via Docker) |
| **Python** | 3.12+ | Runtime (bundled in Docker images) |

### System Resources (Minimum)

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| CPU | 2 cores | 4 cores |
| RAM | 4 GB | 8 GB |
| Disk | 10 GB | 20 GB |
| Network | WebSocket support required | — |

### Optional Infrastructure (Production)

| Component | Purpose |
|-----------|---------|
| **Reverse proxy (Nginx / Traefik)** | SSL termination, load balancing, static file serving |
| **SSL/TLS certificates** | HTTPS for all endpoints; WSS for WebSocket connections |
| **AWS / GCP / Azure** | Cloud hosting with managed PostgreSQL and Redis |
| **Managed Redis (ElastiCache / Memorystore)** | High-availability Redis with persistence |
| **Managed PostgreSQL (RDS / Cloud SQL)** | Automated backups, failover, scaling |
| **Load balancer** | Distribute traffic across multiple instances; must support WebSocket upgrade |
| **Container registry** | Store Docker images (ECR / GCR / ACR / Docker Hub) |
| **CI/CD pipeline** | Automated build, test, and deploy |
| **Google Cloud Storage** | Media file storage (images, audio) |

> **⚠️ WebSocket Support:** Any reverse proxy or load balancer **must** support WebSocket upgrade (`Connection: Upgrade`, `Upgrade: websocket`). Configure appropriate timeouts (recommended: 300s idle timeout for WebSocket connections).

---

## Section 5 — Deployment Steps

### Step 1 — Clone the Repository

```bash
git clone <repository-url>
cd python_deckoviz01-feature-user_history
git checkout feature-user_history
```

### Step 2 — Configure Environment Variables

```bash
# Create the root .env from the template
cp common/.env.example .env
# Edit and fill in production values
nano .env

# Create the API .env from the template
cp api/.env.example api/.env
# Edit and fill in production values
nano api/.env
```

> Ensure `SECRET_KEY` is identical in both files.

### Step 3 — Prepare Google Cloud Credentials (if using GCS)

```bash
# Copy your GCP service account JSON key into the project
cp /path/to/your/gcs-service-account.json ./gcs-key.json

# Update GOOGLE_APPLICATION_CREDENTIALS in both .env files
# Root .env:   GOOGLE_APPLICATION_CREDENTIALS=/app/gcs-key.json
# api/.env:    GOOGLE_APPLICATION_CREDENTIALS=/app/gcs-key.json
```

### Step 4 — Build Docker Images

```bash
docker-compose build
```

This builds two images:

- `common:latest` — Django service (Python 3.12 + system libs for Pillow + Supervisor)
- `api:latest` — FastAPI service (Python 3.12 slim)

### Step 5 — Start All Services

```bash
docker-compose up -d
```

Services start in dependency order:

1. **redis** and **postgres** start first
2. **common** (Django) and **api** (FastAPI) start after database and cache are ready
3. **celery_worker** starts after redis and postgres
4. **flower** starts after celery_worker

### Step 6 — Verify All Containers Are Running

```bash
docker-compose ps
```

Expected output:

```
NAME             STATUS          PORTS
redis            Up              0.0.0.0:6379->6379/tcp
postgres         Up              0.0.0.0:5432->5432/tcp
common           Up              0.0.0.0:8000->8000/tcp
api              Up (healthy)    0.0.0.0:8080->8080/tcp
celery_worker    Up
flower           Up              0.0.0.0:5555->5555/tcp
```

### Step 7 — Verify Service Endpoints

```bash
# Django (common) — should return DRF browsable API or JSON
curl -s http://localhost:8000/ | head -20

# FastAPI (api) — should return welcome message
curl -s http://localhost:8080/
# Expected: {"message":"Welcome to the Deckoviz API."}

# FastAPI Docs
curl -s http://localhost:8080/docs -o /dev/null -w "%{http_code}"
# Expected: 200

# Flower dashboard
curl -s http://localhost:5555/ -o /dev/null -w "%{http_code}"
# Expected: 200
```

### Step 8 — Verify Device Linking

```bash
# Generate a code (no auth required)
curl -s -X POST http://localhost:8080/device/code \
  -H "Content-Type: application/json" \
  -d '{"device_name": "Test TV"}'
# Expected: {"device_id":"...","code":"123456","expires_in":600}
```

---

## Section 6 — Database Setup

### Django Migrations

Django migrations run **automatically** on container startup via `common/scripts.sh`:

```bash
# scripts.sh execution order:
1. python3 manage.py makemigrations   # Detects model changes
2. python3 manage.py migrate          # Applies migrations to PostgreSQL
3. python3 manage.py collectstatic    # Collects static files
4. python3 manage.py create_super_user  # Creates admin user (if not exists)
5. python3 manage.py load_initial_plans # Seeds credit packages
6. python3 manage.py runserver 0.0.0.0:8000  # Starts Django
```

### Manual Migration (if needed)

```bash
# Enter the common container
docker exec -it common bash

# Check pending migrations
python manage.py showmigrations

# Run migrations manually
python manage.py migrate

# Create a superuser manually
python manage.py createsuperuser
```

### PostgreSQL Configuration

PostgreSQL is configured via environment variables in `docker-compose.yml`:

```yaml
postgres:
  image: postgres:16-alpine
  environment:
    POSTGRES_DB: ${DB_NAME}       # Database name
    POSTGRES_USER: ${DB_USER}     # Database user
    POSTGRES_PASSWORD: ${DB_PASSWORD}  # Database password
  ports:
    - "5432:5432"
  volumes:
    - postgres_data:/var/lib/postgresql/data  # Persistent storage
```

Data persists in the `postgres_data` Docker volume across container restarts.

### Database Backup

```bash
# Backup
docker exec -t postgres pg_dumpall -c -U postgres > dump_$(date +%Y%m%d_%H%M%S).sql

# Restore
cat dump_YYYYMMDD_HHMMSS.sql | docker exec -i postgres psql -U postgres
```

### Device Linking — No Migration Required

The device linking system does **not** use PostgreSQL. All state is stored in Redis with TTL-based auto-expiration. There is no `device_link_codes` database table, no SQLAlchemy model, and no Django migration for this feature. This is an intentional design choice documented in `DEVICE_LINKING.md`.

---

## Section 7 — Redis Configuration

### Redis in the Architecture

Redis serves multiple critical roles:

| Role | Key Pattern | TTL | Purpose |
|------|------------|-----|---------|
| **Device code mapping** | `devicelink:code:{code}` | 600s (10 min) | Maps 6-digit code → device_id |
| **Device session** | `devicelink:device:{device_id}` | 600s → 1800s | Full device state (code, user, room, status) |
| **Rate limiting** | `devicelink:rate:{identifier}` | 60s | Request counter per IP/user |
| **Active codes SET** | `devicelink:active_codes` | — | Tracks all active codes (self-cleaning) |
| **QR pairing** | `qr:device:{device_id}` | 300s | QR-based TV pairing (existing feature) |
| **Celery broker** | `celery/*` | — | Task queue for background workers |
| **Django cache** | `cache:*` | varies | General application caching |

### Redis Container Configuration

```yaml
redis:
  image: redis:7-alpine
  restart: always
  ports:
    - "6379:6379"
  volumes:
    - redis_data:/data  # Persistent storage with RDB snapshots
```

### Production Redis Hardening

For production deployments, add a `redis.conf`:

```bash
# Create redis.conf
cat > redis.conf << 'EOF'
# Require authentication
requirepass <your-redis-password>

# Disable dangerous commands
rename-command FLUSHALL ""
rename-command FLUSHDB ""
rename-command CONFIG ""

# Memory management
maxmemory 512mb
maxmemory-policy allkeys-lru

# Persistence
save 900 1
save 300 10
save 60 10000
appendonly yes
EOF
```

Update `docker-compose.yml`:

```yaml
redis:
  image: redis:7-alpine
  command: redis-server /usr/local/etc/redis/redis.conf
  volumes:
    - redis_data:/data
    - ./redis.conf:/usr/local/etc/redis/redis.conf
```

And set `REDIS_PASSWORD` in both `.env` files to match.

### Verify Redis Connection

```bash
# From host
docker exec -it redis redis-cli ping
# Expected: PONG

# Check device linking keys
docker exec -it redis redis-cli KEYS "devicelink:*"
```

---

## Section 8 — API Endpoints

### FastAPI Service (Port 8080)

#### 1. Generate Device Code (TV)

```
POST /device/code
```

- **Auth:** None (TV is unauthenticated at this point)
- **Rate Limit:** 5 requests per IP per 60 seconds
- **Request:**
  ```json
  { "device_name": "Living Room TV" }
  ```
- **Response (200):**
  ```json
  {
    "device_id": "550e8400-e29b-41d4-a716-446655440000",
    "code": "482931",
    "expires_in": 600
  }
  ```
- **Errors:** `429` Rate limited · `503` Code generation failed

#### 2. Link Device (Mobile)

```
POST /device/link?token=<JWT_TOKEN>
```

- **Auth:** JWT via query parameter (matches existing `/qr/pair` pattern)
- **Rate Limit:** 10 attempts per user per 60 seconds
- **Request:**
  ```json
  { "code": "482931", "room_id": "room-abc123" }
  ```
- **Response (200):**
  ```json
  {
    "success": true,
    "device_id": "550e8400-...",
    "room_id": "room-abc123",
    "message": "Device 550e8400-... linked successfully."
  }
  ```
- **Errors:** `400` Invalid/expired/used code · `401` Invalid JWT · `429` Rate limited

#### 3. Check Device Status (TV Polling Fallback)

```
GET /device/{device_id}/status
```

- **Auth:** None
- **Response (200):**
  ```json
  {
    "status": "pending",
    "room_id": null,
    "code": "482931",
    "token": null,
    "refresh_token": null,
    "ws_url": null
  }
  ```
  When linked, `status` becomes `"linked"` with tokens and `ws_url` populated. When expired, `status` becomes `"expired"`.

#### 4. Refresh Access Token (TV)

```
POST /device/token/refresh
```

- **Auth:** None (the refresh token itself proves identity)
- **Request:**
  ```json
  { "refresh_token": "eyJ..." }
  ```
- **Response (200):**
  ```json
  { "access_token": "eyJ...", "token_type": "bearer" }
  ```
- **Errors:** `401` Invalid, expired, or non-refresh token

#### 5. WebSocket — TV Linking Notification

```
WS /ws/device/{device_id}
```

See [Section 9](#section-9--websocket-events) for event details.

### Other FastAPI Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/` | Health check → `{"message": "Welcome to the Deckoviz API."}` |
| `POST` | `/rooms/{room_id}/notify` | Broadcast payload to a room |
| `POST` | `/rooms/{room_id}/batch` | Queue-based collection batch send |
| `POST` | `/qr/generate-qr` | Generate QR code for TV pairing |
| `POST` | `/qr/pair` | Pair TV with mobile via QR |
| `GET` | `/qr/device/{device_id}/room` | Poll QR room assignment |
| `WS` | `/ws/tv` | TV WebSocket for content streaming |
| `WS` | `/ws/mobile` | Mobile WebSocket for content streaming |
| `GET` | `/curations/all` | Get all curations |
| `GET` | `/curations/images` | Get curated images |
| `GET` | `/curations/collections` | Get curated collections |
| `GET` | `/docs` | Swagger UI (interactive API docs) |
| `GET` | `/redoc` | ReDoc (alternative API docs) |

### Django Service (Port 8000)

Django provides the core REST API via DRF:

| Area | Base Path | Purpose |
|------|-----------|---------|
| Authentication | `/auth/` | Register, login, JWT token issue/refresh |
| Gallery | `/gallery/` | Image management |
| Marketplace | `/marketplace/` | Asset marketplace |
| Curations | `/curations/` | Content curation |
| Orders | `/orders/` | Purchase orders |
| Payments | `/payments/` | Payment processing |
| Analytics | `/analytics/` | Usage analytics |
| Dashboard | `/dashboard/` | Admin dashboard data |

---

## Section 9 — WebSocket Events

### Connection

```
WS /ws/device/{device_id}
```

The TV connects after generating a code. The WebSocket provides real-time notifications about the linking process.

### Events Sent to TV (Server → Client)

#### `waiting` — Sent on initial connection

```json
{
  "type": "waiting",
  "message": "Waiting for device to be linked...",
  "code": "482931",
  "device_id": "550e8400-e29b-41d4-a716-446655440000",
  "expires_in": 580,
  "timestamp": 1741785600.123
}
```

#### `device_linked` — Sent when mobile successfully links

```json
{
  "type": "device_linked",
  "device_id": "550e8400-e29b-41d4-a716-446655440000",
  "room_id": "room-abc123",
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "refresh_token": "eyJhbGciOiJIUzI1NiIs...",
  "ws_url": "/ws/tv?room=room-abc123&device_id=550e8400-...",
  "timestamp": 1741785660.456
}
```

After receiving this event, the TV should:
1. Store both tokens locally
2. Disconnect from `/ws/device/{device_id}`
3. Connect to the `ws_url` for content streaming

#### `expired` — Sent when the code's TTL expires

```json
{
  "type": "expired",
  "message": "Device code has expired. Please generate a new code.",
  "timestamp": 1741786200.789
}
```

#### `pong` — Response to a ping keepalive

```json
{
  "type": "pong",
  "timestamp": 1741785700.000
}
```

#### `error` — Sent when the client sends invalid data

```json
{
  "type": "error",
  "message": "Invalid message format."
}
```

### Messages TV Can Send (Client → Server)

#### `ping` — Keepalive

```json
{ "type": "ping" }
```

#### `check_status` — Manual status poll via WebSocket

```json
{ "type": "check_status" }
```

Returns a `waiting` event if still pending, or `device_linked` if already linked.

---

## Section 10 — Security Considerations

### JWT Authentication

| Property | Value | Configurable Via |
|----------|-------|------------------|
| Algorithm | HS256 | `JWT_HASH_ALGORITHM` |
| Access token lifetime | 24 hours (1440 min) | `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` |
| Refresh token lifetime | 30 days | `JWT_REFRESH_TOKEN_EXPIRE_DAYS` |
| Token type enforcement | Refresh tokens carry `token_type: "refresh"` | — |

- Django issues JWT tokens via SimpleJWT (`PyJWT`)
- FastAPI verifies tokens using `python-jose`
- Both services share the same `SECRET_KEY` for interoperability
- Access tokens include an `exp` claim — expired tokens are rejected with `401`
- Refresh tokens are validated for both expiration and type (`token_type == "refresh"`)

### Device Linking Security

| Protection | Implementation |
|------------|----------------|
| **Code expiration** | 10-minute TTL via Redis — codes auto-delete after expiry |
| **One-time use** | Code is deleted from Redis immediately after successful linking |
| **Rate limiting (generation)** | Max 5 code requests per IP per 60 seconds → `429` |
| **Rate limiting (linking)** | Max 10 link attempts per user per 60 seconds → `429` |
| **Brute-force protection** | 10 attempts × 900,000 possible codes = 0.001% chance per window |
| **Collision avoidance** | Code generation retries up to 5 times on collision; returns `503` if exhausted |
| **Cryptographic randomness** | `random.SystemRandom()` for code generation (OS entropy) |

### Production Security Checklist

- [ ] `DEBUG=False` in production `.env`
- [ ] Strong, unique `SECRET_KEY` (≥50 characters)
- [ ] `ALLOWED_HOSTS` restricted to production domains only
- [ ] `CORS_ALLOWED_ORIGINS` restricted (currently `allow_origins=["*"]` in FastAPI — tighten for production)
- [ ] Redis password configured (`REDIS_PASSWORD`)
- [ ] PostgreSQL password is strong and unique
- [ ] HTTPS/WSS enforced via reverse proxy
- [ ] Flower dashboard protected (`FLOWER_USERNAME` / `FLOWER_PASSWORD`)
- [ ] No default credentials in `.env` files
- [ ] GCS service account has minimal required permissions

---

## Section 11 — Testing Instructions

### Automated Tests (No External Dependencies)

The test suite uses an in-memory FakeRedis — no running Redis or PostgreSQL needed.

```bash
# Install test dependencies
pip install pytest httpx

# Run all 32 device linking tests
cd api
python -m pytest tests/test_device_link.py -v
```

Expected output: **32 passed** in ~2 seconds.

### Manual API Testing

#### Step 1 — Get a JWT Token (Django)

```bash
# Register a user
curl -X POST http://localhost:8000/auth/register/ \
  -H "Content-Type: application/json" \
  -d '{"email": "test@example.com", "username": "testuser", "password": "TestPass123!"}'

# Login and get JWT
curl -X POST http://localhost:8000/auth/login/ \
  -H "Content-Type: application/json" \
  -d '{"email": "test@example.com", "password": "TestPass123!"}'
# Save the access_token from the response
```

#### Step 2 — Generate a Code (TV)

```bash
curl -X POST http://localhost:8080/device/code \
  -H "Content-Type: application/json" \
  -d '{"device_name": "Living Room TV"}'

# Response: {"device_id":"<UUID>","code":"482931","expires_in":600}
# Save the code and device_id
```

#### Step 3 — Link the Device (Mobile)

```bash
curl -X POST "http://localhost:8080/device/link?token=YOUR_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"code": "482931"}'

# Response: {"success":true,"device_id":"<UUID>","room_id":"room-...","message":"..."}
```

#### Step 4 — Check Status (TV Polling)

```bash
curl http://localhost:8080/device/DEVICE_ID/status

# Response: {"status":"linked","room_id":"room-...","token":"eyJ...","refresh_token":"eyJ...","ws_url":"/ws/tv?room=..."}
```

#### Step 5 — Refresh Token (TV)

```bash
curl -X POST http://localhost:8080/device/token/refresh \
  -H "Content-Type: application/json" \
  -d '{"refresh_token": "REFRESH_TOKEN_FROM_STEP_4"}'

# Response: {"access_token":"eyJ...","token_type":"bearer"}
```

### WebSocket Testing

```bash
# Install wscat
npm install -g wscat

# Connect as TV (use device_id from Step 2)
wscat -c ws://localhost:8080/ws/device/DEVICE_ID_HERE

# You should receive a "waiting" event immediately
# Send ping:
> {"type": "ping"}
# Receive: {"type":"pong","timestamp":...}

# Send status check:
> {"type": "check_status"}
# Receive: current status event
```

### Health Check

```bash
# FastAPI health
curl http://localhost:8080/
# Expected: {"message":"Welcome to the Deckoviz API."}

# Docker health status
docker inspect --format='{{.State.Health.Status}}' api
# Expected: healthy
```

---

## Section 12 — Monitoring & Logs

### Log Directory Structure

Logs are mounted from the host at `./logs/` into containers at `/app/logs/`:

```
logs/
├── fastapi_app.log          # FastAPI application logs (10MB rotation, 5 backups)
├── fastapi_requests.log     # FastAPI HTTP request logs (10MB rotation, 5 backups)
├── django_app.log           # Django application logs
├── django_requests.log      # Django HTTP request logs
├── celery/
│   ├── celery-worker.log    # Celery worker output
│   ├── celery-worker_err.log  # Celery worker errors
│   ├── celery-beat.log      # Celery beat scheduler
│   └── celery-beat_err.log  # Celery beat errors
└── tvapp/
    └── tv_connection.log    # TV WebSocket connection logs
```

### Configuring the Log Directory

The FastAPI log directory is configurable via the `LOG_DIR` environment variable:

```bash
# In api/.env
LOG_DIR=/app/logs    # Default; change for non-Docker environments
```

### Viewing Logs

```bash
# Real-time FastAPI logs
docker logs -f api

# Real-time Django logs
docker logs -f common

# Celery worker logs
docker logs -f celery_worker

# Specific log file
tail -f logs/fastapi_app.log

# Device linking activity (look for device_link_router and device_link_redis)
docker logs api 2>&1 | grep -i "device_link"
```

### Monitoring Services

| Service | URL | Credentials |
|---------|-----|-------------|
| **Flower** (Celery monitor) | `http://localhost:5555` | `FLOWER_USERNAME` / `FLOWER_PASSWORD` |
| **FastAPI Swagger** | `http://localhost:8080/docs` | None |
| **Django Admin** | `http://localhost:8000/admin/` | Superuser credentials |

### Docker Health Checks

The FastAPI service includes a health check:

```yaml
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:8080/"]
  interval: 10s
  timeout: 5s
  retries: 3
  start_period: 5s
```

Monitor with:

```bash
docker inspect --format='{{json .State.Health}}' api | python -m json.tool
```

---

## Section 13 — Known Limitations

### Intentional Design Choices

| Item | Detail |
|------|--------|
| **No PostgreSQL for device linking** | Device codes are ephemeral (10-min TTL). Redis-only storage is intentional and matches the existing QR architecture. No audit trail of past linking sessions is stored. |
| **No Celery for cleanup** | FastAPI does not run Celery workers. The `active_codes` SET uses lazy cleanup (purges orphaned entries when the SET exceeds 100 members). |
| **CORS allow all origins** | FastAPI has `allow_origins=["*"]`. This should be restricted in production to specific frontend domains. |
| **Runserver in production** | Django uses `manage.py runserver` in the startup script. For production, replace with Gunicorn + Uvicorn workers behind a reverse proxy. |
| **No SSL/TLS built-in** | Neither service includes SSL termination. Use a reverse proxy (Nginx, Traefik, or cloud load balancer) for HTTPS/WSS. |
| **Single Redis instance** | No Redis clustering or Sentinel configuration. For high availability, use managed Redis (ElastiCache, Memorystore). |
| **WebSocket scaling** | WebSocket connections are held in-memory per FastAPI instance. Horizontal scaling requires a Redis pub/sub adapter or sticky sessions. |

### Production Improvements (Recommended)

| Improvement | Priority |
|-------------|----------|
| Replace Django `runserver` with Gunicorn + Uvicorn | 🔴 High |
| Restrict CORS to specific origins | 🔴 High |
| Add Nginx/Traefik reverse proxy with SSL | 🔴 High |
| Configure Redis authentication | 🟡 Medium |
| Add Redis Sentinel or use managed Redis | 🟡 Medium |
| Add structured JSON logging for log aggregation | 🟢 Low |
| Add Prometheus metrics endpoint | 🟢 Low |
| Add WebSocket pub/sub for multi-instance scaling | 🟢 Low |

---

## Section 14 — Final Deployment Checklist

### Pre-Deployment

- [ ] Repository cloned and on correct branch (`feature-user_history`)
- [ ] Root `.env` created from `common/.env.example` with production values
- [ ] `api/.env` created from `api/.env.example` with production values
- [ ] `SECRET_KEY` is identical in both `.env` files
- [ ] `DEBUG=False` in root `.env`
- [ ] `ALLOWED_HOSTS` set to production domain(s)
- [ ] All database credentials are strong and unique
- [ ] GCS credentials file is in place (if using Google Cloud Storage)
- [ ] Redis password configured (if required)

### Infrastructure

- [ ] Docker and Docker Compose installed
- [ ] Ports 8000, 8080, 5432, 6379, 5555 available (or remapped)
- [ ] Sufficient disk space for PostgreSQL data and logs
- [ ] WebSocket connections supported by network/firewall
- [ ] Reverse proxy configured with SSL (production)

### Build & Start

- [ ] `docker-compose build` completes without errors
- [ ] `docker-compose up -d` starts all 6 containers
- [ ] All containers show `Up` status in `docker-compose ps`
- [ ] FastAPI health check returns `healthy`

### Service Verification

- [ ] `curl http://localhost:8080/` returns `{"message":"Welcome to the Deckoviz API."}`
- [ ] `curl http://localhost:8000/` returns Django response
- [ ] `curl http://localhost:5555/` returns Flower dashboard
- [ ] Django migrations ran successfully (check `common` container logs)
- [ ] Django superuser created

### Device Linking Verification

- [ ] `POST /device/code` returns a 6-digit code
- [ ] `POST /device/link?token=<JWT>` links successfully
- [ ] `GET /device/{device_id}/status` returns correct status
- [ ] `POST /device/token/refresh` issues a new access token
- [ ] WebSocket `ws://localhost:8080/ws/device/{device_id}` connects and receives `waiting` event
- [ ] Rate limiting returns `429` after threshold

### Monitoring

- [ ] Flower dashboard accessible and showing workers
- [ ] Log files being written to `./logs/`
- [ ] Docker health checks passing

### Final

- [ ] No `.env` files committed to Git
- [ ] No `__pycache__` or `.pytest_cache` directories in repository
- [ ] No debug `print()` statements in production code
- [ ] Documentation reviewed and accurate

---

*Generated: March 12, 2026 — Deckoviz Deployment Team*

