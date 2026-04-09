"""
@file_name: auth.py
@author: NexusAgent
@date: 2026-04-08
@description: Authentication utilities for cloud deployment

Provides JWT token generation/verification, password hashing,
and FastAPI dependency for extracting current user from requests.
In local mode (SQLite), auth is bypassed — no JWT required.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request
from loguru import logger


# =============================================================================
# Configuration
# =============================================================================

JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-do-not-use-in-production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_DAYS = 7
INVITE_CODE = os.environ.get("INVITE_CODE", "narranexus2026")


def _is_cloud_mode() -> bool:
    """Check if running in cloud mode (MySQL) vs local mode (SQLite)."""
    db_url = os.environ.get("DATABASE_URL", "")
    return not db_url.startswith("sqlite")


# =============================================================================
# Password Hashing
# =============================================================================

def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against its bcrypt hash."""
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


# =============================================================================
# JWT Token
# =============================================================================

def create_token(user_id: str, role: str) -> str:
    """Create a JWT token with user_id and role."""
    payload = {
        "user_id": user_id,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(days=JWT_EXPIRY_DAYS),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    """Decode and verify a JWT token. Raises on invalid/expired tokens."""
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])


# =============================================================================
# FastAPI Dependency
# =============================================================================

class CurrentUser:
    """Represents the authenticated user extracted from JWT or local session."""

    def __init__(self, user_id: str, role: str = "user"):
        self.user_id = user_id
        self.role = role

    @property
    def is_staff(self) -> bool:
        return self.role == "staff"


async def get_current_user(request: Request) -> Optional[CurrentUser]:
    """
    FastAPI dependency that extracts the current user.

    - Cloud mode: Requires valid JWT in Authorization header
    - Local mode: Reads user_id from query params or request body (backward compatible)

    Returns None for auth endpoints (login, register) which handle their own auth.
    """
    if not _is_cloud_mode():
        # Local mode: no JWT enforcement, extract user_id from request
        return None

    # Cloud mode: require JWT
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token = auth_header[7:]
    try:
        payload = decode_token(token)
        return CurrentUser(
            user_id=payload["user_id"],
            role=payload.get("role", "user"),
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


def require_auth(request: Request) -> CurrentUser:
    """Synchronous version for use in route signatures. Use as Depends(require_auth)."""
    # This is handled via middleware instead — see below
    pass


# =============================================================================
# Middleware
# =============================================================================

# Paths that don't require authentication (even in cloud mode)
AUTH_EXEMPT_PATHS = {
    "/api/auth/login",
    "/api/auth/register",
    "/api/providers/claude-status",
    "/docs",
    "/openapi.json",
    "/health",
}

# Prefixes that don't require auth
AUTH_EXEMPT_PREFIXES = (
    "/ws/",  # WebSocket handles its own auth via message payload
)


async def auth_middleware(request: Request, call_next):
    """
    Middleware that enforces JWT authentication in cloud mode.

    Local mode: passes through all requests unchanged.
    Cloud mode: validates JWT for all non-exempt paths, injects user info into request.state.
    """
    # CORS preflight (OPTIONS) requests MUST bypass auth entirely.
    #
    # The CORS spec requires browsers to omit the Authorization header on
    # preflight, so any JWT check here would 401 every cross-origin non-simple
    # request (e.g. requests with Authorization or custom Content-Type). That
    # would kill all /api/* calls from the dev server or from a cloud-app
    # frontend on a different origin.
    #
    # CORSMiddleware is registered in backend/main.py, but FastAPI middleware
    # is LIFO — this auth middleware runs FIRST, so CORSMiddleware never gets
    # a chance at the preflight unless we call_next here. Let the request fall
    # through; CORSMiddleware will intercept and return the correct headers.
    if request.method == "OPTIONS":
        return await call_next(request)

    if not _is_cloud_mode():
        # Local mode: no auth enforcement
        response = await call_next(request)
        return response

    path = request.url.path

    # Check exemptions
    if path in AUTH_EXEMPT_PATHS or any(path.startswith(p) for p in AUTH_EXEMPT_PREFIXES):
        response = await call_next(request)
        return response

    # Static files (frontend assets)
    if not path.startswith("/api/"):
        response = await call_next(request)
        return response

    # Require JWT
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return _json_response(401, {"detail": "Authentication required"})

    token = auth_header[7:]
    try:
        payload = decode_token(token)
        request.state.user_id = payload["user_id"]
        request.state.role = payload.get("role", "user")
    except jwt.ExpiredSignatureError:
        return _json_response(401, {"detail": "Token expired"})
    except jwt.InvalidTokenError:
        return _json_response(401, {"detail": "Invalid token"})

    response = await call_next(request)
    return response


def _json_response(status_code: int, body: dict):
    """Create a JSON response without importing starlette at module level."""
    from starlette.responses import JSONResponse
    return JSONResponse(status_code=status_code, content=body)
