"""
Redis utilities for 6-digit device linking functionality.
Follows the same pattern as QRRedisManager in qr_redis.py.

Redis key structure (replaces a traditional database table):
- devicelink:code:{code}       → JSON {device_id, created_at, status}         TTL 600s
- devicelink:device:{device_id} → JSON {code, user_id, room_id, status, ...}  TTL 600s→1800s
- devicelink:rate:{identifier}  → integer counter                             TTL 60s
- devicelink:active_codes       → Redis SET of currently active codes
"""

import json
import time
import random
import uuid
import logging
from typing import Dict, Any, Optional, Set
from redis import Redis

logger = logging.getLogger("device_link_redis")


class DeviceLinkRedisManager:
    """Redis manager for 6-digit device linking functionality"""

    # Redis key prefixes
    CODE_PREFIX = "devicelink:code:"
    DEVICE_PREFIX = "devicelink:device:"
    RATE_PREFIX = "devicelink:rate:"
    ACTIVE_CODES_KEY = "devicelink:active_codes"

    # Expiration times (seconds)
    CODE_EXPIRY = 60 * 10        # 10 minutes for codes
    DEVICE_EXPIRY = 60 * 10      # 10 minutes for pending devices
    LINKED_DEVICE_EXPIRY = 60 * 30  # 30 minutes for linked devices
    RATE_LIMIT_WINDOW = 60       # 60-second rate limit window

    # Rate limits
    MAX_CODE_GENERATION_ATTEMPTS = 5   # per IP per window
    MAX_LINK_ATTEMPTS = 10             # per user per window

    # Code generation
    MAX_CODE_RETRIES = 5  # retries on collision

    # Lazy cleanup threshold — purge orphaned SET entries when the
    # active_codes SET grows beyond this size.
    ACTIVE_CODES_CLEANUP_THRESHOLD = 100

    def __init__(self, redis_client: Redis):
        """
        Initialize with a Redis client.

        Args:
            redis_client: Redis client instance
        """
        self.redis = redis_client

    # ------------------------------------------------------------------
    # Code generation
    # ------------------------------------------------------------------

    def generate_code(self, device_id: str, device_name: str = None) -> str:
        """
        Generate a unique 6-digit code for a device.

        Args:
            device_id: UUID string identifying the TV device
            device_name: Optional human-readable device name

        Returns:
            6-digit string code

        Raises:
            RuntimeError: If a unique code cannot be generated after retries
        """
        rng = random.SystemRandom()
        # Lazy cleanup: purge orphaned entries in the active_codes SET
        # when it exceeds the threshold.
        try:
            set_size = self.redis.scard(self.ACTIVE_CODES_KEY) or 0
            if set_size > self.ACTIVE_CODES_CLEANUP_THRESHOLD:
                self.cleanup_stale_active_codes()
        except Exception as e:
            logger.warning(f"Lazy cleanup check failed (non-fatal): {e}")


        for attempt in range(self.MAX_CODE_RETRIES):
            code = str(rng.randint(100000, 999999))

            # Check for collision
            code_key = f"{self.CODE_PREFIX}{code}"
            if self.redis.exists(code_key):
                logger.debug(f"Code collision on attempt {attempt + 1}: {code}")
                continue

            # Store code → device mapping
            code_data = {
                "device_id": device_id,
                "created_at": time.time(),
                "status": "pending",
            }
            self.redis.set(code_key, json.dumps(code_data), ex=self.CODE_EXPIRY)

            # Store device → code mapping
            device_data = {
                "code": code,
                "device_id": device_id,
                "device_name": device_name,
                "user_id": None,
                "room_id": None,
                "status": "pending",
                "created_at": time.time(),
                "linked_at": None,
            }
            self.redis.set(
                f"{self.DEVICE_PREFIX}{device_id}",
                json.dumps(device_data),
                ex=self.DEVICE_EXPIRY,
            )

            # Track active code in SET
            self.redis.sadd(self.ACTIVE_CODES_KEY, code)

            logger.info(f"Generated device link code {code} for device {device_id}")
            return code

        raise RuntimeError("Unable to generate a unique device link code. Try again.")

    # ------------------------------------------------------------------
    # Code lookup
    # ------------------------------------------------------------------

    def get_device_for_code(self, code: str) -> Optional[Dict[str, Any]]:
        """
        Look up which device a code belongs to.

        Args:
            code: 6-digit code string

        Returns:
            Code data dict or None if not found / expired
        """
        try:
            key = f"{self.CODE_PREFIX}{code}"
            data = self.redis.get(key)
            if data:
                return json.loads(data)
            return None
        except Exception as e:
            logger.error(f"Error looking up code {code}: {e}")
            return None

    # ------------------------------------------------------------------
    # Device status
    # ------------------------------------------------------------------

    def get_device_status(self, device_id: str) -> Optional[Dict[str, Any]]:
        """
        Get current status/data for a device.

        Args:
            device_id: Device UUID string

        Returns:
            Device data dict or None
        """
        try:
            key = f"{self.DEVICE_PREFIX}{device_id}"
            data = self.redis.get(key)
            if data:
                return json.loads(data)
            return None
        except Exception as e:
            logger.error(f"Error getting device status for {device_id}: {e}")
            return None

    # ------------------------------------------------------------------
    # Linking
    # ------------------------------------------------------------------

    def link_device(self, code: str, user_id: str, room_id: str = None) -> Dict[str, Any]:
        """
        Link a device with the authenticated user.

        Args:
            code: 6-digit code string
            user_id: Authenticated user ID (from JWT)
            room_id: Optional room ID; auto-generated if omitted

        Returns:
            Dict with device_id, room_id, and status

        Raises:
            ValueError: If code not found, expired, or already linked
        """
        # Look up code
        code_data = self.get_device_for_code(code)
        if not code_data:
            raise ValueError("Invalid or expired code.")

        device_id = code_data["device_id"]

        # Look up device
        device_data = self.get_device_status(device_id)
        if not device_data:
            raise ValueError("Device session expired.")

        if device_data.get("status") == "linked":
            raise ValueError("Code has already been used.")

        # Generate room_id if not provided
        if not room_id:
            room_id = f"room-{str(uuid.uuid4())[:8]}"

        # Update device data
        device_data.update({
            "status": "linked",
            "user_id": user_id,
            "room_id": room_id,
            "linked_at": time.time(),
        })
        self.redis.set(
            f"{self.DEVICE_PREFIX}{device_id}",
            json.dumps(device_data),
            ex=self.LINKED_DEVICE_EXPIRY,
        )

        # Remove code (one-time use)
        self._cleanup_code(code)

        logger.info(f"Device {device_id} linked to user {user_id} in room {room_id}")

        return {
            "device_id": device_id,
            "room_id": room_id,
            "user_id": user_id,
            "status": "linked",
        }

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------

    def check_rate_limit(self, identifier: str, max_attempts: int) -> bool:
        """
        Check and increment rate limit counter.

        Args:
            identifier: Unique key (IP address or user ID)
            max_attempts: Maximum allowed attempts in the window

        Returns:
            True if the request is allowed, False if rate-limited
        """
        try:
            key = f"{self.RATE_PREFIX}{identifier}"
            current = self.redis.incr(key)
            if current == 1:
                # First request – set the TTL window
                self.redis.expire(key, self.RATE_LIMIT_WINDOW)
            return current <= max_attempts
        except Exception as e:
            logger.error(f"Rate limit check error: {e}")
            # Fail open – allow the request on error
            return True

    # ------------------------------------------------------------------
    # Cleanup helpers
    # ------------------------------------------------------------------
    def _cleanup_code(self, code: str) -> None:
        """Remove a code from Redis after it has been used or expired."""
        try:
            self.redis.delete(f"{self.CODE_PREFIX}{code}")
            self.redis.srem(self.ACTIVE_CODES_KEY, code)
            logger.debug(f"Cleaned up code {code}")
        except Exception as e:
            logger.error(f"Error cleaning up code {code}: {e}")

    def cleanup_stale_active_codes(self) -> int:
        """
        Iterate the ``devicelink:active_codes`` SET and remove entries
        whose corresponding Redis key (``devicelink:code:{code}``) no
        longer exists (deleted by TTL).

        This prevents the SET from growing indefinitely without
        requiring a Celery task dependency in the FastAPI service.

        Returns:
            Number of orphaned entries removed.
        """
        removed = 0
        try:
            members = self.redis.smembers(self.ACTIVE_CODES_KEY)
            if not members:
                return 0

            for member in list(members):
                code = member if isinstance(member, str) else member.decode("utf-8")
                if not self.redis.exists(f"{self.CODE_PREFIX}{code}"):
                    self.redis.srem(self.ACTIVE_CODES_KEY, code)
                    removed += 1

            if removed:
                logger.info(
                    f"Lazy cleanup: removed {removed} orphaned entries from active_codes SET"
                )
        except Exception as e:
            logger.error(f"Error during active_codes cleanup: {e}")
        return removed

    def invalidate_device(self, device_id: str) -> None:
        """
        Invalidate a device session (e.g. on WebSocket disconnect while pending).

        Args:
            device_id: Device UUID string
        """
        try:
            device_data = self.get_device_status(device_id)
            if device_data and device_data.get("status") == "pending":
                code = device_data.get("code")
                if code:
                    self._cleanup_code(code)
                self.redis.delete(f"{self.DEVICE_PREFIX}{device_id}")
                logger.debug(f"Invalidated pending device {device_id}")
        except Exception as e:
            logger.error(f"Error invalidating device {device_id}: {e}")

