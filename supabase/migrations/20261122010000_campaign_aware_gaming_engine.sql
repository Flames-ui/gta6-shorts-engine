-- =============================================================================
-- Migration: 20261122010000_campaign_aware_gaming_engine.sql
-- Milestone 1: Campaign-Aware Gaming Clipping Engine
-- Models for Campaigns, Sources, Compliance, Submissions, and Earnings
-- =============================================================================

-- 1. Campaigns Table
CREATE TABLE IF NOT EXISTS public.campaigns (
    id TEXT PRIMARY KEY,
    marketplace_id TEXT NOT NULL,
    marketplace_name TEXT NOT NULL,
    campaign_external_id TEXT,
    name TEXT NOT NULL,
    description TEXT,
    url TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'discovered', -- discovered, analyzing, eligible, needs_review, approved, rejected, expired, exhausted
    deadline TIMESTAMPTZ,
    submission_deadline TIMESTAMPTZ,
    accepted_platforms JSONB NOT NULL DEFAULT '["tiktok", "youtube_shorts"]'::jsonb,
    eligible_games JSONB NOT NULL DEFAULT '["gta6"]'::jsonb,
    content_categories JSONB NOT NULL DEFAULT '["gaming"]'::jsonb,
    required_hashtags JSONB NOT NULL DEFAULT '[]'::jsonb,
    required_mentions JSONB NOT NULL DEFAULT '[]'::jsonb,
    required_captions TEXT,
    min_clip_duration_seconds INTEGER DEFAULT 15,
    max_clip_duration_seconds INTEGER DEFAULT 60,
    resolution_requirements TEXT DEFAULT '1080x1920',
    aspect_ratio TEXT DEFAULT '9:16',
    reward_model TEXT NOT NULL DEFAULT 'cpm', -- cpm, prize_pool, fixed_per_clip, bounty
    payout_amount NUMERIC(10, 2) DEFAULT 0.00,
    cpm NUMERIC(10, 2) DEFAULT 0.00,
    reward_per_view NUMERIC(10, 4) DEFAULT 0.0000,
    view_qualification_rules JSONB DEFAULT '{}'::jsonb,
    max_eligible_views INTEGER DEFAULT 0,
    max_payout_per_clip NUMERIC(10, 2) DEFAULT 0.00,
    geographic_eligibility JSONB DEFAULT '["ALL"]'::jsonb,
    account_eligibility JSONB DEFAULT '{}'::jsonb,
    source_video_requirements TEXT,
    content_restrictions JSONB DEFAULT '[]'::jsonb,
    prohibited_editing_techniques JSONB DEFAULT '[]'::jsonb,
    required_attribution TEXT,
    submission_instructions TEXT,
    raw_rules TEXT NOT NULL,
    operator_approved BOOLEAN DEFAULT FALSE,
    rejection_reason TEXT,
    review_notes TEXT,
    ambiguities JSONB DEFAULT '[]'::jsonb,
    date_last_verified TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. Campaign Sources Table
CREATE TABLE IF NOT EXISTS public.campaign_sources (
    id TEXT PRIMARY KEY,
    campaign_id TEXT REFERENCES public.campaigns(id) ON DELETE CASCADE,
    source_url TEXT NOT NULL,
    original_creator TEXT NOT NULL,
    source_type TEXT NOT NULL, -- campaign_provided, creator_authorized, official_trailer, streamer_vod
    permission_evidence TEXT NOT NULL,
    allowed_derivative_uses JSONB NOT NULL DEFAULT '["short_form_clipping", "vertical_reframe", "burn_captions"]'::jsonb,
    authorized_platforms JSONB NOT NULL DEFAULT '["tiktok", "youtube_shorts"]'::jsonb,
    permission_expiry TIMESTAMPTZ,
    source_verification_status TEXT NOT NULL DEFAULT 'pending_verification', -- verified, pending_verification, rejected, expired
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 3. Campaign Clips Compliance Table
CREATE TABLE IF NOT EXISTS public.campaign_clips (
    id TEXT PRIMARY KEY,
    clip_id TEXT NOT NULL,
    campaign_id TEXT REFERENCES public.campaigns(id) ON DELETE CASCADE,
    source_id TEXT REFERENCES public.campaign_sources(id) ON DELETE SET NULL,
    job_id TEXT,
    target_platform TEXT NOT NULL DEFAULT 'tiktok', -- tiktok, youtube_shorts
    compliance_status TEXT NOT NULL DEFAULT 'pending_validation', -- compliant, non_compliant, pending_validation
    compliance_results JSONB NOT NULL DEFAULT '{}'::jsonb,
    submission_ready BOOLEAN DEFAULT FALSE,
    failure_reasons JSONB DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 4. Clip Submissions Table (Earnings Tracking Foundations)
CREATE TABLE IF NOT EXISTS public.clip_submissions (
    id TEXT PRIMARY KEY,
    clip_id TEXT NOT NULL,
    campaign_id TEXT REFERENCES public.campaigns(id) ON DELETE CASCADE,
    platform TEXT NOT NULL, -- tiktok, youtube_shorts
    submitted_at TIMESTAMPTZ DEFAULT NOW(),
    submission_url TEXT,
    submission_post_id TEXT,
    submission_evidence JSONB DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'submitted', -- submitted, under_review, qualified, rejected, rewarded
    eligible_views INTEGER DEFAULT 0,
    pending_rewards NUMERIC(10, 2) DEFAULT 0.00,
    approved_rewards NUMERIC(10, 2) DEFAULT 0.00,
    rejected_rewards NUMERIC(10, 2) DEFAULT 0.00,
    withdrawable_earnings NUMERIC(10, 2) DEFAULT 0.00,
    rejection_reason TEXT,
    last_verified_at TIMESTAMPTZ DEFAULT NOW(),
    operator_notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 5. Earnings Summary Table
CREATE TABLE IF NOT EXISTS public.earnings_summary (
    id TEXT PRIMARY KEY,
    target_amount NUMERIC(10, 2) DEFAULT 500.00,
    period_start TIMESTAMPTZ DEFAULT NOW(),
    period_end TIMESTAMPTZ DEFAULT (NOW() + INTERVAL '30 days'),
    total_submitted_clips INTEGER DEFAULT 0,
    total_eligible_views INTEGER DEFAULT 0,
    total_pending_rewards NUMERIC(10, 2) DEFAULT 0.00,
    total_approved_rewards NUMERIC(10, 2) DEFAULT 0.00,
    total_rejected_rewards NUMERIC(10, 2) DEFAULT 0.00,
    total_withdrawable_earnings NUMERIC(10, 2) DEFAULT 0.00,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Row Level Security
ALTER TABLE public.campaigns ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.campaign_sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.campaign_clips ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.clip_submissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.earnings_summary ENABLE ROW LEVEL SECURITY;

-- Service Role Full Access Policies
CREATE POLICY "Service role full access on campaigns" ON public.campaigns
    FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access on campaign_sources" ON public.campaign_sources
    FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access on campaign_clips" ON public.campaign_clips
    FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access on clip_submissions" ON public.clip_submissions
    FOR ALL TO service_role USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access on earnings_summary" ON public.earnings_summary
    FOR ALL TO service_role USING (true) WITH CHECK (true);

-- Authenticated Operator Read Access
CREATE POLICY "Authenticated users can read campaigns" ON public.campaigns
    FOR SELECT TO authenticated USING (true);
CREATE POLICY "Authenticated users can read campaign_sources" ON public.campaign_sources
    FOR SELECT TO authenticated USING (true);
CREATE POLICY "Authenticated users can read campaign_clips" ON public.campaign_clips
    FOR SELECT TO authenticated USING (true);
CREATE POLICY "Authenticated users can read clip_submissions" ON public.clip_submissions
    FOR SELECT TO authenticated USING (true);
CREATE POLICY "Authenticated users can read earnings_summary" ON public.earnings_summary
    FOR SELECT TO authenticated USING (true);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_campaigns_status ON public.campaigns(status);
CREATE INDEX IF NOT EXISTS idx_campaigns_marketplace ON public.campaigns(marketplace_id);
CREATE INDEX IF NOT EXISTS idx_campaign_sources_campaign ON public.campaign_sources(campaign_id);
CREATE INDEX IF NOT EXISTS idx_campaign_clips_campaign ON public.campaign_clips(campaign_id);
CREATE INDEX IF NOT EXISTS idx_clip_submissions_campaign ON public.clip_submissions(campaign_id);
CREATE INDEX IF NOT EXISTS idx_clip_submissions_status ON public.clip_submissions(status);
