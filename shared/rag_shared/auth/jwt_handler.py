"""JWT token creation and verification utilities."""

from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
import logging

logger = logging.getLogger(__name__)


def create_access_token(
    data: dict,
    secret: str,
    expires_hours: int = 8,
) -> str:
    """Create a JWT access token with the given payload."""
    payload = data.copy()
    payload["exp"] = datetime.now(timezone.utc) + timedelta(hours=expires_hours)
    payload["iat"] = datetime.now(timezone.utc)
    return jwt.encode(payload, secret, algorithm="HS256")


def verify_token(token: str, secret: str) -> Optional[dict]:
    """Verify and decode a JWT token. Returns payload dict or None if invalid."""
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
        return payload
    except jwt.ExpiredSignatureError:
        logger.debug("JWT token has expired")
        return None
    except jwt.InvalidTokenError as e:
        logger.debug(f"Invalid JWT token: {e}")
        return None
