-- =============================================================================
-- GTA 6 & VIRAL SHORTS ENGINE: COMPLETE MULTI-CATEGORY SCHEMA & HARDENED RLS
-- Migration: 20261121010000_multi_category_and_security.sql
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- -----------------------------------------------------------------------------
-- 1. User Profiles (Multi-tenant isolation)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.profiles (
    id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    email TEXT,
    display_name TEXT,
    role TEXT NOT NULL DEFAULT 'creator',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- -----------------------------------------------------------------------------
-- 2. Multi-Category Content System
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.categories (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    icon TEXT DEFAULT 'sparkles',
    is_active BOOLEAN NOT NULL DEFAULT true,
    system_prompt TEXT NOT NULL,
    scoring_weights JSONB NOT NULL DEFAULT '{}'::jsonb,
    default_hashtags JSONB NOT NULL DEFAULT '[]'::jsonb,
    presets JSONB NOT NULL DEFAULT '{}'::jsonb,
    publishing_preferences JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- -----------------------------------------------------------------------------
-- 3. Content Sources & Licensing Permissions
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.content_sources (
    id TEXT PRIMARY KEY,
    user_id UUID REFERENCES auth.users(id) ON DELETE SET NULL,
    source_type TEXT NOT NULL, -- official_api, approved_rss, creator_submission, direct_upload
    name TEXT NOT NULL,
    feed_url TEXT,
    channel_url TEXT,
    category_id TEXT REFERENCES public.categories(id) ON DELETE SET NULL,
    is_active BOOLEAN NOT NULL DEFAULT true,
    auto_clip_enabled BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.source_permissions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id TEXT NOT NULL REFERENCES public.content_sources(id) ON DELETE CASCADE,
    rights_status TEXT NOT NULL DEFAULT 'pending_verification', -- pending_verification, authorized_editing, authorized_platforms, rejected, expired
    license_type TEXT,
    allowed_platforms JSONB NOT NULL DEFAULT '["tiktok", "youtube_shorts"]'::jsonb,
    expires_at TIMESTAMPTZ,
    verified_by UUID REFERENCES auth.users(id),
    verification_notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- -----------------------------------------------------------------------------
-- 4. Discovered Content & Assets
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.discovered_content (
    id TEXT PRIMARY KEY,
    source_id TEXT REFERENCES public.content_sources(id) ON DELETE SET NULL,
    user_id UUID REFERENCES auth.users(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    original_url TEXT,
    media_url TEXT,
    creator_name TEXT,
    category_id TEXT REFERENCES public.categories(id) ON DELETE SET NULL,
    duration_seconds NUMERIC,
    rights_status TEXT NOT NULL DEFAULT 'pending_verification',
    content_hash TEXT,
    discovery_time TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_processed BOOLEAN NOT NULL DEFAULT false
);

CREATE TABLE IF NOT EXISTS public.content_assets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    discovered_content_id TEXT REFERENCES public.discovered_content(id) ON DELETE CASCADE,
    asset_type TEXT NOT NULL, -- raw_video, audio, transcript, thumbnail
    storage_path TEXT NOT NULL,
    storage_bucket TEXT NOT NULL DEFAULT 'shorts',
    file_size_bytes BIGINT,
    mime_type TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- -----------------------------------------------------------------------------
-- 5. Processing Jobs & Execution Logs
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.processing_jobs (
    id TEXT PRIMARY KEY,
    user_id UUID REFERENCES auth.users(id) ON DELETE SET NULL,
    source_id TEXT REFERENCES public.content_sources(id) ON DELETE SET NULL,
    url TEXT NOT NULL,
    category_id TEXT REFERENCES public.categories(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'queued', -- queued, processing, completed, failed, cancelled
    stage TEXT NOT NULL DEFAULT 'pending', -- pending, downloading, transcribing, highlight_detection, cropping_clips, uploading_storage, completed, failed
    progress INTEGER NOT NULL DEFAULT 0,
    num_clips INTEGER NOT NULL DEFAULT 3,
    aspect_ratio TEXT NOT NULL DEFAULT '9:16',
    video_title TEXT,
    video_duration NUMERIC,
    mode TEXT NOT NULL DEFAULT 'local',
    llm_provider TEXT NOT NULL DEFAULT 'gemini',
    error TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Create backward-compatible view for 'jobs' table
CREATE OR REPLACE VIEW public.jobs AS SELECT * FROM public.processing_jobs;

CREATE TABLE IF NOT EXISTS public.processing_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id TEXT NOT NULL REFERENCES public.processing_jobs(id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Backward-compatible view for 'job_logs'
CREATE OR REPLACE VIEW public.job_logs AS SELECT * FROM public.processing_logs;

-- -----------------------------------------------------------------------------
-- 6. Generated Clips & Metadata
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.generated_clips (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES public.processing_jobs(id) ON DELETE CASCADE,
    user_id UUID REFERENCES auth.users(id) ON DELETE SET NULL,
    category_id TEXT REFERENCES public.categories(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    hook_sentence TEXT,
    virality_reason TEXT,
    score NUMERIC NOT NULL DEFAULT 0,
    start_time NUMERIC NOT NULL,
    end_time NUMERIC NOT NULL,
    duration NUMERIC NOT NULL,
    aspect_ratio TEXT NOT NULL DEFAULT '9:16',
    storage_path TEXT,
    storage_url TEXT NOT NULL,
    thumbnail_url TEXT,
    publishing_status TEXT NOT NULL DEFAULT 'draft', -- draft, awaiting_approval, approved, scheduled, publishing, published, failed, cancelled
    suggested_tags JSONB NOT NULL DEFAULT '[]'::jsonb,
    is_quality_approved BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Backward-compatible view for 'clips'
CREATE OR REPLACE VIEW public.clips AS SELECT * FROM public.generated_clips;

CREATE TABLE IF NOT EXISTS public.clip_metadata (
    clip_id TEXT PRIMARY KEY REFERENCES public.generated_clips(id) ON DELETE CASCADE,
    virality_breakdown JSONB NOT NULL DEFAULT '{}'::jsonb,
    platform_copy JSONB NOT NULL DEFAULT '{}'::jsonb, -- per-platform title, hook, caption, tags
    audio_normalized BOOLEAN NOT NULL DEFAULT true,
    has_burned_captions BOOLEAN NOT NULL DEFAULT false,
    quality_metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    attribution_text TEXT
);

-- -----------------------------------------------------------------------------
-- 7. Connected Channels & Buffer Publishing Accounts
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.publishing_accounts (
    id TEXT PRIMARY KEY,
    user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    service TEXT NOT NULL, -- buffer, youtube_oauth
    account_identifier TEXT,
    is_active BOOLEAN NOT NULL DEFAULT true,
    access_token_encrypted TEXT,
    refresh_token_encrypted TEXT,
    token_expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.connected_channels (
    id TEXT PRIMARY KEY,
    publishing_account_id TEXT REFERENCES public.publishing_accounts(id) ON DELETE CASCADE,
    user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    platform TEXT NOT NULL, -- tiktok, youtube_shorts, instagram_reels
    channel_name TEXT NOT NULL,
    channel_id_external TEXT NOT NULL,
    avatar_url TEXT,
    is_enabled BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- -----------------------------------------------------------------------------
-- 8. Publishing Queue & History
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.publishing_queue (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    clip_id TEXT NOT NULL REFERENCES public.generated_clips(id) ON DELETE CASCADE,
    channel_id TEXT NOT NULL REFERENCES public.connected_channels(id) ON DELETE CASCADE,
    user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    scheduled_for TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'scheduled', -- scheduled, publishing, published, failed, cancelled
    platform_update_id TEXT, -- Buffer update ID or YouTube video ID
    error_message TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT unique_clip_channel_schedule UNIQUE (clip_id, channel_id)
);

CREATE TABLE IF NOT EXISTS public.publishing_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    clip_id TEXT NOT NULL REFERENCES public.generated_clips(id) ON DELETE CASCADE,
    channel_id TEXT NOT NULL REFERENCES public.connected_channels(id) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    published_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    external_post_url TEXT,
    initial_metrics JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- -----------------------------------------------------------------------------
-- 9. Analytics & Optimization Feedback
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.platform_analytics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    clip_id TEXT NOT NULL REFERENCES public.generated_clips(id) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    views BIGINT,
    reach BIGINT,
    likes BIGINT,
    comments BIGINT,
    shares BIGINT,
    saves BIGINT,
    average_watch_time_seconds NUMERIC,
    completion_rate NUMERIC,
    engagement_rate NUMERIC,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.category_performance (
    category_id TEXT PRIMARY KEY REFERENCES public.categories(id) ON DELETE CASCADE,
    total_clips_published INTEGER NOT NULL DEFAULT 0,
    total_views BIGINT NOT NULL DEFAULT 0,
    average_engagement_rate NUMERIC DEFAULT 0,
    top_performing_duration_range TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- -----------------------------------------------------------------------------
-- 10. Production Schedules & System Settings
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.production_schedules (
    id TEXT PRIMARY KEY DEFAULT 'default',
    user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    daily_target_min INTEGER NOT NULL DEFAULT 15,
    daily_target_max INTEGER NOT NULL DEFAULT 20,
    timezone TEXT NOT NULL DEFAULT 'UTC',
    is_paused BOOLEAN NOT NULL DEFAULT false,
    category_distribution JSONB NOT NULL DEFAULT '{"gaming": 0.35, "viral_entertainment": 0.25, "tech_ai": 0.20, "sports": 0.10, "animals_nature": 0.10}'::jsonb,
    publishing_windows JSONB NOT NULL DEFAULT '[{"start_time": "11:00", "end_time": "14:00"}, {"start_time": "17:00", "end_time": "22:00"}]'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.system_settings (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- =============================================================================
-- INDEXES FOR PERFORMANCE
-- =============================================================================
CREATE INDEX IF NOT EXISTS idx_processing_jobs_user ON public.processing_jobs(user_id);
CREATE INDEX IF NOT EXISTS idx_processing_jobs_status ON public.processing_jobs(status);
CREATE INDEX IF NOT EXISTS idx_processing_jobs_url ON public.processing_jobs(url);
CREATE INDEX IF NOT EXISTS idx_generated_clips_job ON public.generated_clips(job_id);
CREATE INDEX IF NOT EXISTS idx_generated_clips_user ON public.generated_clips(user_id);
CREATE INDEX IF NOT EXISTS idx_generated_clips_status ON public.generated_clips(publishing_status);
CREATE INDEX IF NOT EXISTS idx_publishing_queue_scheduled ON public.publishing_queue(scheduled_for, status);

-- =============================================================================
-- ROW LEVEL SECURITY (RLS) POLICIES — PRIVATE BY DEFAULT, TENANT ISOLATED
-- =============================================================================

-- Enable RLS on all tenant tables
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.categories ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.content_sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.source_permissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.discovered_content ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.content_assets ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.processing_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.processing_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.generated_clips ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.clip_metadata ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.publishing_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.connected_channels ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.publishing_queue ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.publishing_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.platform_analytics ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.production_schedules ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.system_settings ENABLE ROW LEVEL SECURITY;

-- 1. Service Role Full Access (Backend Engine & Worker)
CREATE POLICY "Service role full access on profiles" ON public.profiles FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access on categories" ON public.categories FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access on content_sources" ON public.content_sources FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access on source_permissions" ON public.source_permissions FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access on discovered_content" ON public.discovered_content FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access on content_assets" ON public.content_assets FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access on processing_jobs" ON public.processing_jobs FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access on processing_logs" ON public.processing_logs FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access on generated_clips" ON public.generated_clips FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access on clip_metadata" ON public.clip_metadata FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access on publishing_accounts" ON public.publishing_accounts FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access on connected_channels" ON public.connected_channels FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access on publishing_queue" ON public.publishing_queue FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access on publishing_history" ON public.publishing_history FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access on platform_analytics" ON public.platform_analytics FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access on production_schedules" ON public.production_schedules FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access on system_settings" ON public.system_settings FOR ALL TO service_role USING (true) WITH CHECK (true);

-- 2. Authenticated User Policies (Lovable Frontend via Supabase Auth)
CREATE POLICY "Users can manage own profile" ON public.profiles FOR ALL TO authenticated USING (auth.uid() = id) WITH CHECK (auth.uid() = id);
CREATE POLICY "Users can view active categories" ON public.categories FOR SELECT TO authenticated USING (is_active = true);
CREATE POLICY "Users can manage own content sources" ON public.content_sources FOR ALL TO authenticated USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
CREATE POLICY "Users can view permissions of own sources" ON public.source_permissions FOR SELECT TO authenticated USING (EXISTS (SELECT 1 FROM public.content_sources WHERE id = source_permissions.source_id AND user_id = auth.uid()));
CREATE POLICY "Users can manage own discovered content" ON public.discovered_content FOR ALL TO authenticated USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
CREATE POLICY "Users can manage own jobs" ON public.processing_jobs FOR ALL TO authenticated USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
CREATE POLICY "Users can view logs of own jobs" ON public.processing_logs FOR SELECT TO authenticated USING (EXISTS (SELECT 1 FROM public.processing_jobs WHERE id = processing_logs.job_id AND user_id = auth.uid()));
CREATE POLICY "Users can manage own clips" ON public.generated_clips FOR ALL TO authenticated USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
CREATE POLICY "Users can view metadata of own clips" ON public.clip_metadata FOR SELECT TO authenticated USING (EXISTS (SELECT 1 FROM public.generated_clips WHERE id = clip_metadata.clip_id AND user_id = auth.uid()));
CREATE POLICY "Users can manage own connected channels" ON public.connected_channels FOR ALL TO authenticated USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
CREATE POLICY "Users can manage own publishing queue" ON public.publishing_queue FOR ALL TO authenticated USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
CREATE POLICY "Users can view own publishing history" ON public.publishing_history FOR SELECT TO authenticated USING (EXISTS (SELECT 1 FROM public.generated_clips WHERE id = publishing_history.clip_id AND user_id = auth.uid()));
CREATE POLICY "Users can view analytics of own clips" ON public.platform_analytics FOR SELECT TO authenticated USING (EXISTS (SELECT 1 FROM public.generated_clips WHERE id = platform_analytics.clip_id AND user_id = auth.uid()));
CREATE POLICY "Users can manage own schedule" ON public.production_schedules FOR ALL TO authenticated USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);

-- Note: Anonymous role (anon) is granted NO access. All tables remain private.
