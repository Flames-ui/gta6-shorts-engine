"""Serve local-storage files through signed, expiring links (used by browser players)."""
from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse

from ...errors import Forbidden, StudioError
from ...storage import LocalStorage, get_storage

router = APIRouter(prefix="/files", tags=["files"])


@router.get("/{token}", summary="Signed file download (local storage only)")
def signed_file(token: str, sig: str = Query(..., min_length=20, max_length=200)):
    storage = get_storage()
    if not isinstance(storage, LocalStorage):
        raise Forbidden("Signed file links are not used with this storage backend.", code="link_not_supported")
    try:
        key = storage.verify_token(token, sig)
    except ValueError as exc:
        if str(exc) == "expired":
            raise StudioError("This download link has expired. Request a new one.", code="link_expired",
                              status_code=410) from exc
        raise Forbidden("This download link is invalid.", code="invalid_link") from exc
    return FileResponse(storage.local_path(key), media_type="application/octet-stream")
