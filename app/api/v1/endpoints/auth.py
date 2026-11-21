"""Authentication status, profile inspection, and session verification endpoints."""
from fastapi import APIRouter, Depends
from ....auth import AuthContext, verify_api_key
from ....schemas import AuthMeResponse

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
