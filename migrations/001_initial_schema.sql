-- GTA 6 Shorts Engine: Initial Database Schema Migration
-- Compatible with Supabase Postgres

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 1. Source Videos
CREATE TABLE IF NOT EXISTS public.source_videos (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    youtube_id TEXT,
    url TEXT NOT NULL,
    title TEXT,
    duration NUMERIC,
    thumbnail_url TEXT,
    channel_id TEXT,
    channel_name TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 2. Processing Jobs
CREATE TABLE IF NOT EXISTS public.jobs (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    stage TEXT NOT NULL DEFAULT 'pending',
    progress INTEGER NOT NULL DEFAULT 0,
    num_clips INTEGER NOT NULL DEFAULT 3,
    aspect_ratio TEXT NOT NULL DEFAULT '9:16',
    video_title TEXT,
    video_duration NUMERIC,
    mode TEXT NOT NULL DEFAULT 'local',
    llm_provider TEXT NOT NULL DEFAULT 'gemini',
    gta_preset BOOLEAN NOT NULL DEFAULT true,
    error TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ
);

-- Index for duplicate checks and status queries
CREATE INDEX IF NOT EXISTS idx_jobs_url_status ON public.jobs (url, status);
CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON public.jobs (created_at DESC);

-- 3. Generated Clips
CREATE TABLE IF NOT EXISTS public.clips (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES public.jobs(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT,
    hook TEXT,
    score INTEGER DEFAULT 0,
    start_time NUMERIC NOT NULL,
    end_time NUMERIC NOT NULL,
    duration NUMERIC NOT NULL,
    aspect_ratio TEXT NOT NULL DEFAULT '9:16',
    clip_url TEXT,
    storage_path TEXT,
    thumbnail_url TEXT,
    captions TEXT,
    hashtags JSONB NOT NULL DEFAULT '[]'::jsonb,
    publishing_status TEXT NOT NULL DEFAULT 'draft',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_clips_job_id ON public.clips (job_id);
CREATE INDEX IF NOT EXISTS idx_clips_publishing_status ON public.clips (publishing_status);

-- 4. Processing Logs
CREATE TABLE IF NOT EXISTS public.processing_logs (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES public.jobs(id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_logs_job_id ON public.processing_logs (job_id, id ASC);

-- 5. Monitored Channels
CREATE TABLE IF NOT EXISTS public.channels (
    id TEXT PRIMARY KEY,
    channel_url TEXT NOT NULL,
    channel_name TEXT NOT NULL,
    auto_clip_new_videos BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Enable RLS (Service role key bypasses automatically for backend operations)
ALTER TABLE public.source_videos ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.clips ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.processing_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.channels ENABLE ROW LEVEL SECURITY;

-- Allow public read access on clips and jobs for dashboards when configured
CREATE POLICY "Allow public read access to completed clips"
    ON public.clips FOR SELECT
    USING (true);

CREATE POLICY "Allow public read access to jobs"
    ON public.jobs FOR SELECT
    USING (true);

CREATE POLICY "Allow public read access to channels"
    ON public.channels FOR SELECT
    USING (true);

-- Storage bucket creation for clips (id: 'gta6-shorts')
INSERT INTO storage.buckets (id, name, public)
VALUES ('gta6-shorts', 'gta6-shorts', true)
ON CONFLICT (id) DO NOTHING;

-- Storage public read policy
CREATE POLICY "Public clip bucket read"
ON storage.objects FOR SELECT
USING (bucket_id = 'gta6-shorts');
