-- Migration 005: Candidate Moment Evaluations and Quality Provenance
-- Required for Milestones 2.2 and 3 production metrics & genuine quality gates

CREATE TABLE IF NOT EXISTS candidate_evaluations (
    id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    campaign_id TEXT REFERENCES campaigns(id) ON DELETE SET NULL,
    source_id TEXT REFERENCES campaign_sources(id) ON DELETE SET NULL,
    start_time REAL,
    end_time REAL,
    duration REAL,
    quality_score REAL NOT NULL,
    tier TEXT NOT NULL,
    verdict TEXT NOT NULL,
    factor_breakdown JSONB,
    analysis_evidence JSONB,
    rejection_reasons JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_candidate_evaluations_campaign_id ON candidate_evaluations(campaign_id);
CREATE INDEX IF NOT EXISTS idx_candidate_evaluations_verdict ON candidate_evaluations(verdict);
CREATE INDEX IF NOT EXISTS idx_candidate_evaluations_score ON candidate_evaluations(quality_score);
