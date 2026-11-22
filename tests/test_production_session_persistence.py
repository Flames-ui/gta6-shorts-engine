"""Tests for persistent session storage, dual-persistence, and production startup guards."""
import os
import shutil
import sqlite3
import pytest
from unittest.mock import MagicMock
from app.config import settings
from app.services.supabase_client import DatabaseClient


def test_session_db_parent_directory_auto_created(tmp_path):
    """Verify that parent directories for session storage are created automatically."""
    custom_dir = tmp_path / "nested" / "storage" / "sessions"
    db_file = str(custom_dir / "sessions.sqlite3")

    orig_path = settings.SESSION_DB_PATH
    try:
        settings.SESSION_DB_PATH = db_file
        db_instance = DatabaseClient()
        assert os.path.exists(str(custom_dir))
        assert os.path.exists(db_file)
        assert db_instance.session_db_path == db_file

        # Verify table exists in SQLite
        with sqlite3.connect(db_file) as conn:
            cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='dashboard_sessions'")
            assert cur.fetchone() is not None
    finally:
        settings.SESSION_DB_PATH = orig_path


def test_session_lifecycle_with_sqlite_and_supabase_sync(tmp_path):
    """Verify create_session, get_session, and delete_session work seamlessly."""
    db_file = str(tmp_path / "lifecycle_sessions.sqlite3")
    orig_path = settings.SESSION_DB_PATH
    try:
        settings.SESSION_DB_PATH = db_file
        db_instance = DatabaseClient()

        # Create session
        session_data = {
            "session_id": "test_token_abc_123",
            "user_id": "system_admin",
            "role": "admin",
            "is_admin": True,
            "csrf_token": "csrf_sample_token",
            "created_at": "2026-11-22T14:00:00+00:00",
            "expires_at": "2026-12-22T14:00:00+00:00",
            "ip_address": "127.0.0.1",
        }
        saved = db_instance.create_session(session_data)
        assert saved["session_id"] == "test_token_abc_123"

        # Retrieve session
        retrieved = db_instance.get_session("test_token_abc_123")
        assert retrieved is not None
        assert retrieved["user_id"] == "system_admin"
        assert retrieved["csrf_token"] == "csrf_sample_token"
        assert retrieved["is_admin"] is True

        # Simulate fresh process reading the same persistent database
        fresh_db = DatabaseClient()
        fresh_retrieved = fresh_db.get_session("test_token_abc_123")
        assert fresh_retrieved is not None
        assert fresh_retrieved["user_id"] == "system_admin"

        # Delete session
        deleted = db_instance.delete_session("test_token_abc_123")
        assert deleted is True

        # Verify gone
        assert db_instance.get_session("test_token_abc_123") is None
    finally:
        settings.SESSION_DB_PATH = orig_path


def test_production_guard_fails_closed_when_directory_not_writable(tmp_path, monkeypatch):
    """Verify production guard fails closed if storage directory is unwritable."""
    unwritable_dir = tmp_path / "readonly_dir"
    unwritable_dir.mkdir()
    db_file = str(unwritable_dir / "unwritable.sqlite3")

    orig_env = settings.ENVIRONMENT
    orig_path = settings.SESSION_DB_PATH
    orig_url = settings.SUPABASE_URL
    orig_key = settings.SUPABASE_SERVICE_ROLE_KEY
    try:
        settings.ENVIRONMENT = "production"
        settings.SESSION_DB_PATH = db_file
        settings.SUPABASE_URL = "https://example.supabase.co"
        settings.SUPABASE_SERVICE_ROLE_KEY = "test-service-key"

        # Mock supabase create_client so credentials check passes
        import supabase
        monkeypatch.setattr(supabase, "create_client", lambda url, key: MagicMock())

        # Mock os.access to simulate read-only / un-writable filesystem
        monkeypatch.setattr(os, "access", lambda path, mode: False)

        with pytest.raises(RuntimeError, match="SESSION_DB_PATH directory must be writable in production"):
            DatabaseClient()
    finally:
        settings.ENVIRONMENT = orig_env
        settings.SESSION_DB_PATH = orig_path
        settings.SUPABASE_URL = orig_url
        settings.SUPABASE_SERVICE_ROLE_KEY = orig_key
