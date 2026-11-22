"""Regression tests for discovery polling with the production YouTube channel source.

Production source src_08a19e7ab7: a youtube source with user_id null, no feed_url, a valid channel
URL and authorized_platforms rights. Before this fix the poll skipped it silently and returned
polled_sources 0. These tests use fake database, rights and HTTP layers; they do not contact
Supabase, YouTube or Render.
"""
import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("ENVIRONMENT", "test")

from app.api.v1.endpoints import discovery as ep  # noqa: E402
from app.auth import AuthContext  # noqa: E402
from app.services.discovery import rss_adapter as rss_mod  # noqa: E402
from app.services.discovery import youtube_channel_adapter as yt  # noqa: E402
from app.services.discovery.base import RightsStatus  # noqa: E402
from app.services.discovery.poll_eligibility import evaluate_poll_eligibility  # noqa: E402

NOW = datetime(2026, 11, 22, 17, 0, tzinfo=timezone.utc)
CHANNEL_ID = "UC" + "A" * 22
CHANNEL_URL = f"https://www.youtube.com/channel/{CHANNEL_ID}"
ADMIN = AuthContext(user_id="system_admin", role="admin", is_admin=True, auth_method="api_key")

FEED_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns="http://www.w3.org/2005/Atom">
  <author><name>Fixture Channel</name></author>
  <entry><id>yt:video:AAAAAAAAAAA</id><yt:videoId>AAAAAAAAAAA</yt:videoId>
    <title>First upload</title><published>2026-11-21T10:00:00+00:00</published></entry>
  <entry><id>yt:video:BBBBBBBBBBB</id><yt:videoId>BBBBBBBBBBB</yt:videoId>
    <title>Second upload</title><published>2026-11-20T10:00:00+00:00</published></entry>
  <entry><id>yt:video:not-a-valid-id</id><yt:videoId>bad id</yt:videoId><title>Skipped</title></entry>
</feed>"""


def production_source(**overrides):
    source = {
        "id": "src_08a19e7ab7",
        "user_id": None,
        "source_type": "youtube",
        "is_active": True,
        "auto_clip_enabled": True,
        "category_id": "gta6",
        "feed_url": "",
        "channel_url": CHANNEL_URL,
        "status": "active",
    }
    source.update(overrides)
    return source


def production_permission(**overrides):
    permission = {
        "rights_status": RightsStatus.AUTHORIZED_PLATFORMS,
        "allowed_platforms": ["tiktok", "youtube_shorts"],
        "license_type": "creator_authorized",
        "expires_at": None,
        "revoked_at": None,
        "derivative_rights_allowed": True,
    }
    permission.update(overrides)
    return permission


class FakeResponse:
    def __init__(self, status_code=200, content=FEED_XML):
        self.status_code = status_code
        self.content = content


class FakeDB:
    def __init__(self, sources):
        self.sources = sources
        self.saved = []
        self.discovered = []
        self.health = []

    def get_source(self, source_id):
        return next((dict(s) for s in self.sources if s["id"] == source_id), None)

    def list_sources(self, user_id=None, category_id=None, active_only=True):
        rows = []
        for s in self.sources:
            if user_id is not None and s.get("user_id") != user_id:
                continue
            if category_id is not None and s.get("category_id") != category_id:
                continue
            if active_only and not s.get("is_active"):
                continue
            rows.append(dict(s))
        return rows

    def list_discovered_content(self, user_id=None, limit=200):
        return list(self.discovered)

    def save_discovered_content(self, item):
        self.saved.append(dict(item))
        self.discovered.append(dict(item))
        return item

    def update_source_health(self, source_id, status=None, error=None, last_polled_at=None,
                             last_success_at=None, increment_failures=False, reset_failures=False):
        self.health.append({
            "source_id": source_id, "status": status, "error": error,
            "last_polled_at": last_polled_at, "last_success_at": last_success_at,
        })


class FakeRights:
    def __init__(self, permissions):
        self.permissions = permissions

    def get_permission(self, source_id):
        perm = self.permissions.get(source_id)
        return dict(perm) if perm else None


def install(monkeypatch, sources, permissions):
    fake_db = FakeDB(sources)
    monkeypatch.setattr(ep, "db", fake_db)
    monkeypatch.setattr(ep, "rights_manager", FakeRights(permissions))
    monkeypatch.setattr(rss_mod, "db", fake_db)
    return fake_db


def poll(source_id=None):
    return asyncio.run(ep.poll_content_sources(source_id=source_id, category_id=None, auth=ADMIN))


def feed_fetcher(monkeypatch, response=None, calls=None):
    def fake_get(url, headers=None, timeout=15, **kwargs):
        if calls is not None:
            calls.append(url)
        return response or FakeResponse()
    monkeypatch.setattr(yt, "safe_requests_get", fake_get)


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

def test_exact_production_source_is_eligible_with_null_user_id():
    decision = evaluate_poll_eligibility(production_source(), production_permission(), NOW)
    assert decision.eligible is True
    assert decision.reason is None


@pytest.mark.parametrize("auto_clip", [True, False])
def test_auto_clip_flag_does_not_gate_discovery(auto_clip):
    decision = evaluate_poll_eligibility(production_source(auto_clip_enabled=auto_clip), production_permission(), NOW)
    assert decision.eligible is True


def test_youtube_channel_without_feed_url_is_eligible():
    decision = evaluate_poll_eligibility(production_source(feed_url=None), production_permission(), NOW)
    assert decision.eligible is True


@pytest.mark.parametrize("permission, reason", [
    (None, "no_permission_record"),
    (production_permission(rights_status=RightsStatus.PENDING_VERIFICATION), "rights_not_authorized:pending_verification"),
    (production_permission(rights_status=RightsStatus.REJECTED), "rights_not_authorized:rejected"),
    (production_permission(rights_status=RightsStatus.EXPIRED), "rights_not_authorized:expired"),
    (production_permission(rights_status="authorized"), "invalid_rights_status"),
    (production_permission(revoked_at="2026-11-01T00:00:00+00:00"), "rights_revoked"),
    (production_permission(expires_at="2026-11-01T00:00:00+00:00"), "rights_expired"),
    (production_permission(expires_at="not-a-date"), "rights_expiry_unreadable"),
    (production_permission(allowed_platforms=[]), "no_allowed_platforms"),
])
def test_rights_failures_exclude_source_with_reason(permission, reason):
    decision = evaluate_poll_eligibility(production_source(), permission, NOW)
    assert decision.eligible is False
    assert decision.reason == reason


def test_future_expiry_with_naive_timestamp_is_eligible():
    future = (NOW + timedelta(days=30)).replace(tzinfo=None).isoformat()
    decision = evaluate_poll_eligibility(production_source(), production_permission(expires_at=future), NOW)
    assert decision.eligible is True


def test_inactive_source_is_excluded():
    decision = evaluate_poll_eligibility(production_source(is_active=False), production_permission(), NOW)
    assert (decision.eligible, decision.reason) == (False, "inactive")


def test_youtube_source_without_any_url_is_excluded():
    decision = evaluate_poll_eligibility(production_source(channel_url=None, feed_url=""), production_permission(), NOW)
    assert (decision.eligible, decision.reason) == (False, "missing_channel_url")


def test_unknown_source_type_is_excluded_not_guessed():
    decision = evaluate_poll_eligibility(production_source(source_type="scraper"), production_permission(), NOW)
    assert (decision.eligible, decision.reason) == (False, "unsupported_source_type:scraper")


def test_rss_source_with_feed_url_still_eligible():
    source = production_source(source_type="approved_rss", channel_url=None, feed_url="https://example.com/feed.xml")
    assert evaluate_poll_eligibility(source, production_permission(), NOW).eligible is True


def test_rss_source_without_feed_url_is_excluded():
    source = production_source(source_type="approved_rss", channel_url=None, feed_url="")
    decision = evaluate_poll_eligibility(source, production_permission(), NOW)
    assert (decision.eligible, decision.reason) == (False, "missing_feed_url")


# ---------------------------------------------------------------------------
# Poll endpoint with the production source
# ---------------------------------------------------------------------------

def test_poll_fetches_production_channel_and_records_success(monkeypatch):
    fake_db = install(monkeypatch, [production_source()], {"src_08a19e7ab7": production_permission()})
    calls = []
    feed_fetcher(monkeypatch, calls=calls)

    result = poll()

    assert calls == [f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}"]
    assert result["polled_sources"] == 1
    assert result["new_items_discovered"] == 2
    assert result["duplicates_skipped"] == 0
    assert result["errors"] == 0
    assert result["eligible_sources"] == 1
    assert result["excluded_sources"] == []

    assert [s["id"] for s in fake_db.saved] == ["yt_AAAAAAAAAAA", "yt_BBBBBBBBBBB"]
    first = fake_db.saved[0]
    assert first["original_url"] == "https://www.youtube.com/watch?v=AAAAAAAAAAA"
    assert first["source_id"] == "src_08a19e7ab7"
    assert first["category_id"] == "gta6"
    assert first["rights_status"] == "authorized_platforms"
    assert first["user_id"] is None

    health = fake_db.health[-1]
    assert health["status"] == "healthy"
    assert health["error"] == ""
    assert health["last_polled_at"] and health["last_success_at"]


def test_second_poll_skips_already_discovered_videos(monkeypatch):
    install(monkeypatch, [production_source()], {"src_08a19e7ab7": production_permission()})
    feed_fetcher(monkeypatch)
    poll()
    second = poll()
    assert second["new_items_discovered"] == 0
    assert second["duplicates_skipped"] == 2


def test_channel_feed_http_error_is_recorded_as_error_not_success(monkeypatch):
    fake_db = install(monkeypatch, [production_source()], {"src_08a19e7ab7": production_permission()})
    feed_fetcher(monkeypatch, response=FakeResponse(status_code=503, content=b""))

    result = poll()

    assert result["polled_sources"] == 1
    assert result["errors"] == 1
    assert result["new_items_discovered"] == 0
    health = fake_db.health[-1]
    assert health["status"] == "error"
    assert health["error"].startswith("feed_http_error")
    assert health["last_success_at"] is None
    assert result["source_results"][0]["outcome"] == "error"


def test_handle_url_without_api_key_fails_explicitly(monkeypatch):
    handle_source = production_source(channel_url="https://www.youtube.com/@gta6fan")
    fake_db = install(monkeypatch, [handle_source], {"src_08a19e7ab7": production_permission()})
    calls = []
    feed_fetcher(monkeypatch, calls=calls)
    monkeypatch.setattr(yt.settings, "YOUTUBE_API_KEY", "")

    result = poll()

    assert calls == []
    assert result["polled_sources"] == 1
    assert result["errors"] == 1
    assert fake_db.health[-1]["error"].startswith("channel_id_unresolved")


def test_handle_url_resolves_through_data_api_without_leaking_key(monkeypatch):
    handle_source = production_source(channel_url="https://www.youtube.com/@gta6fan")
    fake_db = install(monkeypatch, [handle_source], {"src_08a19e7ab7": production_permission()})
    feed_fetcher(monkeypatch)
    monkeypatch.setattr(yt.settings, "YOUTUBE_API_KEY", "test-key-123")
    seen = {}

    class FakeAPI:
        status_code = 200

        def json(self):
            return {"items": [{"id": CHANNEL_ID}]}

    def fake_api_get(url, params=None, timeout=None, **kwargs):
        seen.update(params or {})
        return FakeAPI()

    monkeypatch.setattr(yt.requests, "get", fake_api_get)

    result = poll()

    assert seen["forHandle"] == "@gta6fan"
    assert result["new_items_discovered"] == 2
    assert "test-key-123" not in json.dumps(result)
    assert "test-key-123" not in json.dumps(fake_db.health)


def test_unsupported_channel_url_is_reported_not_skipped(monkeypatch):
    bad_source = production_source(channel_url="https://example.com/some-channel")
    fake_db = install(monkeypatch, [bad_source], {"src_08a19e7ab7": production_permission()})
    feed_fetcher(monkeypatch)

    result = poll()

    assert result["polled_sources"] == 1
    assert result["errors"] == 1
    assert fake_db.health[-1]["error"].startswith("unsupported_channel_url")


def test_excluded_sources_are_listed_and_never_fetched(monkeypatch):
    # Inactive sources are removed by the active_only query and never reach selection.
    sources = [production_source(id="src_pending")]
    permissions = {
        "src_pending": production_permission(rights_status=RightsStatus.PENDING_VERIFICATION),
    }
    fake_db = install(monkeypatch, sources, permissions)
    calls = []
    feed_fetcher(monkeypatch, calls=calls)

    result = poll()

    assert calls == []
    assert result["polled_sources"] == 0
    assert result["eligible_sources"] == 0
    assert {"source_id": "src_pending", "reason": "rights_not_authorized:pending_verification"} in result["excluded_sources"]
    assert fake_db.health == []


def test_rss_source_still_polls_through_feed(monkeypatch):
    rss_source = production_source(source_type="approved_rss", channel_url=None,
                                   feed_url="https://example.com/feed.xml", id="src_rss")
    fake_db = install(monkeypatch, [rss_source], {"src_rss": production_permission()})
    rss_xml = b"<rss><channel><title>RSS Creator</title><item><title>Clip</title><link>https://example.com/clip-1</link></item></channel></rss>"

    class RSSResponse:
        status_code = 200
        content = rss_xml

    monkeypatch.setattr(rss_mod, "safe_requests_get", lambda *a, **k: RSSResponse())

    result = poll()

    assert result["polled_sources"] == 1
    assert result["new_items_discovered"] == 1
    assert result["source_results"][0]["route"] == "rss"


def test_selection_log_reports_counts_and_exclusion_reasons(monkeypatch, caplog):
    sources = [production_source(), production_source(id="src_pending")]
    permissions = {
        "src_08a19e7ab7": production_permission(),
        "src_pending": production_permission(rights_status=RightsStatus.PENDING_VERIFICATION),
    }
    install(monkeypatch, sources, permissions)
    feed_fetcher(monkeypatch)

    with caplog.at_level(logging.INFO, logger="gta6_shorts.discovery"):
        poll()

    selection = next(r.getMessage() for r in caplog.records if "discovery.poll.selection" in r.getMessage())
    payload = json.loads(selection.split(" ", 1)[1])
    assert payload["candidate_sources"] == 2
    assert payload["active_sources"] == 2
    assert payload["auto_clip_enabled_sources"] == 2
    assert payload["eligible_sources"] == 1
    assert payload["eligible_source_ids"] == ["src_08a19e7ab7"]
    assert payload["excluded_sources"] == [
        {"source_id": "src_pending", "reason": "rights_not_authorized:pending_verification"},
    ]


# ---------------------------------------------------------------------------
# Adapter parsing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url, expected", [
    (f"https://www.youtube.com/channel/{CHANNEL_ID}", ("channel_id", CHANNEL_ID)),
    ("youtube.com/channel/" + CHANNEL_ID, ("channel_id", CHANNEL_ID)),
    ("https://www.youtube.com/@gta6fan", ("handle", "@gta6fan")),
    ("https://www.youtube.com/@gta6fan/videos", ("handle", "@gta6fan")),
    ("https://www.youtube.com/c/GTA6Clips", ("username", "GTA6Clips")),
    ("https://www.youtube.com/user/GTA6Clips", ("username", "GTA6Clips")),
])
def test_channel_url_forms_are_parsed(url, expected):
    assert yt.parse_channel_url(url) == expected


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=AAAAAAAAAAA",
    "https://example.com/channel/" + CHANNEL_ID,
    "ftp://www.youtube.com/@gta6fan",
    "",
])
def test_non_channel_urls_are_rejected(url):
    with pytest.raises(yt.YouTubeDiscoveryError):
        yt.parse_channel_url(url)


def test_feed_parser_uses_namespaces_and_skips_invalid_ids():
    channel_title, videos = yt.parse_channel_feed(FEED_XML, limit=10)
    assert channel_title == "Fixture Channel"
    assert [v["video_id"] for v in videos] == ["AAAAAAAAAAA", "BBBBBBBBBBB"]
    assert [v["title"] for v in videos] == ["First upload", "Second upload"]


def test_feed_parser_rejects_non_atom_xml():
    from app.services.discovery.youtube_channel_adapter import YouTubeDiscoveryError
    with pytest.raises(YouTubeDiscoveryError) as exc:
        yt.parse_channel_feed(b"<html><body>not a feed</body></html>")
    assert exc.value.code == "feed_parse_error"
