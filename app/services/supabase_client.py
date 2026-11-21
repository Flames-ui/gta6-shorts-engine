"""Supabase database and storage integration with resilient local fallback."""
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from ..config import settings

logger = logging.getLogger("gta6_shorts.supabase")


class DatabaseClient:
    """Manages persistence for jobs, clips, logs, and channel monitoring."""

    def __init__(self):
        self.client = None
        self.has_supabase = bool(settings.SUPABASE_URL and settings.SUPABASE_SERVICE_ROLE_KEY)
        self.lock = threading.RLock()

        if self.has_supabase:
            try:
                from supabase import create_client, Client
                self.client: Client = create_client(
                    settings.SUPABASE_URL,
                    settings.SUPABASE_SERVICE_ROLE_KEY,
                )
                logger.info("Supabase client initialized with URL: %s", settings.SUPABASE_URL)
            except Exception as e:
                logger.error("Failed to initialize Supabase client, falling back to SQLite: %s", e)
                self.has_supabase = False

        if not self.has_supabase:
            logger.info("Running with embedded SQLite persistence (Supabase credentials not configured).")
            self._init_sqlite()

    def _init_sqlite(self):
        """Initialize local SQLite tables matching the Supabase schema."""
        db_path = os.path.join(settings.LOCAL_OUTPUT_DIR, "shorts_engine.db")
        os.makedirs(settings.LOCAL_OUTPUT_DIR, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self.lock:
            cur = self.conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    url TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    progress INTEGER DEFAULT 0,
                    num_clips INTEGER DEFAULT 3,
                    video_title TEXT,
                    video_duration REAL,
                    mode TEXT DEFAULT 'local',
                    llm_provider TEXT DEFAULT 'gemini',
                    gta_preset INTEGER DEFAULT 1,
                    error TEXT,
                    retry_count INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT,
                    completed_at TEXT
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS clips (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT,
                    hook TEXT,
                    score INTEGER DEFAULT 0,
                    start_time REAL NOT NULL,
                    end_time REAL NOT NULL,
                    duration REAL NOT NULL,
                    aspect_ratio TEXT DEFAULT '9:16',
                    clip_url TEXT,
                    storage_path TEXT,
                    thumbnail_url TEXT,
                    captions TEXT,
                    hashtags TEXT,
                    publishing_status TEXT DEFAULT 'draft',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES jobs(id)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS processing_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES jobs(id)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS channels (
                    id TEXT PRIMARY KEY,
                    channel_url TEXT NOT NULL,
                    channel_name TEXT NOT NULL,
                    auto_clip_new_videos INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL
                )
            """)
            self.conn.commit()

    # --- Job Operations ---

    def create_job(self, job_data: Dict[str, Any]) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        job_data["created_at"] = now
        job_data["updated_at"] = now

        if self.has_supabase:
            try:
                row = {
                    "id": job_data["id"],
                    "url": job_data["url"],
                    "status": job_data["status"],
                    "stage": job_data.get("stage", "pending"),
                    "progress": job_data.get("progress", 0),
                    "num_clips": job_data.get("num_clips", 3),
                    "video_title": job_data.get("video_title"),
                    "video_duration": job_data.get("video_duration"),
                    "mode": job_data.get("mode", "local"),
                    "llm_provider": job_data.get("llm_provider", "gemini"),
                    "gta_preset": bool(job_data.get("gta_preset", True)),
                    "retry_count": job_data.get("retry_count", 0),
                    "created_at": now,
                    "updated_at": now,
                }
                res = self.client.table("jobs").insert(row).execute()
                return res.data[0] if res.data else job_data
            except Exception as e:
                logger.error("Supabase insert job error: %s", e)

        # Fallback to SQLite
        with self.lock:
            cur = self.conn.cursor()
            cur.execute("""
                INSERT OR REPLACE INTO jobs (id, url, status, stage, progress, num_clips, video_title, video_duration, mode, llm_provider, gta_preset, retry_count, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                job_data["id"],
                job_data["url"],
                str(job_data["status"]),
                str(job_data.get("stage", "pending")),
                job_data.get("progress", 0),
                job_data.get("num_clips", 3),
                job_data.get("video_title"),
                job_data.get("video_duration"),
                job_data.get("mode", "local"),
                job_data.get("llm_provider", "gemini"),
                1 if job_data.get("gta_preset", True) else 0,
                job_data.get("retry_count", 0),
                now,
                now,
            ))
            self.conn.commit()
        return job_data

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        job_dict = None
        if self.has_supabase:
            try:
                res = self.client.table("jobs").select("*").eq("id", job_id).execute()
                if res.data:
                    job_dict = dict(res.data[0])
            except Exception as e:
                logger.error("Supabase get_job error: %s", e)

        if not job_dict:
            with self.lock:
                cur = self.conn.cursor()
                cur.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
                row = cur.fetchone()
                if row:
                    job_dict = dict(row)
                    job_dict["gta_preset"] = bool(job_dict.get("gta_preset", 1))

        if not job_dict:
            return None

        # Attach clips and logs
        job_dict["clips"] = self.get_clips_for_job(job_id)
        job_dict["logs"] = self.get_logs_for_job(job_id)
        return job_dict

    def find_active_job_by_url(self, url: str) -> Optional[Dict[str, Any]]:
        """Used for duplicate detection (status: queued or processing)."""
        if self.has_supabase:
            try:
                res = (
                    self.client.table("jobs")
                    .select("*")
                    .eq("url", url)
                    .in_("status", ["queued", "processing"])
                    .order("created_at", desc=True)
                    .limit(1)
                    .execute()
                )
                if res.data:
                    return dict(res.data[0])
            except Exception as e:
                logger.error("Supabase duplicate check error: %s", e)

        with self.lock:
            cur = self.conn.cursor()
            cur.execute("""
                SELECT * FROM jobs
                WHERE url = ? AND status IN ('queued', 'processing')
                ORDER BY created_at DESC LIMIT 1
            """, (url,))
            row = cur.fetchone()
            if row:
                d = dict(row)
                d["gta_preset"] = bool(d.get("gta_preset", 1))
                return d
        return None

    def list_jobs(self, limit: int = 20, offset: int = 0, status: Optional[str] = None) -> (List[Dict[str, Any]], int):
        if self.has_supabase:
            try:
                q = self.client.table("jobs").select("*", count="exact")
                if status:
                    q = q.eq("status", status)
                res = q.order("created_at", desc=True).range(offset, offset + limit - 1).execute()
                total = res.count if res.count is not None else len(res.data)
                items = [dict(r) for r in res.data]
                for it in items:
                    it["clips"] = self.get_clips_for_job(it["id"])
                    it["logs"] = []
                return items, total
            except Exception as e:
                logger.error("Supabase list_jobs error: %s", e)

        with self.lock:
            cur = self.conn.cursor()
            if status:
                cur.execute("SELECT COUNT(*) FROM jobs WHERE status = ?", (status,))
                total = cur.fetchone()[0]
                cur.execute("SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC LIMIT ? OFFSET ?", (status, limit, offset))
            else:
                cur.execute("SELECT COUNT(*) FROM jobs")
                total = cur.fetchone()[0]
                cur.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset))
            rows = cur.fetchall()
            items = []
            for r in rows:
                d = dict(r)
                d["gta_preset"] = bool(d.get("gta_preset", 1))
                d["clips"] = self.get_clips_for_job(d["id"])
                d["logs"] = []
                items.append(d)
            return items, total

    def update_job_progress(
        self,
        job_id: str,
        stage: str,
        progress: int,
        status: Optional[str] = None,
        error: Optional[str] = None,
        video_title: Optional[str] = None,
        video_duration: Optional[float] = None,
        retry_count: Optional[int] = None,
    ):
        now = datetime.now(timezone.utc).isoformat()
        updates: Dict[str, Any] = {
            "stage": stage,
            "progress": progress,
            "updated_at": now,
        }
        if status:
            updates["status"] = status
            if status == "completed":
                updates["completed_at"] = now
        if error:
            updates["error"] = error
        if video_title:
            updates["video_title"] = video_title
        if video_duration is not None:
            updates["video_duration"] = video_duration
        if retry_count is not None:
            updates["retry_count"] = retry_count

        if self.has_supabase:
            try:
                self.client.table("jobs").update(updates).eq("id", job_id).execute()
            except Exception as e:
                logger.error("Supabase update_job error: %s", e)

        with self.lock:
            cur = self.conn.cursor()
            set_parts = [f"{k} = ?" for k in updates.keys()]
            vals = list(updates.values()) + [job_id]
            cur.execute(f"UPDATE jobs SET {', '.join(set_parts)} WHERE id = ?", vals)
            self.conn.commit()

    # --- Clip Operations ---

    def create_clip(self, clip_data: Dict[str, Any]) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        clip_data["created_at"] = now
        import json
        hashtags_json = json.dumps(clip_data.get("hashtags", []))

        if self.has_supabase:
            try:
                row = {
                    "id": clip_data["id"],
                    "job_id": clip_data["job_id"],
                    "title": clip_data["title"],
                    "description": clip_data.get("description"),
                    "hook": clip_data.get("hook"),
                    "score": clip_data.get("score", 0),
                    "start_time": clip_data["start_time"],
                    "end_time": clip_data["end_time"],
                    "duration": clip_data["duration"],
                    "aspect_ratio": clip_data.get("aspect_ratio", "9:16"),
                    "clip_url": clip_data.get("clip_url"),
                    "storage_path": clip_data.get("storage_path"),
                    "thumbnail_url": clip_data.get("thumbnail_url"),
                    "captions": clip_data.get("captions"),
                    "hashtags": clip_data.get("hashtags", []),
                    "publishing_status": clip_data.get("publishing_status", "draft"),
                    "created_at": now,
                }
                res = self.client.table("clips").insert(row).execute()
                return res.data[0] if res.data else clip_data
            except Exception as e:
                logger.error("Supabase insert clip error: %s", e)

        with self.lock:
            cur = self.conn.cursor()
            cur.execute("""
                INSERT OR REPLACE INTO clips (id, job_id, title, description, hook, score, start_time, end_time, duration, aspect_ratio, clip_url, storage_path, thumbnail_url, captions, hashtags, publishing_status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                clip_data["id"],
                clip_data["job_id"],
                clip_data["title"],
                clip_data.get("description"),
                clip_data.get("hook"),
                clip_data.get("score", 0),
                clip_data["start_time"],
                clip_data["end_time"],
                clip_data["duration"],
                clip_data.get("aspect_ratio", "9:16"),
                clip_data.get("clip_url"),
                clip_data.get("storage_path"),
                clip_data.get("thumbnail_url"),
                clip_data.get("captions"),
                hashtags_json,
                clip_data.get("publishing_status", "draft"),
                now,
            ))
            self.conn.commit()
        return clip_data

    def get_clip(self, clip_id: str) -> Optional[Dict[str, Any]]:
        import json
        if self.has_supabase:
            try:
                res = self.client.table("clips").select("*").eq("id", clip_id).execute()
                if res.data:
                    c = dict(res.data[0])
                    if isinstance(c.get("hashtags"), str):
                        try:
                            c["hashtags"] = json.loads(c["hashtags"])
                        except Exception:
                            c["hashtags"] = []
                    return c
            except Exception as e:
                logger.error("Supabase get_clip error: %s", e)

        with self.lock:
            cur = self.conn.cursor()
            cur.execute("SELECT * FROM clips WHERE id = ?", (clip_id,))
            row = cur.fetchone()
            if row:
                c = dict(row)
                try:
                    c["hashtags"] = json.loads(c.get("hashtags") or "[]")
                except Exception:
                    c["hashtags"] = []
                return c
        return None

    def get_clips_for_job(self, job_id: str) -> List[Dict[str, Any]]:
        import json
        if self.has_supabase:
            try:
                res = self.client.table("clips").select("*").eq("job_id", job_id).order("score", desc=True).execute()
                items = []
                for row in res.data:
                    c = dict(row)
                    if isinstance(c.get("hashtags"), str):
                        try:
                            c["hashtags"] = json.loads(c["hashtags"])
                        except Exception:
                            c["hashtags"] = []
                    items.append(c)
                return items
            except Exception as e:
                logger.error("Supabase get_clips_for_job error: %s", e)

        with self.lock:
            cur = self.conn.cursor()
            cur.execute("SELECT * FROM clips WHERE job_id = ? ORDER BY score DESC", (job_id,))
            rows = cur.fetchall()
            items = []
            for r in rows:
                c = dict(r)
                try:
                    c["hashtags"] = json.loads(c.get("hashtags") or "[]")
                except Exception:
                    c["hashtags"] = []
                items.append(c)
            return items

    def update_clip(self, clip_id: str, updates: Dict[str, Any]):
        import json
        if "hashtags" in updates and isinstance(updates["hashtags"], list):
            updates_sql = dict(updates)
            updates_sql["hashtags"] = json.dumps(updates["hashtags"])
        else:
            updates_sql = updates

        if self.has_supabase:
            try:
                self.client.table("clips").update(updates).eq("id", clip_id).execute()
            except Exception as e:
                logger.error("Supabase update_clip error: %s", e)

        with self.lock:
            cur = self.conn.cursor()
            set_parts = [f"{k} = ?" for k in updates_sql.keys()]
            vals = list(updates_sql.values()) + [clip_id]
            cur.execute(f"UPDATE clips SET {', '.join(set_parts)} WHERE id = ?", vals)
            self.conn.commit()

    # --- Logs ---

    def add_log(self, job_id: str, stage: str, message: str):
        now = datetime.now(timezone.utc).isoformat()
        if self.has_supabase:
            try:
                self.client.table("processing_logs").insert({
                    "job_id": job_id,
                    "stage": stage,
                    "message": message,
                    "created_at": now,
                }).execute()
            except Exception:
                pass

        with self.lock:
            cur = self.conn.cursor()
            cur.execute(
                "INSERT INTO processing_logs (job_id, stage, message, created_at) VALUES (?, ?, ?, ?)",
                (job_id, stage, message, now),
            )
            self.conn.commit()

    def get_logs_for_job(self, job_id: str) -> List[Dict[str, str]]:
        if self.has_supabase:
            try:
                res = self.client.table("processing_logs").select("*").eq("job_id", job_id).order("id", desc=False).execute()
                return [{"timestamp": r["created_at"], "stage": r["stage"], "message": r["message"]} for r in res.data]
            except Exception:
                pass

        with self.lock:
            cur = self.conn.cursor()
            cur.execute("SELECT stage, message, created_at FROM processing_logs WHERE job_id = ? ORDER BY id ASC", (job_id,))
            return [{"timestamp": r["created_at"], "stage": r["stage"], "message": r["message"]} for r in cur.fetchall()]

    # --- Channels ---

    def create_channel(self, channel_data: Dict[str, Any]) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        channel_data["created_at"] = now
        with self.lock:
            cur = self.conn.cursor()
            cur.execute("""
                INSERT OR REPLACE INTO channels (id, channel_url, channel_name, auto_clip_new_videos, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (
                channel_data["id"],
                channel_data["channel_url"],
                channel_data["channel_name"],
                1 if channel_data.get("auto_clip_new_videos") else 0,
                now,
            ))
            self.conn.commit()
        return channel_data

    def list_channels(self) -> List[Dict[str, Any]]:
        with self.lock:
            cur = self.conn.cursor()
            cur.execute("SELECT * FROM channels ORDER BY created_at DESC")
            return [dict(r) for r in cur.fetchall()]


# Singleton database instance
db = DatabaseClient()
