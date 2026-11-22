"""
Operator action queue and alert dispatcher for marketplace events.
Surfaces required operator interventions: logins, CAPTCHAs, rule reviews,
source authorizations, and deadline warnings without silent bypasses.
"""
import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("gta6_shorts.action_queue")


class OperatorActionQueue:
    """
    Queue and notification manager for human operator decisions and interventions.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._actions: Dict[str, Dict[str, Any]] = {}

    def push_action(
        self,
        action_type: str,
        title: str,
        message: str,
        marketplace_id: str = "content_rewards",
        campaign_id: Optional[str] = None,
        severity: str = "warning",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Record an action that requires operator intervention.
        Deduplicates identical active action types for the same campaign/marketplace.
        """
        with self._lock:
            # Check for existing pending action of same type and context
            for item in self._actions.values():
                if (
                    item["status"] == "pending"
                    and item["action_type"] == action_type
                    and item["marketplace_id"] == marketplace_id
                    and item.get("campaign_id") == campaign_id
                ):
                    # Update message and timestamp
                    item["message"] = message
                    item["updated_at"] = datetime.now(timezone.utc).isoformat()
                    return item

            action_id = f"act_{uuid.uuid4().hex[:10]}"
            now_str = datetime.now(timezone.utc).isoformat()
            record = {
                "id": action_id,
                "action_type": action_type,
                "title": title,
                "message": message,
                "marketplace_id": marketplace_id,
                "campaign_id": campaign_id,
                "severity": severity,
                "status": "pending",
                "metadata": metadata or {},
                "created_at": now_str,
                "updated_at": now_str,
                "resolved_at": None,
                "resolved_by": None,
            }
            self._actions[action_id] = record
            logger.info("Queued operator action: [%s] %s (%s)", severity.upper(), title, action_id)
            return record

    def list_pending_actions(self) -> List[Dict[str, Any]]:
        """Convenience alias to list pending actions."""
        return self.list_actions(status="pending")

    def list_actions(
        self,
        status: Optional[str] = "pending",
        marketplace_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List operator actions filtered by status and marketplace."""
        with self._lock:
            items = list(self._actions.values())

        if status:
            items = [i for i in items if i["status"] == status]
        if marketplace_id:
            items = [i for i in items if i["marketplace_id"] == marketplace_id]

        items.sort(key=lambda x: x["created_at"], reverse=True)
        return items

    def resolve_action(
        self,
        action_id: str,
        resolved_by: str = "operator",
        notes: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Mark an action as resolved by the operator."""
        with self._lock:
            item = self._actions.get(action_id)
            if not item:
                return None
            item["status"] = "resolved"
            item["resolved_by"] = resolved_by
            item["resolved_at"] = datetime.now(timezone.utc).isoformat()
            if notes:
                item["metadata"]["resolution_notes"] = notes
            logger.info("Resolved operator action %s by %s", action_id, resolved_by)
            return item

    def clear_all(self):
        """Clears all queued actions (useful for testing/resets)."""
        with self._lock:
            self._actions.clear()

    def clear_actions_by_type(self, action_type: str, marketplace_id: Optional[str] = None):
        """Batch-resolve actions of a specific type (e.g. login_required after login)."""
        with self._lock:
            for item in self._actions.values():
                if item["status"] == "pending" and item["action_type"] == action_type:
                    if marketplace_id is None or item["marketplace_id"] == marketplace_id:
                        item["status"] = "resolved"
                        item["resolved_at"] = datetime.now(timezone.utc).isoformat()
                        item["resolved_by"] = "system"


operator_action_queue = OperatorActionQueue()
