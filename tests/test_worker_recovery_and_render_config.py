"""
Tests for production Render single-instance deployment, combined process supervision,
worker restart recovery, publishing automation guards, and health/readiness endpoints.
"""
import os
import subprocess
import yaml
import pytest
from unittest.mock import MagicMock, patch
from app.config import settings
from app.queue import JobQueue
from app.services.supabase_client import db
from app.services.internal_scheduler import internal_scheduler


def test_render_blueprint_structure_and_combined_env_wiring():
    """Verify render.yaml runs API and worker together inside the web service without a separate paid worker."""
    blueprint_path = os.path.join(os.path.dirname(__file__), "..", "render.yaml")
    assert os.path.exists(blueprint_path), "render.yaml must exist"

    with open(blueprint_path, "r", encoding="utf-8") as f:
        blueprint = yaml.safe_load(f)

    services = blueprint.get("services", [])
    service_map = {s["name"]: s for s in services}

    # Verify no separate worker service exists (avoiding duplicate/paid background worker)
    assert "gta6-shorts-engine" in service_map, "Web service must be defined"
    assert "gta6-shorts-redis" in service_map, "Redis service must be defined"
    assert "gta6-shorts-worker" not in service_map, "Separate background worker must NOT be present in single-instance deployment"

    web = service_map["gta6-shorts-engine"]
    redis = service_map["gta6-shorts-redis"]

    # Web service runs the combined Docker container
    assert web.get("runtime") == "docker", "Web service must use Docker runtime"
    assert web.get("plan") in ["standard", "pro", "pro_plus"], "Combined service must use standard plan or higher"
    assert web.get("healthCheckPath") == "/health", "Health check path must be /health"

    # Redis private security
    assert redis.get("ipAllowList") == [], "Redis must have empty ipAllowList (private only)"

    # Verify web env vars contain both API and worker configurations
    web_env_keys = {e["key"]: e for e in web.get("envVars", [])}
    assert web_env_keys.get("ENVIRONMENT", {}).get("value") == "production"
    assert web_env_keys.get("FAIL_CLOSED_IN_PRODUCTION", {}).get("value") == "true"
    assert web_env_keys.get("PUBLISHING_AUTOMATION_ENABLED", {}).get("value") == "false"
    assert web_env_keys.get("ENGINE_API_KEY", {}).get("generateValue") is True
    assert web_env_keys.get("TOKEN_ENCRYPTION_KEY", {}).get("generateValue") is True
    assert "SUPABASE_URL" in web_env_keys
    assert "SUPABASE_SERVICE_ROLE_KEY" in web_env_keys

    # Verify Redis wiring from keyvalue service
    redis_ref = web_env_keys.get("REDIS_URL", {}).get("fromService", {})
    assert redis_ref.get("name") == "gta6-shorts-redis"
    assert redis_ref.get("property") == "connectionString"

    # Verify queue and processing settings for combined worker
    assert web_env_keys.get("QUEUE_NAME", {}).get("value") == "viral-shorts"
    assert web_env_keys.get("MAX_CONCURRENT_JOBS", {}).get("value") == "2"
    assert web_env_keys.get("LOCAL_WHISPER_MODEL", {}).get("value") == "base"
    assert web_env_keys.get("DAILY_TARGET_MIN", {}).get("value") == "15"
    assert web_env_keys.get("DAILY_TARGET_MAX", {}).get("value") == "20"


def test_dockerfile_and_start_combined_script():
    """Verify Dockerfile uses start_combined.sh and contains required runtime libraries."""
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    dockerfile_path = os.path.join(repo_root, "Dockerfile")
    start_sh_path = os.path.join(repo_root, "app", "start_combined.sh")

    assert os.path.exists(dockerfile_path)
    assert os.path.exists(start_sh_path)

    # Verify executable bit
    assert os.access(start_sh_path, os.X_OK), "app/start_combined.sh must be executable"

    # Verify shell syntax via bash -n
    syntax_check = subprocess.run(["bash", "-n", start_sh_path], capture_output=True, text=True)
    assert syntax_check.returncode == 0, f"Shell syntax error in start_combined.sh: {syntax_check.stderr}"

    # Verify script content
    with open(start_sh_path, "r", encoding="utf-8") as f:
        sh_content = f.read()

    assert "python -m app.worker" in sh_content, "Supervisor must launch worker"
    assert "uvicorn app.main:app" in sh_content, "Supervisor must launch uvicorn"
    assert "trap" in sh_content, "Supervisor must configure signal traps"
    assert "wait -n" in sh_content, "Supervisor must supervise both processes via wait -n"

    # Verify Dockerfile CMD
    with open(dockerfile_path, "r", encoding="utf-8") as f:
        docker_content = f.read()

    assert 'CMD ["bash", "app/start_combined.sh"]' in docker_content, "Dockerfile must use start_combined.sh as CMD"
    assert "ffmpeg" in docker_content, "Dockerfile must install ffmpeg"
    assert "fonts-dejavu-core" in docker_content, "Dockerfile must install subtitle fonts"
    assert "libgl1" in docker_content, "Dockerfile must install OpenGL/OpenCV runtime libraries"


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
