"""Source eligibility for discovery polling.

Every exclusion carries a stable reason code, so the poll response and logs show why a source was
not polled. Rights checks fail closed: unknown, unreadable or expired rights exclude the source.
Discovery does not require auto_clip_enabled or derivative_rights_allowed; production rights checks
still apply those before any clip is made.
"""
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .base import AUTHORIZED_RIGHTS_STATUSES, RightsStatus
from .youtube_channel_adapter import YOUTUBE_SOURCE_TYPES

RSS_SOURCE_TYPES = frozenset({"approved_rss", "rss"})


@dataclass(frozen=True)
class PollDecision:
    source_id: str
    eligible: bool
    reason: Optional[str] = None

    def as_dict(self) -> Dict[str, Optional[str]]:
        return {"source_id": self.source_id, "reason": self.reason}


def as_flag(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("true", "t", "1", "yes")
    return bool(value)


def _parse_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _platforms(value: Any) -> list:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    return [str(p).strip() for p in value] if isinstance(value, list) else []


def evaluate_poll_eligibility(
    source: Dict[str, Any],
    permission: Optional[Dict[str, Any]],
    now: Optional[datetime] = None,
) -> PollDecision:
    now = now or datetime.now(timezone.utc)
    source_id = str(source.get("id"))

    def excluded(reason: str) -> PollDecision:
        return PollDecision(source_id, False, reason)

    if not as_flag(source.get("is_active")):
        return excluded("inactive")

    if not permission:
        return excluded("no_permission_record")

    raw_status = permission.get("rights_status")
    try:
        status = raw_status if isinstance(raw_status, RightsStatus) else RightsStatus(raw_status)
    except ValueError:
        return excluded("invalid_rights_status")
    if status not in AUTHORIZED_RIGHTS_STATUSES:
        return excluded(f"rights_not_authorized:{status.value}")

    if permission.get("revoked_at"):
        return excluded("rights_revoked")

    expires_at = permission.get("expires_at")
    if expires_at not in (None, ""):
        expiry = _parse_datetime(expires_at)
        if expiry is None:
            return excluded("rights_expiry_unreadable")
        if expiry <= now:
            return excluded("rights_expired")

    if not _platforms(permission.get("allowed_platforms")):
        return excluded("no_allowed_platforms")

    source_type = str(source.get("source_type") or "").strip().lower()
    has_channel = bool(str(source.get("channel_url") or "").strip())
    has_feed = bool(str(source.get("feed_url") or "").strip())
    if source_type in YOUTUBE_SOURCE_TYPES:
        if not (has_channel or has_feed):
            return excluded("missing_channel_url")
    elif source_type in RSS_SOURCE_TYPES:
        if not has_feed:
            return excluded("missing_feed_url")
    else:
        return excluded(f"unsupported_source_type:{source_type or 'none'}")

    return PollDecision(source_id, True, None)
