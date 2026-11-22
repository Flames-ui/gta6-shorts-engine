"""
Tests for production Render deployment configuration, worker restart recovery,
publishing automation guards, and health/readiness endpoints.
"""
import os
import yaml
import pytest
from unittest.mock import MagicMock, patch
from app.config import settings
from app.queue import JobQueue
from app.services.supabase_client import db
from app.services.internal_scheduler import internal_scheduler


def test_render_blueprint_structure_and_env_wiring():
    """Verify render.yaml satisfies all production architectural requirements."""
    blueprint_path = os.path.join(os.path.dirname(__file__), "..", "render.yaml")
    assert os.path.exists(blueprint_path), "render.yaml must exist"

    with open(blueprint_path, "r", encoding="utf-8") as f:
        blueprint = yaml.safe_load(f)

    services = blueprint.get("services", [])
    service_map = {s["name"]: s for s in services}

    assert "gta6-shorts-engine" in service_map, "Web service must be defined"
    assert "gta6-shorts-worker" in service_map, "Worker service must be defined"
    assert "gta6-shorts-redis" in service_map, "Redis service must be defined"

    web = service_map["gta6-shorts-engine"]
    worker = service_map["gta6-shorts-worker"]
    redis = service_map["gta6-shorts-redis"]

    # Docker runtime validation
    assert web.get("runtime") == "docker", "Web service must use Docker runtime"
    assert worker.get("runtime") == "docker", "Worker service must use Docker runtime"
    assert worker.get("dockerCommand") == "python -m app.worker", "Worker must run python -m app.worker"

    # Worker instance sizing: video processing must have standard plan or higher
    assert worker.get("plan") in ["standard", "pro", "pro_plus"], "Worker must use standard plan or higher"

    # Health check path
    assert web.get("healthCheckPath") == "/health", "Health check path must be /health"

    # Redis private security
    assert redis.get("ipAllowList") == [], "Redis must have empty ipAllowList (private only)"

    # Verify web env vars
    web_env_keys = {e["key"]: e for e in web.get("envVars", [])}
    assert web_env_keys.get("ENVIRONMENT", {}).get("value") == "production"
    assert web_env_keys.get("FAIL_CLOSED_IN_PRODUCTION", {}).get("value") == "true"
    assert web_env_keys.get("PUBLISHING_AUTOMATION_ENABLED", {}).get("value") == "false"
    assert web_env_keys.get("ENGINE_API_KEY", {}).get("generateValue") is True
    assert web_env_keys.get("TOKEN_ENCRYPTION_KEY", {}).get("generateValue") is True
    assert "SUPABASE_URL" in web_env_keys
    assert "SUPABASE_SERVICE_ROLE_KEY" in web_env_keys

    # Verify worker env vars are synchronized from web service and redis
    worker_env_keys = {e["key"]: e for e in worker.get("envVars", [])}
    assert worker_env_keys.get("PUBLISHING_AUTOMATION_ENABLED", {}).get("value") == "false"

    # Ensure worker receives Redis from the exact same private keyvalue service
    redis_ref = worker_env_keys.get("REDIS_URL", {}).get("fromService", {})
    assert redis_ref.get("name") == "gta6-shorts-redis"
    assert redis_ref.get("property") == "connectionString"

    # Ensure worker receives keys from web service
    for synchronized_key in [
        "ENGINE_API_KEY",
        "TOKEN_ENCRYPTION_KEY",
        "SUPABASE_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_JWT_SECRET",
        "GEMINI_API_KEY",
        "OPENAI_API_KEY",
    ]:
        ref = worker_env_keys.get(synchronized_key, {}).get("fromService", {})
        assert ref.get("name") == "gta6-shorts-engine", f"Worker {synchronized_key} must come from web service"
        assert ref.get("envVarKey") == synchronized_key, f"Worker {synchronized_key} envVarKey must match"


def test_dockerfile_dependencies_and_directories():
    """Verify Dockerfile contains FFmpeg, fonts, OpenCV dependencies, and directories."""
    dockerfile_path = os.path.join(os.path.dirname(__file__), "..", "Dockerfile")
    assert os.path.exists(dockerfile_path)

    with open(dockerfile_path, "r", encoding="utf-8") as f:
        docker_content = f.read()

    assert "ffmpeg" in docker_content, "Dockerfile must install ffmpeg"
    assert "fonts-dejavu-core" in docker_content, "Dockerfile must install subtitle fonts"
    assert "libgl1" in docker_content, "Dockerfile must install OpenGL/OpenCV runtime libraries"
    assert "/app/output/clips" in docker_content, "Dockerfile must create output directory"
    assert "/app/tmp" in docker_content, "Dockerfile must create temporary directory"


def test_publishing_automation_disabled_by_default():
    """Verify that internal scheduler never auto-dispatches when PUBLISHING_AUTOMATION_ENABLED=false."""
    with patch.object(settings, "PUBLISHING_AUTOMATION_ENABLED", False):
        res = internal_scheduler.process_due_publications()
        assert res.get("status") == "disabled"
        assert res.get("dispatched") == 0


def test_worker_restart_recovers_orphaned_processing_jobs():
    """Test that jobs left in 'processing' state when worker crashes are safely recovered."""
    test_queue = JobQueue()
    test_queue.has_redis = False
    test_queue.enqueue_job = MagicMock()
    test_queue.release_job_lock = MagicMock()

    # Create dummy jobs in processing status
    job_recovering = {
        "id": "job_recover_test_01",
        "user_id": "test_user_rec",
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "status": "processing",
        "stage": "multitrack_editing",
        "retry_count": 1,
    }
    job_exhausted = {
        "id": "job_exhaust_test_02",
        "user_id": "test_user_rec",
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "status": "processing",
        "stage": "multitrack_editing",
        "retry_count": 3,
    }

    db.create_job(job_recovering)
    db.create_job(job_exhausted)

    # Force status to 'processing'
    db.update_job_progress(job_recovering["id"], status="processing", retry_count=1)
    db.update_job_progress(job_exhausted["id"], status="processing", retry_count=3)

    # Run recovery
    test_queue.recover_orphaned_jobs()

    # Check recovering job: status should now be queued with incremented retries
    rec_check = db.get_job(job_recovering["id"])
    assert rec_check["status"] == "queued"
    assert rec_check["retry_count"] == 2
    test_queue.enqueue_job.assert_called()

    # Check exhausted job: status should now be failed
    exh_check = db.get_job(job_exhausted["id"])
    assert exh_check["status"] == "failed"
    assert "maximum retry limit reached" in exh_check.get("error", "")

    # Cleanup test jobs
    if not db.has_supabase:
        import sqlite3
        with db.lock, sqlite3.connect(db.sqlite_path) as conn:
            conn.execute("DELETE FROM jobs WHERE id IN (?, ?)", (job_recovering["id"], job_exhausted["id"]))


def test_health_and_readiness_endpoints_behavior(client):
    """Verify /health and /health/live respond correctly."""
    resp_health = client.get("/health")
    assert resp_health.status_code == 200
    data = resp_health.json()
    assert data["status"] in ["healthy", "degraded"]
    assert "redis" in data
    assert "supabase" in data

    resp_live = client.get("/health/live")
    assert resp_live.status_code == 200
    assert resp_live.json().get("status") == "alive"


def test_secrets_are_not_exposed_in_api_responses(client):
    """Verify sensitive environment variables and tokens are never returned in responses."""
    # Test root endpoint
    root_res = client.get("/").json()
    root_str = str(root_res)
    assert settings.ENGINE_API_KEY not in root_str or not settings.ENGINE_API_KEY
    assert settings.SUPABASE_SERVICE_ROLE_KEY not in root_str or not settings.SUPABASE_SERVICE_ROLE_KEY
    assert settings.TOKEN_ENCRYPTION_KEY not in root_str or not settings.TOKEN_ENCRYPTION_KEY

    # Test health endpoint
    health_res = client.get("/health").json()
    health_str = str(health_res)
    assert settings.ENGINE_API_KEY not in health_str or not settings.ENGINE_API_KEY
    assert settings.SUPABASE_SERVICE_ROLE_KEY not in health_str or not settings.SUPABASE_SERVICE_ROLE_KEY
    assert settings.TOKEN_ENCRYPTION_KEY not in health_str or not settings.TOKEN_ENCRYPTION_KEY
