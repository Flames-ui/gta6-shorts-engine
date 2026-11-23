-- Rollback for 001_studio_schema.sql. DESTRUCTIVE: drops all studio data.
-- Only run on empty or disposable databases. Take a backup first.
DROP TABLE IF EXISTS clip_captions, generated_clips, detected_highlights, job_events,
    processing_usage, worker_heartbeats, editing_jobs, video_sources CASCADE;
DROP FUNCTION IF EXISTS set_updated_at();
DELETE FROM schema_migrations WHERE version = '001_studio_schema';
