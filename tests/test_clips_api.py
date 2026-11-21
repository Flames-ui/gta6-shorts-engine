"""Test clips retrieval and publishing workflow endpoints."""
from app.services.supabase_client import db


def test_clip_not_found(client, auth_headers):
    res = client.get("/api/v1/clips/non_existent_clip", headers=auth_headers)
    assert res.status_code == 404


def test_clip_retrieval_and_publish_approval(client, auth_headers):
    clip_id = "clip_test_123"
    job_id = "job_test_parent"
    db.create_job({
        "id": job_id,
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "status": "completed",
    })
    db.create_clip({
        "id": clip_id,
        "job_id": job_id,
        "title": "GTA 6 Vice City Stunt",
        "hook_sentence": "You won't believe this jump!",
        "score": 95,
        "start_time": 10.0,
        "end_time": 45.0,
        "duration": 35.0,
        "storage_url": "https://supabase.co/storage/v1/object/public/shorts/test.mp4",
        "aspect_ratio": "9:16",
        "suggested_tags": ["GTA6", "Shorts", "ViceCity"],
        "publishing_status": "draft",
    })

    # Retrieve clip
    res = client.get(f"/api/v1/clips/{clip_id}", headers=auth_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == clip_id
    assert data["title"] == "GTA 6 Vice City Stunt"
    assert data["score"] == 95

    # 1. Attempt to publish while still in draft status (unapproved)
    unapproved_res = client.post(f"/api/v1/clips/{clip_id}/publish", headers=auth_headers)
    assert unapproved_res.status_code == 400
    assert "Clip must be approved" in unapproved_res.json()["detail"]

    # 2. Approve clip via approval endpoint
    approve_res = client.post(
        f"/api/v1/clips/{clip_id}/approve",
        json={"approved_for_publishing": True, "custom_title": "Approved GTA 6 Stunt"},
        headers=auth_headers,
    )
    assert approve_res.status_code == 200
    assert approve_res.json()["publishing_status"] == "approved"
    assert approve_res.json()["title"] == "Approved GTA 6 Stunt"

    # 3. Publish approved clip (returns 501 when YouTube OAuth credentials are not set)
    publish_res = client.post(f"/api/v1/clips/{clip_id}/publish", headers=auth_headers)
    assert publish_res.status_code in (200, 501)
    if publish_res.status_code == 501:
        assert "YouTube Data API OAuth is not configured" in publish_res.json()["detail"]
