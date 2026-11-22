"""Marketplace adapter package for campaign discovery and rule acquisition."""
from .base import BaseMarketplaceAdapter
from .content_rewards import ContentRewardsAdapter, content_rewards_adapter

__all__ = ["BaseMarketplaceAdapter", "ContentRewardsAdapter", "content_rewards_adapter"]
