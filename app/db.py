"""PostgreSQL access layer (psycopg 3 with a connection pool).

All SQL is parameterised. Status changes are compare-and-set on the current
status, so two workers can never both advance the same job.
"""
from __future__ import annotations

import json
import logging
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from . import states
from .config import settings
from .errors import Conflict, ServiceUnavailable

logger = logging.getLogger("studio.db")

_pool: Optional[ConnectionPool] = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        if not settings.DATABASE_URL:
            raise ServiceUnavailable("Database is not configured.", code="database_not_configured")
        _pool = ConnectionPool(
            conninfo=settings.DATABASE_URL,
            min_size=1,
            max_size=10,
            timeout=10,
            kwargs={"row_factory": dict_row, "autocommit": False, "client_encoding": "UTF8"},
            open=True,
        )
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def connection():
    """Yield a connection; commit on success, roll back on any error."""
    try:
        pool = get_pool()
        with pool.connection() as conn:
            yield conn
    except psycopg.OperationalError as exc:
        logger.error("database connection failure: %s", type(exc).__name__)
        raise ServiceUnavailable("Database is temporarily unavailable.", code="database_unavailable") from exc


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid(value: Any) -> str:
    return str(uuid.UUID(str(value)))


class Repository:
    """Thin, explicit queries. Each method runs in its own transaction unless given a cursor."""

    # ----------------------------------------------------------- video sources
    def create_source(self, owner_id: str, **fields: Any) -> Dict[str, Any]:
        cols = ["owner_id"] + list(fields.keys())
        vals = [owner_id] + list(fields.values())
        sql = (
            f"INSERT INTO video_sources ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) "
            "RETURNING *"
        )
        with connection() as conn, conn.cursor() as cur:
            cur.execute(sql, vals)
            row = cur.fetchone()
            conn.commit()
            return row

    def get_source(self, source_id: str) -> Optional[Dict[str, Any]]:
        with connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM video_sources WHERE id = %s", (_uuid(source_id),))
            return cur.fetchone()

    def find_live_youtube_source(self, owner_id: str, video_id: str) -> Optional[Dict[str, Any]]:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM video_sources WHERE owner_id = %s AND youtube_video_id = %s "
                "AND source_type = 'youtube' AND status <> 'failed' LIMIT 1",
                (owner_id, video_id),
            )
            return cur.fetchone()

    def find_live_upload_by_sha(self, owner_id: str, sha256: str) -> Optional[Dict[str, Any]]:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM video_sources WHERE owner_id = %s AND sha256 = %s AND status <> 'failed' LIMIT 1",
                (owner_id, sha256),
            )
            return cur.fetchone()

    def update_source(self, source_id: str, **fields: Any) -> Dict[str, Any]:
        if not fields:
            return self.get_source(source_id)
        assignments = ", ".join(f"{k} = %s" for k in fields)
        with connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"UPDATE video_sources SET {assignments} WHERE id = %s RETURNING *",
                list(fields.values()) + [_uuid(source_id)],
            )
            row = cur.fetchone()
            conn.commit()
            return row

    # -------------------------------------------------------------------- jobs
    def create_job(self, owner_id: str, source_id: str, **fields: Any) -> Dict[str, Any]:
        cols = ["owner_id", "source_id"] + list(fields.keys())
        vals = [owner_id, _uuid(source_id)] + [Jsonb(v) if isinstance(v, dict) else v for v in fields.values()]
        sql = (
            f"INSERT INTO editing_jobs ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) RETURNING *"
        )
        with connection() as conn, conn.cursor() as cur:
            cur.execute(sql, vals)
            job = cur.fetchone()
            cur.execute(
                "INSERT INTO job_events (job_id, event_type, to_status, stage, message) "
                "VALUES (%s, 'created', 'queued', 'queued', 'Job created and persisted')",
                (job["id"],),
            )
            cur.execute(
                "INSERT INTO processing_usage (owner_id, job_id, metric, quantity) VALUES (%s, %s, 'jobs_created', 1)",
                (owner_id, job["id"]),
            )
            conn.commit()
            return job

    def get_job(self, job_id: str, owner_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        with connection() as conn, conn.cursor() as cur:
            if owner_id is None:
                cur.execute("SELECT * FROM editing_jobs WHERE id = %s", (_uuid(job_id),))
            else:
                cur.execute("SELECT * FROM editing_jobs WHERE id = %s AND owner_id = %s", (_uuid(job_id), owner_id))
            return cur.fetchone()

    def find_job_by_idempotency(self, owner_id: str, key: str) -> Optional[Dict[str, Any]]:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM editing_jobs WHERE owner_id = %s AND idempotency_key = %s",
                (owner_id, key),
            )
            return cur.fetchone()

    def list_jobs(self, owner_id: str, *, status: Optional[str], limit: int, offset: int) -> Dict[str, Any]:
        where = "owner_id = %s"
        params: List[Any] = [owner_id]
        if status:
            where += " AND status = %s"
            params.append(status)
        with connection() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS total FROM editing_jobs WHERE {where}", params)
            total = cur.fetchone()["total"]
            cur.execute(
                f"SELECT * FROM editing_jobs WHERE {where} ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s",
                params + [limit, offset],
            )
            return {"items": cur.fetchall(), "total": total}

    def transition_job(
        self,
        job_id: str,
        to_status: str,
        *,
        stage: Optional[str] = None,
        progress: Optional[int] = None,
        message: Optional[str] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
        worker_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Advance a job one legal step, atomically, and record an event.

        Raises Conflict if the job is no longer in a status that allows the move
        (for example it was cancelled or another worker already advanced it).
        """
        job = self.get_job(job_id)
        if job is None:
            raise Conflict("Job not found.", code="job_not_found")
        from_status = job["status"]
        if not states.allowed(from_status, to_status):
            raise Conflict(
                f"Illegal job transition {from_status} -> {to_status}.",
                code="illegal_transition",
                details={"from": from_status, "to": to_status},
            )
        sets: Dict[str, Any] = {"status": to_status}
        if stage is not None:
            sets["stage"] = stage
        if progress is not None:
            sets["progress"] = max(0, min(100, int(progress)))
        if worker_id is not None:
            sets["worker_id"] = worker_id
            sets["heartbeat_at"] = _now()
        if to_status in states.ACTIVE and from_status == states.QUEUED and job.get("started_at") is None:
            sets["started_at"] = _now()
        if to_status == states.QUEUED and from_status in states.TERMINAL | states.ACTIVE:
            sets["attempt_count"] = int(job["attempt_count"]) + (1 if from_status in states.ACTIVE else 0)
        if to_status == states.COMPLETED:
            sets["completed_at"] = _now()
            sets["progress"] = 100
        if to_status == states.FAILED:
            sets["completed_at"] = _now()
            sets["error_code"] = error_code or "unknown_error"
            sets["error_message"] = (error_message or "The job failed.")[:2000]
        if to_status == states.CANCELLED:
            sets["cancelled_at"] = _now()
            sets["completed_at"] = _now()
        if to_status == states.QUEUED and from_status == states.FAILED:
            sets.update({"error_code": None, "error_message": None, "completed_at": None, "progress": 0})
        if extra:
            sets.update(extra)

        assignments = ", ".join(f"{k} = %s" for k in sets)
        with connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"UPDATE editing_jobs SET {assignments} WHERE id = %s AND status = %s RETURNING *",
                list(sets.values()) + [_uuid(job_id), from_status],
            )
            updated = cur.fetchone()
            if updated is None:
                conn.rollback()
                raise Conflict("Job changed while it was being updated; the operation was not applied.",
                               code="concurrent_update")
            cur.execute(
                "INSERT INTO job_events (job_id, event_type, from_status, to_status, stage, message, details) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (job_id, "status_changed", from_status, to_status, updated["stage"], message or error_message,
                 Jsonb(details or {})),
            )
            conn.commit()
            return updated

    def set_job_stage(self, job_id: str, *, stage: Optional[str] = None, progress: Optional[int] = None,
                      worker_id: Optional[str] = None) -> None:
        """Update stage/progress/heartbeat without changing status."""
        sets: Dict[str, Any] = {"heartbeat_at": _now()}
        if stage is not None:
            sets["stage"] = stage
        if progress is not None:
            sets["progress"] = max(0, min(100, int(progress)))
        if worker_id is not None:
            sets["worker_id"] = worker_id
        assignments = ", ".join(f"{k} = %s" for k in sets)
        with connection() as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE editing_jobs SET {assignments} WHERE id = %s", list(sets.values()) + [_uuid(job_id)])
            conn.commit()

    def request_cancel(self, job_id: str, owner_id: str) -> Optional[Dict[str, Any]]:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE editing_jobs SET cancel_requested = true WHERE id = %s AND owner_id = %s "
                "AND status NOT IN ('completed', 'failed', 'cancelled') RETURNING *",
                (_uuid(job_id), owner_id),
            )
            row = cur.fetchone()
            if row:
                cur.execute(
                    "INSERT INTO job_events (job_id, event_type, from_status, stage, message) "
                    "VALUES (%s, 'cancel_requested', %s, %s, 'Cancellation requested by owner')",
                    (job_id, row["status"], row["stage"]),
                )
            conn.commit()
            return row

    def is_cancel_requested(self, job_id: str) -> bool:
        with connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT cancel_requested FROM editing_jobs WHERE id = %s", (_uuid(job_id),))
            row = cur.fetchone()
            return bool(row and row["cancel_requested"])

    def list_events(self, job_id: str) -> List[Dict[str, Any]]:
        with connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM job_events WHERE job_id = %s ORDER BY created_at, id", (_uuid(job_id),))
            return cur.fetchall()

    def add_event(self, job_id: str, event_type: str, *, message: str = "", stage: Optional[str] = None,
                  details: Optional[Dict[str, Any]] = None) -> None:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO job_events (job_id, event_type, stage, message, details) VALUES (%s, %s, %s, %s, %s)",
                (_uuid(job_id), event_type, stage, message[:2000], Jsonb(details or {})),
            )
            conn.commit()

    def queued_older_than(self, seconds: int) -> List[Dict[str, Any]]:
        cutoff = _now() - timedelta(seconds=seconds)
        with connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM editing_jobs WHERE status = 'queued' AND queued_at < %s ORDER BY queued_at",
                        (cutoff,))
            return cur.fetchall()

    def jobs_with_stale_heartbeat(self, older_than_seconds: int) -> List[Dict[str, Any]]:
        cutoff = _now() - timedelta(seconds=older_than_seconds)
        with connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM editing_jobs WHERE status NOT IN ('completed', 'failed', 'cancelled') "
                "AND status <> 'queued' AND (heartbeat_at IS NULL OR heartbeat_at < %s) ORDER BY created_at",
                (cutoff,),
            )
            return cur.fetchall()

    # --------------------------------------------------------------- highlights
    def replace_highlights(self, job_id: str, rows: Iterable[Dict[str, Any]]) -> List[str]:
        ids: List[str] = []
        with connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM detected_highlights WHERE job_id = %s", (_uuid(job_id),))
            for r in rows:
                cur.execute(
                    """INSERT INTO detected_highlights (job_id, candidate_rank, start_seconds, end_seconds,
                       duration_seconds, suggested_title, description, transcript, selection_rationale,
                       engagement_score, content_category, confidence, selected, rejection_reason)
                       VALUES (%(job_id)s, %(candidate_rank)s, %(start_seconds)s, %(end_seconds)s,
                       %(duration_seconds)s, %(suggested_title)s, %(description)s, %(transcript)s,
                       %(selection_rationale)s, %(engagement_score)s, %(content_category)s, %(confidence)s,
                       %(selected)s, %(rejection_reason)s) RETURNING id""",
                    {**r, "job_id": _uuid(job_id)},
                )
                ids.append(str(cur.fetchone()["id"]))
            conn.commit()
        return ids

    def list_highlights(self, job_id: str) -> List[Dict[str, Any]]:
        with connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM detected_highlights WHERE job_id = %s ORDER BY candidate_rank",
                        (_uuid(job_id),))
            return cur.fetchall()

    # ------------------------------------------------------------------- clips
    def create_clip(self, **fields: Any) -> Dict[str, Any]:
        cols = list(fields.keys())
        sql = (f"INSERT INTO generated_clips ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) "
               "ON CONFLICT (job_id, clip_number) DO UPDATE SET "
               + ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c not in ("job_id", "clip_number"))
               + " RETURNING *")
        with connection() as conn, conn.cursor() as cur:
            cur.execute(sql, [Jsonb(v) if isinstance(v, dict) else v for v in fields.values()])
            row = cur.fetchone()
            conn.commit()
            return row

    def update_clip(self, clip_id: str, **fields: Any) -> Dict[str, Any]:
        assignments = ", ".join(f"{k} = %s" for k in fields)
        with connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"UPDATE generated_clips SET {assignments} WHERE id = %s RETURNING *",
                [Jsonb(v) if isinstance(v, dict) else v for v in fields.values()] + [_uuid(clip_id)],
            )
            row = cur.fetchone()
            conn.commit()
            return row

    def list_clips(self, job_id: str) -> List[Dict[str, Any]]:
        with connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM generated_clips WHERE job_id = %s ORDER BY clip_number", (_uuid(job_id),))
            return cur.fetchall()

    def get_clip(self, clip_id: str, owner_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        with connection() as conn, conn.cursor() as cur:
            if owner_id is None:
                cur.execute("SELECT * FROM generated_clips WHERE id = %s", (_uuid(clip_id),))
            else:
                cur.execute("SELECT * FROM generated_clips WHERE id = %s AND owner_id = %s",
                            (_uuid(clip_id), owner_id))
            return cur.fetchone()

    def save_caption(self, clip_id: str, fmt: str, **fields: Any) -> None:
        cols = ["clip_id", "format"] + list(fields.keys())
        vals = [_uuid(clip_id), fmt] + [Jsonb(v) if isinstance(v, dict) else v for v in fields.values()]
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in fields)
        with connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO clip_captions ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) "
                f"ON CONFLICT (clip_id, format) DO UPDATE SET {updates}",
                vals,
            )
            conn.commit()

    # ------------------------------------------------------------------- usage
    def record_usage(self, owner_id: str, job_id: Optional[str], metric: str, quantity: float) -> None:
        if quantity <= 0:
            return
        with connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO processing_usage (owner_id, job_id, metric, quantity) VALUES (%s, %s, %s, %s)",
                (owner_id, _uuid(job_id) if job_id else None, metric, quantity),
            )
            conn.commit()

    def usage_summary(self, owner_id: str, since: datetime) -> Dict[str, float]:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT metric, SUM(quantity) AS total FROM processing_usage "
                "WHERE owner_id = %s AND recorded_at >= %s GROUP BY metric",
                (owner_id, since),
            )
            return {r["metric"]: float(r["total"]) for r in cur.fetchall()}

    def owner_job_counts(self, owner_id: str, since: datetime) -> Dict[str, int]:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT status, COUNT(*) AS n FROM editing_jobs WHERE owner_id = %s AND created_at >= %s GROUP BY status",
                (owner_id, since),
            )
            return {r["status"]: int(r["n"]) for r in cur.fetchall()}

    def job_status_counts(self) -> Dict[str, int]:
        with connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT status, COUNT(*) AS n FROM editing_jobs GROUP BY status")
            return {r["status"]: int(r["n"]) for r in cur.fetchall()}

    # ----------------------------------------------------------------- workers
    def heartbeat(self, worker_id: str, *, hostname: str, pid: int, status: str,
                  current_job_id: Optional[str], version: str) -> None:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO worker_heartbeats (worker_id, hostname, pid, status, current_job_id, version, last_seen_at)
                   VALUES (%s, %s, %s, %s, %s, %s, now())
                   ON CONFLICT (worker_id) DO UPDATE SET hostname = EXCLUDED.hostname, pid = EXCLUDED.pid,
                     status = EXCLUDED.status, current_job_id = EXCLUDED.current_job_id,
                     version = EXCLUDED.version, last_seen_at = now()""",
                (worker_id, hostname, pid, status, _uuid(current_job_id) if current_job_id else None, version),
            )
            conn.commit()

    def list_workers(self, fresh_within_seconds: int) -> List[Dict[str, Any]]:
        cutoff = _now() - timedelta(seconds=fresh_within_seconds)
        with connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM worker_heartbeats ORDER BY last_seen_at DESC")
            rows = cur.fetchall()
        for r in rows:
            r["is_fresh"] = r["last_seen_at"] >= cutoff
        return rows

    def ping(self) -> None:
        with connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 AS ok")
            cur.fetchone()


repo = Repository()


def json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))
