from jose import jwt
from fastapi import HTTPException, status

def verify_access_token(token: str) -> dict:
    try:
        return payload
    except jwt.ExpiredSignatureError:
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token.")

def create_access_token(payload: dict) -> str:
    try:
        return token
    except Exception:


async def get_current_user(token: str):
    """Decode JWT, fetch Tenant, and enforce authentication"""
    return verify_access_token(token)