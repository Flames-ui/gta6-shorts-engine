"""Migrations: fresh install, idempotent re-run, tamper detection, RLS and rollback file."""
import pathlib
import shutil
import uuid
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest

from app.migrate import MIGRATIONS_DIR, discover, migrate

from .conftest import TEST_DB_URL, requires_db

pytestmark = [requires_db]
TABLES = ["video_sources", "editing_jobs", "job_events", "detected_highlights", "generated_clips",
          "clip_captions", "processing_usage", "worker_heartbeats", "schema_migrations"]


@pytest.fixture
def scratch_db():
    parts = urlsplit(TEST_DB_URL)
    name = f"studio_mig_{uuid.uuid4().hex[:8]}"
    admin = urlunsplit((parts.scheme, parts.netloc, "/postgres", "", ""))
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}" ENCODING \'UTF8\' TEMPLATE template0')
    url = urlunsplit((parts.scheme, parts.netloc, f"/{name}", "", ""))
    yield url
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_fresh_database_gets_every_table_with_rls(scratch_db):
    applied = migrate(scratch_db)
    assert applied == [p for p, _ in discover()]
    with psycopg.connect(scratch_db) as conn:
        names = {r[0] for r in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'").fetchall()}
        assert set(TABLES) <= names
        rls = dict(conn.execute("SELECT relname, relrowsecurity FROM pg_class WHERE relname = ANY(%s)",
                                (TABLES[:-1],)).fetchall())
        assert all(rls.values()), rls


def test_rerunning_migrations_is_a_no_op(scratch_db):
    migrate(scratch_db)
    assert migrate(scratch_db) == []


def test_modified_applied_migration_is_refused(scratch_db, tmp_path):
    folder = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS_DIR, folder)
    migrate(scratch_db, directory=folder)
    target = next(p for p in folder.glob("*.sql") if not p.name.endswith(".down.sql"))
    target.write_text(target.read_text() + "\n-- tampered\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="edited after it was applied"):
        migrate(scratch_db, directory=folder)


def test_a_failing_migration_rolls_back_entirely(scratch_db, tmp_path):
    folder = tmp_path / "migrations"
    folder.mkdir()
    (folder / "001_ok.sql").write_text("CREATE TABLE t_ok (id int);", encoding="utf-8")
    (folder / "002_bad.sql").write_text("CREATE TABLE t_partial (id int); SELECT 1/0;", encoding="utf-8")
    with pytest.raises(Exception):
        migrate(scratch_db, directory=folder)
    with psycopg.connect(scratch_db) as conn:
        names = {r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")}
    assert "t_partial" not in names
    assert "t_ok" in names  # earlier, successful migration stays applied


def test_rollback_script_exists_and_is_explicitly_destructive():
    down = pathlib.Path(MIGRATIONS_DIR, "001_studio_schema.down.sql").read_text()
    assert "DROP TABLE" in down
    assert "DESTRUCTIVE" in down.upper()


def test_migrations_do_not_contain_secrets():
    for path in MIGRATIONS_DIR.glob("*.sql"):
        text = path.read_text().lower()
        assert "password" not in text and "service_role_key" not in text
