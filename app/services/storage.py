"""Storage management for rendered video clips and thumbnails with signed URL support."""
import logging
import os
import shutil
from typing import Optional, Tuple
from ..config import settings

logger = logging.getLogger("gta6_shorts.storage")


class StorageService:
    def __init__(self):
        self.bucket = settings.SUPABASE_STORAGE_BUCKET or "shorts"
        self.has_supabase = bool(settings.SUPABASE_URL and settings.SUPABASE_SERVICE_ROLE_KEY)
        self.is_prod = settings.ENVIRONMENT.lower() == "production"

        if self.is_prod and not self.has_supabase and settings.FAIL_CLOSED_IN_PRODUCTION:
            raise RuntimeError(
                "FATAL: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be configured for Storage in production."
            )

        self.local_dir = os.path.join(settings.LOCAL_OUTPUT_DIR, "clips")
        os.makedirs(self.local_dir, exist_ok=True)

    def upload_clip(
        self,
        local_file_path: str,
        destination_name: str,
        job_id: str = "shared",
        user_id: Optional[str] = None,
        is_private: bool = False,
    ) -> Tuple[str, str]:
        """
        Upload rendered MP4 clip to Supabase Storage and return (access_url, storage_path).
        Organizes files into hierarchical tenant folders: {user_id}/{job_id}/{filename}.
        """
        folder_prefix = f"{user_id or 'shared'}/{job_id}"
        storage_path = f"{folder_prefix}/{destination_name}"

        if self.has_supabase:
            try:
                from supabase import create_client
                supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)

                with open(local_file_path, "rb") as f:
                    file_bytes = f.read()

                supabase.storage.from_(self.bucket).upload(
                    path=storage_path,
                    file=file_bytes,
                    file_options={"content-type": "video/mp4", "upsert": "true"},
                )

                if is_private:
                    # Generate signed URL valid for 7 days (604800 seconds)
                    signed_res = supabase.storage.from_(self.bucket).create_signed_url(storage_path, 604800)
                    url = signed_res.get("signedURL") or signed_res.get("signedUrl")
                else:
                    url = supabase.storage.from_(self.bucket).get_public_url(storage_path)

                logger.info("Successfully uploaded %s to Supabase Storage: %s", destination_name, url)
                return url, storage_path
            except Exception as e:
                logger.error("Failed to upload to Supabase Storage: %s", e)
                if self.is_prod:
                    raise RuntimeError(f"Storage upload failed in production: {e}")

        # Local development fallback
        dest_local = os.path.join(self.local_dir, destination_name)
        if os.path.abspath(local_file_path) != os.path.abspath(dest_local):
            shutil.copy2(local_file_path, dest_local)

        mock_url = f"http://localhost:{settings.PORT}/static/clips/{destination_name}"
        logger.info("Stored clip locally (development mode): %s", dest_local)
        return mock_url, storage_path

    def upload_thumbnail(
        self,
        local_file_path: str,
        destination_name: str,
        job_id: str = "shared",
        user_id: Optional[str] = None,
    ) -> str:
        """Upload thumbnail image to storage."""
        folder_prefix = f"{user_id or 'shared'}/{job_id}"
        storage_path = f"{folder_prefix}/{destination_name}"

        if self.has_supabase:
            try:
                from supabase import create_client
                supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)
                with open(local_file_path, "rb") as f:
                    file_bytes = f.read()

                supabase.storage.from_(self.bucket).upload(
                    path=storage_path,
                    file=file_bytes,
                    file_options={"content-type": "image/jpeg", "upsert": "true"},
                )
                return supabase.storage.from_(self.bucket).get_public_url(storage_path)
            except Exception as e:
                logger.error("Failed to upload thumbnail to Supabase Storage: %s", e)

        dest_local = os.path.join(self.local_dir, destination_name)
        if os.path.abspath(local_file_path) != os.path.abspath(dest_local):
            shutil.copy2(local_file_path, dest_local)
        return f"http://localhost:{settings.PORT}/static/clips/{destination_name}"


storage_service = StorageService()
