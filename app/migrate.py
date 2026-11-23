"""Apply forward-only SQL migrations.

Usage:  python -m app.migrate [--status]

* Reads migrations/NNN_name.sql in lexical order (ignores *.down.sql).
* Runs each migration in its own transaction, under a Postgres advisory lock,
  so concurrent deploys cannot apply the same migration twice.
* Records a checksum per migration and refuses to run if an applied file changed.
"""
from __future__ import annotations

import hashlib
import logging
import pathlib
import sys
from typing import List, Tuple

import psycopg

from .config import settings, validate_database_settings

logger = logging.getLogger("studio.migrate")
MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "migrations"
LOCK_KEY = 947_201_337


def discover(directory: pathlib.Path = MIGRATIONS_DIR) -> List[Tuple[str, pathlib.Path]]:
    files = sorted(p for p in directory.glob("*.sql") if not p.name.endswith(".down.sql"))
    return [(p.stem, p) for p in files]


def _checksum(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def migrate(database_url: str, directory: pathlib.Path = MIGRATIONS_DIR, dry_run: bool = False) -> List[str]:
    applied_now: List[str] = []
    with psycopg.connect(database_url, autocommit=False, client_encoding="UTF8") as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
            cur.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                " version text PRIMARY KEY, checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            conn.commit()
            cur.execute("SELECT version, checksum FROM schema_migrations")
            recorded = {r[0]: r[1] for r in cur.fetchall()}
            try:
                for version, path in discover(directory):
                    digest = _checksum(path)
                    if version in recorded:
                        if recorded[version] != digest:
                            raise RuntimeError(
                                f"Migration {version} was edited after it was applied. "
                                "Migrations are forward-only; add a new migration instead."
                            )
                        continue
                    if dry_run:
                        applied_now.append(version)
                        continue
                    logger.info("applying migration %s", version)
                    cur.execute(path.read_text(encoding="utf-8"))
                    cur.execute(
                        "INSERT INTO schema_migrations (version, checksum) VALUES (%s, %s)", (version, digest)
                    )
                    conn.commit()
                    applied_now.append(version)
                    # Tell PostgREST (Supabase API layer) to reload its schema cache.
                    cur.execute("NOTIFY pgrst, 'reload schema'")
                    conn.commit()
            finally:
                cur.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
                conn.commit()
    return applied_now


def main(argv: List[str]) -> int:
    logging.basicConfig(level=settings.LOG_LEVEL.upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        validate_database_settings()
    except Exception as exc:  # ConfigError lists every problem
        print(str(exc), file=sys.stderr)
        return 2
    if "--status" in argv:
        for version, path in discover():
            print(version)
        return 0
    try:
        applied = migrate(settings.DATABASE_URL)
    except Exception as exc:
        print(f"Migration failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print("Applied: " + (", ".join(applied) if applied else "none (database is up to date)"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
