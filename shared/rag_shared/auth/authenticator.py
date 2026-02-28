"""
Environment-aware user authentication.

DEV:      Validates against hardcoded credentials.
STG/PROD: Placeholder for Active Directory / SSO integration.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

DEV_USERS: dict[str, dict] = {
    "admin": {"password": "admin123", "role": "admin", "name": "Admin User"},
    "dev": {"password": "dev123", "role": "viewer", "name": "Dev User"},
}


async def authenticate_user(
    username: str,
    password: str,
    environment: str,
) -> Optional[dict]:
    """
    Authenticate a user based on the current environment.

    Returns a dict with keys (username, role, name) on success, or None on failure.
    """
    if environment.upper() == "DEV":
        user = DEV_USERS.get(username)
        if user and user["password"] == password:
            return {
                "username": username,
                "role": user["role"],
                "name": user["name"],
            }
        return None

    # ── STG / PROD ───────────────────────────────────────────────────────────
    # TODO: Integrate with Active Directory / LDAP / OIDC here.
    #
    # Example integration point:
    #   if environment.upper() in ("STG", "PROD"):
    #       return await validate_against_active_directory(username, password)
    #
    # For now, log a warning and reject all logins in non-DEV environments
    # until the SSO integration is implemented.
    logger.warning(
        f"Authentication attempted in {environment} environment — "
        "SSO/AD integration not yet configured. Login rejected."
    )
    return None
