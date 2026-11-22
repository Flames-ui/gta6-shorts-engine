"""Metadata resolution for discovered items: production item shape, batching and explicit failure reasons.

The YouTube Data API is replaced with a fake, so these tests never contact YouTube.
"""
import pytest

from app.services.discovery import media_pipeline
from app.services.discovery.youtube_metadata import YouTubeMetadataError, availability_for

PRODUCTION_ITEM = {
    "id": "yt_Ve2M5i_DSKI",
    "source_id": "src_08a19e7ab7",
    "user_id": None,
    "title": "GTA 6 Gameplay Walkthrough FULL GAME [4K PS5 PRO] No Commentary",
    "original_url": "https://www.youtube.com/watch?v=Ve2M5i_DSKI",
    "media_url": None,
    "creator_name": "Shirrako",
    "category_id": "gta6",
    "rights_status": "authorized_platforms",
    "duration_seconds": 0.0,
    "is_processed": False,
    "metadata_status": None,
    "media_status": None,
    "analysis_status": None,
}


class FakeDB:
    def __init__(self, items=()):
        self.items = {i["id"]: dict(i) for i in items}

    def get_discovered_content(self, item_id):
        item = self.items.get(item_id)
        return dict(item) if item else None

    def update_discovered_content(self, item_id, updates):
        self.items[item_id].update(updates)
        return dict(self.items[item_id])

    def list_discovered_content(self, user_id=None, is_processed=None, limit=20):
        rows = [dict(i) for i in self.items.values()
                if is_processed is None or bool(i.get("is_processed")) == is_processed]
        return rows[:limit]


def video_meta(video_id, privacy="public", upload="processed", duration=303.0):
    meta = {
        "youtube_video_id": video_id,
        "title": "GTA 6 Walkthrough",
        "channel_title": "Shirrako",
        "published_at": "2026-11-20T10:00:00Z",
        "thumbnail_url": f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg",
        "duration_seconds": duration,
        "privacy_status": privacy,
        "upload_status": upload,
    }
    meta["availability"] = availability_for(meta)
    return meta


def install_fake_youtube(monkeypatch, store, error_on_call=None):
    """Serve metadata from `store`. `error_on_call` maps a 1-based call number to an error code."""
    calls = []

    def fake_fetch(video_ids, api_key=None, timeout=10):
        calls.append(list(video_ids))
        code = (error_on_call or {}).get(len(calls))
        if code:
            raise YouTubeMetadataError(code, "fake failure")
        return {vid: store[vid] for vid in video_ids if vid in store}

    monkeypatch.setattr(media_pipeline, "fetch_video_metadata", fake_fetch)
    return calls


def install_db(monkeypatch, items):
    fake = FakeDB(items)
    monkeypatch.setattr(media_pipeline, "db", fake)
    return fake


def test_production_item_without_user_id_resolves_and_keeps_rights_and_processing_state(monkeypatch):
    store = {"Ve2M5i_DSKI": video_meta("Ve2M5i_DSKI")}
    install_fake_youtube(monkeypatch, store)
    fake_db = install_db(monkeypatch, [PRODUCTION_ITEM])

    result = media_pipeline.resolve_metadata("yt_Ve2M5i_DSKI", api_key="test-key")

    item = fake_db.items["yt_Ve2M5i_DSKI"]
    assert result["metadata_status"] == "resolved"
    assert item["duration_seconds"] == 303.0
    assert item["availability"] == "available"
    assert item["channel_title"] == "Shirrako"
    assert item["youtube_video_id"] == "Ve2M5i_DSKI"
    assert item["rights_status"] == "authorized_platforms"
    assert item["is_processed"] is False
    assert item["media_url"] is None


def test_missing_api_key_is_an_explicit_failure_and_duration_is_not_invented(monkeypatch):
    install_fake_youtube(monkeypatch, {}, error_on_call={1: "youtube_api_key_missing"})
    fake_db = install_db(monkeypatch, [PRODUCTION_ITEM])

    summary = media_pipeline.resolve_metadata_batch(["yt_Ve2M5i_DSKI"])

    item = fake_db.items["yt_Ve2M5i_DSKI"]
    assert item["metadata_status"] == "failed"
    assert item["metadata_error"] == "youtube_api_key_missing"
    assert item["duration_seconds"] == 0.0
    assert item["is_processed"] is False
    assert summary["failure_codes"] == {"youtube_api_key_missing": 1}


@pytest.mark.parametrize("privacy,upload,expected", [
    ("private", "processed", "video_not_available:private"),
    ("public", "rejected", "video_not_available:unavailable"),
])
def test_unavailable_videos_are_never_marked_resolved(monkeypatch, privacy, upload, expected):
    store = {"Ve2M5i_DSKI": video_meta("Ve2M5i_DSKI", privacy=privacy, upload=upload)}
    install_fake_youtube(monkeypatch, store)
    fake_db = install_db(monkeypatch, [PRODUCTION_ITEM])

    media_pipeline.resolve_metadata_batch(["yt_Ve2M5i_DSKI"])

    assert fake_db.items["yt_Ve2M5i_DSKI"]["metadata_status"] == "failed"
    assert fake_db.items["yt_Ve2M5i_DSKI"]["metadata_error"] == expected


def test_video_missing_from_api_response_is_video_not_found(monkeypatch):
    install_fake_youtube(monkeypatch, {})
    fake_db = install_db(monkeypatch, [PRODUCTION_ITEM])

    media_pipeline.resolve_metadata_batch(["yt_Ve2M5i_DSKI"])

    assert fake_db.items["yt_Ve2M5i_DSKI"]["metadata_error"] == "video_not_found"


def test_non_youtube_url_fails_without_any_api_call(monkeypatch):
    item = {**PRODUCTION_ITEM, "id": "yt_vimeo", "original_url": "https://vimeo.com/123456"}
    calls = install_fake_youtube(monkeypatch, {})
    fake_db = install_db(monkeypatch, [item])

    media_pipeline.resolve_metadata_batch(["yt_vimeo"])

    assert calls == []
    assert fake_db.items["yt_vimeo"]["metadata_error"] == "not_a_youtube_video"


def test_batch_makes_one_call_per_fifty_distinct_videos(monkeypatch):
    items, store = [], {}
    for i in range(120):
        vid = f"vid{i:08d}"
        store[vid] = video_meta(vid)
        items.append({**PRODUCTION_ITEM, "id": f"yt_{i}", "original_url": f"https://www.youtube.com/watch?v={vid}"})
    # A duplicate record for an already-listed video must not add a call or an ID.
    items.append({**PRODUCTION_ITEM, "id": "yt_dup", "original_url": "https://www.youtube.com/watch?v=vid00000000"})
    calls = install_fake_youtube(monkeypatch, store)
    fake_db = install_db(monkeypatch, items)

    summary = media_pipeline.resolve_metadata_batch([i["id"] for i in items])

    assert [len(c) for c in calls] == [50, 50, 20]
    assert summary["api_call_attempts"] == 3
    assert summary["resolved"] == 121
    assert all(fake_db.items[i["id"]]["metadata_status"] == "resolved" for i in items)


def test_one_failed_call_fails_only_its_own_items(monkeypatch):
    items, store = [], {}
    for i in range(60):
        vid = f"vid{i:08d}"
        store[vid] = video_meta(vid)
        items.append({**PRODUCTION_ITEM, "id": f"yt_{i}", "original_url": f"https://www.youtube.com/watch?v={vid}"})
    install_fake_youtube(monkeypatch, store, error_on_call={1: "metadata_http_error"})
    fake_db = install_db(monkeypatch, items)

    summary = media_pipeline.resolve_metadata_batch([i["id"] for i in items])

    assert fake_db.items["yt_0"]["metadata_error"] == "metadata_http_error"
    assert fake_db.items["yt_49"]["metadata_error"] == "metadata_http_error"
    assert fake_db.items["yt_50"]["metadata_status"] == "resolved"
    assert summary["resolved"] == 10 and summary["failed"] == 50


def test_transient_failure_is_selected_again_and_then_resolves(monkeypatch):
    store = {"Ve2M5i_DSKI": video_meta("Ve2M5i_DSKI")}
    install_fake_youtube(monkeypatch, store, error_on_call={1: "metadata_request_failed"})
    fake_db = install_db(monkeypatch, [PRODUCTION_ITEM])

    media_pipeline.resolve_metadata_batch(["yt_Ve2M5i_DSKI"])
    assert media_pipeline.unresolved_item_ids(fake_db.list_discovered_content()) == ["yt_Ve2M5i_DSKI"]

    install_fake_youtube(monkeypatch, store)
    media_pipeline.resolve_metadata_batch(media_pipeline.unresolved_item_ids(fake_db.list_discovered_content()))
    assert fake_db.items["yt_Ve2M5i_DSKI"]["metadata_status"] == "resolved"
    assert media_pipeline.unresolved_item_ids(fake_db.list_discovered_content()) == []


def test_resolved_items_are_not_selected_again():
    items = [
        {"id": "a", "metadata_status": "resolved"},
        {"id": "b", "metadata_status": None},
        {"id": "c", "metadata_status": "failed"},
    ]
    assert media_pipeline.unresolved_item_ids(items) == ["b", "c"]
