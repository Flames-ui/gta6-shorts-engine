"""Authentication and authorization.

Users authenticate with a bearer JWT (HS256) issued by the identity provider
configured through AUTH_JWT_SECRET (for example Supabase Auth, whose JWT secret
is compatible). The `sub` claim is the owner id; owner ids are never accepted
from request bodies or query strings.

Operators can call diagnostics with the ADMIN_API_KEY header, or with a JWT
whose subject is listed in ADMIN_USER_IDS.
"""
from __future__ import annotations

import hmac
import time
from dataclasses import dataclass
from typing import Optional

import jwt
from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import settings
from .errors import Forbidden, Unauthorized

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    user_id: str
    is_admin: bool = False


def decode_token(token: str) -> Principal:
    secret = settings.AUTH_JWT_SECRET
    if not secret:
        raise Unauthorized("Authentication is not configured on this server.", code="auth_not_configured")
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience=settings.AUTH_JWT_AUDIENCE,
            options={"require": ["exp", "sub"]},
            leeway=settings.AUTH_TOKEN_LEEWAY_SECONDS,
        )
    except jwt.ExpiredSignatureError as exc:
        raise Unauthorized("Your session has expired. Sign in again.", code="token_expired") from exc
    except jwt.PyJWTError as exc:
        raise Unauthorized("Invalid or missing credentials.", code="invalid_token") from exc
    sub = str(claims.get("sub", "")).strip()
    if not sub or len(sub) > 128:
        raise Unauthorized("Invalid or missing credentials.", code="invalid_token")
    is_admin = sub in settings.admin_user_ids or claims.get("app_role") == "admin"
    return Principal(user_id=sub, is_admin=bool(is_admin))


def current_principal(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> Principal:
    if creds is None or creds.scheme.lower() != "bearer" or not creds.credentials:
        raise Unauthorized("Authentication required.", code="auth_required")
    return decode_token(creds.credentials)


def require_admin(
    operator_key: Optional[str] = Header(default=None, alias="X-Admin-Key"),
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> Principal:
    if operator_key and settings.ADMIN_API_KEY and hmac.compare_digest(operator_key, settings.ADMIN_API_KEY):
        return Principal(user_id="operator", is_admin=True)
    if creds is not None and creds.scheme.lower() == "bearer" and creds.credentials:
        principal = decode_token(creds.credentials)
        if principal.is_admin:
            return principal
    raise Forbidden("Administrator access is required.", code="admin_required")


def now_epoch() -> int:
    return int(time.time())
