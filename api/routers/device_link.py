"""
Device-linking router — 6-digit code flow for TV ↔ Mobile pairing.

Endpoints:
  POST /device/code              – TV requests a 6-digit code  (unauthenticated)
  POST /device/link              – Mobile links the code to a user (authenticated)
  GET  /device/{device_id}/status – TV polls linking status  (unauthenticated)
  POST /device/token/refresh     – TV refreshes its access token (unauthenticated)
  WS   /ws/device/{device_id}   – TV real-time linking notification
"""

import time
import uuid
import logging
from typing import Dict

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)

from schemas.device_link import (
    RefreshTokenRequest,
    RefreshTokenResponse,
    GenerateCodeRequest,
    GenerateCodeResponse,
    LinkDeviceRequest,
    LinkDeviceResponse,
    DeviceStatusResponse,
)
from utils.token import (
    get_current_user,
    create_access_token,
    create_refresh_token,
    verify_refresh_token,
)
from utils.device_link_redis import DeviceLinkRedisManager
from utils.qr_redis import QRRedisManager
from databases.configs import get_redis_client
from utils.websocket_manager import manager
from utils.json_helpers import safe_parse_json

# ── Logging ──────────────────────────────────────────────────────────

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("device_link_router")

# ── Router ───────────────────────────────────────────────────────────

router = APIRouter(tags=["device-link"])

# ── Dependencies ─────────────────────────────────────────────────────

def get_device_link_manager() -> DeviceLinkRedisManager:
    """Dependency that returns a DeviceLinkRedisManager instance."""
    redis_client = get_redis_client()
    return DeviceLinkRedisManager(redis_client)


def get_qr_redis_manager() -> QRRedisManager:
    """Reuse existing QRRedisManager for room tracking."""
    redis_client = get_redis_client()
    return QRRedisManager(redis_client)


# ── WebSocket-to-device mapping (in-memory, same pattern as qr_code_redis.py) ──

device_ws_connections: Dict[str, WebSocket] = {}

# ── REST endpoints ───────────────────────────────────────────────────


@router.post("/device/code", response_model=GenerateCodeResponse)
async def generate_device_code(
    request_body: GenerateCodeRequest,
    request: Request,
    link_manager: DeviceLinkRedisManager = Depends(get_device_link_manager),
):
    """
    TV calls this endpoint to obtain a 6-digit linking code.
    The TV displays this code on screen so the mobile user can enter it.
    """
    # Rate-limit by client IP
    client_ip = request.client.host if request.client else "unknown"
    if not link_manager.check_rate_limit(
        f"gen:{client_ip}",
        link_manager.MAX_CODE_GENERATION_ATTEMPTS,
    ):
        raise HTTPException(
            status_code=429,
            detail="Too many code generation requests. Please wait and try again.",
        )

    # Generate a device ID
    device_id = str(uuid.uuid4())

    # Generate a 6-digit code
    try:
        code = link_manager.generate_code(
            device_id=device_id,
            device_name=request_body.device_name,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    logger.info(f"Generated device code {code} for device {device_id}")

    return GenerateCodeResponse(
        device_id=device_id,
        code=code,
        expires_in=link_manager.CODE_EXPIRY,
    )


@router.post("/device/link", response_model=LinkDeviceResponse)
async def link_device(
    request_body: LinkDeviceRequest,
    request: Request,
    user_id: str = Depends(get_current_user),
    link_manager: DeviceLinkRedisManager = Depends(get_device_link_manager),
    qr_manager: QRRedisManager = Depends(get_qr_redis_manager),
):
    """
    Authenticated mobile user calls this endpoint with the 6-digit code
    displayed on the TV.  Backend links the device to the user's account
    and pushes a WebSocket event to the TV.
    """
    # Rate-limit by user identity
    user_key = str(user_id) if isinstance(user_id, dict) else user_id
    if not link_manager.check_rate_limit(
        f"link:{user_key}",
        link_manager.MAX_LINK_ATTEMPTS,
    ):
        raise HTTPException(
            status_code=429,
            detail="Too many link attempts. Please wait and try again.",
        )

    # Link the device
    try:
        result = link_manager.link_device(
            code=request_body.code,
            user_id=user_key,
            room_id=request_body.room_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    device_id = result["device_id"]
    room_id = result["room_id"]

    # Register the room in QRRedisManager so the standard /ws/tv flow works
    qr_manager.add_room(room_id)
    qr_manager.store_room_metadata(room_id, {
        "created_at": time.time(),
        "creator_user_id": user_key,
        "device_id": device_id,
        "last_activity": time.time(),
        "connection_count": 0,
        "linked_via": "device_code",
    })

    # Create a JWT access token for the TV device
    token_payload = user_id if isinstance(user_id, dict) else {"sub": str(user_id)}
    access_token = create_access_token(token_payload)
    refresh_token = create_refresh_token(token_payload)

    # Build the ws_url so the TV knows where to connect after linking
    ws_url = f"/ws/tv?room={room_id}&device_id={device_id}"

    # ── Push WebSocket event to the TV ───────────────────────────────
    ws_room_key = f"devicelink:{device_id}"
    linked_event = {
        "type": "device_linked",
        "device_id": device_id,
        "room_id": room_id,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "ws_url": ws_url,
        "timestamp": time.time(),
    }
    await manager.broadcast(ws_room_key, linked_event)

    logger.info(
        f"Device {device_id} linked to user {user_key} in room {room_id}"
    )

    return LinkDeviceResponse(
        success=True,
        device_id=device_id,
        room_id=room_id,
        message=f"Device {device_id} linked successfully.",
    )


@router.get("/device/{device_id}/status", response_model=DeviceStatusResponse)
async def get_device_status(
    device_id: str,
    link_manager: DeviceLinkRedisManager = Depends(get_device_link_manager),
):
    """
    TV polls this endpoint as a fallback to check whether the device has
    been linked (in case the WebSocket connection is interrupted).
    """
    device_data = link_manager.get_device_status(device_id)

    if not device_data:
        return DeviceStatusResponse(status="expired")

    current_status = device_data.get("status", "pending")

    if current_status == "linked":
        # Build a token for the TV
        room_id = device_data.get("room_id")
        user_id = device_data.get("user_id")
        token_payload = (
            user_id if isinstance(user_id, dict) else {"sub": str(user_id)}
        )
        access_token = create_access_token(token_payload)
        refresh_token = create_refresh_token(token_payload)
        ws_url = f"/ws/tv?room={room_id}&device_id={device_id}" if room_id else None

        return DeviceStatusResponse(
            status="linked",
            room_id=room_id,
            token=access_token,
            refresh_token=refresh_token,
            ws_url=ws_url,
        )

    return DeviceStatusResponse(
        status=current_status,
        code=device_data.get("code"),
    )


@router.post("/device/token/refresh", response_model=RefreshTokenResponse)
async def refresh_device_token(
    request_body: RefreshTokenRequest,
):
    """
    TV calls this endpoint with its refresh token to obtain a fresh
    access token without requiring the user to re-enter a 6-digit code.
    """
    # Verify the refresh token (raises 401 on failure)
    payload = verify_refresh_token(request_body.refresh_token)

    # Build a new access token from the same claims (excluding refresh metadata)
    token_payload = {k: v for k, v in payload.items() if k not in ("token_type", "exp", "iat")}
    access_token = create_access_token(token_payload)

    logger.info(f"Refreshed access token for sub={payload.get('sub', 'unknown')}")

    return RefreshTokenResponse(
        access_token=access_token,
        token_type="bearer",
    )


# ── WebSocket endpoint ───────────────────────────────────────────────


@router.websocket("/ws/device/{device_id}")
async def websocket_device_endpoint(websocket: WebSocket, device_id: str):
    """
    WebSocket endpoint for TV devices waiting for a linking event.

    Flow:
      1. TV connects after calling POST /device/code.
      2. Server sends a 'waiting' event with the code.
      3. When POST /device/link is called, server broadcasts a
         'device_linked' event with access_token + room_id.
      4. TV disconnects and reconnects to /ws/tv?room={room_id}.
    """
    # Get Redis manager
    redis_client = get_redis_client()
    link_manager = DeviceLinkRedisManager(redis_client)

    # Verify the device exists
    device_data = link_manager.get_device_status(device_id)
    if not device_data:
        await websocket.accept()
        await websocket.send_json({
            "type": "error",
            "message": "Invalid or expired device ID.",
        })
        await websocket.close()
        return

    # Accept the connection
    await websocket.accept()

    # Register in ConnectionManager with room key = devicelink:{device_id}
    ws_room_key = f"devicelink:{device_id}"
    await manager.connect(websocket, ws_room_key, is_accepted=True)

    # Store direct reference for fast lookup
    device_ws_connections[device_id] = websocket

    try:
        # Send initial waiting event
        code = device_data.get("code", "")
        ttl = link_manager.redis.ttl(f"{link_manager.CODE_PREFIX}{code}")
        expires_in = max(ttl, 0) if ttl and ttl > 0 else link_manager.CODE_EXPIRY

        await manager.send_to_client(websocket, {
            "type": "waiting",
            "message": "Waiting for device to be linked. Enter the code on your mobile app.",
            "code": code,
            "device_id": device_id,
            "expires_in": expires_in,
            "timestamp": time.time(),
        })

        logger.debug(f"TV device {device_id} connected to WS, waiting for link")

        # Keep connection alive and listen for messages
        while True:
            raw_data = await websocket.receive_text()
            data = safe_parse_json(raw_data)

            if data is None:
                await websocket.send_json({
                    "type": "error",
                    "message": "Invalid JSON format.",
                })
                continue

            # Handle ping/pong keepalive
            if isinstance(data, dict) and data.get("type") == "ping":
                await websocket.send_json({
                    "type": "pong",
                    "timestamp": time.time(),
                })
                continue

            # Handle status check request
            if isinstance(data, dict) and data.get("type") == "check_status":
                current_data = link_manager.get_device_status(device_id)
                if current_data and current_data.get("status") == "linked":
                    user_id = current_data.get("user_id")
                    token_payload = (
                        user_id
                        if isinstance(user_id, dict)
                        else {"sub": str(user_id)}
                    )
                    access_token = create_access_token(token_payload)
                    refresh_token = create_refresh_token(token_payload)
                    linked_room_id = current_data.get("room_id")
                    ws_url = (
                        f"/ws/tv?room={linked_room_id}&device_id={device_id}"
                        if linked_room_id
                        else None
                    )
                    await manager.send_to_client(websocket, {
                        "type": "device_linked",
                        "device_id": device_id,
                        "room_id": linked_room_id,
                        "access_token": access_token,
                        "refresh_token": refresh_token,
                        "ws_url": ws_url,
                        "timestamp": time.time(),
                    })
                elif not current_data:
                    await manager.send_to_client(websocket, {
                        "type": "expired",
                        "message": "Device code has expired. Please request a new code.",
                        "timestamp": time.time(),
                    })
                else:
                    # Still pending – send remaining TTL
                    code = current_data.get("code", "")
                    ttl = link_manager.redis.ttl(
                        f"{link_manager.CODE_PREFIX}{code}"
                    )
                    await manager.send_to_client(websocket, {
                        "type": "waiting",
                        "code": code,
                        "expires_in": max(ttl, 0) if ttl and ttl > 0 else 0,
                        "timestamp": time.time(),
                    })
                continue

            # Echo back unrecognised messages
            logger.debug(
                f"Received unknown message from device {device_id}: {data}"
            )

    except WebSocketDisconnect:
        logger.debug(f"TV device {device_id} disconnected from linking WS")
    finally:
        # Clean up
        manager.disconnect(websocket, ws_room_key)
        device_ws_connections.pop(device_id, None)

