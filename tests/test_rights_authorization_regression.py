"""Regression tests: source rights authorization must validate input, persist every field, and fail closed.

Covers the production failure where rights_status="authorized" reached RightsStatus(...) and raised
ValueError (HTTP 500). Invalid values must return 422. An admin-authorized update must persist its
fields, activate the source only after every write succeeds, and never grant derivative rights for a
pending, rejected, expired or revoked source. Also covers the audit_logs write path and its grants.
"""
import asyncio
import os
import sqlite3
import threading
import types
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

os.environ.setdefault("ENVIRONMENT", "test")

from app.api.v1.endpoints import discovery as ep  # noqa: E402
from app.schemas import SourceCreateRequest, SourceRightsUpdateRequest  # noqa: E402
from app.services.discovery import rights_manager as rm_module  # noqa: E402
from app.services.discovery.base import AUTHORIZED_RIGHTS_STATUSES, RightsStatus  # noqa: E402
from app.services.discovery.rights_manager import RightsManager  # noqa: E402
from app.services.supabase_client import DatabaseClient  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
API_KEY = "test-secret-key-12345"


def _is_uuid(value) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (TypeError, ValueError):
        return False


class FakeRightsDB:
    """In-memory stand-in for the tables the rights endpoint uses.

    set_source_permission mirrors PostgreSQL: a non-UUID verified_by raises (22P02), and an upsert on
    source_id keeps the existing id. Every write is recorded in order so tests can check sequencing.
    """

    def __init__(self):
        self.sources = {}
        self.permissions = {}
        self.events = []
        self.audit = []
        self.fail_permission_writes = False
        self.fail_audit_writes = False
        self.fail_deactivation = False

    def add_source(self, source_id="src_1", user_id=None, is_active=False):
        self.sources[source_id] = {"id": source_id, "user_id": user_id, "is_active": is_active, "category_id": "gta6"}
        return source_id

    def get_source(self, source_id):
        row = self.sources.get(source_id)
        return dict(row) if row else None

    def update_source(self, source_id, updates):
        self.events.append(("source_update", source_id, dict(updates)))
        if updates.get("is_active") is False and self.fail_deactivation:
            raise RuntimeError("simulated database outage on deactivation")
        if source_id not in self.sources:
            return None
        self.sources[source_id].update(updates)
        return dict(self.sources[source_id])

    def set_source_permission(self, source_id, permission_data):
        self.events.append(("permission_write", source_id, dict(permission_data)))
        if self.fail_permission_writes:
            raise RuntimeError("simulated database outage")
        if permission_data.get("verified_by") and not _is_uuid(permission_data["verified_by"]):
            raise RuntimeError('invalid input syntax for type uuid (22P02)')
        existing = self.permissions.get(source_id)
        row = dict(permission_data)
        row["source_id"] = source_id
        row["id"] = existing["id"] if existing else str(uuid.uuid4())
        row["created_at"] = existing["created_at"] if existing else datetime.now(timezone.utc).isoformat()
        self.permissions[source_id] = row
        return dict(row)

    def get_source_permission(self, source_id):
        row = self.permissions.get(source_id)
        return dict(row) if row else None

    def create_audit_log(self, **kwargs):
        self.events.append(("audit_write", kwargs.get("resource_id"), kwargs))
        if self.fail_audit_writes:
            raise RuntimeError("simulated audit write failure (403 permission denied for table audit_logs)")
        self.audit.append(kwargs)
        return kwargs

    def list_sources(self, **kwargs):
        return [dict(row) for row in self.sources.values()]


@pytest.fixture
def fake_db(monkeypatch):
    fake = FakeRightsDB()
    monkeypatch.setattr(rm_module, "db", fake)
    monkeypatch.setattr(ep, "db", fake)
    monkeypatch.setattr(ep, "rights_manager", RightsManager())
    return fake


def _admin(user_id=None):
    return types.SimpleNamespace(user_id=user_id or str(uuid.uuid4()), is_admin=True)


def _owner():
    return types.SimpleNamespace(user_id=str(uuid.uuid4()), is_admin=False)


def _rights(**overrides):
    data = {
        "rights_status": "authorized_editing",
        "allowed_platforms": ["youtube_shorts", "TikTok"],
        "license_type": "written_license",
        "expires_at": datetime.now(timezone.utc) + timedelta(days=90),
        "notes": "Signed footage release, reference LIC-2026-014",
    }
    data.update(overrides)
    return SourceRightsUpdateRequest(**data)


def _update(source_id, payload, auth):
    return asyncio.run(ep.update_source_rights(source_id, payload, auth))


# ---------------------------------------------------------------------------
# 1. The enum and request validation
# ---------------------------------------------------------------------------

def test_rights_status_enum_values_are_exact():
    assert [status.value for status in RightsStatus] == [
        "pending_verification",
        "authorized_editing",
        "authorized_platforms",
        "rejected",
        "expired",
    ]
    assert AUTHORIZED_RIGHTS_STATUSES == {RightsStatus.AUTHORIZED_EDITING, RightsStatus.AUTHORIZED_PLATFORMS}


@pytest.mark.parametrize("value", ["authorized", "revoked", "", "AUTHORIZED_EDITING", "approved"])
def test_invalid_rights_status_is_rejected_by_request_models(value):
    with pytest.raises(ValidationError):
        SourceRightsUpdateRequest(rights_status=value)
    with pytest.raises(ValidationError):
        SourceCreateRequest(source_type="approved_rss", name="x", feed_url="https://example.com/f.xml", rights_status=value)


def test_bare_authorized_error_points_to_the_valid_value():
    with pytest.raises(ValidationError) as excinfo:
        SourceRightsUpdateRequest(rights_status="authorized")
    message = str(excinfo.value)
    assert "authorized_editing" in message and "authorized_platforms" in message


@pytest.mark.parametrize("value", [s.value for s in RightsStatus])
def test_every_enum_value_is_accepted_by_request_models(value):
    payload = SourceRightsUpdateRequest(rights_status=value, allowed_platforms=["youtube_shorts"])
    assert payload.rights_status == value


def test_authorized_platforms_requires_at_least_one_platform():
    with pytest.raises(ValidationError):
        SourceRightsUpdateRequest(rights_status="authorized_platforms", allowed_platforms=[" ", ""])


def test_authorized_status_rejects_an_expiry_in_the_past():
    with pytest.raises(ValidationError):
        SourceRightsUpdateRequest(
            rights_status="authorized_editing",
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        )


def test_rejected_status_may_carry_a_past_expiry():
    payload = SourceRightsUpdateRequest(
        rights_status="rejected", expires_at=datetime.now(timezone.utc) - timedelta(days=1)
    )
    assert payload.rights_status == "rejected"


def test_platforms_are_normalised_and_deduplicated():
    payload = _rights(allowed_platforms=[" YouTube_Shorts ", "tiktok", "tiktok", ""])
    assert payload.allowed_platforms == ["youtube_shorts", "tiktok"]


def test_http_invalid_status_returns_422_and_writes_nothing(client, fake_db, monkeypatch):
    source_id = fake_db.add_source()
    response = client.post(
        f"/api/v1/discovery/sources/{source_id}/rights",
        json={"rights_status": "authorized", "allowed_platforms": ["youtube_shorts"]},
        headers={"X-API-Key": API_KEY},
    )
    assert response.status_code == 422
    assert fake_db.events == []
    assert fake_db.sources[source_id]["is_active"] is False


def test_http_valid_authorization_returns_200_and_activates(client, fake_db):
    source_id = fake_db.add_source()
    response = client.post(
        f"/api/v1/discovery/sources/{source_id}/rights",
        json={"rights_status": "authorized_editing", "allowed_platforms": ["youtube_shorts"]},
        headers={"X-API-Key": API_KEY},
    )
    assert response.status_code == 200, response.text
    assert response.json()["is_active"] is True
    assert fake_db.sources[source_id]["is_active"] is True


# ---------------------------------------------------------------------------
# 2. An admin-authorized update persists every field
# ---------------------------------------------------------------------------

def test_authorized_update_persists_all_fields(fake_db):
    source_id = fake_db.add_source()
    admin_id = str(uuid.uuid4())
    payload = _rights()

    result = _update(source_id, payload, _admin(admin_id))

    permission = fake_db.permissions[source_id]
    assert permission["rights_status"] == "authorized_editing"
    assert permission["allowed_platforms"] == ["youtube_shorts", "tiktok"]
    assert permission["license_type"] == "written_license"
    assert permission["expires_at"] == payload.expires_at.isoformat()
    assert permission["verification_notes"] == "Signed footage release, reference LIC-2026-014"
    assert permission["verified_by"] == admin_id
    assert permission["derivative_rights_allowed"] is True
    assert fake_db.sources[source_id]["is_active"] is True
    assert result["status"] == "updated"
    assert result["rights_status"] == "authorized_editing"
    assert result["is_active"] is True


def test_api_key_admin_leaves_verified_by_empty_and_records_actor(fake_db):
    source_id = fake_db.add_source()

    _update(source_id, _rights(), _admin("system_admin"))

    assert fake_db.permissions[source_id]["verified_by"] is None
    (audit,) = fake_db.audit
    assert audit["user_id"] == "system_admin"
    assert audit["details"]["actor"] == "system_admin"
    assert audit["details"]["rights_status"] == "authorized_editing"
    assert fake_db.sources[source_id]["is_active"] is True


def test_authorized_platforms_persists_only_the_listed_platforms(fake_db):
    source_id = fake_db.add_source()
    _update(source_id, _rights(rights_status="authorized_platforms", allowed_platforms=["youtube_shorts"]), _admin())
    permission = fake_db.permissions[source_id]
    assert permission["rights_status"] == "authorized_platforms"
    assert permission["allowed_platforms"] == ["youtube_shorts"]
    assert ep.rights_manager.check_rights_for_production(source_id, "youtube_shorts")[0] is True
    assert ep.rights_manager.check_rights_for_production(source_id, "tiktok")[0] is False


@pytest.mark.parametrize("status_value", ["pending_verification", "rejected", "expired"])
def test_non_authorized_status_never_grants_derivative_rights(fake_db, status_value):
    source_id = fake_db.add_source(is_active=True)

    _update(source_id, _rights(rights_status=status_value, expires_at=None), _admin())

    assert fake_db.permissions[source_id]["derivative_rights_allowed"] is False
    assert fake_db.sources[source_id]["is_active"] is False
    assert ep.rights_manager.check_rights_for_production(source_id, "youtube_shorts")[0] is False


def test_derivative_rights_are_not_granted_by_a_revoked_authorization(fake_db):
    source_id = fake_db.add_source()
    ep.rights_manager.set_permission(
        source_id=source_id,
        status=RightsStatus.AUTHORIZED_EDITING,
        derivative_rights_allowed=True,
        revoked_at=datetime.now(timezone.utc),
    )
    assert fake_db.permissions[source_id]["derivative_rights_allowed"] is False


# ---------------------------------------------------------------------------
# 3. Activation happens last, and failures leave the source inactive
# ---------------------------------------------------------------------------

def test_activation_is_written_only_after_permission_and_audit_writes(fake_db):
    source_id = fake_db.add_source()

    _update(source_id, _rights(), _admin())

    order = []
    for kind, _source_id, data in fake_db.events:
        if kind == "source_update":
            order.append("activate" if data.get("is_active") is True else "deactivate")
        else:
            order.append(kind)
    assert order == ["deactivate", "permission_write", "audit_write", "activate"]


def test_failed_permission_write_returns_503_and_keeps_source_inactive(fake_db):
    source_id = fake_db.add_source()
    fake_db.fail_permission_writes = True

    with pytest.raises(HTTPException) as excinfo:
        _update(source_id, _rights(), _admin())

    assert excinfo.value.status_code == 503
    assert fake_db.sources[source_id]["is_active"] is False
    assert fake_db.audit == []
    assert ep.rights_manager.check_rights_for_production(source_id)[0] is False


def test_failed_required_audit_write_returns_503_and_keeps_source_inactive(fake_db):
    source_id = fake_db.add_source()
    fake_db.fail_audit_writes = True

    with pytest.raises(HTTPException) as excinfo:
        _update(source_id, _rights(), _admin())

    assert excinfo.value.status_code == 503
    assert fake_db.sources[source_id]["is_active"] is False


def test_failed_deactivation_stops_the_update_before_any_rights_are_written(fake_db):
    source_id = fake_db.add_source(is_active=True)
    fake_db.fail_deactivation = True

    with pytest.raises(HTTPException) as excinfo:
        _update(source_id, _rights(), _admin())

    assert excinfo.value.status_code == 503
    assert fake_db.permissions == {}


def test_revocation_deactivates_an_active_source(fake_db):
    source_id = fake_db.add_source(is_active=True)
    _update(source_id, _rights(rights_status="rejected", expires_at=None), _admin())
    assert fake_db.sources[source_id]["is_active"] is False


def test_non_admin_cannot_update_rights_and_nothing_is_written(fake_db):
    source_id = fake_db.add_source()
    with pytest.raises(HTTPException) as excinfo:
        _update(source_id, _rights(), _owner())
    assert excinfo.value.status_code == 403
    assert fake_db.events == []


def test_unknown_source_returns_404(fake_db):
    with pytest.raises(HTTPException) as excinfo:
        _update("src_missing", _rights(), _admin())
    assert excinfo.value.status_code == 404
    assert fake_db.events == []


# ---------------------------------------------------------------------------
# 4. Fail-closed for pending, expired, revoked and unauthorized sources
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status_value", ["pending_verification", "rejected", "expired"])
def test_pending_rejected_and_expired_sources_are_blocked(fake_db, status_value):
    source_id = fake_db.add_source()
    ep.rights_manager.set_permission(source_id=source_id, status=RightsStatus(status_value))
    allowed, reason = ep.rights_manager.check_rights_for_production(source_id, "youtube_shorts")
    assert allowed is False
    assert reason


def test_revoked_authorization_is_blocked_even_if_status_is_still_authorized(fake_db):
    source_id = fake_db.add_source()
    ep.rights_manager.set_permission(source_id=source_id, status=RightsStatus.AUTHORIZED_EDITING)
    ep.rights_manager.revoke_permission(source_id, reason="Creator withdrew consent", verified_by=None)
    assert ep.rights_manager.check_rights_for_production(source_id, "youtube_shorts")[0] is False


def test_authorization_whose_expiry_has_passed_is_blocked(fake_db):
    source_id = fake_db.add_source()
    ep.rights_manager.set_permission(
        source_id=source_id,
        status=RightsStatus.AUTHORIZED_EDITING,
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    allowed, reason = ep.rights_manager.check_rights_for_production(source_id, "youtube_shorts")
    assert allowed is False
    assert "expired" in reason


def test_source_with_no_permission_record_is_blocked(fake_db):
    assert ep.rights_manager.check_rights_for_production("src_none", "youtube_shorts")[0] is False


def test_cached_permission_reloaded_from_storage_keeps_derivative_flag(fake_db):
    source_id = fake_db.add_source()
    _update(source_id, _rights(rights_status="rejected", expires_at=None), _admin())
    fresh = RightsManager()  # empty cache, forces a reload from storage
    permission = fresh.get_permission(source_id)
    assert permission["derivative_rights_allowed"] is False
    assert fresh.check_rights_for_production(source_id, "youtube_shorts")[0] is False


# ---------------------------------------------------------------------------
# 5. UUID handling and the audit_logs write path
# ---------------------------------------------------------------------------

class _AuditTable:
    def __init__(self, client):
        self.client = client
        self._row = None

    def insert(self, row):
        self._row = dict(row)
        return self

    def execute(self):
        if self.client.error is not None:
            raise self.client.error
        self.client.inserted.append(self._row)
        return types.SimpleNamespace(data=[self._row])


class _AuditClient:
    def __init__(self, error=None):
        self.error = error
        self.inserted = []

    def table(self, name):
        assert name == "audit_logs"
        return _AuditTable(self)


def _supabase_db(client):
    database = DatabaseClient.__new__(DatabaseClient)
    database.has_supabase = True
    database.client = client
    database.lock = threading.RLock()
    return database


def test_supabase_audit_insert_moves_non_uuid_actor_into_details():
    client = _AuditClient()
    database = _supabase_db(client)

    database.create_audit_log(
        action="source_rights_updated",
        resource_type="source",
        user_id="system_admin",
        resource_id="src_1",
        details={"rights_status": "authorized_editing"},
        required=True,
    )

    (row,) = client.inserted
    assert row["user_id"] is None
    assert row["details"] == {"actor": "system_admin", "rights_status": "authorized_editing"}


def test_supabase_audit_insert_keeps_uuid_actor_in_user_id():
    client = _AuditClient()
    actor = str(uuid.uuid4())
    _supabase_db(client).create_audit_log(action="source_rights_updated", resource_type="source", user_id=actor)
    assert client.inserted[0]["user_id"] == actor


def test_required_audit_write_raises_when_supabase_rejects_it():
    client = _AuditClient(error=RuntimeError('new row violates row-level security policy for table "audit_logs" (403)'))
    with pytest.raises(RuntimeError):
        _supabase_db(client).create_audit_log(
            action="source_rights_updated", resource_type="source", user_id=str(uuid.uuid4()), required=True
        )


def test_optional_audit_write_still_returns_when_supabase_rejects_it():
    client = _AuditClient(error=RuntimeError("permission denied"))
    entry = _supabase_db(client).create_audit_log(action="a", resource_type="source", user_id=str(uuid.uuid4()))
    assert entry["action"] == "a"


def test_required_audit_write_raises_on_sqlite_failure(tmp_path):
    path = tmp_path / "audit.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE unrelated (id TEXT)")  # audit_logs missing on purpose
    database = DatabaseClient.__new__(DatabaseClient)
    database.has_supabase = False
    database.lock = threading.RLock()
    database.sqlite_path = str(path)
    with pytest.raises(Exception):
        database.create_audit_log(action="a", resource_type="source", required=True)
    # Optional writes keep the old behaviour: log and return.
    assert database.create_audit_log(action="a", resource_type="source")["action"] == "a"


# ---------------------------------------------------------------------------
# 6. audit_logs grants are least-privilege and RLS stays on
# ---------------------------------------------------------------------------

def _migration_sql():
    path = REPO_ROOT / "supabase" / "migrations" / "20261122070000_audit_logs_backend_least_privilege.sql"
    assert path.exists(), "least-privilege audit_logs migration is missing"
    return path.read_text()


def test_audit_logs_migration_grants_backend_only_select_and_insert():
    sql = _migration_sql()
    assert "GRANT SELECT, INSERT ON TABLE public.audit_logs TO service_role;" in sql
    service_grants = [line for line in sql.splitlines() if "service_role" in line and line.strip().upper().startswith("GRANT")]
    assert service_grants == ["GRANT SELECT, INSERT ON TABLE public.audit_logs TO service_role;"]


def test_audit_logs_migration_keeps_rls_enabled_and_grants_nothing_broad():
    sql = _migration_sql().upper()
    assert "DISABLE ROW LEVEL SECURITY" not in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "GRANT ALL" not in sql
    assert "TO PUBLIC" not in sql and "TO ANON" not in sql
    grant_lines = [line for line in sql.splitlines() if line.strip().startswith("GRANT")]
    assert not any("UPDATE" in line or "DELETE" in line or "TRUNCATE" in line for line in grant_lines)
    assert "DROP POLICY" not in sql and "CREATE POLICY" not in sql


def test_audit_logs_migration_does_not_change_rights_schema():
    sql = _migration_sql().upper()
    for forbidden in ("SOURCE_PERMISSIONS", "CREATE TABLE", "DROP TABLE", "ALTER COLUMN"):
        assert forbidden not in sql
