"""System security and operational audit logs endpoint."""
from typing import List, Optional
from fastapi import APIRouter, Depends, Query
from ....auth import AuthContext, verify_api_key
from ....schemas import AuditLogEntryResponse
from ....services.supabase_client import db

router = APIRouter(prefix="/audit-logs", tags=["Audit Logs"])


@router.get("", response_model=List[AuditLogEntryResponse])
async def list_audit_logs(
    action: Optional[str] = Query(None, description="Filter by action name"),
    limit: int = Query(50, ge=1, le=200),
    auth: AuthContext = Depends(verify_api_key),
):
    """List system audit log entries. Scoped to authenticated user unless caller is verified admin."""
    user_id = None if auth.is_admin else auth.user_id
    entries = db.list_audit_logs(user_id=user_id, action=action, limit=limit)
    out = []
    for e in entries:
        out.append(AuditLogEntryResponse(
            id=e["id"],
            user_id=e.get("user_id"),
            action=e["action"],
            resource_type=e["resource_type"],
            resource_id=e.get("resource_id"),
            details=e.get("details") or {},
            ip_address=e.get("ip_address"),
            created_at=e.get("created_at") or "",
        ))
    return out
