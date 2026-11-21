"""Authentication and security dependencies."""
import logging
import secrets
from typing import Optional
from fastapi import Header, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import settings

logger = logging.getLogger("gta6_shorts.auth")

security_bearer = HTTPBearer(auto_error=False)


async def verify_api_key(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    bearer: Optional[HTTPAuthorizationCredentials] = Security(security_bearer),
) -> str:
    """Validate incoming API key from either 'X-API-Key' header or 'Authorization: Bearer' token."""
    configured_key = settings.ENGINE_API_KEY.strip()

    # If no ENGINE_API_KEY is configured in development, allow requests and warn
    if not configured_key:
        if settings.ENVIRONMENT.lower() == "production":
            logger.warning("ENGINE_API_KEY is not set in production! Endpoint is open.")
        return "unauthenticated-dev-access"

    token = None
    if x_api_key:
        token = x_api_key.strip()
    elif bearer and bearer.credentials:
        token = bearer.credentials.strip()

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key. Provide 'X-API-Key' header or 'Authorization: Bearer <key>'",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Constant-time comparison to prevent timing attacks
    if not secrets.compare_digest(token, configured_key):
        logger.warning("Unauthorized API access attempt with invalid key")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return token
