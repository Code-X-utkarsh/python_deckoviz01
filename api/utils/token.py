from jose import jwt
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException, status
from utils.settings import SECRET_KEY, JWT_HASH_ALGORITHM, JWT_ACCESS_TOKEN_EXPIRE_MINUTES, JWT_REFRESH_TOKEN_EXPIRE_DAYS


def verify_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[JWT_HASH_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Access token has expired.")
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token.")


def create_access_token(payload: dict) -> str:
    """
    Create a short-lived access token with an ``exp`` claim based on
    ``JWT_ACCESS_TOKEN_EXPIRE_MINUTES`` from settings.
    """
    try:
        token_payload = {**payload}
        token_payload["exp"] = datetime.now(timezone.utc) + timedelta(
            minutes=JWT_ACCESS_TOKEN_EXPIRE_MINUTES,
        )
        token = jwt.encode(token_payload, SECRET_KEY, algorithm=JWT_HASH_ALGORITHM)
        return token
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create access token.",
        )


async def get_current_user(token: str):
    """Decode JWT, fetch Tenant, and enforce authentication"""
    return verify_access_token(token)


def create_refresh_token(payload: dict) -> str:
    """
    Create a long-lived refresh token for TV devices so they stay
    authenticated without requiring the user to re-enter a code.

    The token embeds ``token_type: "refresh"`` and an ``exp`` claim
    based on ``JWT_REFRESH_TOKEN_EXPIRE_DAYS`` from settings.
    """
    try:
        refresh_payload = {**payload}
        refresh_payload["token_type"] = "refresh"
        refresh_payload["exp"] = datetime.now(timezone.utc) + timedelta(
            days=JWT_REFRESH_TOKEN_EXPIRE_DAYS,
        )
        token = jwt.encode(refresh_payload, SECRET_KEY, algorithm=JWT_HASH_ALGORITHM)
        return token
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create refresh token.",
        )


def verify_refresh_token(token: str) -> dict:
    """
    Decode and validate a refresh token.  Raises 401 if the token is
    invalid, expired, or not of type ``refresh``.
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[JWT_HASH_ALGORITHM])
        if payload.get("token_type") != "refresh":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type. Expected a refresh token.",
            )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has expired. Please re-link the device.",
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token.",
        )
