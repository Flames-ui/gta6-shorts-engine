"""Authentication, authorization, and tenant isolation dependencies."""
import base64
import json
import logging
import secrets
from typing import Any, Dict, Optional
from fastapi import Header, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from .config import settings

logger = logging.getLogger("gta6_shorts.auth")
security_bearer = HTTPBearer(auto_error=False)


class AuthContext(BaseModel):
    """Authenticated caller context."""
    user_id: str
    role: str = "authenticated"
    is_admin: bool = False
    auth_method: str = "api_key"  # api_key, supabase_jwt, or dev_fallback


def decode_jwt_unverified_claims(token: str) -> Optional[Dict[str, Any]]:
    """Extract claims from a JWT token safely without external library dependencies."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        # Add padding
        payload = parts[1]
        padded = payload + "=" * (-len(payload) % 4)
        data = base64.urlsafe_b64decode(padded.encode("ascii"))
        return json.loads(data.decode("utf-8"))
    except Exception as exc:
        logger.debug("Failed to decode JWT claims: %s", exc)
        return None


async def verify_api_key(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    bearer: Optional[HTTPAuthorizationCredentials] = Security(security_bearer),
) -> AuthContext:
    """
    Validate incoming request credentials.
    Supports:
    1. System Admin API Key (via X-API-Key or Bearer token) with constant-time matching.
    2. Graceful key rotation (ENGINE_API_KEY_PREVIOUS).
    3. User-level Supabase Auth JWT token (via Bearer token) for Lovable dashboard isolation.
    Fails closed in production if credentials or configuration are absent.
    """
    configured_key = settings.ENGINE_API_KEY.strip()
    previous_key = settings.ENGINE_API_KEY_PREVIOUS.strip()
    is_prod = settings.ENVIRONMENT.lower() == "production"

    # In production, require at least ENGINE_API_KEY to be configured
    if is_prod and not configured_key and settings.FAIL_CLOSED_IN_PRODUCTION:
        logger.critical("Security Violation: ENGINE_API_KEY is not configured in production. Rejecting request.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server security misconfiguration: ENGINE_API_KEY must be configured in production.",
        )

    # In development / test with no configured key, allow a mock context
    if not configured_key and not is_prod:
        return AuthContext(user_id="dev_user", role="admin", is_admin=True, auth_method="dev_fallback")

    token: Optional[str] = None
    if x_api_key:
        token = x_api_key.strip()
    elif bearer and bearer.credentials:
        token = bearer.credentials.strip()

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Provide X-API-Key header or Authorization: Bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 1. Check against primary ENGINE_API_KEY with constant-time comparison
    if configured_key and secrets.compare_digest(token, configured_key):
        return AuthContext(user_id="system_admin", role="admin", is_admin=True, auth_method="api_key")

    # 2. Check against previous key for rotation
    if previous_key and secrets.compare_digest(token, previous_key):
        logger.warning("Request authenticated with rotated ENGINE_API_KEY_PREVIOUS.")
        return AuthContext(user_id="system_admin", role="admin", is_admin=True, auth_method="api_key")

    # 3. Check for Supabase Auth JWT
    claims = decode_jwt_unverified_claims(token)
    if claims and "sub" in claims:
        # Check standard Supabase claim fields
        user_id = str(claims["sub"])
        user_role = claims.get("role", "authenticated")
        is_service_role = user_role == "service_role"

        return AuthContext(
            user_id=user_id,
            role=user_role,
            is_admin=is_service_role,
            auth_method="supabase_jwt",
        )

    # Reject invalid credentials
    logger.warning("Authentication failed: invalid token provided.")
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def enforce_resource_ownership(
    resource_owner_id: Optional[str],
    auth: AuthContext,
    resource_type: str = "resource",
) -> None:
    """Ensure non-admin users cannot access resources owned by other users."""
    if auth.is_admin:
        return

    if not resource_owner_id:
        return

    if str(resource_owner_id) != str(auth.user_id):
        logger.warning(
            "Access denied for user %s on %s owned by %s",
            auth.user_id,
            resource_type,
            resource_owner_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied: You do not own this {resource_type}.",
        )
