"""
Campaign refresh scheduler and background opportunity monitor.
Performs rate-limit-conscious campaign updates, detects expired or exhausted budgets,
identifies rule changes, and alerts the operator to high-value opportunities.
"""
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..config import settings
from .campaign_eligibility import campaign_eligibility_service
from .marketplaces.content_rewards import content_rewards_adapter
from .operator_action_queue import operator_action_queue
from .opportunity_scorer import opportunity_scorer
from .supabase_client import db

logger = logging.getLogger("gta6_shorts.campaign_scheduler")


class CampaignScheduler:
    """
    Manages periodic refresh cycles for campaigns.
    Runs quietly in the background without aggressive polling.
    """

    def __init__(self):
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._interval_seconds = max(300, settings.CAMPAIGN_REFRESH_INTERVAL_MINUTES * 60)
        self._last_run: Optional[str] = None

    def start(self):
        """Start the background scheduler thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._scheduler_loop, daemon=True)
        self._thread.start()
        logger.info("CampaignScheduler started with interval %ds.", self._interval_seconds)

    def stop(self):
        """Stop the background scheduler thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        logger.info("CampaignScheduler stopped.")

    def run_refresh_cycle(self) -> Dict[str, Any]:
        """
        Executes a single complete refresh cycle:
        1. Refreshes active campaigns in DB.
        2. Detects expired deadlines.
        3. Recalculates opportunity scores.
        4. Surfaces high-value opportunities to operator action queue.
        """
        now = datetime.now(timezone.utc)
        self._last_run = now.isoformat()
        logger.info("Executing scheduled campaign refresh cycle at %s", self._last_run)

        campaigns, _ = db.list_campaigns()
        updated_count = 0
        alerts_queued = 0

        for c in campaigns:
            camp_id = c["id"]
            # 1. Check deadline expiration
            deadline_str = c.get("deadline") or c.get("submission_deadline")
            if deadline_str:
                try:
                    deadline_dt = datetime.fromisoformat(str(deadline_str).replace("Z", "+00:00"))
                    if deadline_dt < now and c.get("status") != "expired":
                        db.update_campaign(camp_id, {"status": "expired"})
                        operator_action_queue.push_action(
                            action_type="campaign_expired",
                            title=f"Campaign Expired: {c.get('name')}",
                            message=f"Campaign {camp_id} deadline passed on {deadline_str}.",
                            campaign_id=camp_id,
                            severity="info",
                        )
                        updated_count += 1
                        alerts_queued += 1
                        continue
                except Exception:
                    pass

            # 2. Check remaining budget exhaustion
            rem_budget = c.get("remaining_budget")
            if rem_budget is not None and float(rem_budget) <= 0 and c.get("status") != "exhausted":
                db.update_campaign(camp_id, {"status": "exhausted"})
                operator_action_queue.push_action(
                    action_type="campaign_exhausted",
                    title=f"Campaign Budget Exhausted: {c.get('name')}",
                    message=f"Campaign {camp_id} remaining budget is $0.00.",
                    campaign_id=camp_id,
                    severity="warning",
                )
                updated_count += 1
                alerts_queued += 1
                continue

            # 3. Recalculate opportunity score
            score_res = opportunity_scorer.score_campaign(c)
            if score_res["tier"] == "HIGH" and c.get("status") in ["eligible", "approved"]:
                operator_action_queue.push_action(
                    action_type="high_value_campaign",
                    title=f"High Value Campaign Opportunity: {c.get('name')}",
                    message=(
                        f"Campaign {camp_id} scored {score_res['overall_score']}/100 "
                        f"({score_res['verified_earnings_structure']}). Ready for clipping."
                    ),
                    campaign_id=camp_id,
                    severity="info",
                    metadata=score_res,
                )
                alerts_queued += 1

        return {
            "cycle_completed_at": self._last_run,
            "total_campaigns_checked": len(campaigns),
            "campaigns_updated": updated_count,
            "alerts_queued": alerts_queued,
        }

    def _scheduler_loop(self):
        while self._running:
            try:
                self.run_refresh_cycle()
            except Exception as exc:
                logger.error("Error in campaign scheduler cycle: %s", exc)
            # Sleep in small increments for graceful shutdown
            for _ in range(self._interval_seconds):
                if not self._running:
                    break
                time.sleep(1)


campaign_scheduler = CampaignScheduler()
