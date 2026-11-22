"""Authentication, authorization, and tenant isolation dependencies."""
import logging
import secrets
from typing import Any, Dict, Optional
import jwt
from fastapi import Header, HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from .config import settings

logger = logging.getLogger("gta6_shorts.auth")
security_bearer = HTTPBearer(auto_error=False)



def _check_is_admin_claim(claims: Dict[str, Any], user_id: str) -> bool:
    """
    Validate administrator status using only trusted, server-controlled attributes.
    Never trusts user_metadata, which end-users can self-modify in Supabase.
    """
    if claims.get("role") in ("service_role", "admin"):
        return True
    app_meta = claims.get("app_metadata", {})
    if app_meta.get("is_admin") is True or app_meta.get("role") in ("admin", "service_role"):
        return True
    if user_id in settings.ADMIN_USER_IDS:
        return True
    email = claims.get("email")
    if email and str(email).lower() in settings.ADMIN_EMAILS:
        return True
    return False


class AuthContext(BaseModel):
    """Authenticated caller context."""
    user_id: str
    role: str = "authenticated"
    is_admin: bool = False
    auth_method: str = "api_key"  # api_key, supabase_jwt, or dev_fallback


def verify_supabase_jwt(token: str) -> Optional[Dict[str, Any]]:
    """
    Cryptographically verify Supabase JWT signature, expiry, audience, and algorithm.
    Never accepts unverified tokens, unverified claims, or algorithm 'none'.
    """
    secret = settings.SUPABASE_JWT_SECRET.strip()
    is_prod = settings.ENVIRONMENT.lower() == "production"

    if not secret:
        if is_prod and settings.FAIL_CLOSED_IN_PRODUCTION:
            logger.critical("Security Violation: SUPABASE_JWT_SECRET is not configured in production.")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Server security misconfiguration: SUPABASE_JWT_SECRET must be configured in production.",
            )
        logger.debug("SUPABASE_JWT_SECRET not configured; cannot verify JWT signatures.")
        return None

    try:
        # Determine expected issuer if SUPABASE_URL is configured
        issuer = None
        if settings.SUPABASE_URL:
            base_url = settings.SUPABASE_URL.rstrip("/")
            issuer = f"{base_url}/auth/v1"

        # Explicitly restrict to HS256 algorithm; disallow 'none' and RS256/symmetric confusion
        options = {
            "verify_signature": True,
            "verify_exp": True,
            "verify_aud": True,
            "require": ["exp", "sub"],
        }

        # Verify token cryptographically
        payload = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience="authenticated",
            options=options,
        )

        # Issuer check if available
        token_iss = payload.get("iss")
        if issuer and token_iss and token_iss != issuer and token_iss != "supabase":
            logger.warning("JWT issuer mismatch: expected %s or supabase, got %s", issuer, token_iss)
            return None

        return payload
    except jwt.ExpiredSignatureError:
        logger.warning("Supabase JWT has expired.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token has expired.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError as exc:
        logger.warning("Supabase JWT signature verification failed: %s", exc)
        return None
    except Exception as exc:
        logger.error("Unexpected error during JWT verification: %s", exc)
        return None


async def verify_api_key(
    request: Request,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    bearer: Optional[HTTPAuthorizationCredentials] = Security(security_bearer),
) -> AuthContext:
    """
    Validate incoming request credentials.
    Supports:
    1. System Admin API Key (via X-API-Key or Bearer token) with constant-time matching.
    2. Graceful key rotation (ENGINE_API_KEY_PREVIOUS) with constant-time matching.
    3. User-level Supabase Auth JWT token (via Bearer token) with cryptographic signature validation.
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

    token: Optional[str] = None
    if x_api_key:
        token = x_api_key.strip()
    elif bearer and bearer.credentials:
        token = bearer.credentials.strip()

    # Check for valid operator session cookie
    if not token and request is not None:
        session_id = request.cookies.get("session_id")
        if session_id:
            from .services.supabase_client import db
            sess = db.get_session(session_id)
            if sess:
                exp = sess.get("expires_at")
                from datetime import datetime, timezone
                if not exp or exp >= datetime.now(timezone.utc).isoformat():
                    return AuthContext(
                        user_id=sess.get("user_id", "system_admin"),
                        role=sess.get("role", "admin"),
                        is_admin=bool(sess.get("is_admin", True)),
                        auth_method="session_cookie",
                    )

    # In development / test with no configured key, allow a mock context only if no key was passed
    if not configured_key and not is_prod:
        # Check if caller passed a test JWT
        if token and settings.SUPABASE_JWT_SECRET:
            claims = verify_supabase_jwt(token)
            if claims and "sub" in claims:
                user_id = str(claims["sub"])
                user_role = claims.get("role", "authenticated")
                is_admin = _check_is_admin_claim(claims, user_id)
                return AuthContext(
                    user_id=user_id,
                    role=user_role,
                    is_admin=is_admin,
                    auth_method="supabase_jwt",
                )
        return AuthContext(user_id="dev_user", role="admin", is_admin=True, auth_method="dev_fallback")

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Provide X-API-Key header or Authorization: Bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 1. Check against primary ENGINE_API_KEY with constant-time comparison
    if configured_key and secrets.compare_digest(token, configured_key):
        return AuthContext(user_id="system_admin", role="admin", is_admin=True, auth_method="api_key")

    # 2. Check against previous key for rotation with constant-time comparison
    if previous_key and secrets.compare_digest(token, previous_key):
        logger.warning("Request authenticated with rotated ENGINE_API_KEY_PREVIOUS.")
        return AuthContext(user_id="system_admin", role="admin", is_admin=True, auth_method="api_key")

    # 3. Check for cryptographically verified Supabase Auth JWT
    claims = verify_supabase_jwt(token)
    if claims and "sub" in claims:
        user_id = str(claims["sub"])
        user_role = claims.get("role", "authenticated")

        # Admin privileges require verified service_role or admin claim in verified JWT
        is_admin = _check_is_admin_claim(claims, user_id)

        return AuthContext(
            user_id=user_id,
            role=user_role,
            is_admin=is_admin,
            auth_method="supabase_jwt",
        )

    # Reject invalid credentials (fail-closed)
    logger.warning("Authentication failed: invalid token or signature verification failure.")
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication credentials or signature verification failure.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def enforce_resource_ownership(
    resource_owner_id: Optional[str],
    auth: AuthContext,
    resource_type: str = "resource",
) -> None:
    """
    Ensure non-admin users cannot access resources owned by other users.
    FAIL-CLOSED: Missing ownership information on user-owned resources results in denial.
    """
    if auth.is_admin:
        return

    # If ownership information is missing, deny access
    if not resource_owner_id or not str(resource_owner_id).strip():
        logger.warning("Access denied: %s has missing ownership information", resource_type)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied: Missing ownership information on this {resource_type}.",
        )

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


def require_admin(auth: AuthContext) -> None:
    """Ensure the caller possesses verified administrator privileges."""
    if not auth.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: Administrator privileges required.",
        )
