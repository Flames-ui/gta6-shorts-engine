import pytest
from app.services.supabase_client import db

def test_frontend_dashboard_serves_html(client):
    res = client.get("/")
    assert res.status_code == 200
    html = res.text
    assert "GTA 6 Shorts Studio" in html
    assert "app-sidebar" in html
    # Check all 6 tracks
    assert "track-row-video" in html
    assert "track-row-audio" in html
    assert "track-row-captions" in html
    assert "track-row-hook" in html
    assert "track-row-framing" in html
    assert "track-row-outro" in html

    # Check navigation tabs
    assert 'data-tab="studio"' in html
    assert 'data-tab="autonomous"' in html
    assert 'data-tab="registry"' in html
    assert 'data-tab="outro"' in html
    assert 'data-tab="library"' in html
    assert 'data-tab="publishing"' in html
    assert 'data-tab="health"' in html

    # Check modals exist
    assert 'id="login-modal"' in html
    assert 'id="add-source-modal"' in html
    assert 'id="schedule-clip-modal"' in html
    assert 'id="compliance-modal"' in html

    # Check inspector panels exist
    assert 'id="inspector-panel-hook"' in html
    assert 'id="inspector-panel-captions"' in html
    assert 'id="inspector-panel-framing"' in html
    assert 'id="inspector-panel-outro"' in html
    assert 'id="inspector-panel-preflight"' in html

def test_static_assets_serve(client):
    css_res = client.get("/static/styles.css")
    assert css_res.status_code == 200
    assert "--bg-deep" in css_res.text
    assert "--accent-pink" in css_res.text

    js_res = client.get("/static/app.js")
    assert js_res.status_code == 200
    assert "GTA 6 Shorts Studio" in js_res.text
    assert "showToast" in js_res.text

def test_studio_events_endpoint(client, auth_headers):
    res = client.get("/api/v1/studio/events", headers=auth_headers)
    assert res.status_code == 200
    data = res.json()
    assert "events" in data
    assert "total" in data

def test_studio_timeline_api_lifecycle(client, auth_headers):
    clip_id = "test_studio_clip_001"
    db.create_clip({
        "id": clip_id,
        "title": "Epic Vice City Pursuit",
        "hook_text": "GTA 6 POLICE CHASE",
        "duration": 25.0,
        "status": "ready"
    })

    # 1. Fetch timeline
    res = client.get(f"/api/v1/studio/clips/{clip_id}/timeline", headers=auth_headers)
    assert res.status_code == 200
    tl = res.json()
    assert tl["clip_id"] == clip_id
    assert "hooks_track" in tl
    assert "captions_track" in tl
    assert "reframing_track" in tl
    assert "outro_track" in tl
    assert "video_track" in tl
    assert "audio_track" in tl

    # 2. Update timeline
    updated_payload = {
        "clip_id": clip_id,
        "duration": 25.0,
        "title": "Epic Vice City Pursuit",
        "video_track": {"start_time": 0.0, "end_time": 25.0, "speed": 1.0, "volume": 1.0},
        "audio_track": {"ducking": False, "bg_music": None, "bg_volume": 0.0, "normalize_loudness": True},
        "hooks_track": {
            "text": "UNBELIEVABLE DRIFT",
            "start": 0.0,
            "end": 2.5,
            "preset": "neon_magenta",
            "color": "#FF2A85"
        },
        "captions_track": [
            {"start": 0.5, "end": 2.0, "text": "Look at that turn!", "highlight": False},
            {"start": 2.1, "end": 4.0, "text": "Helicopter incoming!", "highlight": False}
        ],
        "reframing_track": [
            {"time": 1.5, "zoom": 1.1, "crop_x_percent": 60.0, "crop_y_percent": 50.0}
        ],
        "outro_track": {
            "enabled": True,
            "promo_text": "Follow for more GTA 6 stunts",
            "destination_url": "https://youtube.com/@gta6shorts",
            "duration": 4.0
        }
    }
    post_res = client.post(
        f"/api/v1/studio/clips/{clip_id}/timeline",
        json=updated_payload,
        headers=auth_headers
    )
    assert post_res.status_code == 200
    saved = post_res.json()
    assert saved["hooks_track"]["text"] == "UNBELIEVABLE DRIFT"
    assert len(saved["captions_track"]) == 2
    assert saved["reframing_track"][0]["crop_x_percent"] == 60.0
    assert saved["outro_track"]["enabled"] is True

    # 3. Verify get returns updated timeline
    get_res2 = client.get(f"/api/v1/studio/clips/{clip_id}/timeline", headers=auth_headers)
    assert get_res2.status_code == 200
    assert get_res2.json()["hooks_track"]["text"] == "UNBELIEVABLE DRIFT"
