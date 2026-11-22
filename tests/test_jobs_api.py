"""Test job lifecycle, validation, auth, and state transitions."""

def test_api_key_auth(client):
    # Without auth header
    res = client.get("/api/v1/status")
    assert res.status_code == 401

    # With invalid key
    res = client.get("/api/v1/status", headers={"X-API-Key": "wrong-key"})
    assert res.status_code == 401

    # With valid key
    res = client.get("/api/v1/status", headers={"X-API-Key": "test-secret-key-12345"})
    assert res.status_code == 200
    assert res.json()["status"] == "operational"


def test_job_create_and_lifecycle(client, auth_headers):
    # 1. Create a job
    valid_url = "https://www.youtube.com/watch?v=QdBZY2fkU-0"
    payload = {
        "url": valid_url,
        "num_clips": 3,
        "aspect_ratio": "9:16",
        "mode": "local",
        "llm_provider": "gemini",
        "gta_preset": True,
        "force": True,
    }
    create_res = client.post("/api/v1/jobs", json=payload, headers=auth_headers)
    assert create_res.status_code == 201
    job = create_res.json()
    job_id = job["id"]
    assert job["url"] == valid_url
    assert job["status"] in ("queued", "processing", "completed", "failed")
    assert job["num_clips"] == 3

    # 2. Duplicate detection without force
    dup_payload = dict(payload, force=False)
    dup_res = client.post("/api/v1/jobs", json=dup_payload, headers=auth_headers)
    current_job = client.get(f"/api/v1/jobs/{job_id}", headers=auth_headers).json()
    if current_job["status"] in ("queued", "processing"):
        assert dup_res.status_code == 409

    # 3. Retrieve job
    get_res = client.get(f"/api/v1/jobs/{job_id}", headers=auth_headers)
    assert get_res.status_code == 200
    assert get_res.json()["id"] == job_id

    # 4. List jobs
    list_res = client.get("/api/v1/jobs?limit=10&offset=0", headers=auth_headers)
    assert list_res.status_code == 200
    data = list_res.json()
    assert data["total"] >= 1
    assert any(item["id"] == job_id for item in data["items"])

    # 5. Cancel job
    cancel_res = client.post(f"/api/v1/jobs/{job_id}/cancel", json={"reason": "Testing cancel"}, headers=auth_headers)
    if get_res.json()["status"] not in ("completed", "failed"):
        assert cancel_res.status_code == 200
        assert cancel_res.json()["status"] == "cancelled"

    # 6. Retry job
    retry_res = client.post(f"/api/v1/jobs/{job_id}/retry", json={"reason": "Testing retry"}, headers=auth_headers)
    assert retry_res.status_code == 200
    assert retry_res.json()["retry_count"] >= 1


def test_job_not_found(client, auth_headers):
    res = client.get("/api/v1/jobs/non_existent_id_12345", headers=auth_headers)
    assert res.status_code == 404
