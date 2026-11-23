-- =============================================================================
-- 001: AI Video Editing Studio initial schema (independent PostgreSQL database)
--
-- Forward-only. Applied by `python -m app.migrate` inside one transaction.
-- Owner identifiers are the external auth subject (text), never trusted from
-- client input. Rollback: see 001_studio_schema.down.sql (destructive; for
-- empty or disposable databases only).
-- =============================================================================

CREATE TABLE video_sources (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id            text NOT NULL CHECK (char_length(owner_id) BETWEEN 1 AND 128),
    source_type         text NOT NULL CHECK (source_type IN ('youtube', 'upload')),
    status              text NOT NULL DEFAULT 'pending_retrieval'
                        CHECK (status IN ('pending_retrieval', 'retrieving', 'validating', 'ready', 'failed')),
    original_url        text,
    youtube_video_id    text CHECK (youtube_video_id IS NULL OR youtube_video_id ~ '^[A-Za-z0-9_-]{11}$'),
    title               text CHECK (title IS NULL OR char_length(title) <= 300),
    channel_title       text CHECK (channel_title IS NULL OR char_length(channel_title) <= 200),
    duration_seconds    numeric(10, 3) CHECK (duration_seconds IS NULL OR duration_seconds > 0),
    file_key            text,
    file_size_bytes     bigint CHECK (file_size_bytes IS NULL OR file_size_bytes > 0),
    container_format    text,
    video_codec         text,
    audio_codec         text,
    width               integer CHECK (width IS NULL OR width > 0),
    height              integer CHECK (height IS NULL OR height > 0),
    has_audio           boolean,
    sha256              text CHECK (sha256 IS NULL OR sha256 ~ '^[a-f0-9]{64}$'),
    thumbnail_key       text,
    failure_code        text,
    failure_message     text CHECK (failure_message IS NULL OR char_length(failure_message) <= 2000),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT video_sources_youtube_has_id CHECK (source_type <> 'youtube' OR youtube_video_id IS NOT NULL),
    CONSTRAINT video_sources_ready_has_file CHECK (status <> 'ready' OR file_key IS NOT NULL)
);

-- Duplicate protection: one live source per owner per YouTube video or file hash.
CREATE UNIQUE INDEX video_sources_owner_youtube_uniq
    ON video_sources (owner_id, youtube_video_id)
    WHERE source_type = 'youtube' AND status <> 'failed';
CREATE UNIQUE INDEX video_sources_owner_sha256_uniq
    ON video_sources (owner_id, sha256)
    WHERE sha256 IS NOT NULL AND status <> 'failed';
CREATE INDEX video_sources_owner_created_idx ON video_sources (owner_id, created_at DESC);

CREATE TABLE editing_jobs (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id            text NOT NULL CHECK (char_length(owner_id) BETWEEN 1 AND 128),
    source_id           uuid NOT NULL REFERENCES video_sources (id) ON DELETE RESTRICT,
    status              text NOT NULL DEFAULT 'queued' CHECK (status IN (
                            'queued', 'validating', 'retrieving', 'uploading_source', 'analyzing',
                            'transcribing', 'selecting_highlights', 'extracting_clips', 'rendering',
                            'uploading_outputs', 'validating_outputs', 'completed', 'failed', 'cancelled')),
    stage               text NOT NULL DEFAULT 'queued',
    progress            smallint NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
    clip_count          smallint NOT NULL DEFAULT 5 CHECK (clip_count = 5),
    aspect_ratio        text NOT NULL DEFAULT '9:16' CHECK (aspect_ratio = '9:16'),
    caption_preset      text NOT NULL DEFAULT 'bold_creator',
    language            text CHECK (language IS NULL OR language ~ '^[a-z]{2,3}(-[A-Za-z]{2,4})?$'),
    min_clip_seconds    smallint NOT NULL DEFAULT 15,
    max_clip_seconds    smallint NOT NULL DEFAULT 90,
    requested_config    jsonb NOT NULL DEFAULT '{}'::jsonb,
    cancel_requested    boolean NOT NULL DEFAULT false,
    attempt_count       smallint NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    error_code          text,
    error_message       text CHECK (error_message IS NULL OR char_length(error_message) <= 2000),
    idempotency_key     text CHECK (idempotency_key IS NULL OR char_length(idempotency_key) BETWEEN 8 AND 128),
    worker_id           text,
    heartbeat_at        timestamptz,
    queued_at           timestamptz NOT NULL DEFAULT now(),
    started_at          timestamptz,
    completed_at        timestamptz,
    cancelled_at        timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT editing_jobs_clip_bounds CHECK (min_clip_seconds >= 5 AND max_clip_seconds <= 180 AND min_clip_seconds < max_clip_seconds),
    CONSTRAINT editing_jobs_completed_has_time CHECK (status <> 'completed' OR completed_at IS NOT NULL),
    CONSTRAINT editing_jobs_failed_has_error CHECK (status <> 'failed' OR error_code IS NOT NULL)
);

CREATE UNIQUE INDEX editing_jobs_owner_idempotency_uniq
    ON editing_jobs (owner_id, idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX editing_jobs_owner_created_idx ON editing_jobs (owner_id, created_at DESC);
CREATE INDEX editing_jobs_status_idx ON editing_jobs (status);
CREATE INDEX editing_jobs_active_heartbeat_idx ON editing_jobs (heartbeat_at)
    WHERE status NOT IN ('completed', 'failed', 'cancelled');
CREATE INDEX editing_jobs_source_idx ON editing_jobs (source_id);

CREATE TABLE job_events (
    id                  bigserial PRIMARY KEY,
    job_id              uuid NOT NULL REFERENCES editing_jobs (id) ON DELETE CASCADE,
    event_type          text NOT NULL CHECK (char_length(event_type) BETWEEN 1 AND 64),
    from_status         text,
    to_status           text,
    stage               text,
    message             text CHECK (message IS NULL OR char_length(message) <= 2000),
    details             jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX job_events_job_created_idx ON job_events (job_id, created_at, id);

CREATE TABLE detected_highlights (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id                  uuid NOT NULL REFERENCES editing_jobs (id) ON DELETE CASCADE,
    candidate_rank          integer NOT NULL CHECK (candidate_rank >= 1),
    start_seconds           numeric(10, 3) NOT NULL CHECK (start_seconds >= 0),
    end_seconds             numeric(10, 3) NOT NULL,
    duration_seconds        numeric(10, 3) NOT NULL CHECK (duration_seconds > 0),
    suggested_title         text NOT NULL CHECK (char_length(suggested_title) BETWEEN 1 AND 120),
    description             text,
    transcript              text,
    selection_rationale     text,
    engagement_score        smallint NOT NULL CHECK (engagement_score BETWEEN 0 AND 100),
    content_category        text,
    confidence              numeric(4, 3) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    selected                boolean NOT NULL DEFAULT false,
    rejection_reason        text,
    created_at              timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT detected_highlights_range CHECK (end_seconds > start_seconds
        AND abs(duration_seconds - (end_seconds - start_seconds)) < 0.01)
);
CREATE INDEX detected_highlights_job_idx ON detected_highlights (job_id, candidate_rank);

CREATE TABLE generated_clips (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id              uuid NOT NULL REFERENCES editing_jobs (id) ON DELETE CASCADE,
    owner_id            text NOT NULL CHECK (char_length(owner_id) BETWEEN 1 AND 128),
    highlight_id        uuid REFERENCES detected_highlights (id) ON DELETE SET NULL,
    clip_number         smallint NOT NULL CHECK (clip_number BETWEEN 1 AND 5),
    title               text NOT NULL CHECK (char_length(title) BETWEEN 1 AND 120),
    description         text,
    start_seconds       numeric(10, 3) NOT NULL CHECK (start_seconds >= 0),
    end_seconds         numeric(10, 3) NOT NULL,
    duration_seconds    numeric(10, 3) NOT NULL CHECK (duration_seconds > 0),
    storage_key         text,
    thumbnail_key       text,
    caption_key         text,
    file_size_bytes     bigint CHECK (file_size_bytes IS NULL OR file_size_bytes > 0),
    width               integer CHECK (width IS NULL OR width > 0),
    height              integer CHECK (height IS NULL OR height > 0),
    video_codec         text,
    audio_codec         text,
    render_status       text NOT NULL DEFAULT 'pending'
                        CHECK (render_status IN ('pending', 'rendering', 'rendered', 'failed')),
    validation_status   text NOT NULL DEFAULT 'pending'
                        CHECK (validation_status IN ('pending', 'passed', 'failed')),
    validation_details  jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT generated_clips_range CHECK (end_seconds > start_seconds),
    CONSTRAINT generated_clips_validated_has_file CHECK (validation_status <> 'passed' OR storage_key IS NOT NULL),
    UNIQUE (job_id, clip_number)
);
CREATE INDEX generated_clips_owner_idx ON generated_clips (owner_id, created_at DESC);
CREATE INDEX generated_clips_highlight_idx ON generated_clips (highlight_id);

CREATE TABLE clip_captions (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    clip_id             uuid NOT NULL REFERENCES generated_clips (id) ON DELETE CASCADE,
    format              text NOT NULL CHECK (format IN ('srt', 'vtt', 'ass')),
    storage_key         text NOT NULL,
    cue_count           integer NOT NULL CHECK (cue_count >= 0),
    transcript_text     text,
    language            text,
    word_timestamps     boolean NOT NULL DEFAULT false,
    sync_validation     jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (clip_id, format)
);

CREATE TABLE processing_usage (
    id                  bigserial PRIMARY KEY,
    owner_id            text NOT NULL CHECK (char_length(owner_id) BETWEEN 1 AND 128),
    job_id              uuid REFERENCES editing_jobs (id) ON DELETE SET NULL,
    metric              text NOT NULL CHECK (metric IN (
                            'jobs_created', 'source_seconds', 'transcription_seconds', 'ai_input_tokens',
                            'ai_output_tokens', 'render_seconds', 'storage_bytes')),
    quantity            numeric(20, 3) NOT NULL CHECK (quantity >= 0),
    recorded_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX processing_usage_owner_idx ON processing_usage (owner_id, recorded_at);
CREATE INDEX processing_usage_job_idx ON processing_usage (job_id);

CREATE TABLE worker_heartbeats (
    worker_id           text PRIMARY KEY CHECK (char_length(worker_id) BETWEEN 1 AND 200),
    hostname            text,
    pid                 integer,
    status              text NOT NULL DEFAULT 'idle' CHECK (status IN ('idle', 'busy', 'stopping')),
    current_job_id      uuid REFERENCES editing_jobs (id) ON DELETE SET NULL,
    version             text,
    started_at          timestamptz NOT NULL DEFAULT now(),
    last_seen_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX worker_heartbeats_seen_idx ON worker_heartbeats (last_seen_at);

-- -----------------------------------------------------------------------------
-- Row Level Security: enabled on every table. The backend connects with a
-- server-side database role that bypasses RLS. Client roles (when present,
-- e.g. Supabase `anon` / `authenticated`) get read access to their own rows
-- only and no write access.
-- -----------------------------------------------------------------------------
ALTER TABLE video_sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE editing_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE job_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE detected_highlights ENABLE ROW LEVEL SECURITY;
ALTER TABLE generated_clips ENABLE ROW LEVEL SECURITY;
ALTER TABLE clip_captions ENABLE ROW LEVEL SECURITY;
ALTER TABLE processing_usage ENABLE ROW LEVEL SECURITY;
ALTER TABLE worker_heartbeats ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON ALL TABLES IN SCHEMA public FROM authenticated;
    END IF;
END
$$;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated')
       AND EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                   WHERE n.nspname = 'auth' AND p.proname = 'uid') THEN
        GRANT SELECT ON video_sources, editing_jobs, job_events, detected_highlights,
                        generated_clips, clip_captions, processing_usage TO authenticated;

        CREATE POLICY video_sources_owner_select ON video_sources FOR SELECT TO authenticated
            USING (owner_id = auth.uid()::text);
        CREATE POLICY editing_jobs_owner_select ON editing_jobs FOR SELECT TO authenticated
            USING (owner_id = auth.uid()::text);
        CREATE POLICY job_events_owner_select ON job_events FOR SELECT TO authenticated
            USING (EXISTS (SELECT 1 FROM editing_jobs j WHERE j.id = job_events.job_id AND j.owner_id = auth.uid()::text));
        CREATE POLICY detected_highlights_owner_select ON detected_highlights FOR SELECT TO authenticated
            USING (EXISTS (SELECT 1 FROM editing_jobs j WHERE j.id = detected_highlights.job_id AND j.owner_id = auth.uid()::text));
        CREATE POLICY generated_clips_owner_select ON generated_clips FOR SELECT TO authenticated
            USING (owner_id = auth.uid()::text);
        CREATE POLICY clip_captions_owner_select ON clip_captions FOR SELECT TO authenticated
            USING (EXISTS (SELECT 1 FROM generated_clips c WHERE c.id = clip_captions.clip_id AND c.owner_id = auth.uid()::text));
        CREATE POLICY processing_usage_owner_select ON processing_usage FOR SELECT TO authenticated
            USING (owner_id = auth.uid()::text);
    END IF;
END
$$;

-- Keep updated_at honest on writes.
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

CREATE TRIGGER video_sources_set_updated_at BEFORE UPDATE ON video_sources
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER editing_jobs_set_updated_at BEFORE UPDATE ON editing_jobs
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER generated_clips_set_updated_at BEFORE UPDATE ON generated_clips
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
