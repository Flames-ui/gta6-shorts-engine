"""Authentication status, profile inspection, and session verification endpoints."""
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from ....auth import AuthContext, _check_is_admin_claim, verify_api_key, verify_supabase_jwt
from ....config import settings
from ....schemas import AuthMeResponse, DashboardLoginRequest
from ....services.supabase_client import db

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.get("/me", response_model=AuthMeResponse)
async def get_current_user_profile(auth: AuthContext = Depends(verify_api_key)):
    """Retrieve verified authentication context, user identity, and administrator status."""
    return AuthMeResponse(
        user_id=auth.user_id,
        role=auth.role,
        is_admin=auth.is_admin,
        auth_method=auth.auth_method,
    )


@router.post("/login", response_model=Dict[str, Any])
async def login_operator(
    payload: DashboardLoginRequest,
    request: Request,
    response: Response,
):
    """Authenticate operator session via API key or Supabase token and issue HttpOnly session cookie."""
    configured_key = settings.ENGINE_API_KEY.strip()
    previous_key = settings.ENGINE_API_KEY_PREVIOUS.strip()
    is_prod = settings.ENVIRONMENT.lower() == "production"

    authenticated = False
    user_id = "system_admin"
    role = "admin"
    is_admin = True

    if payload.api_key:
        candidate_key = payload.api_key.strip()
        if configured_key and secrets.compare_digest(candidate_key, configured_key):
            authenticated = True
        elif previous_key and secrets.compare_digest(candidate_key, previous_key):
            authenticated = True
        elif not configured_key and not is_prod:
            authenticated = True
    elif payload.token:
        claims = verify_supabase_jwt(payload.token.strip())
        if claims and "sub" in claims:
            user_id = str(claims["sub"])
            role = claims.get("role", "authenticated")
            is_admin = _check_is_admin_claim(claims, user_id)
            authenticated = True

    if not authenticated:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid operator credentials.",
        )

    session_id = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(days=7)).isoformat()

    session_record = {
        "session_id": session_id,
        "user_id": user_id,
        "role": role,
        "is_admin": is_admin,
        "csrf_token": csrf_token,
        "created_at": now.isoformat(),
        "expires_at": expires_at,
        "ip_address": request.client.host if request.client else "unknown",
    }
    db.create_session(session_record)

    response.set_cookie(
        key="session_id",
        value=session_id,
        httponly=True,
        secure=settings.ENVIRONMENT.lower() == "production",
        samesite="lax",
        path="/",
        max_age=7 * 86400,
    )

    return {
        "status": "ok",
        "user_id": user_id,
        "role": role,
        "csrf_token": csrf_token,
    }


@router.get("/session", response_model=Dict[str, Any])
async def get_session(request: Request):
    """Retrieve active operator session context and CSRF token."""
    session_id = request.cookies.get("session_id")
    if not session_id:
        return {"authenticated": False}

    sess = db.get_session(session_id)
    if not sess:
        return {"authenticated": False}

    exp = sess.get("expires_at")
    if exp and exp < datetime.now(timezone.utc).isoformat():
        db.delete_session(session_id)
        return {"authenticated": False}

    return {
        "authenticated": True,
        "user_id": sess.get("user_id", "system_admin"),
        "role": sess.get("role", "admin"),
        "csrf_token": sess.get("csrf_token"),
    }


@router.post("/logout", response_model=Dict[str, Any])
async def logout(request: Request, response: Response):
    """Terminate operator session and clear session cookie."""
    session_id = request.cookies.get("session_id")
    if session_id:
        db.delete_session(session_id)
    response.delete_cookie(key="session_id", path="/")
    return {"status": "ok", "authenticated": False}
