"""Test configuration. Environment is set before any app module is imported.

Database tests need TEST_DATABASE_URL pointing at a PostgreSQL server the test user can create
databases on. Each session creates a fresh database, applies every migration, and drops it.
Media tests need `ffmpeg` and `ffprobe` on PATH.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
import uuid

_TMP = tempfile.mkdtemp(prefix="studio-tests-")
SECRET = "test-secret-0123456789abcdef0123456789ab"
os.environ.update({
    "ENVIRONMENT": "test",
    "LOG_LEVEL": "WARNING",
    "AUTH_JWT_SECRET": SECRET,
    "AUTH_JWT_AUDIENCE": "authenticated",
    "ADMIN_API_KEY": "test-admin-key-0123456789abcdef",
    "ADMIN_USER_IDS": "admin-user",
    "STORAGE_BACKEND": "local",
    "STORAGE_LOCAL_ROOT": os.path.join(_TMP, "storage"),
    "TEMP_DIR": os.path.join(_TMP, "tmp"),
    "YOUTUBE_RETRIEVAL_ENABLED": "false",
    "AI_PROVIDER": "fake",
    "REDIS_URL": "",
    "DATABASE_URL": "postgresql://unused:unused@127.0.0.1:1/unused",
    "CORS_ORIGINS": "http://localhost:5173",
    "MAX_JOB_ATTEMPTS": "3",
})

import pytest  # noqa: E402

from tests.helpers import make_video  # noqa: E402

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "")
requires_db = pytest.mark.skipif(not TEST_DB_URL, reason="TEST_DATABASE_URL not set")
requires_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                                     reason="ffmpeg/ffprobe not installed")


def make_token(sub: str, *, expires_in: int = 3600, secret: str = SECRET, aud: str = "authenticated",
               extra: dict | None = None) -> str:
    import jwt

    claims = {"sub": sub, "aud": aud, "exp": int(time.time()) + expires_in}
    claims.update(extra or {})
    return jwt.encode(claims, secret, algorithm="HS256")


class FakeRedis:
    """Only the SET NX/EX behaviour the worker relies on."""

    def __init__(self):
        self.keys = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.keys:
            return None
        self.keys[key] = value
        return True


class FakeQueue:
    """In-memory stand-in for the Redis/RQ queue."""

    def __init__(self):
        self.enqueued = []
        self.locks = {}
        self.fail = False
        self.redis = FakeRedis()

    def ping(self):
        return True

    def depth(self):
        return len(self.enqueued)

    def enqueue(self, job_id, delay_seconds=0):
        from app.errors import ServiceUnavailable

        if self.fail:
            raise ServiceUnavailable("The processing queue is unavailable. Please retry shortly.",
                                     code="queue_unavailable")
        self.enqueued.append((job_id, delay_seconds))

    def acquire_lock(self, job_id):
        if job_id in self.locks:
            return None
        token = uuid.uuid4().hex
        self.locks[job_id] = token
        return token

    def release_lock(self, job_id, token):
        if self.locks.get(job_id) == token:
            del self.locks[job_id]

    def force_release_lock(self, job_id):
        self.locks.pop(job_id, None)


@pytest.fixture
def fake_queue(monkeypatch):
    import app.queue as queue_mod

    q = FakeQueue()
    monkeypatch.setattr(queue_mod, "_queue", q)
    return q


@pytest.fixture(scope="session")
def media_dir():
    path = os.path.join(_TMP, "media")
    os.makedirs(path, exist_ok=True)
    return path


@pytest.fixture(scope="session")
def video_clip(media_dir):
    """12 s, 1280x720, with audio."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    return make_video(os.path.join(media_dir, "short-720p.mp4"), duration=12)


@pytest.fixture(scope="session")
def silent_video(media_dir):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    return make_video(os.path.join(media_dir, "silent-720p.mp4"), duration=8, audio=False)


@pytest.fixture(scope="session")
def pipeline_video(media_dir):
    """150 s, 1280x720 with audio: long enough for five 15-30 s moments."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    return make_video(os.path.join(media_dir, "pipeline-150s.mp4"), duration=150)


@pytest.fixture(scope="session")
def database_url():
    if not TEST_DB_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    import psycopg
    from urllib.parse import urlsplit, urlunsplit

    from app.config import settings
    from app.migrate import migrate

    name = f"studio_pytest_{uuid.uuid4().hex[:10]}"
    parts = urlsplit(TEST_DB_URL)
    admin_url = urlunsplit((parts.scheme, parts.netloc, "/postgres", "", ""))
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}" ENCODING \'UTF8\' TEMPLATE template0')
    url = urlunsplit((parts.scheme, parts.netloc, f"/{name}", "", ""))
    settings.DATABASE_URL = url
    migrate(url)
    yield url
    from app.db import close_pool

    close_pool()
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def clean_db(database_url):
    """Empty every application table before the test (keeps the schema)."""
    from app.db import connection

    with connection() as conn:
        conn.execute("TRUNCATE worker_heartbeats, processing_usage, clip_captions, generated_clips, "
                     "detected_highlights, job_events, editing_jobs, video_sources RESTART IDENTITY CASCADE")
    return database_url


@pytest.fixture
def storage(tmp_path, monkeypatch):
    from app.config import settings
    from app.storage import reset_storage

    monkeypatch.setattr(settings, "STORAGE_LOCAL_ROOT", str(tmp_path / "storage"))
    reset_storage()
    yield
    reset_storage()
