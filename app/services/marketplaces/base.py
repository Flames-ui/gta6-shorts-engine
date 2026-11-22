"""Abstract base adapter for campaign marketplaces."""
import abc
from typing import Any, Dict, List, Optional


class BaseMarketplaceAdapter(abc.ABC):
    """Common interface for marketplace campaign discovery and rule acquisition."""

    def __init__(self, marketplace_id: str, marketplace_name: str, base_url: str):
        self.marketplace_id = marketplace_id
        self.marketplace_name = marketplace_name
        self.base_url = base_url

    @abc.abstractmethod
    def discover_campaigns(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Discover live gaming campaigns from marketplace."""
        raise NotImplementedError

    @abc.abstractmethod
    def fetch_campaign_details(self, campaign_url_or_id: str) -> Dict[str, Any]:
        """Fetch complete rules and details for a specific campaign."""
        raise NotImplementedError

    @abc.abstractmethod
    def check_session_status(self) -> Dict[str, Any]:
        """
        Check authentication and security challenges (login, 2FA, CAPTCHA).
        Must detect when operator intervention is required and never bypass restrictions.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def manual_import(
        self,
        raw_rules: str,
        campaign_url: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Compliant fallback workflow allowing operator to manually import campaign rules."""
        raise NotImplementedError
