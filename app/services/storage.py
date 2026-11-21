"""Storage management for rendered video clips and thumbnails."""
import logging
import os
import shutil
from typing import Optional, Tuple
from ..config import settings

logger = logging.getLogger("gta6_shorts.storage")


class StorageService:
    def __init__(self):
        self.bucket = settings.SUPABASE_STORAGE_BUCKET or "gta6-shorts"
        self.has_supabase = bool(settings.SUPABASE_URL and settings.SUPABASE_SERVICE_ROLE_KEY)
        self.local_dir = os.path.join(settings.LOCAL_OUTPUT_DIR, "clips")
        os.makedirs(self.local_dir, exist_ok=True)

    def upload_clip(self, local_file_path: str, destination_name: str) -> Tuple[str, str]:
        """Upload a rendered clip and return (public_url, storage_path)."""
        storage_path = f"clips/{destination_name}"

        if self.has_supabase:
            try:
                from supabase import create_client
                supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)

                # Read file bytes
                with open(local_file_path, "rb") as f:
                    file_bytes = f.read()

                # Upload to Supabase Storage
                res = supabase.storage.from_(self.bucket).upload(
                    path=storage_path,
                    file=file_bytes,
                    file_options={"content-type": "video/mp4", "upsert": "true"},
                )
                public_url = supabase.storage.from_(self.bucket).get_public_url(storage_path)
                logger.info("Successfully uploaded clip to Supabase Storage: %s", public_url)
                return public_url, storage_path
            except Exception as e:
                logger.error("Failed to upload to Supabase Storage, using local fallback: %s", e)

        # Local storage fallback
        dest_local_path = os.path.join(self.local_dir, destination_name)
        if os.path.abspath(local_file_path) != os.path.abspath(dest_local_path):
            shutil.copy2(local_file_path, dest_local_path)

        local_url = f"/static/clips/{destination_name}"
        return local_url, storage_path


storage_service = StorageService()
