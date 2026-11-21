"""Approved RSS Feed content discovery adapter."""
import logging
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import requests
from ..security import validate_safe_url
from .base import BaseSourceAdapter, DiscoveredContentItem, RightsStatus, SourceType

logger = logging.getLogger("gta6_shorts.discovery.rss")


class RSSSourceAdapter(BaseSourceAdapter):
    """Discovers content from approved, creator-owned RSS media feeds."""

    def __init__(self):
        super().__init__(adapter_type=SourceType.APPROVED_RSS)

    def discover_content(self, source_config: Dict[str, Any], limit: int = 10) -> List[DiscoveredContentItem]:
        feed_url = source_config.get("feed_url")
        if not feed_url:
            return []

        # Validate URL against SSRF
        try:
            safe_feed_url = validate_safe_url(feed_url, require_https=True)
        except Exception as e:
            logger.error("RSS feed URL failed security check: %s", e)
            return []

        items: List[DiscoveredContentItem] = []
        try:
            resp = requests.get(safe_feed_url, timeout=10, headers={"User-Agent": "GTA6-Shorts-Discovery/2.0"})
            if resp.status_code != 200:
                logger.warning("Failed to fetch RSS feed: HTTP %d", resp.status_code)
                return []

            root = ET.fromstring(resp.content)
            channel = root.find("channel") or root
            channel_title = channel.findtext("title") or "Unknown RSS Creator"

            entries = channel.findall("item") or root.findall("{http://www.w3.org/2005/Atom}entry")
            for entry in entries[:limit]:
                title = entry.findtext("title") or "Untitled Episode"
                link = entry.findtext("link")
                pub_date_str = entry.findtext("pubDate")

                # Look for media enclosure
                enclosure = entry.find("enclosure")
                media_url = enclosure.get("url") if enclosure is not None else None

                item_id = f"rss_{uuid.uuid4().hex[:10]}"
                item = DiscoveredContentItem(
                    id=item_id,
                    source_id=source_config.get("id", "rss_source"),
                    source_type=SourceType.APPROVED_RSS,
                    title=title,
                    original_url=link,
                    media_url=media_url,
                    creator_name=channel_title,
                    category=source_config.get("category", "gaming"),
                    rights_status=RightsStatus(source_config.get("default_rights_status", RightsStatus.AUTHORIZED_EDITING.value)),
                    attribution_text=f"Source: {channel_title} ({link or safe_feed_url})",
                )
                items.append(item)
        except Exception as exc:
            logger.error("Error parsing RSS feed %s: %s", feed_url, exc)

        return items

    def get_metadata(self, source_url_or_id: str) -> Optional[Dict[str, Any]]:
        return {"adapter": "rss", "source": source_url_or_id}

    def verify_rights(self, item: DiscoveredContentItem) -> RightsStatus:
        return item.rights_status

# Alias for backward compatibility
RSSDiscoveryAdapter = RSSSourceAdapter
