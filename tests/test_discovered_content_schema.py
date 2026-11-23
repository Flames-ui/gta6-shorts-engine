"""Schema regression: every column the discovery pipeline writes must exist in a migration.

Reads the repository's SQL migrations and checks them against the backend's own write allow-list.
This catches the production PGRST204 failure (a column written by the backend but absent from the
database) without needing a live database.
"""
import re
from pathlib import Path

import pytest

from app.services.discovery import media_pipeline
from app.services.supabase_client import DatabaseClient

REPO = Path(__file__).resolve().parent.parent
MIGRATIONS = sorted((REPO / "supabase" / "migrations").glob("*.sql"))
RECONCILE = "20261123000000_discovered_content_pipeline_reconcile.sql"


def _sql(name_suffix=None):
    return "\n".join(p.read_text() for p in MIGRATIONS if name_suffix is None or p.name.endswith(name_suffix))


def _base_table_block():
    text = _sql()
    start = text.index("CREATE TABLE IF NOT EXISTS public.discovered_content (")
    return text[start:text.index(");", start)]


def _column_present(column: str) -> bool:
    added = re.search(rf"ADD COLUMN IF NOT EXISTS {column}\b", _sql())
    return bool(added) or bool(re.search(rf"^\s*{column}\s", _base_table_block(), re.MULTILINE))


def test_every_pipeline_write_column_exists_in_a_migration():
    missing = sorted(c for c in DatabaseClient._PIPELINE_COLUMNS if not _column_present(c))
    assert missing == [], f"Backend writes columns no migration creates: {missing}"


def test_availability_column_is_text_with_a_migration():
    assert "availability" in DatabaseClient._PIPELINE_COLUMNS
    assert re.search(r"ADD COLUMN IF NOT EXISTS availability text;", _sql())


def test_reconcile_migration_exists_and_is_forward_only_and_idempotent():
    sql = (REPO / "supabase" / "migrations" / RECONCILE).read_text()
    statements = [s.strip() for s in sql.split(";") if s.strip()]
    for stmt in statements:
        body = "\n".join(line for line in stmt.splitlines() if not line.strip().startswith("--")).strip()
        if not body:
            continue
        upper = body.upper()
        assert not re.search(r"\b(DROP|DELETE|TRUNCATE|RENAME)\b", upper), body
        assert "ALTER COLUMN" not in upper, body
        assert "POLICY" not in upper and "GRANT" not in upper and "ROW LEVEL SECURITY" not in upper, body
        if upper.startswith("ALTER TABLE") and "ADD COLUMN" in upper:
            assert "IF NOT EXISTS" in upper, body
        if upper.startswith("CREATE INDEX"):
            assert "IF NOT EXISTS" in upper, body


def test_reconcile_migration_reloads_postgrest_schema_cache():
    sql = (REPO / "supabase" / "migrations" / RECONCILE).read_text()
    assert re.search(r"NOTIFY\s+pgrst\s*,\s*'reload schema'\s*;", sql)


def test_reconcile_migration_is_the_latest_migration():
    assert MIGRATIONS[-1].name == RECONCILE


@pytest.mark.parametrize("meta", [
    {"title": "T", "channel_title": "C", "channel_id": "UC1", "published_at": "2026-11-20T10:00:00Z",
     "thumbnail_url": "https://i.ytimg.com/vi/x/x.jpg", "duration_seconds": 303.0,
     "availability": "available", "youtube_video_id": "Ve2M5i_DSKI"},
    {"title": "T", "channel_title": "C", "published_at": None, "thumbnail_url": None,
     "duration_seconds": None, "availability": "private", "youtube_video_id": "Ve2M5i_DSKI"},
])
def test_metadata_write_keys_are_all_allowed_pipeline_columns(meta):
    item = {"id": "yt_Ve2M5i_DSKI", "title": "Original", "creator_name": "Shirrako", "original_url": "https://www.youtube.com/watch?v=Ve2M5i_DSKI"}
    updates = media_pipeline._metadata_from_meta(item, "Ve2M5i_DSKI", meta, "2026-11-23T07:45:00+00:00")
    unknown = set(updates) - DatabaseClient._PIPELINE_COLUMNS
    assert unknown == set(), f"Metadata write uses non-pipeline columns: {sorted(unknown)}"
    assert all(_column_present(k) for k in updates)


def test_failed_metadata_never_writes_fabricated_duration_or_availability():
    item = {"id": "yt_x", "title": "Original", "original_url": "https://www.youtube.com/watch?v=Ve2M5i_DSKI"}
    updates = media_pipeline._metadata_from_meta(item, "Ve2M5i_DSKI", None, "2026-11-23T07:45:00+00:00")
    assert updates["metadata_status"] == "failed"
    assert updates["metadata_error"] == "video_not_found"
    assert "duration_seconds" not in updates
    assert "availability" not in updates
