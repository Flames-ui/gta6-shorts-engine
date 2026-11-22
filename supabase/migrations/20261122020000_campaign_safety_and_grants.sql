-- =============================================================================
-- Migration: 20261122020000_campaign_safety_and_grants.sql
-- Milestone 1.1: Campaign Safety Corrections, Explicit Grants, and Audit Columns
-- =============================================================================

-- 1. Add Safety & Audit Columns to Campaign Tables
ALTER TABLE public.campaign_sources
    ADD COLUMN IF NOT EXISTS verified_by TEXT,
    ADD COLUMN IF NOT EXISTS verified_at TIMESTAMPTZ;

ALTER TABLE public.clip_submissions
    ADD COLUMN IF NOT EXISTS estimated_rewards NUMERIC(10, 2) DEFAULT 0.00,
    ADD COLUMN IF NOT EXISTS evidence_source TEXT,
    ADD COLUMN IF NOT EXISTS evidence_url TEXT,
    ADD COLUMN IF NOT EXISTS verified_by TEXT;

ALTER TABLE public.campaigns
    ADD COLUMN IF NOT EXISTS campaign_budget NUMERIC(10, 2),
    ADD COLUMN IF NOT EXISTS remaining_budget NUMERIC(10, 2);

-- 2. Explicit Service Role Grants (Full Management Permissions)
GRANT ALL ON TABLE public.campaigns TO service_role;
GRANT ALL ON TABLE public.campaign_sources TO service_role;
GRANT ALL ON TABLE public.campaign_clips TO service_role;
GRANT ALL ON TABLE public.clip_submissions TO service_role;
GRANT ALL ON TABLE public.earnings_summary TO service_role;

-- 3. Authenticated Operator Grants (Read Only)
GRANT SELECT ON TABLE public.campaigns TO authenticated;
GRANT SELECT ON TABLE public.campaign_sources TO authenticated;
GRANT SELECT ON TABLE public.campaign_clips TO authenticated;
GRANT SELECT ON TABLE public.clip_submissions TO authenticated;
GRANT SELECT ON TABLE public.earnings_summary TO authenticated;

-- 4. Revoke All Public and Anonymous Access (Fail-Closed Security)
REVOKE ALL ON TABLE public.campaigns FROM anon, public;
REVOKE ALL ON TABLE public.campaign_sources FROM anon, public;
REVOKE ALL ON TABLE public.campaign_clips FROM anon, public;
REVOKE ALL ON TABLE public.clip_submissions FROM anon, public;
REVOKE ALL ON TABLE public.earnings_summary FROM anon, public;
