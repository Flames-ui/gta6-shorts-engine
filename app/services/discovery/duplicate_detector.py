"""Deduplication engine based on canonical URLs, content hashes, and metadata."""
import hashlib
import logging
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

logger = logging.getLogger("gta6_shorts.dedupe")


class DuplicateDetector:
    """Detects duplicate source content across URLs, hashes, and IDs."""

    @staticmethod
    def compute_content_hash(media_bytes: bytes) -> str:
        """Compute SHA-256 hash of media file header or complete bytes."""
        hasher = hashlib.sha256()
        # Hash first 1MB of content for speed and uniqueness
        hasher.update(media_bytes[: 1024 * 1024])
        return hasher.hexdigest()

    @staticmethod
    def normalize_url(url: str) -> str:
        """Strip tracking parameters (utm_*, ref, etc.) to produce canonical URL."""
        if not url:
            return ""
        try:
            parsed = urlsplit(url.strip())
            query_params = parse_qs(parsed.query, keep_blank_values=False)

            # Filter out analytics & tracking parameters
            tracking_keys = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "ref", "fbclid", "gclid"}
            filtered_params = {k: v for k, v in query_params.items() if k.lower() not in tracking_keys}

            # Reconstruct sorted query string
            sorted_query = urlencode(filtered_params, doseq=True)
            canonical = urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, sorted_query, ""))
            return canonical.rstrip("/")
        except Exception:
            return url.strip()


duplicate_detector = DuplicateDetector()
