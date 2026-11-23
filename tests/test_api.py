"""HTTP API: authentication, ownership, validation, uploads, job lifecycle and error envelope."""
import io
import os
import time

import pytest
from fastapi.testclient import TestClient

from app import states
from app.config import settings
from app.db import repo
from app.main import app

from .conftest import SECRET, make_token, requires_db, requires_ffmpeg

pytestmark = [requires_db, requires_ffmpeg]
ALICE = {"Authorization": f"Bearer {make_token('user-alice')}"}
BOB = {"Authorization": f"Bearer {make_token('user-bob')}"}
ADMIN = {"Authorization": f"Bearer {make_token('admin-user')}"}
YT_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


@pytest.fixture
def client(clean_db, fake_queue, storage):
    return TestClient(app, raise_server_exceptions=False)


def _upload(client, path, headers=ALICE, name=None):
    with open(path, "rb") as fh:
        return client.post("/api/v1/sources/upload", headers=headers,
                           files={"file": (name or os.path.basename(path), fh, "video/mp4")})


def _ready_source(client, pipeline_video):
    res = _upload(client, pipeline_video)
    assert res.status_code == 201, res.text
    return res.json()


# ------------------------------------------------------------------ health

def test_liveness_needs_no_auth(client):
    res = client.get("/health")
    assert res.status_code == 200 and res.json()["status"] == "ok"


def test_readiness_reports_each_dependency(client):
    res = client.get("/ready")
    assert res.status_code == 200 or res.status_code == 503
    body = res.json()
    assert set(body["checks"]) == {"database", "queue", "storage", "media_tools"}
    assert body["checks"]["database"]["ok"] is True
    assert "password" not in res.text.lower()


# ------------------------------------------------------------------ auth

@pytest.mark.parametrize("headers,code", [
    ({}, "auth_required"),
    ({"Authorization": "Basic abc"}, "auth_required"),
    ({"Authorization": "Bearer not-a-jwt"}, "invalid_token"),
    ({"Authorization": f"Bearer {make_token('u', expires_in=-120)}"}, "token_expired"),
    ({"Authorization": f"Bearer {make_token('u', secret='x' * 40)}"}, "invalid_token"),
    ({"Authorization": f"Bearer {make_token('u', aud='someone-else')}"}, "invalid_token"),
])
def test_protected_endpoints_reject_bad_credentials(client, headers, code):
    res = client.get("/api/v1/jobs", headers=headers)
    assert res.status_code == 401
    assert res.json()["error"]["code"] == code


def test_owner_cannot_be_chosen_by_the_client(client, video_clip):
    src = _ready_source_quick(client)
    res = client.post("/api/v1/jobs", headers=ALICE, json={"source_id": src["id"], "owner_id": "user-bob",
                                                           "clip_count": 5})
    assert res.status_code == 202
    assert repo.get_job(res.json()["id"])["owner_id"] == "user-alice"


def _ready_source_quick(client):
    src = repo.create_source("user-alice", source_type="upload", status="ready", file_key="sources/a/b/o.mp4",
                             sha256=os.urandom(16).hex() + "0" * 32, title="Quick", duration_seconds=90.0)
    return {"id": str(src["id"])}


# ------------------------------------------------------------------ validation

def test_validate_accepts_supported_urls_without_network(client):
    res = client.post("/api/v1/sources/validate", headers=ALICE, json={"url": "https://youtu.be/dQw4w9WgXcQ"})
    assert res.status_code == 200
    body = res.json()
    assert body["video_id"] == "dQw4w9WgXcQ"
    assert body["normalized_url"] == YT_URL
    assert body["retrieval_enabled"] is False


def test_validate_rejects_unsupported_hosts_with_422(client):
    res = client.post("/api/v1/sources/validate", headers=ALICE, json={"url": "https://evil.example.com/x"})
    assert res.status_code == 422
    assert res.json()["error"]["code"] in {"unsupported_host", "invalid_youtube_url", "unsupported_url"}


def test_youtube_registration_is_refused_when_retrieval_is_disabled(client):
    res = client.post("/api/v1/sources/youtube", headers=ALICE, json={"url": YT_URL})
    assert res.status_code == 422
    assert res.json()["error"]["details"]["category"] == "retrieval_disabled"


def test_job_request_validation_is_strict(client):
    src = _ready_source_quick(client)
    cases = [
        {"source_id": src["id"], "clip_count": 3},                       # product fixes five clips
        {"source_id": src["id"], "min_clip_seconds": 60, "max_clip_seconds": 30},
        {"source_id": src["id"], "caption_preset": "comic_sans"},
        {"source_id": src["id"], "language": "english!!"},
        {"source_id": "not-a-uuid"},
    ]
    for body in cases:
        res = client.post("/api/v1/jobs", headers=ALICE, json=body)
        assert res.status_code == 422, body
        assert res.json()["error"]["code"] == "validation_failed"


def test_validation_errors_do_not_echo_request_values(client):
    res = client.post("/api/v1/jobs", headers=ALICE, json={"source_id": "x", "caption_preset": "<script>"})
    assert "<script>" not in res.text


# ------------------------------------------------------------------ uploads

def test_upload_is_stored_privately_and_described(client, pipeline_video):
    body = _ready_source(client, pipeline_video)
    assert body["status"] == "ready"
    assert body["width"] == 1280 and body["height"] == 720 and body["has_audio"] is True
    assert body["duration_seconds"] == pytest.approx(150.0, abs=0.5)
    assert "file_key" not in body and "owner_id" not in body
    assert body["sha256"] and len(body["sha256"]) == 64


def test_identical_upload_returns_the_existing_source(client, pipeline_video):
    first = _ready_source(client, pipeline_video)
    again = _upload(client, pipeline_video)
    assert again.status_code == 200
    assert again.json()["id"] == first["id"]


def test_same_file_for_another_user_is_stored_separately(client, pipeline_video):
    a = _ready_source(client, pipeline_video)
    b = _upload(client, pipeline_video, headers=BOB)
    assert b.status_code == 201 and b.json()["id"] != a["id"]


def test_unsupported_extension_is_refused(client, tmp_path):
    p = tmp_path / "malware.exe"
    p.write_bytes(b"MZ" + b"\x00" * 200)
    res = _upload(client, str(p), name="malware.exe")
    assert res.status_code == 422 and res.json()["error"]["code"] == "unsupported_format"


def test_renamed_non_video_is_refused_by_content(client, tmp_path):
    p = tmp_path / "fake.mp4"
    p.write_bytes(b"This is plain text pretending to be a video. " * 40)
    res = _upload(client, str(p), name="fake.mp4")
    assert res.status_code == 422
    assert res.json()["error"]["code"] in {"content_type_mismatch", "unreadable_media"}


def test_too_short_video_is_refused(client, video_clip):
    res = _upload(client, video_clip)
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "duration_below_minimum"


def test_oversized_upload_is_refused_before_it_is_read(client, pipeline_video, monkeypatch):
    monkeypatch.setattr(settings, "MAX_UPLOAD_BYTES", 1_000_000)
    big = io.BytesIO(b"\x00\x00\x00\x18ftypisom" + os.urandom(2_500_000))
    res = client.post("/api/v1/sources/upload", headers=ALICE,
                      files={"file": ("big.mp4", big, "video/mp4")})
    assert res.status_code == 413
    assert res.json()["error"]["code"] == "upload_too_large"


def test_other_users_sources_are_not_visible(client, pipeline_video):
    src = _ready_source(client, pipeline_video)
    assert client.get(f"/api/v1/sources/{src['id']}", headers=BOB).status_code == 404
    assert client.get(f"/api/v1/sources/{src['id']}", headers=ALICE).status_code == 200
    assert client.get("/api/v1/sources/not-a-uuid", headers=ALICE).status_code == 404


# ------------------------------------------------------------------ jobs

def test_job_creation_persists_and_enqueues_before_responding(client, fake_queue, pipeline_video):
    src = _ready_source(client, pipeline_video)
    res = client.post("/api/v1/jobs", headers=ALICE, json={"source_id": src["id"], "caption_preset": "clean_minimal"})
    assert res.status_code == 202
    body = res.json()
    assert body["status"] == states.QUEUED and body["clip_count"] == 5 and body["aspect_ratio"] == "9:16"
    assert [j for j, _ in fake_queue.enqueued] == [body["id"]]
    assert repo.get_job(body["id"])["status"] == states.QUEUED


def test_idempotency_key_returns_the_same_job(client, fake_queue, pipeline_video):
    src = _ready_source(client, pipeline_video)
    headers = {**ALICE, "Idempotency-Key": "submit-0001-abcdef"}
    first = client.post("/api/v1/jobs", headers=headers, json={"source_id": src["id"]})
    second = client.post("/api/v1/jobs", headers=headers, json={"source_id": src["id"]})
    assert first.status_code == 202 and second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert len(fake_queue.enqueued) == 1


def test_queue_outage_is_reported_and_the_job_is_not_left_queued(client, fake_queue, pipeline_video):
    src = _ready_source(client, pipeline_video)
    fake_queue.fail = True
    res = client.post("/api/v1/jobs", headers=ALICE, json={"source_id": src["id"]})
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "queue_unavailable"
    jobs = repo.list_jobs("user-alice", status=None, limit=10, offset=0)["items"]
    assert jobs[0]["status"] == states.FAILED and jobs[0]["error_code"] == "queue_unavailable"


def test_job_on_unready_source_is_refused(client, fake_queue):
    src = repo.create_source("user-alice", source_type="upload", status="failed", failure_code="x",
                             failure_message="x", title="bad")
    res = client.post("/api/v1/jobs", headers=ALICE, json={"source_id": str(src["id"])})
    assert res.status_code == 409 and res.json()["error"]["code"] == "source_not_ready"


def test_cannot_create_a_job_from_someone_elses_source(client, fake_queue, pipeline_video):
    src = _ready_source(client, pipeline_video)
    res = client.post("/api/v1/jobs", headers=BOB, json={"source_id": src["id"]})
    assert res.status_code == 404


def test_job_list_is_paginated_and_owner_scoped(client, fake_queue, pipeline_video):
    src = _ready_source(client, pipeline_video)
    for i in range(3):
        client.post("/api/v1/jobs", headers={**ALICE, "Idempotency-Key": f"list-key-{i:04d}xyz"},
                    json={"source_id": src["id"]})
    res = client.get("/api/v1/jobs?limit=2&offset=0", headers=ALICE).json()
    assert res["total"] == 3 and len(res["items"]) == 2
    assert client.get("/api/v1/jobs", headers=BOB).json()["total"] == 0
    assert client.get("/api/v1/jobs?status=bogus", headers=ALICE).status_code == 422


def test_job_detail_and_cross_user_access(client, fake_queue, pipeline_video):
    src = _ready_source(client, pipeline_video)
    job_id = client.post("/api/v1/jobs", headers=ALICE, json={"source_id": src["id"]}).json()["id"]
    detail = client.get(f"/api/v1/jobs/{job_id}", headers=ALICE)
    assert detail.status_code == 200
    body = detail.json()
    assert body["clips"] == [] and body["events"][0]["event_type"] == "created"
    assert client.get(f"/api/v1/jobs/{job_id}", headers=BOB).status_code == 404
    assert client.get("/api/v1/jobs/00000000-0000-0000-0000-000000000000", headers=ALICE).status_code == 404


def test_cancel_queued_job_and_refuse_finished_job(client, fake_queue, pipeline_video):
    src = _ready_source(client, pipeline_video)
    job_id = client.post("/api/v1/jobs", headers=ALICE, json={"source_id": src["id"]}).json()["id"]
    res = client.post(f"/api/v1/jobs/{job_id}/cancel", headers=ALICE)
    assert res.status_code == 202 and res.json()["cancel_requested"] is True
    repo.transition_job(job_id, states.VALIDATING)
    repo.transition_job(job_id, states.CANCELLED)
    again = client.post(f"/api/v1/jobs/{job_id}/cancel", headers=ALICE)
    assert again.status_code == 409 and again.json()["error"]["code"] == "job_already_finished"


# ------------------------------------------------------------------ clips and downloads

def _seed_clip(job_id, owner="user-alice", validation="passed", payload=b"\x00\x00\x00\x18ftypisom"):
    from app.storage import get_storage

    key = f"jobs/{job_id}/clips/1/clip.mp4"
    tmp = os.path.join(settings.TEMP_DIR, f"seed-{time.time_ns()}.mp4")
    os.makedirs(settings.TEMP_DIR, exist_ok=True)
    with open(tmp, "wb") as fh:
        fh.write(payload)
    get_storage().put_file(key, tmp, "video/mp4")
    os.remove(tmp)
    clip = repo.create_clip(job_id=job_id, owner_id=owner, highlight_id=None, clip_number=1,
                            title="Great moment", description=None, start_seconds=1.0, end_seconds=21.0,
                            duration_seconds=20.0, render_status="rendered", validation_status="pending")
    return repo.update_clip(str(clip["id"]), storage_key=key, validation_status=validation,
                            validation_details={"width": 1080, "height": 1920})


def _job_for(owner="user-alice"):
    src = repo.create_source(owner, source_type="upload", status="ready", file_key="sources/a/b/s.mp4",
                             sha256=os.urandom(32).hex(), title="S", duration_seconds=90.0)
    job = repo.create_job(owner, str(src["id"]), clip_count=5, aspect_ratio="9:16", caption_preset="bold_creator",
                          min_clip_seconds=15, max_clip_seconds=90)
    return str(job["id"])


def test_only_validated_clips_can_be_downloaded(client):
    job_id = _job_for()
    clip = _seed_clip(job_id, validation="pending")
    res = client.get(f"/api/v1/clips/{clip['id']}/download-url", headers=ALICE)
    assert res.status_code == 409 and res.json()["error"]["code"] == "clip_not_ready"


def test_clip_detail_and_signed_download(client):
    job_id = _job_for()
    clip = _seed_clip(job_id)
    detail = client.get(f"/api/v1/clips/{clip['id']}", headers=ALICE)
    assert detail.status_code == 200
    assert detail.json()["title"] == "Great moment" and detail.json()["validation"]["width"] == 1080
    assert "storage_key" not in detail.text

    link = client.get(f"/api/v1/clips/{clip['id']}/download-url", headers=ALICE).json()
    assert link["filename"].endswith(".mp4") and link["expires_at"] > time.time()
    fetched = client.get(link["url"])
    assert fetched.status_code == 200 and fetched.content.startswith(b"\x00\x00\x00\x18ftyp")

    direct = client.get(f"/api/v1/clips/{clip['id']}/download", headers=ALICE)
    assert direct.status_code == 200 and direct.headers["content-type"].startswith("video/mp4")


def test_clips_are_private_to_their_owner(client):
    clip = _seed_clip(_job_for("user-alice"))
    assert client.get(f"/api/v1/clips/{clip['id']}", headers=BOB).status_code == 404
    assert client.get(f"/api/v1/clips/{clip['id']}/download-url", headers=BOB).status_code == 404
    assert client.get(f"/api/v1/clips/{clip['id']}/download", headers=BOB).status_code == 404


def test_signed_links_expire_and_cannot_be_forged(client):
    clip = _seed_clip(_job_for())
    link = client.get(f"/api/v1/clips/{clip['id']}/download-url", headers=ALICE).json()["url"]
    token, sig = link.split("/files/")[1].split("?sig=")
    assert client.get(f"/api/v1/files/{token}?sig={'0' * len(sig)}").status_code == 403
    from app.storage import get_storage

    expired_url, _ = get_storage().signed_url(clip["storage_key"], ttl_seconds=-30, filename="x.mp4")
    et, es = expired_url.split("/files/")[1].split("?sig=")
    res = client.get(f"/api/v1/files/{et}?sig={es}")
    assert res.status_code == 410 and res.json()["error"]["code"] == "link_expired"


def test_caption_downloads_are_available_for_validated_clips(client):
    from app.storage import get_storage

    job_id = _job_for()
    clip = _seed_clip(job_id)
    tmp = os.path.join(settings.TEMP_DIR, "cap.srt")
    os.makedirs(settings.TEMP_DIR, exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("1\n00:00:00,000 --> 00:00:01,000\nHello\n")
    get_storage().put_file(f"jobs/{job_id}/clips/1/captions.srt", tmp, "text/plain")
    res = client.get(f"/api/v1/clips/{clip['id']}/captions/srt", headers=ALICE)
    assert res.status_code == 200 and "Hello" in res.text
    assert client.get(f"/api/v1/clips/{clip['id']}/captions/exe", headers=ALICE).status_code == 422


# ------------------------------------------------------------------ usage, admin, misc

def test_usage_summarises_the_callers_jobs(client, fake_queue, pipeline_video):
    src = _ready_source(client, pipeline_video)
    client.post("/api/v1/jobs", headers=ALICE, json={"source_id": src["id"]})
    body = client.get("/api/v1/usage?days=7", headers=ALICE).json()
    assert body["jobs"].get("queued") == 1
    assert "period_start" in body and "metrics" in body
    assert client.get("/api/v1/usage?days=0", headers=ALICE).status_code == 422


def test_diagnostics_are_admin_only_and_secret_free(client):
    assert client.get("/api/v1/admin/diagnostics", headers=ALICE).status_code == 403
    assert client.get("/api/v1/admin/diagnostics").status_code == 403
    admin = client.get("/api/v1/admin/diagnostics", headers=ADMIN)
    assert admin.status_code == 200
    assert client.get("/api/v1/admin/diagnostics", headers={"X-Admin-Key": settings.ADMIN_API_KEY}).status_code == 200
    text = admin.text
    assert settings.ADMIN_API_KEY not in text and SECRET not in text
    assert "GEMINI" not in text and "password" not in text.lower()


def test_rate_limit_returns_429_with_retry_hint(client, monkeypatch):
    from app import ratelimit

    monkeypatch.setattr(ratelimit.limiter, "limit", 3)
    ratelimit.limiter._hits.clear()
    statuses = [client.get("/api/v1/usage", headers=BOB).status_code for _ in range(4)]
    assert statuses == [200, 200, 200, 429]
    body = client.get("/api/v1/usage", headers=BOB).json()
    assert body["error"]["code"] == "rate_limited"
    assert body["error"]["details"]["retry_after_seconds"] == 60
    ratelimit.limiter._hits.clear()


def test_error_envelope_is_consistent_for_unknown_routes(client):
    res = client.get("/api/v1/does-not-exist", headers=ALICE)
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"
    assert "traceback" not in res.text.lower()


def test_responses_carry_request_ids_and_safe_headers(client):
    res = client.get("/health", headers={"X-Request-ID": "trace-123"})
    assert res.headers["X-Request-ID"] == "trace-123"
    assert res.headers["X-Content-Type-Options"] == "nosniff"


def test_cors_allows_only_configured_origins(client):
    ok = client.options("/api/v1/usage", headers={"Origin": "http://localhost:5173",
                                                  "Access-Control-Request-Method": "GET"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    bad = client.options("/api/v1/usage", headers={"Origin": "https://evil.example.com",
                                                   "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in bad.headers
