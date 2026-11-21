import uuid
from .security import encrypt_secret, decrypt_secret
"""Supabase database persistence service with multi-category support and fail-closed production safety."""
import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..config import settings

logger = logging.getLogger("gta6_shorts.supabase")


class DatabaseClient:
    """Manages persistent data for jobs, clips, categories, sources, publishing, and scheduling."""

    def __init__(self):
        self.client = None
        self.lock = threading.RLock()
        is_prod = settings.ENVIRONMENT.lower() == "production"
        has_creds = bool(settings.SUPABASE_URL and settings.SUPABASE_SERVICE_ROLE_KEY)

        if is_prod:
            if not has_creds:
                logger.critical("FATAL: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are mandatory in production.")
                raise RuntimeError(
                    "FATAL: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be configured in production. "
                    "Fail-closed policy forbids running with SQLite fallback in production."
                )
            try:
                from supabase import Client, create_client
                self.client: Client = create_client(
                    settings.SUPABASE_URL,
                    settings.SUPABASE_SERVICE_ROLE_KEY,
                )
                logger.info("Supabase client initialized with endpoint: %s", settings.SUPABASE_URL)
                self.has_supabase = True
            except Exception as e:
                logger.critical("FATAL: Failed to initialize Supabase client in production: %s", e)
                raise RuntimeError(f"FATAL: Supabase initialization failed in production: {e}") from e
        else:
            if has_creds:
                try:
                    from supabase import Client, create_client
                    self.client = create_client(
                        settings.SUPABASE_URL,
                        settings.SUPABASE_SERVICE_ROLE_KEY,
                    )
                    self.has_supabase = True
                except Exception as e:
                    logger.warning("Supabase initialization failed in dev (%s); falling back to SQLite", e)
                    self.has_supabase = False
            else:
                self.has_supabase = False

        if not self.has_supabase:
            logger.info("Initializing embedded SQLite storage for development/test environment.")
            self._init_sqlite()

    def _init_sqlite(self):
        """Initialize comprehensive SQLite database for local development and testing."""
        db_path = os.path.join(settings.LOCAL_OUTPUT_DIR, "shorts_engine.db")
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.sqlite_path = db_path

        with self.lock, sqlite3.connect(self.sqlite_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS categories (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    icon TEXT,
                    is_active INTEGER DEFAULT 1,
                    system_prompt TEXT,
                    scoring_weights TEXT,
                    default_hashtags TEXT,
                    presets TEXT,
                    publishing_preferences TEXT,
                    created_at TEXT
                );

                CREATE TABLE IF NOT EXISTS content_sources (
                    id TEXT PRIMARY KEY,
                    user_id TEXT,
                    source_type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    feed_url TEXT,
                    channel_url TEXT,
                    category_id TEXT,
                    is_active INTEGER DEFAULT 1,
                    auto_clip_enabled INTEGER DEFAULT 0,
                    last_polled_at TEXT,
                    last_success_at TEXT,
                    last_error TEXT,
                    consecutive_failures INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'active',
                    created_at TEXT
                );

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

                CREATE TABLE IF NOT EXISTS discovered_content (
                    id TEXT PRIMARY KEY,
                    source_id TEXT,
                    user_id TEXT,
                    title TEXT NOT NULL,
                    original_url TEXT,
                    media_url TEXT,
                    creator_name TEXT,
                    category_id TEXT,
                    rights_status TEXT,
                    content_hash TEXT,
                    duration_seconds REAL,
                    discovery_time TEXT,
                    is_processed INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    user_id TEXT,
                    url TEXT NOT NULL,
                    category_id TEXT DEFAULT 'gaming',
                    status TEXT NOT NULL DEFAULT 'queued',
                    stage TEXT NOT NULL DEFAULT 'pending',
                    progress INTEGER NOT NULL DEFAULT 0,
                    num_clips INTEGER NOT NULL DEFAULT 3,
                    aspect_ratio TEXT NOT NULL DEFAULT '9:16',
                    video_title TEXT,
                    video_duration REAL,
                    mode TEXT NOT NULL DEFAULT 'local',
                    llm_provider TEXT NOT NULL DEFAULT 'gemini',
                    gta_preset INTEGER DEFAULT 1,
                    error TEXT,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT,
                    updated_at TEXT
                );

                CREATE TABLE IF NOT EXISTS job_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at TEXT
                );

                CREATE TABLE IF NOT EXISTS clips (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    user_id TEXT,
                    category_id TEXT DEFAULT 'gaming',
                    title TEXT NOT NULL,
                    hook_sentence TEXT,
                    virality_reason TEXT,
                    score REAL NOT NULL DEFAULT 0,
                    start_time REAL NOT NULL,
                    end_time REAL NOT NULL,
                    duration REAL NOT NULL,
                    aspect_ratio TEXT NOT NULL DEFAULT '9:16',
                    storage_path TEXT,
                    storage_url TEXT NOT NULL,
                    thumbnail_url TEXT,
                    publishing_status TEXT NOT NULL DEFAULT 'draft',
                    suggested_tags TEXT,
                    is_quality_approved INTEGER DEFAULT 1,
                    created_at TEXT,
                    updated_at TEXT
                );

                CREATE TABLE IF NOT EXISTS channels (
                    id TEXT PRIMARY KEY,
                    user_id TEXT,
                    platform TEXT DEFAULT 'youtube',
                    channel_name TEXT NOT NULL,
                    channel_url TEXT NOT NULL,
                    auto_clip_new_videos INTEGER DEFAULT 0,
                    created_at TEXT
                );

                CREATE TABLE IF NOT EXISTS oauth_states (
                    state TEXT PRIMARY KEY,
                    user_id TEXT,
                    service TEXT NOT NULL,
                    redirect_uri TEXT,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    used_at TEXT
                );

                CREATE TABLE IF NOT EXISTS publishing_accounts (
                    id TEXT PRIMARY KEY,
                    user_id TEXT,
                    service TEXT NOT NULL,
                    account_identifier TEXT,
                    is_active INTEGER DEFAULT 1,
                    access_token_encrypted TEXT,
                    refresh_token_encrypted TEXT,
                    token_expires_at TEXT,
                    created_at TEXT,
                    updated_at TEXT
                );

                CREATE TABLE IF NOT EXISTS publishing_queue (
                    id TEXT PRIMARY KEY,
                    clip_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    user_id TEXT,
                    platform TEXT NOT NULL,
                    scheduled_for TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'scheduled',
                    platform_update_id TEXT,
                    error_message TEXT,
                    retry_count INTEGER DEFAULT 0,
                    created_at TEXT,
                    updated_at TEXT,
                    UNIQUE(clip_id, channel_id)
                );

                CREATE TABLE IF NOT EXISTS publishing_history (
                    id TEXT PRIMARY KEY,
                    clip_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    user_id TEXT,
                    platform TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    external_post_url TEXT,
                    initial_metrics TEXT
                );

                CREATE TABLE IF NOT EXISTS platform_analytics (
                    id TEXT PRIMARY KEY,
                    clip_id TEXT NOT NULL,
                    user_id TEXT,
                    platform TEXT NOT NULL,
                    views INTEGER DEFAULT 0,
                    reach INTEGER DEFAULT 0,
                    likes INTEGER DEFAULT 0,
                    comments INTEGER DEFAULT 0,
                    shares INTEGER DEFAULT 0,
                    saves INTEGER DEFAULT 0,
                    average_watch_time_seconds REAL,
                    completion_rate REAL,
                    engagement_rate REAL,
                    synced_at TEXT NOT NULL,
                    UNIQUE(clip_id, platform)
                );

                CREATE TABLE IF NOT EXISTS production_schedules (
                    id TEXT PRIMARY KEY DEFAULT 'default',
                    user_id TEXT,
                    daily_target_min INTEGER NOT NULL DEFAULT 15,
                    daily_target_max INTEGER NOT NULL DEFAULT 20,
                    timezone TEXT NOT NULL DEFAULT 'UTC',
                    is_paused INTEGER NOT NULL DEFAULT 0,
                    category_distribution TEXT,
                    publishing_windows TEXT,
                    updated_at TEXT
                );

                CREATE TABLE IF NOT EXISTS audit_logs (
                    id TEXT PRIMARY KEY,
                    user_id TEXT,
                    action TEXT NOT NULL,
                    resource_type TEXT NOT NULL,
                    resource_id TEXT,
                    details TEXT,
                    ip_address TEXT,
                    created_at TEXT
                );
            """)

    # -------------------------------------------------------------------------
    # Jobs CRUD
    # -------------------------------------------------------------------------
    def create_job(self, job_data: Dict[str, Any]) -> Dict[str, Any]:
        now_iso = datetime.now(timezone.utc).isoformat()
        job_data["created_at"] = job_data.get("created_at") or now_iso
        job_data["updated_at"] = now_iso
        job_data.setdefault("category_id", "gaming")

        if self.has_supabase:
            payload = dict(job_data)
            payload.pop("custom_prompt", None)
            payload.pop("title_prefix", None)
            payload.pop("target_format", None)
            payload.pop("language", None)
            res = self.client.table("processing_jobs").insert(payload).execute()
            return res.data[0] if res.data else job_data

        with self.lock, sqlite3.connect(self.sqlite_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO jobs
                (id, user_id, url, category_id, status, stage, progress, num_clips, aspect_ratio,
                 video_title, video_duration, mode, llm_provider, gta_preset, error, retry_count, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_data["id"],
                    job_data.get("user_id"),
                    job_data["url"],
                    job_data.get("category_id", "gaming"),
                    job_data.get("status", "queued"),
                    job_data.get("stage", "pending"),
                    job_data.get("progress", 0),
                    job_data.get("num_clips", 3),
                    job_data.get("aspect_ratio", "9:16"),
                    job_data.get("video_title"),
                    job_data.get("video_duration"),
                    job_data.get("mode", "local"),
                    job_data.get("llm_provider", "gemini"),
                    1 if job_data.get("gta_preset", True) else 0,
                    job_data.get("error"),
                    job_data.get("retry_count", 0),
                    job_data["created_at"],
                    job_data["updated_at"],
                ),
            )
        return job_data

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        if self.has_supabase:
            res = self.client.table("processing_jobs").select("*").eq("id", job_id).execute()
            if not res.data:
                return None
            job = res.data[0]
            clips_res = self.client.table("generated_clips").select("*").eq("job_id", job_id).execute()
            job["clips"] = clips_res.data or []
            logs_res = self.client.table("processing_logs").select("*").eq("job_id", job_id).order("created_at").execute()
            job["logs"] = logs_res.data or []
            return job

        with self.lock, sqlite3.connect(self.sqlite_path) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            row = cur.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not row:
                return None
            job = dict(row)
            job["gta_preset"] = bool(job.get("gta_preset", 1))

            clip_rows = cur.execute("SELECT * FROM clips WHERE job_id = ?", (job_id,)).fetchall()
            job["clips"] = [self._format_clip_row(c) for c in clip_rows]

            log_rows = cur.execute("SELECT stage, message, created_at FROM job_logs WHERE job_id = ? ORDER BY id ASC", (job_id,)).fetchall()
            job["logs"] = [{"stage": r["stage"], "message": r["message"], "timestamp": r["created_at"]} for r in log_rows]
            return job

    def list_jobs(
        self,
        limit: int = 20,
        offset: int = 0,
        status: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], int]:
        if self.has_supabase:
            query = self.client.table("processing_jobs").select("*", count="exact")
            if status:
                query = query.eq("status", status)
            if user_id:
                query = query.eq("user_id", user_id)
            res = query.order("created_at", desc=True).range(offset, offset + limit - 1).execute()
            return res.data or [], res.count or 0

        with self.lock, sqlite3.connect(self.sqlite_path) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            conditions = []
            params: List[Any] = []
            if status:
                conditions.append("status = ?")
                params.append(status)
            if user_id:
                conditions.append("user_id = ?")
                params.append(user_id)

            where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            total = cur.execute(f"SELECT COUNT(*) FROM jobs {where_clause}", params).fetchone()[0]

            query_sql = f"SELECT * FROM jobs {where_clause} ORDER BY created_at DESC LIMIT ? OFFSET ?"
            rows = cur.execute(query_sql, params + [limit, offset]).fetchall()
            items = []
            for r in rows:
                item = dict(r)
                item["gta_preset"] = bool(item.get("gta_preset", 1))
                item["clips"] = []
                item["logs"] = []
                items.append(item)
            return items, total

    def update_job_progress(
        self,
        job_id: str,
        stage: Optional[str] = None,
        progress: Optional[int] = None,
        status: Optional[str] = None,
        error: Optional[str] = None,
        retry_count: Optional[int] = None,
        video_title: Optional[str] = None,
        video_duration: Optional[float] = None,
    ):
        now_iso = datetime.now(timezone.utc).isoformat()
        updates: Dict[str, Any] = {"updated_at": now_iso}
        if stage is not None: updates["stage"] = stage
        if progress is not None: updates["progress"] = progress
        if status is not None: updates["status"] = status
        if error is not None: updates["error"] = error
        if retry_count is not None: updates["retry_count"] = retry_count
        if video_title is not None: updates["video_title"] = video_title
        if video_duration is not None: updates["video_duration"] = video_duration

        if self.has_supabase:
            self.client.table("processing_jobs").update(updates).eq("id", job_id).execute()
            return

        with self.lock, sqlite3.connect(self.sqlite_path) as conn:
            set_clauses = [f"{k} = ?" for k in updates.keys()]
            vals = list(updates.values()) + [job_id]
            conn.execute(f"UPDATE jobs SET {', '.join(set_clauses)} WHERE id = ?", vals)

    def find_active_job_by_url(self, url: str) -> Optional[Dict[str, Any]]:
        active_statuses = ["queued", "processing"]
        if self.has_supabase:
            res = self.client.table("processing_jobs").select("*").eq("url", url).in_("status", active_statuses).limit(1).execute()
            return res.data[0] if res.data else None

        with self.lock, sqlite3.connect(self.sqlite_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM jobs WHERE url = ? AND status IN ('queued', 'processing') LIMIT 1", (url,)).fetchone()
            return dict(row) if row else None

    # -------------------------------------------------------------------------
    # Logs CRUD
    # -------------------------------------------------------------------------
    def add_log(self, job_id: str, stage: str, message: str):
        now_iso = datetime.now(timezone.utc).isoformat()
        if self.has_supabase:
            try:
                self.client.table("processing_logs").insert({
                    "job_id": job_id,
                    "stage": stage,
                    "message": message,
                    "created_at": now_iso,
                }).execute()
            except Exception as e:
                logger.error("Failed to write log to Supabase: %s", e)
            return

        with self.lock, sqlite3.connect(self.sqlite_path) as conn:
            conn.execute("INSERT INTO job_logs (job_id, stage, message, created_at) VALUES (?, ?, ?, ?)",
                         (job_id, stage, message, now_iso))

    # -------------------------------------------------------------------------
    # Clips CRUD
    # -------------------------------------------------------------------------
    def create_clip(self, clip_data: Dict[str, Any]) -> Dict[str, Any]:
        now_iso = datetime.now(timezone.utc).isoformat()
        clip_data["created_at"] = clip_data.get("created_at") or now_iso
        clip_data["updated_at"] = now_iso
        clip_data.setdefault("category_id", "gaming")

        if self.has_supabase:
            payload = dict(clip_data)
            res = self.client.table("generated_clips").insert(payload).execute()
            return res.data[0] if res.data else clip_data

        with self.lock, sqlite3.connect(self.sqlite_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO clips
                (id, job_id, user_id, category_id, title, hook_sentence, virality_reason, score,
                 start_time, end_time, duration, aspect_ratio, storage_path, storage_url,
                 thumbnail_url, publishing_status, suggested_tags, is_quality_approved, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    clip_data["id"],
                    clip_data["job_id"],
                    clip_data.get("user_id"),
                    clip_data.get("category_id", "gaming"),
                    clip_data["title"],
                    clip_data.get("hook_sentence"),
                    clip_data.get("virality_reason"),
                    clip_data.get("score", 0),
                    clip_data["start_time"],
                    clip_data["end_time"],
                    clip_data["duration"],
                    clip_data.get("aspect_ratio", "9:16"),
                    clip_data.get("storage_path"),
                    clip_data["storage_url"],
                    clip_data.get("thumbnail_url"),
                    clip_data.get("publishing_status", "draft"),
                    json.dumps(clip_data.get("suggested_tags", [])),
                    1 if clip_data.get("is_quality_approved", True) else 0,
                    clip_data["created_at"],
                    clip_data["updated_at"],
                ),
            )
        return clip_data

    def get_clip(self, clip_id: str) -> Optional[Dict[str, Any]]:
        if self.has_supabase:
            res = self.client.table("generated_clips").select("*").eq("id", clip_id).execute()
            return res.data[0] if res.data else None

        with self.lock, sqlite3.connect(self.sqlite_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
            return self._format_clip_row(row) if row else None

    def list_clips(
        self,
        limit: int = 20,
        offset: int = 0,
        category_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], int]:
        if self.has_supabase:
            query = self.client.table("generated_clips").select("*", count="exact")
            if category_id:
                query = query.eq("category_id", category_id)
            if user_id:
                query = query.eq("user_id", user_id)
            res = query.order("created_at", desc=True).range(offset, offset + limit - 1).execute()
            return res.data or [], res.count or 0

        with self.lock, sqlite3.connect(self.sqlite_path) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            conds = []
            params = []
            if category_id:
                conds.append("category_id = ?")
                params.append(category_id)
            if user_id:
                conds.append("user_id = ?")
                params.append(user_id)

            where = f"WHERE {' AND '.join(conds)}" if conds else ""
            total = cur.execute(f"SELECT COUNT(*) FROM clips {where}", params).fetchone()[0]
            rows = cur.execute(f"SELECT * FROM clips {where} ORDER BY created_at DESC LIMIT ? OFFSET ?", params + [limit, offset]).fetchall()
            return [self._format_clip_row(r) for r in rows], total

    def update_clip(self, clip_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        if self.has_supabase:
            res = self.client.table("generated_clips").update(updates).eq("id", clip_id).execute()
            return res.data[0] if res.data else None

        with self.lock, sqlite3.connect(self.sqlite_path) as conn:
            set_clauses = []
            vals = []
            for k, v in updates.items():
                set_clauses.append(f"{k} = ?")
                vals.append(json.dumps(v) if isinstance(v, (list, dict)) else v)
            vals.append(clip_id)
            conn.execute(f"UPDATE clips SET {', '.join(set_clauses)} WHERE id = ?", vals)
        return self.get_clip(clip_id)

    def _format_clip_row(self, row: Optional[sqlite3.Row]) -> Dict[str, Any]:
        if not row:
            return {}
        d = dict(row)
        if isinstance(d.get("suggested_tags"), str):
            try:
                d["suggested_tags"] = json.loads(d["suggested_tags"])
            except Exception:
                d["suggested_tags"] = []
        d["is_quality_approved"] = bool(d.get("is_quality_approved", 1))
        return d

    # -------------------------------------------------------------------------
    # Content Sources CRUD
    # -------------------------------------------------------------------------
    def create_source(self, source_data: Dict[str, Any]) -> Dict[str, Any]:
        now_iso = datetime.now(timezone.utc).isoformat()
        source_data["created_at"] = now_iso
        if self.has_supabase:
            res = self.client.table("content_sources").insert(source_data).execute()
            return res.data[0] if res.data else source_data

        with self.lock, sqlite3.connect(self.sqlite_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO content_sources
                (id, user_id, source_type, name, feed_url, channel_url, category_id, is_active, auto_clip_enabled, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_data["id"],
                    source_data.get("user_id"),
                    source_data["source_type"],
                    source_data["name"],
                    source_data.get("feed_url"),
                    source_data.get("channel_url"),
                    source_data.get("category_id", "gaming"),
                    1 if source_data.get("is_active", True) else 0,
                    1 if source_data.get("auto_clip_enabled", False) else 0,
                    now_iso,
                ),
            )
        return source_data

    def get_source(self, source_id: str) -> Optional[Dict[str, Any]]:
        if self.has_supabase:
            res = self.client.table("content_sources").select("*").eq("id", source_id).execute()
            return res.data[0] if res.data else None

        with self.lock, sqlite3.connect(self.sqlite_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM content_sources WHERE id = ?", (source_id,)).fetchone()
            if not row:
                return None
            d = dict(row)
            d["is_active"] = bool(d.get("is_active", 1))
            d["auto_clip_enabled"] = bool(d.get("auto_clip_enabled", 0))
            return d

    def list_sources(
        self,
        user_id: Optional[str] = None,
        category_id: Optional[str] = None,
        active_only: bool = True,
    ) -> List[Dict[str, Any]]:
        if self.has_supabase:
            query = self.client.table("content_sources").select("*")
            if user_id:
                query = query.eq("user_id", user_id)
            if category_id:
                query = query.eq("category_id", category_id)
            if active_only:
                query = query.eq("is_active", True)
            res = query.order("created_at", desc=True).execute()
            return res.data or []

        with self.lock, sqlite3.connect(self.sqlite_path) as conn:
            conn.row_factory = sqlite3.Row
            conds = []
            params = []
            if user_id:
                conds.append("user_id = ?")
                params.append(user_id)
            if category_id:
                conds.append("category_id = ?")
                params.append(category_id)
            if active_only:
                conds.append("is_active = 1")
            where = f"WHERE {' AND '.join(conds)}" if conds else ""
            rows = conn.execute(f"SELECT * FROM content_sources {where} ORDER BY created_at DESC", params).fetchall()
            results = []
            for r in rows:
                d = dict(r)
                d["is_active"] = bool(d.get("is_active", 1))
                d["auto_clip_enabled"] = bool(d.get("auto_clip_enabled", 0))
                results.append(d)
            return results

    # -------------------------------------------------------------------------
    # Source Permissions CRUD
    # -------------------------------------------------------------------------
    def set_source_permission(self, source_id: str, permission_data: Dict[str, Any]) -> Dict[str, Any]:
        now_iso = datetime.now(timezone.utc).isoformat()
        permission_data["source_id"] = source_id
        permission_data["updated_at"] = now_iso

        if self.has_supabase:
            res = self.client.table("source_permissions").upsert(permission_data, on_conflict="source_id").execute()
            return res.data[0] if res.data else permission_data

        with self.lock, sqlite3.connect(self.sqlite_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO source_permissions
                (id, source_id, rights_status, license_type, allowed_platforms, expires_at,
                 attribution_requirements, verification_notes, verified_by, original_creator,
                 source_url, permission_evidence, derivative_rights_allowed, revoked_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    permission_data.get("id", f"perm_{source_id}"),
                    source_id,
                    permission_data.get("rights_status", "pending_verification"),
                    permission_data.get("license_type"),
                    json.dumps(permission_data.get("allowed_platforms", ["tiktok", "youtube_shorts"])),
                    permission_data.get("expires_at"),
                    permission_data.get("attribution_requirements"),
                    permission_data.get("verification_notes"),
                    permission_data.get("verified_by"),
                    permission_data.get("original_creator"),
                    permission_data.get("source_url"),
                    permission_data.get("permission_evidence"),
                    1 if permission_data.get("derivative_rights_allowed", True) else 0,
                    permission_data.get("revoked_at"),
                    permission_data.get("created_at", now_iso),
                    now_iso,
                ),
            )
        return permission_data

    def get_source_permission(self, source_id: str) -> Optional[Dict[str, Any]]:
        if self.has_supabase:
            res = self.client.table("source_permissions").select("*").eq("source_id", source_id).limit(1).execute()
            return res.data[0] if res.data else None

        with self.lock, sqlite3.connect(self.sqlite_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM source_permissions WHERE source_id = ?", (source_id,)).fetchone()
            if not row:
                return None
            d = dict(row)
            if isinstance(d.get("allowed_platforms"), str):
                try:
                    d["allowed_platforms"] = json.loads(d["allowed_platforms"])
                except Exception:
                    d["allowed_platforms"] = ["tiktok", "youtube_shorts"]
            return d

    # -------------------------------------------------------------------------
    # Discovered Content CRUD
    # -------------------------------------------------------------------------
    def save_discovered_content(self, item_data: Dict[str, Any]) -> Dict[str, Any]:
        now_iso = datetime.now(timezone.utc).isoformat()
        item_data["discovery_time"] = item_data.get("discovery_time") or now_iso
        if self.has_supabase:
            res = self.client.table("discovered_content").upsert(item_data, on_conflict="id").execute()
            return res.data[0] if res.data else item_data

        with self.lock, sqlite3.connect(self.sqlite_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO discovered_content
                (id, source_id, user_id, title, original_url, media_url, creator_name, category_id,
                 rights_status, content_hash, duration_seconds, discovery_time, is_processed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_data["id"],
                    item_data.get("source_id"),
                    item_data.get("user_id"),
                    item_data["title"],
                    item_data.get("original_url"),
                    item_data.get("media_url"),
                    item_data.get("creator_name"),
                    item_data.get("category_id", "gaming"),
                    item_data.get("rights_status", "pending_verification"),
                    item_data.get("content_hash"),
                    item_data.get("duration_seconds", 0.0),
                    item_data["discovery_time"],
                    1 if item_data.get("is_processed", False) else 0,
                ),
            )
        return item_data

    def list_discovered_content(
        self,
        category_id: Optional[str] = None,
        user_id: Optional[str] = None,
        is_processed: Optional[bool] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        if self.has_supabase:
            query = self.client.table("discovered_content").select("*")
            if category_id:
                query = query.eq("category_id", category_id)
            if user_id:
                query = query.eq("user_id", user_id)
            if is_processed is not None:
                query = query.eq("is_processed", is_processed)
            res = query.order("discovery_time", desc=True).limit(limit).execute()
            return res.data or []

        with self.lock, sqlite3.connect(self.sqlite_path) as conn:
            conn.row_factory = sqlite3.Row
            conds = []
            params = []
            if category_id:
                conds.append("category_id = ?")
                params.append(category_id)
            if user_id:
                conds.append("user_id = ?")
                params.append(user_id)
            if is_processed is not None:
                conds.append("is_processed = ?")
                params.append(1 if is_processed else 0)
            where = f"WHERE {' AND '.join(conds)}" if conds else ""
            rows = conn.execute(f"SELECT * FROM discovered_content {where} ORDER BY discovery_time DESC LIMIT ?", params + [limit]).fetchall()
            results = []
            for r in rows:
                d = dict(r)
                d["is_processed"] = bool(d.get("is_processed", 0))
                results.append(d)
            return results

    # -------------------------------------------------------------------------
    # OAuth States CRUD (Single-Use Validation)
    # -------------------------------------------------------------------------
    def save_oauth_state(
        self,
        state: str,
        user_id: Optional[str],
        service: str,
        redirect_uri: Optional[str] = None,
        expires_in_seconds: int = 600,
    ) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=expires_in_seconds)
        data = {
            "state": state,
            "user_id": user_id,
            "service": service,
            "redirect_uri": redirect_uri,
            "created_at": now.isoformat(),
            "expires_at": expires.isoformat(),
            "used_at": None,
        }
        if self.has_supabase:
            self.client.table("oauth_states").insert(data).execute()
            return data

        with self.lock, sqlite3.connect(self.sqlite_path) as conn:
            conn.execute(
                "INSERT INTO oauth_states (state, user_id, service, redirect_uri, created_at, expires_at, used_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (state, user_id, service, redirect_uri, data["created_at"], data["expires_at"], None),
            )
        return data

    def validate_and_consume_oauth_state(self, state: str, service: str) -> Optional[Dict[str, Any]]:
        """
        Validate OAuth state atomically:
        Must exist, match service, not be expired, and NOT have been used.
        Marks state as used (single-use) immediately.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        if self.has_supabase:
            res = self.client.table("oauth_states").select("*").eq("state", state).eq("service", service).is_("used_at", "null").execute()
            if not res.data:
                return None
            record = res.data[0]
            # Check expiry
            if record["expires_at"] < now_iso:
                logger.warning("OAuth state %s has expired", state)
                return None
            # Mark consumed
            self.client.table("oauth_states").update({"used_at": now_iso}).eq("state", state).execute()
            return record

        with self.lock, sqlite3.connect(self.sqlite_path) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            row = cur.execute(
                "SELECT * FROM oauth_states WHERE state = ? AND service = ? AND used_at IS NULL",
                (state, service),
            ).fetchone()
            if not row:
                return None
            record = dict(row)
            if record["expires_at"] < now_iso:
                logger.warning("OAuth state %s has expired", state)
                return None
            cur.execute("UPDATE oauth_states SET used_at = ? WHERE state = ?", (now_iso, state))
            return record

    # -------------------------------------------------------------------------
    # Publishing Accounts CRUD
    # -------------------------------------------------------------------------
    def save_publishing_account(self, account_data: Dict[str, Any]) -> Dict[str, Any]:
        now_iso = datetime.now(timezone.utc).isoformat()
        if "access_token" in account_data and account_data["access_token"]:
            account_data["access_token_encrypted"] = encrypt_secret(account_data["access_token"])
        if "refresh_token" in account_data and account_data["refresh_token"]:
            account_data["refresh_token_encrypted"] = encrypt_secret(account_data["refresh_token"])
        account_data["created_at"] = account_data.get("created_at") or now_iso
        account_data["updated_at"] = now_iso

        if self.has_supabase:
            res = self.client.table("publishing_accounts").upsert(account_data, on_conflict="id").execute()
            return res.data[0] if res.data else account_data

        with self.lock, sqlite3.connect(self.sqlite_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO publishing_accounts
                (id, user_id, service, account_identifier, is_active, access_token_encrypted, refresh_token_encrypted, token_expires_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_data["id"],
                    account_data.get("user_id"),
                    account_data["service"],
                    account_data.get("account_identifier"),
                    1 if account_data.get("is_active", True) else 0,
                    account_data.get("access_token_encrypted"),
                    account_data.get("refresh_token_encrypted"),
                    account_data.get("token_expires_at"),
                    account_data["created_at"],
                    account_data["updated_at"],
                ),
            )
        return account_data

    def get_publishing_account_for_user(self, user_id: str, service: str) -> Optional[Dict[str, Any]]:
        acc = None
        if self.has_supabase:
            res = self.client.table("publishing_accounts").select("*").eq("user_id", user_id).eq("service", service).eq("is_active", True).limit(1).execute()
            acc = res.data[0] if res.data else None
        else:
            with self.lock, sqlite3.connect(self.sqlite_path) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT * FROM publishing_accounts WHERE user_id = ? AND service = ? AND is_active = 1 LIMIT 1",
                    (user_id, service),
                ).fetchone()
                acc = dict(row) if row else None

        if acc:
            if acc.get("access_token_encrypted"):
                acc["access_token"] = decrypt_secret(acc["access_token_encrypted"])
            if acc.get("refresh_token_encrypted"):
                acc["refresh_token"] = decrypt_secret(acc["refresh_token_encrypted"])
        return acc

    # -------------------------------------------------------------------------
    # Publishing Queue CRUD
    # -------------------------------------------------------------------------
    def create_publishing_queue_item(self, item_data: Dict[str, Any]) -> Dict[str, Any]:
        now_iso = datetime.now(timezone.utc).isoformat()
        item_data["created_at"] = now_iso
        item_data["updated_at"] = now_iso

        if self.has_supabase:
            res = self.client.table("publishing_queue").insert(item_data).execute()
            return res.data[0] if res.data else item_data

        with self.lock, sqlite3.connect(self.sqlite_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO publishing_queue
                (id, clip_id, channel_id, user_id, platform, scheduled_for, status, platform_update_id, error_message, retry_count, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_data["id"],
                    item_data["clip_id"],
                    item_data["channel_id"],
                    item_data.get("user_id"),
                    item_data["platform"],
                    item_data["scheduled_for"],
                    item_data.get("status", "scheduled"),
                    item_data.get("platform_update_id"),
                    item_data.get("error_message"),
                    item_data.get("retry_count", 0),
                    now_iso,
                    now_iso,
                ),
            )
        return item_data

    def list_publishing_queue(
        self,
        user_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        if self.has_supabase:
            query = self.client.table("publishing_queue").select("*")
            if user_id:
                query = query.eq("user_id", user_id)
            if status:
                query = query.eq("status", status)
            res = query.order("scheduled_for", desc=False).limit(limit).execute()
            return res.data or []

        with self.lock, sqlite3.connect(self.sqlite_path) as conn:
            conn.row_factory = sqlite3.Row
            conds = []
            params = []
            if user_id:
                conds.append("user_id = ?")
                params.append(user_id)
            if status:
                conds.append("status = ?")
                params.append(status)
            where = f"WHERE {' AND '.join(conds)}" if conds else ""
            rows = conn.execute(f"SELECT * FROM publishing_queue {where} ORDER BY scheduled_for ASC LIMIT ?", params + [limit]).fetchall()
            return [dict(r) for r in rows]

    # -------------------------------------------------------------------------
    # Platform Analytics CRUD
    # -------------------------------------------------------------------------
    def save_platform_analytics(self, clip_id: str, platform: str, metrics: Dict[str, Any], user_id: Optional[str] = None) -> Dict[str, Any]:
        now_iso = datetime.now(timezone.utc).isoformat()
        record = {
            "id": f"{clip_id}_{platform}",
            "clip_id": clip_id,
            "user_id": user_id,
            "platform": platform,
            "views": int(metrics.get("views") or 0),
            "reach": int(metrics.get("reach") or 0),
            "likes": int(metrics.get("likes") or 0),
            "comments": int(metrics.get("comments") or 0),
            "shares": int(metrics.get("shares") or 0),
            "saves": int(metrics.get("saves") or 0),
            "average_watch_time_seconds": metrics.get("average_watch_time_seconds"),
            "completion_rate": metrics.get("completion_rate"),
            "engagement_rate": metrics.get("engagement_rate"),
            "synced_at": now_iso,
        }

        if self.has_supabase:
            res = self.client.table("platform_analytics").upsert(record, on_conflict="clip_id,platform").execute()
            return res.data[0] if res.data else record

        with self.lock, sqlite3.connect(self.sqlite_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO platform_analytics
                (id, clip_id, user_id, platform, views, reach, likes, comments, shares, saves,
                 average_watch_time_seconds, completion_rate, engagement_rate, synced_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["id"],
                    clip_id,
                    user_id,
                    platform,
                    record["views"],
                    record["reach"],
                    record["likes"],
                    record["comments"],
                    record["shares"],
                    record["saves"],
                    record["average_watch_time_seconds"],
                    record["completion_rate"],
                    record["engagement_rate"],
                    now_iso,
                ),
            )
        return record

    def get_platform_analytics(self, clip_id: str, platform: Optional[str] = None) -> Optional[Dict[str, Any]]:
        if self.has_supabase:
            query = self.client.table("platform_analytics").select("*").eq("clip_id", clip_id)
            if platform:
                query = query.eq("platform", platform)
            res = query.limit(1).execute()
            return res.data[0] if res.data else None

        with self.lock, sqlite3.connect(self.sqlite_path) as conn:
            conn.row_factory = sqlite3.Row
            conds = ["clip_id = ?"]
            params = [clip_id]
            if platform:
                conds.append("platform = ?")
                params.append(platform)
            row = conn.execute(f"SELECT * FROM platform_analytics WHERE {' AND '.join(conds)} LIMIT 1", params).fetchone()
            return dict(row) if row else None

    def get_analytics_overview(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Calculate real aggregate metrics from verified stored analytics."""
        if self.has_supabase:
            query = self.client.table("platform_analytics").select("*")
            if user_id:
                query = query.eq("user_id", user_id)
            res = query.execute()
            rows = res.data or []
        else:
            with self.lock, sqlite3.connect(self.sqlite_path) as conn:
                conn.row_factory = sqlite3.Row
                cond = "WHERE user_id = ?" if user_id else ""
                params = [user_id] if user_id else []
                rows = [dict(r) for r in conn.execute(f"SELECT * FROM platform_analytics {cond}", params).fetchall()]

        total_clips = len(rows)
        total_views = sum(r.get("views", 0) or 0 for r in rows)
        rates = [float(r["engagement_rate"]) for r in rows if r.get("engagement_rate") is not None]
        avg_rate = round(sum(rates) / len(rates), 2) if rates else 0.0

        # Platform count
        platform_counts: Dict[str, int] = {}
        for r in rows:
            p = r.get("platform", "unknown")
            platform_counts[p] = platform_counts.get(p, 0) + (r.get("views", 0) or 0)
        top_platform = max(platform_counts, key=platform_counts.get) if platform_counts else "tiktok"

        # Category breakdown
        cat_stats: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            cid = r.get("clip_id")
            clip = self.get_clip(cid) if cid else None
            cat_id = (clip.get("category_id") if clip else None) or "gaming"
            if cat_id not in cat_stats:
                cat_stats[cat_id] = {"category_id": cat_id, "total_views": 0, "rates": [], "count": 0}
            cat_stats[cat_id]["total_views"] += (r.get("views", 0) or 0)
            cat_stats[cat_id]["count"] += 1
            if r.get("engagement_rate") is not None:
                cat_stats[cat_id]["rates"].append(float(r["engagement_rate"]))

        cat_breakdown = []
        for cat_id, s in cat_stats.items():
            avg_eng = round(sum(s["rates"]) / len(s["rates"]), 2) if s["rates"] else 0.0
            cat_breakdown.append({
                "category_id": cat_id,
                "total_views": s["total_views"],
                "total_clips": s["count"],
                "avg_engagement_rate": avg_eng,
            })

        return {
            "total_published_clips": total_clips,
            "total_clips_analyzed": total_clips,
            "total_views": total_views,
            "aggregate_views": total_views,
            "average_engagement_rate": avg_rate,
            "top_performing_platform": top_platform,
            "category_breakdown": cat_breakdown,
        }

    # -------------------------------------------------------------------------
    # Channels CRUD
    # -------------------------------------------------------------------------
    def list_channels(self, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if self.has_supabase:
            query = self.client.table("connected_channels").select("*")
            if user_id:
                query = query.eq("user_id", user_id)
            res = query.order("created_at", desc=True).execute()
            return res.data or []

        with self.lock, sqlite3.connect(self.sqlite_path) as conn:
            conn.row_factory = sqlite3.Row
            cond = "WHERE user_id = ?" if user_id else ""
            params = [user_id] if user_id else []
            rows = conn.execute(f"SELECT * FROM channels {cond} ORDER BY created_at DESC", params).fetchall()
            return [dict(r) for r in rows]

    def create_channel(self, channel_data: Dict[str, Any]) -> Dict[str, Any]:
        now_iso = datetime.now(timezone.utc).isoformat()
        channel_data["created_at"] = now_iso

        if self.has_supabase:
            self.client.table("connected_channels").insert(channel_data).execute()
            return channel_data

        with self.lock, sqlite3.connect(self.sqlite_path) as conn:
            conn.execute(
                "INSERT INTO channels (id, user_id, channel_name, channel_url, auto_clip_new_videos, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    channel_data["id"],
                    channel_data.get("user_id"),
                    channel_data.get("channel_name", "Channel"),
                    channel_data.get("channel_url", ""),
                    1 if channel_data.get("auto_clip_new_videos", False) else 0,
                    now_iso,
                ),
            )
        return channel_data



    # -------------------------------------------------------------------------
    # Audit Logs
    # -------------------------------------------------------------------------
    def create_audit_log(
        self,
        action: str,
        resource_type: str,
        user_id: Optional[str] = None,
        resource_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        ip_address: Optional[str] = None,
    ) -> Dict[str, Any]:
        log_id = str(uuid.uuid4())
        now_iso = datetime.now(timezone.utc).isoformat()
        entry = {
            "id": log_id,
            "user_id": user_id,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "details": details or {},
            "ip_address": ip_address,
            "created_at": now_iso,
        }

        if self.has_supabase:
            try:
                self.client.table("audit_logs").insert(entry).execute()
            except Exception as exc:
                logger.error("Failed to persist audit log to Supabase: %s", exc)
            return entry

        try:
            with self.lock, sqlite3.connect(self.sqlite_path) as conn:
                conn.execute(
                    """
                    INSERT INTO audit_logs (id, user_id, action, resource_type, resource_id, details, ip_address, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (log_id, user_id, action, resource_type, resource_id, json.dumps(details or {}), ip_address, now_iso),
                )
        except Exception as exc:
            logger.error("Failed to persist audit log to SQLite: %s", exc)
        return entry

    def list_audit_logs(
        self,
        user_id: Optional[str] = None,
        action: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        if self.has_supabase:
            query = self.client.table("audit_logs").select("*")
            if user_id:
                query = query.eq("user_id", user_id)
            if action:
                query = query.eq("action", action)
            res = query.order("created_at", desc=True).limit(limit).execute()
            return res.data or []

        with self.lock, sqlite3.connect(self.sqlite_path) as conn:
            conn.row_factory = sqlite3.Row
            conds = []
            params = []
            if user_id:
                conds.append("user_id = ?")
                params.append(user_id)
            if action:
                conds.append("action = ?")
                params.append(action)
            where = f"WHERE {' AND '.join(conds)}" if conds else ""
            rows = conn.execute(f"SELECT * FROM audit_logs {where} ORDER BY created_at DESC LIMIT ?", params + [limit]).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                if isinstance(d.get("details"), str):
                    try:
                        d["details"] = json.loads(d["details"])
                    except Exception:
                        pass
                out.append(d)
            return out

    # -------------------------------------------------------------------------
    # Content Source Health Tracking
    # -------------------------------------------------------------------------
    def update_source_health(
        self,
        source_id: str,
        status: Optional[str] = None,
        error: Optional[str] = None,
        last_polled_at: Optional[str] = None,
        last_success_at: Optional[str] = None,
        increment_failures: bool = False,
        reset_failures: bool = False,
    ) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        polled_at = last_polled_at or now_iso
        updates: Dict[str, Any] = {"last_polled_at": polled_at}
        if status:
            updates["status"] = status
        if error is not None:
            updates["last_error"] = error
        if last_success_at:
            updates["last_success_at"] = last_success_at

        if self.has_supabase:
            try:
                self.client.table("content_sources").update(updates).eq("id", source_id).execute()
            except Exception as exc:
                logger.warning("Failed to update source health in Supabase: %s", exc)
            return

        with self.lock, sqlite3.connect(self.sqlite_path) as conn:
            cols = []
            vals = []
            for k, v in updates.items():
                cols.append(f"{k} = ?")
                vals.append(v)
            if increment_failures:
                cols.append("consecutive_failures = COALESCE(consecutive_failures, 0) + 1")
            elif reset_failures:
                cols.append("consecutive_failures = 0")
            if cols:
                vals.append(source_id)
                conn.execute(f"UPDATE content_sources SET {', '.join(cols)} WHERE id = ?", vals)

    # -------------------------------------------------------------------------
    # Multi-Category Database Persistence
    # -------------------------------------------------------------------------
    def list_categories(self, active_only: bool = False) -> List[Dict[str, Any]]:
        if self.has_supabase:
            query = self.client.table("categories").select("*")
            if active_only:
                query = query.eq("is_active", True)
            res = query.order("name").execute()
            return res.data or []

        with self.lock, sqlite3.connect(self.sqlite_path) as conn:
            conn.row_factory = sqlite3.Row
            where = "WHERE is_active = 1" if active_only else ""
            rows = conn.execute(f"SELECT * FROM categories {where} ORDER BY name").fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["is_active"] = bool(d.get("is_active", 1))
                for json_col in ("scoring_weights", "default_hashtags", "presets", "publishing_preferences"):
                    if isinstance(d.get(json_col), str):
                        try:
                            d[json_col] = json.loads(d[json_col])
                        except Exception:
                            pass
                out.append(d)
            return out

    def get_category(self, category_id: str) -> Optional[Dict[str, Any]]:
        if self.has_supabase:
            res = self.client.table("categories").select("*").eq("id", category_id).limit(1).execute()
            return res.data[0] if res.data else None

        with self.lock, sqlite3.connect(self.sqlite_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM categories WHERE id = ? LIMIT 1", (category_id,)).fetchone()
            if not row:
                return None
            d = dict(row)
            d["is_active"] = bool(d.get("is_active", 1))
            for json_col in ("scoring_weights", "default_hashtags", "presets", "publishing_preferences"):
                if isinstance(d.get(json_col), str):
                    try:
                        d[json_col] = json.loads(d[json_col])
                    except Exception:
                        pass
            return d

    def save_category(self, category_data: Dict[str, Any]) -> Dict[str, Any]:
        now_iso = datetime.now(timezone.utc).isoformat()
        category_data.setdefault("created_at", now_iso)
        cat_id = category_data["id"]

        if self.has_supabase:
            self.client.table("categories").upsert(category_data, on_conflict="id").execute()
            return category_data

        with self.lock, sqlite3.connect(self.sqlite_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO categories
                (id, name, description, icon, is_active, system_prompt, scoring_weights, default_hashtags, presets, publishing_preferences, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cat_id,
                    category_data.get("name", cat_id),
                    category_data.get("description", ""),
                    category_data.get("icon", "sparkles"),
                    1 if category_data.get("is_active", True) else 0,
                    category_data.get("system_prompt", ""),
                    json.dumps(category_data.get("scoring_weights", {})),
                    json.dumps(category_data.get("default_hashtags", [])),
                    json.dumps(category_data.get("presets", {})),
                    json.dumps(category_data.get("publishing_preferences", {})),
                    category_data["created_at"],
                ),
            )
        return category_data

    def update_category(self, category_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        current = self.get_category(category_id)
        if not current:
            return None
        current.update(updates)
        return self.save_category(current)

    def list_publishing_accounts(self, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if self.has_supabase:
            query = self.client.table("publishing_accounts").select("*").eq("is_active", True)
            if user_id:
                query = query.eq("user_id", user_id)
            res = query.order("created_at", desc=True).execute()
            accounts = res.data or []
        else:
            with self.lock, sqlite3.connect(self.sqlite_path) as conn:
                conn.row_factory = sqlite3.Row
                cond = "WHERE is_active = 1"
                params = []
                if user_id:
                    cond += " AND user_id = ?"
                    params.append(user_id)
                rows = conn.execute(f"SELECT * FROM publishing_accounts {cond} ORDER BY created_at DESC", params).fetchall()
                accounts = [dict(r) for r in rows]

        for acc in accounts:
            if acc.get("access_token_encrypted"):
                acc["access_token"] = decrypt_secret(acc["access_token_encrypted"])
            if acc.get("refresh_token_encrypted"):
                acc["refresh_token"] = decrypt_secret(acc["refresh_token_encrypted"])
        return accounts


db = DatabaseClient()

