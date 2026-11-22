"""Regression tests: source permission IDs must be UUIDs and registration must fail closed.

Production (Supabase) stores source_permissions.id as UUID DEFAULT gen_random_uuid() and
upserts on source_id. These tests cover the SQLite fallback against a real database file
and the Supabase code path against a stand-in client that mirrors PostgREST/Postgres rules.
They do not replace running the migration and integration suite against the live schema.
"""
import asyncio
import os
import sqlite3
import threading
import types
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

os.environ.setdefault("ENVIRONMENT", "test")

from app.api.v1.endpoints import discovery as ep  # noqa: E402
from app.schemas import SourceCreateRequest  # noqa: E402
from app.services.discovery import rights_manager as rm_module  # noqa: E402
from app.services.discovery.base import RightsStatus  # noqa: E402
from app.services.discovery.rights_manager import RightsManager  # noqa: E402
from app.services.supabase_client import DatabaseClient  # noqa: E402

SQLITE_SOURCE_PERMISSIONS_DDL = """
CREATE TABLE IF NOT EXISTS source_permissions (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL UNIQUE,
    rights_status TEXT NOT NULL,
    license_type TEXT,
    allowed_platforms TEXT,
    expires_at TEXT,
    attribution_requirements TEXT,
    verification_notes TEXT,
    verified_by TEXT,
    original_creator TEXT,
    source_url TEXT,
    permission_evidence TEXT,
    derivative_rights_allowed INTEGER DEFAULT 1,
    revoked_at TEXT,
    created_at TEXT,
    updated_at TEXT
);
"""


def _is_uuid(value) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (TypeError, ValueError):
        return False


class FakeSupabaseSourceDB:
    """In-memory stand-in for the Supabase tables used by source registration.

    set_source_permission mirrors the production rules: a non-UUID id is rejected
    (PostgreSQL 22P02), and an upsert on source_id keeps the existing row's id and created_at.
    """

    def __init__(self):
        self.sources = {}
        self.permissions = {}
        self.permission_payloads = []
        self.audit = []
        self.fail_permission_writes = False

    def create_source(self, data):
        row = dict(data)
        self.sources[row["id"]] = row
        return dict(row)

    def get_source(self, source_id):
        row = self.sources.get(source_id)
        return dict(row) if row else None

    def list_sources(self, user_id=None, category_id=None, active_only=True):
        out = []
        for row in self.sources.values():
            if user_id and row.get("user_id") != user_id:
                continue
            if category_id and row.get("category_id") != category_id:
                continue
            if active_only and not row.get("is_active"):
                continue
            out.append(dict(row))
        return out

    def update_source(self, source_id, updates):
        if source_id not in self.sources:
            return None
        self.sources[source_id].update(updates)
        return dict(self.sources[source_id])

    def get_source_permission(self, source_id):
        row = self.permissions.get(source_id)
        return dict(row) if row else None

    def set_source_permission(self, source_id, permission_data):
        self.permission_payloads.append(dict(permission_data))
        if self.fail_permission_writes:
            raise RuntimeError("simulated database outage")
        if permission_data.get("verified_by") and not _is_uuid(permission_data["verified_by"]):
            raise RuntimeError(
                f'invalid input syntax for type uuid: "{permission_data["verified_by"]}" (22P02)'
            )
        if "id" in permission_data and not _is_uuid(permission_data["id"]):
            raise RuntimeError(
                f'invalid input syntax for type uuid: "{permission_data["id"]}" (22P02)'
            )
        existing = self.permissions.get(source_id)
        now = datetime.now(timezone.utc).isoformat()
        row = dict(permission_data)
        row["source_id"] = source_id
        row["id"] = existing["id"] if existing else str(uuid.uuid4())
        row["created_at"] = existing["created_at"] if existing else now
        self.permissions[source_id] = row
        return dict(row)

    def create_audit_log(self, **kwargs):
        self.audit.append(kwargs)


@pytest.fixture
def fake_db(monkeypatch):
    fake = FakeSupabaseSourceDB()
    monkeypatch.setattr(rm_module, "db", fake)
    monkeypatch.setattr(ep, "db", fake)
    monkeypatch.setattr(ep, "rights_manager", RightsManager())
    return fake


def _admin(user_id="admin-1"):
    return types.SimpleNamespace(user_id=user_id, is_admin=True)


def _owner(user_id="owner-1"):
    return types.SimpleNamespace(user_id=user_id, is_admin=False)


def _request(rights_status="pending_verification", feed_url="https://example.com/feed.xml"):
    return SourceCreateRequest(
        source_type="approved_rss",
        name="Example feed",
        feed_url=feed_url,
        rights_status=rights_status,
        license_type="licensed",
        allowed_platforms=["youtube_shorts"],
    )


def _register(payload, auth):
    return asyncio.run(ep.register_content_source(payload, auth))


# ---------------------------------------------------------------------------
# 1. Permission IDs are UUIDs and the upsert conflict key is preserved
# ---------------------------------------------------------------------------

class _RecordingTable:
    def __init__(self, calls):
        self.calls = calls
        self._payload = None
        self._on_conflict = None

    def upsert(self, payload, on_conflict=None):
        self.calls.append({"payload": dict(payload), "on_conflict": on_conflict})
        self._payload = payload
        return self

    def execute(self):
        return types.SimpleNamespace(data=[self._payload])


class _RecordingClient:
    def __init__(self):
        self.calls = []

    def table(self, name):
        assert name == "source_permissions"
        return _RecordingTable(self.calls)


def test_supabase_permission_upsert_omits_string_id_and_keeps_source_id_conflict(monkeypatch):
    client = _RecordingClient()
    supabase_db = DatabaseClient.__new__(DatabaseClient)
    supabase_db.has_supabase = True
    supabase_db.client = client
    supabase_db.lock = threading.RLock()
    monkeypatch.setattr(rm_module, "db", supabase_db)

    RightsManager().set_permission(
        source_id="src_898f0ee77f",
        status=RightsStatus.AUTHORIZED_PLATFORMS,
        allowed_platforms=["youtube_shorts"],
        verified_by=str(uuid.uuid4()),
    )

    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["on_conflict"] == "source_id"
    assert "id" not in call["payload"], "id must be left to the database default (UUID)"
    assert call["payload"]["source_id"] == "src_898f0ee77f"
    assert call["payload"]["rights_status"] == "authorized_platforms"


# ---------------------------------------------------------------------------
# 2. SQLite fallback: real upsert with a UUID id, single row, id and created_at preserved
# ---------------------------------------------------------------------------

def test_sqlite_permission_upsert_uses_uuid_and_keeps_one_row(tmp_path, monkeypatch):
    path = tmp_path / "perm.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(SQLITE_SOURCE_PERMISSIONS_DDL)

    local_db = DatabaseClient.__new__(DatabaseClient)
    local_db.has_supabase = False
    local_db.lock = threading.RLock()
    local_db.sqlite_path = str(path)
    monkeypatch.setattr(rm_module, "db", local_db)

    manager = RightsManager()
    manager.set_permission(source_id="src_local", status=RightsStatus.PENDING_VERIFICATION)
    with sqlite3.connect(path) as conn:
        first_id, first_created = conn.execute(
            "SELECT id, created_at FROM source_permissions WHERE source_id = 'src_local'"
        ).fetchone()

    manager.set_permission(
        source_id="src_local",
        status=RightsStatus.AUTHORIZED_PLATFORMS,
        allowed_platforms=["youtube_shorts"],
    )

    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            "SELECT id, created_at, rights_status FROM source_permissions WHERE source_id = 'src_local'"
        ).fetchall()
    assert len(rows) == 1
    row_id, row_created, status_value = rows[0]
    assert _is_uuid(row_id)
    assert row_id == first_id, "conflicting upsert must keep the existing permission id"
    assert row_created == first_created
    assert status_value == "authorized_platforms"


# ---------------------------------------------------------------------------
# 3. Failed permission write cannot authorize or activate a source
# ---------------------------------------------------------------------------

def test_failed_permission_write_does_not_authorize_source(fake_db):
    fake_db.fail_permission_writes = True
    with pytest.raises(RuntimeError):
        ep.rights_manager.set_permission(source_id="src_x", status=RightsStatus.AUTHORIZED_PLATFORMS)
    assert ep.rights_manager.check_rights_for_production("src_x")[0] is False


def test_failed_permission_write_leaves_admin_registration_inactive(fake_db):
    fake_db.fail_permission_writes = True
    with pytest.raises(HTTPException) as excinfo:
        _register(_request(rights_status="authorized_platforms"), _admin())
    assert excinfo.value.status_code == 503

    (source,) = fake_db.sources.values()
    assert source["is_active"] is False
    assert fake_db.permissions == {}
    assert ep.rights_manager.check_rights_for_production(source["id"])[0] is False


# ---------------------------------------------------------------------------
# 4. Registration rules: pending for non-admins, activation only for verified admin grants
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("admin_id", ["system_admin", str(uuid.uuid4())])
def test_admin_authorized_registration_activates_with_uuid_permission(fake_db, admin_id):
    response = _register(_request(rights_status="authorized_platforms"), _admin(admin_id))
    assert response["status"] == "created"

    source_id = response["source"]["id"]
    assert fake_db.sources[source_id]["is_active"] is True
    permission = fake_db.permissions[source_id]
    assert _is_uuid(permission["id"])
    assert permission["rights_status"] == "authorized_platforms"
    assert ep.rights_manager.check_rights_for_production(source_id, "youtube_shorts")[0] is True


def test_non_admin_registration_stays_pending_and_inactive(fake_db):
    response = _register(_request(rights_status="authorized_platforms"), _owner())
    source_id = response["source"]["id"]

    assert fake_db.sources[source_id]["is_active"] is False
    permission = fake_db.permissions[source_id]
    assert permission["rights_status"] == "pending_verification"
    assert permission["verified_by"] is None
    assert ep.rights_manager.check_rights_for_production(source_id)[0] is False


# ---------------------------------------------------------------------------
# 5. Existing permission records are preserved
# ---------------------------------------------------------------------------

def test_non_admin_reregistration_preserves_existing_permission(fake_db):
    first = _register(_request(), _owner())
    source_id = first["source"]["id"]

    # An administrator has verified this source out of band.
    fake_db.permissions[source_id] = {
        "id": str(uuid.uuid4()),
        "source_id": source_id,
        "rights_status": "authorized_platforms",
        "created_at": "2026-11-20T00:00:00+00:00",
    }
    before = dict(fake_db.permissions[source_id])
    writes_before = len(fake_db.permission_payloads)

    again = _register(_request(), _owner())

    assert again["source"]["id"] == source_id
    assert fake_db.permissions[source_id] == before
    assert len(fake_db.permission_payloads) == writes_before


# ---------------------------------------------------------------------------
# 6. Repeated registration does not duplicate source or permission records
# ---------------------------------------------------------------------------

def test_repeated_admin_registration_does_not_duplicate_records(fake_db):
    first = _register(_request(rights_status="authorized_platforms"), _admin())
    second = _register(_request(rights_status="authorized_platforms"), _admin())

    assert first["source"]["id"] == second["source"]["id"]
    assert len(fake_db.sources) == 1
    assert len(fake_db.permissions) == 1
    assert fake_db.sources[first["source"]["id"]]["is_active"] is True
    assert second["status"] == "exists"
