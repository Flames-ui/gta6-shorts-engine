-- Migration 006: Production Safety, Clip Inspections & Operator Audit Trail
-- Forward-only migration hardening candidate evaluations, clip inspections, and operator audit trail.
-- Preserves existing migrations 001-005 without modification.

-- 1. Create clip_inspections table
CREATE TABLE IF NOT EXISTS clip_inspections (
    id TEXT PRIMARY KEY,
    clip_id TEXT NOT NULL REFERENCES public.generated_clips(id) ON DELETE CASCADE,
    campaign_id TEXT REFERENCES campaigns(id) ON DELETE SET NULL,
    video_path TEXT NOT NULL,
    gate_a_passed BOOLEAN NOT NULL DEFAULT FALSE,
    gate_b_passed BOOLEAN NOT NULL DEFAULT FALSE,
    overall_approved BOOLEAN NOT NULL DEFAULT FALSE,
    quality_score REAL NOT NULL DEFAULT 0.0,
    media_metadata JSONB DEFAULT '{}'::jsonb,
    frame_samples JSONB DEFAULT '[]'::jsonb,
    failures JSONB DEFAULT '[]'::jsonb,
    remediation_attempts INTEGER NOT NULL DEFAULT 0,
    inspected_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2. Create operator_audit_logs table
CREATE TABLE IF NOT EXISTS operator_audit_logs (
    id TEXT PRIMARY KEY,
    operator_id TEXT NOT NULL,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    previous_value JSONB,
    new_value JSONB,
    reason TEXT NOT NULL,
    evidence_reference TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 3. Enhance candidate_evaluations, clips, and clip_submissions with any missing columns
ALTER TABLE candidate_evaluations ADD COLUMN IF NOT EXISTS source_duration REAL;
ALTER TABLE candidate_evaluations ADD COLUMN IF NOT EXISTS is_selected BOOLEAN DEFAULT FALSE;

ALTER TABLE public.generated_clips ADD COLUMN IF NOT EXISTS storage_path TEXT;
ALTER TABLE public.generated_clips ADD COLUMN IF NOT EXISTS publishing_status TEXT DEFAULT 'awaiting_approval';

-- Ensure the public.clips view remains aligned with public.generated_clips
CREATE OR REPLACE VIEW public.clips WITH (security_invoker = true) AS SELECT * FROM public.generated_clips;

ALTER TABLE clip_submissions ADD COLUMN IF NOT EXISTS marketplace_reward_ref TEXT;
ALTER TABLE clip_submissions ADD COLUMN IF NOT EXISTS withdrawn_earnings NUMERIC(10, 2) DEFAULT 0.00;

-- 4. Create Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_clip_inspections_clip_campaign ON clip_inspections(clip_id, campaign_id);
CREATE INDEX IF NOT EXISTS idx_clip_inspections_approved_time ON clip_inspections(overall_approved, inspected_at DESC);
CREATE INDEX IF NOT EXISTS idx_operator_audit_logs_operator_time ON operator_audit_logs(operator_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_operator_audit_logs_entity ON operator_audit_logs(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_candidate_evaluations_score_time ON candidate_evaluations(quality_score DESC, created_at DESC);

-- 5. Enable Row-Level Security (RLS) on newly created tables
ALTER TABLE clip_inspections ENABLE ROW LEVEL SECURITY;
ALTER TABLE operator_audit_logs ENABLE ROW LEVEL SECURITY;

-- 6. Strict RLS Policies
-- Service Role: Full access for backend services
DROP POLICY IF EXISTS service_role_all_clip_inspections ON clip_inspections;
CREATE POLICY service_role_all_clip_inspections ON clip_inspections
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

DROP POLICY IF EXISTS service_role_all_operator_audit_logs ON operator_audit_logs;
CREATE POLICY service_role_all_operator_audit_logs ON operator_audit_logs
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- Sensitive quality inspections and operator audit entries are service-role-only.
DROP POLICY IF EXISTS authenticated_select_clip_inspections ON clip_inspections;
DROP POLICY IF EXISTS authenticated_select_operator_audit_logs ON operator_audit_logs;
REVOKE ALL ON clip_inspections FROM PUBLIC, anon, authenticated;
REVOKE ALL ON operator_audit_logs FROM PUBLIC, anon, authenticated;
GRANT ALL ON clip_inspections TO service_role;
GRANT ALL ON operator_audit_logs TO service_role;
