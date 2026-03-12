"""
Pydantic schemas for the 6-digit device linking system.
Follows the same pattern as schemas/qr_code.py.
"""

from pydantic import BaseModel, Field
from typing import Optional


# ── Request schemas ──────────────────────────────────────────────────

class GenerateCodeRequest(BaseModel):
    """Request body for POST /device/code (called by TV)."""
    device_name: Optional[str] = Field(
        None,
        description="Optional human-readable name for the TV device",
    )


class LinkDeviceRequest(BaseModel):
    """Request body for POST /device/link (called by authenticated mobile user)."""
    code: str = Field(
        ...,
        min_length=6,
        max_length=6,
        pattern=r"^\d{6}$",
        description="6-digit numeric code displayed on the TV",
    )
    room_id: Optional[str] = Field(
        None,
        description="Optional room ID; auto-generated if omitted",
    )


class RefreshTokenRequest(BaseModel):
    """Request body for POST /device/token/refresh (called by TV to renew access)."""
    refresh_token: str = Field(
        ...,
        description="Refresh token previously issued to the TV device",
    )


# ── Response schemas ─────────────────────────────────────────────────

class GenerateCodeResponse(BaseModel):
    """Response for POST /device/code."""
    device_id: str
    code: str
    expires_in: int = Field(
        description="Seconds until the code expires",
    )


class LinkDeviceResponse(BaseModel):
    """Response for POST /device/link."""
    success: bool
    device_id: str
    room_id: str
    message: str


class RefreshTokenResponse(BaseModel):
    """Response for POST /device/token/refresh."""
    access_token: str = Field(
        description="Newly issued short-lived access token",
    )
    token_type: str = Field(
        default="bearer",
        description="Token type (always 'bearer')",
    )


class DeviceStatusResponse(BaseModel):
    """Response for GET /device/{device_id}/status."""
    status: str = Field(
        description="pending | linked | expired",
    )
    room_id: Optional[str] = None
    code: Optional[str] = None
    token: Optional[str] = None
    refresh_token: Optional[str] = Field(
        None,
        description="Long-lived refresh token for the TV (only when linked)",
    )
    ws_url: Optional[str] = Field(
        None,
        description="WebSocket URL for room-based communication after linking",
    )
