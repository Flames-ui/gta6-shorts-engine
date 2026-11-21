"""Supabase database persistence service with multi-category support and fail-closed production safety."""
import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
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

        if has_creds:
            try:
                from supabase import Client, create_client
                self.client: Client = create_client(
                    settings.SUPABASE_URL,
                    settings.SUPABASE_SERVICE_ROLE_KEY,
                )
                logger.info("Supabase client initialized with endpoint: %s", settings.SUPABASE_URL)
                self.has_supabase = True
            except Exception as e:
                logger.error("Failed to initialize Supabase client: %s", e)
                if is_prod and settings.FAIL_CLOSED_IN_PRODUCTION:
                    raise RuntimeError(f"FATAL: Supabase initialization failed in production: {e}")
                self.has_supabase = False
        else:
            if is_prod and settings.FAIL_CLOSED_IN_PRODUCTION:
                raise RuntimeError(
                    "FATAL: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be configured in production. "
                    "Fail-closed policy prevents ephemeral SQLite fallback."
                )
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
                    created_at TEXT
                );

                CREATE TABLE IF NOT EXISTS source_permissions (
                    id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    rights_status TEXT NOT NULL,
                    license_type TEXT,
                    allowed_platforms TEXT,
                    expires_at TEXT,
                    created_at TEXT
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
                    updated_at TEXT
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
            # Remove transient attributes
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
            # Fetch associated clips and logs
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

            # Fetch clips
            clip_rows = cur.execute("SELECT * FROM clips WHERE job_id = ?", (job_id,)).fetchall()
            job["clips"] = [self._format_clip_row(c) for c in clip_rows]

            # Fetch logs
            log_rows = cur.execute("SELECT stage, message, created_at FROM job_logs WHERE job_id = ? ORDER BY id ASC", (job_id,)).fetchall()
            job["logs"] = [{"stage": r["stage"], "message": r["message"], "timestamp": r["created_at"]} for r in log_rows]
            return job

    def list_jobs(self, limit: int = 20, offset: int = 0, status: Optional[str] = None, user_id: Optional[str] = None) -> Tuple[List[Dict[str, Any]], int]:
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

    def list_clips(self, limit: int = 20, offset: int = 0, category_id: Optional[str] = None) -> Tuple[List[Dict[str, Any]], int]:
        if self.has_supabase:
            query = self.client.table("generated_clips").select("*", count="exact")
            if category_id:
                query = query.eq("category_id", category_id)
            res = query.order("created_at", desc=True).range(offset, offset + limit - 1).execute()
            return res.data or [], res.count or 0

        with self.lock, sqlite3.connect(self.sqlite_path) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            conds = ["category_id = ?"] if category_id else []
            params = [category_id] if category_id else []
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
    # Channels CRUD
    # -------------------------------------------------------------------------
    def list_channels(self) -> List[Dict[str, Any]]:
        if self.has_supabase:
            res = self.client.table("connected_channels").select("*").execute()
            return res.data or []

        with self.lock, sqlite3.connect(self.sqlite_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM channels ORDER BY created_at DESC").fetchall()
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


db = DatabaseClient()
