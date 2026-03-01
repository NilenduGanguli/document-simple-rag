"""
Auth router — login, token verification, and environment config.

Endpoints:
    POST /auth/login   — Authenticate with credentials, receive JWT
    GET  /auth/me      — Verify JWT and return current user info
    GET  /auth/config  — Public endpoint returning environment & auth method
"""

import logging

from fastapi import APIRouter, HTTPException, Header, status
from pydantic import BaseModel

from rag_shared.config import get_settings
from rag_shared.auth.jwt_handler import create_access_token, verify_token
from rag_shared.auth.authenticator import authenticate_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

settings = get_settings()


# ── Request / Response schemas ───────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class AuthUser(BaseModel):
    username: str
    role: str
    name: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: AuthUser
    environment: str


class AuthConfigResponse(BaseModel):
    environment: str
    auth_method: str  # "credentials" | "sso" (future)


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest):
    """Authenticate with username/password and receive a JWT."""
    user = await authenticate_user(
        username=body.username,
        password=body.password,
        environment=settings.environment,
    )

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    token = create_access_token(
        data={"sub": user["username"], "role": user["role"], "name": user["name"]},
        secret=settings.jwt_secret,
        expires_hours=settings.jwt_expiry_hours,
    )

    logger.info(f"User '{user['username']}' logged in (env={settings.environment})")

    return LoginResponse(
        access_token=token,
        user=AuthUser(**user),
        environment=settings.environment,
    )


@router.get("/me", response_model=AuthUser)
async def get_current_user(authorization: str = Header(...)):
    """Verify the JWT and return the current user's info."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header must be: Bearer <token>",
        )

    token = authorization[7:]  # strip "Bearer "
    payload = verify_token(token, settings.jwt_secret)

    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    return AuthUser(
        username=payload["sub"],
        role=payload.get("role", "viewer"),
        name=payload.get("name", payload["sub"]),
    )


@router.get("/config", response_model=AuthConfigResponse)
async def get_auth_config():
    """
    Public endpoint — returns environment and auth method.
    Frontend uses this to decide whether to show a login form or SSO button.
    """
    auth_method = "credentials"
    # Future: if settings.environment in ("STG", "PROD") and SSO configured:
    #   auth_method = "sso"

    return AuthConfigResponse(
        environment=settings.environment,
        auth_method=auth_method,
    )
