-- Durable production state for discovery, rights, rendering, scheduling and reconciliation.
-- Safe to apply more than once. Run through Supabase CLI or SQL Editor as a database owner.
BEGIN;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Bring legacy tables into alignment with the running API and workers.
ALTER TABLE public.source_permissions ADD COLUMN IF NOT EXISTS original_creator text;
ALTER TABLE public.source_permissions ADD COLUMN IF NOT EXISTS source_url text;
ALTER TABLE public.source_permissions ADD COLUMN IF NOT EXISTS permission_evidence text;
ALTER TABLE public.source_permissions ADD COLUMN IF NOT EXISTS attribution_requirements text;
ALTER TABLE public.source_permissions ADD COLUMN IF NOT EXISTS derivative_rights_allowed boolean NOT NULL DEFAULT false;
ALTER TABLE public.source_permissions ADD COLUMN IF NOT EXISTS revoked_at timestamptz;
ALTER TABLE public.source_permissions ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE public.source_permissions ADD COLUMN IF NOT EXISTS user_id uuid REFERENCES auth.users(id) ON DELETE CASCADE;
ALTER TABLE public.discovered_content ADD COLUMN IF NOT EXISTS viral_potential_score numeric NOT NULL DEFAULT 0;
ALTER TABLE public.discovered_content ADD COLUMN IF NOT EXISTS metadata jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE public.discovered_content ADD COLUMN IF NOT EXISTS discovered_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE public.discovered_content ADD COLUMN IF NOT EXISTS user_id uuid REFERENCES auth.users(id) ON DELETE CASCADE;
ALTER TABLE public.publishing_queue ADD COLUMN IF NOT EXISTS idempotency_key text;
ALTER TABLE public.publishing_queue ADD COLUMN IF NOT EXISTS privacy_status text NOT NULL DEFAULT 'private';
ALTER TABLE public.publishing_queue ADD COLUMN IF NOT EXISTS retry_after timestamptz;
ALTER TABLE public.publishing_queue ADD COLUMN IF NOT EXISTS provider_status text;
ALTER TABLE public.publishing_queue ADD COLUMN IF NOT EXISTS last_synced_at timestamptz;
ALTER TABLE public.publishing_queue ADD COLUMN IF NOT EXISTS user_id uuid REFERENCES auth.users(id) ON DELETE CASCADE;
ALTER TABLE public.publishing_history ADD COLUMN IF NOT EXISTS user_id uuid REFERENCES auth.users(id) ON DELETE CASCADE;
ALTER TABLE public.platform_analytics ADD COLUMN IF NOT EXISTS user_id uuid REFERENCES auth.users(id) ON DELETE CASCADE;
ALTER TABLE public.platform_analytics ADD COLUMN IF NOT EXISTS watch_time_seconds numeric;
ALTER TABLE public.platform_analytics ADD COLUMN IF NOT EXISTS follower_growth bigint;
ALTER TABLE public.platform_analytics ADD COLUMN IF NOT EXISTS hook_type text;
ALTER TABLE public.platform_analytics ADD COLUMN IF NOT EXISTS duration_seconds numeric;
ALTER TABLE public.platform_analytics ADD COLUMN IF NOT EXISTS published_at timestamptz;
ALTER TABLE public.processing_jobs ADD COLUMN IF NOT EXISTS lock_token text;
ALTER TABLE public.processing_jobs ADD COLUMN IF NOT EXISTS locked_at timestamptz;
ALTER TABLE public.processing_jobs ADD COLUMN IF NOT EXISTS heartbeat_at timestamptz;
ALTER TABLE public.processing_jobs ADD COLUMN IF NOT EXISTS idempotency_key text;
ALTER TABLE public.processing_jobs ADD COLUMN IF NOT EXISTS source_id text REFERENCES public.content_sources(id) ON DELETE SET NULL;
ALTER TABLE public.content_sources ADD COLUMN IF NOT EXISTS last_polled_at timestamptz;
ALTER TABLE public.content_sources ADD COLUMN IF NOT EXISTS last_success_at timestamptz;
ALTER TABLE public.content_sources ADD COLUMN IF NOT EXISTS last_error text;
ALTER TABLE public.content_sources ADD COLUMN IF NOT EXISTS consecutive_failures integer NOT NULL DEFAULT 0;
ALTER TABLE public.content_sources ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'active';

-- Persistent task/event tables omitted by the original migrations but referenced by app modules.
CREATE TABLE IF NOT EXISTS public.discovery_runs (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid REFERENCES auth.users(id) ON DELETE CASCADE,
 source_id text REFERENCES public.content_sources(id) ON DELETE CASCADE, status text NOT NULL DEFAULT 'queued'
 CHECK (status IN ('queued','running','completed','failed','cancelled')),
 items_found integer NOT NULL DEFAULT 0, items_eligible integer NOT NULL DEFAULT 0, error text,
 started_at timestamptz, finished_at timestamptz, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS public.autonomous_config (
 id text PRIMARY KEY DEFAULT 'default', user_id uuid REFERENCES auth.users(id) ON DELETE CASCADE,
 is_active boolean NOT NULL DEFAULT false, daily_target integer NOT NULL DEFAULT 15 CHECK (daily_target BETWEEN 0 AND 100),
 auto_publish boolean NOT NULL DEFAULT false, min_score numeric NOT NULL DEFAULT 8,
 outro_enabled boolean NOT NULL DEFAULT true, updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS public.outro_settings (
 id text PRIMARY KEY DEFAULT 'default', user_id uuid REFERENCES auth.users(id) ON DELETE CASCADE,
 enabled boolean NOT NULL DEFAULT false, outro_path text, promo_text text, destination_url text,
 duration_seconds numeric NOT NULL DEFAULT 4 CHECK (duration_seconds BETWEEN 0 AND 15),
 qr_code_enabled boolean NOT NULL DEFAULT false, updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS public.clip_timelines (
 clip_id text PRIMARY KEY REFERENCES public.generated_clips(id) ON DELETE CASCADE,
 user_id uuid REFERENCES auth.users(id) ON DELETE CASCADE, timeline_data jsonb NOT NULL,
 updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS public.discovered_moments (
 id text PRIMARY KEY, source_id text REFERENCES public.content_sources(id) ON DELETE SET NULL,
 user_id uuid REFERENCES auth.users(id) ON DELETE CASCADE, video_url text, title text NOT NULL,
 start_time numeric NOT NULL CHECK (start_time >= 0), end_time numeric NOT NULL CHECK (end_time > start_time),
 duration numeric NOT NULL CHECK (duration > 0), score numeric NOT NULL DEFAULT 0,
 hook_text text, event_type text, status text NOT NULL DEFAULT 'detected', created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS public.daily_publication_reconciliations (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid REFERENCES auth.users(id) ON DELETE CASCADE,
 platform text NOT NULL, reconciliation_date date NOT NULL, status text NOT NULL DEFAULT 'running'
 CHECK (status IN ('running','completed','partial','failed')),
 scanned_count integer NOT NULL DEFAULT 0, matched_count integer NOT NULL DEFAULT 0,
 metrics_updated integer NOT NULL DEFAULT 0, error text, started_at timestamptz NOT NULL DEFAULT now(),
 finished_at timestamptz, UNIQUE(user_id, platform, reconciliation_date)
);
CREATE TABLE IF NOT EXISTS public.job_events (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), job_id text NOT NULL REFERENCES public.processing_jobs(id) ON DELETE CASCADE,
 user_id uuid REFERENCES auth.users(id) ON DELETE CASCADE, event_type text NOT NULL,
 payload jsonb NOT NULL DEFAULT '{}'::jsonb, created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sources_owner_status ON public.content_sources(user_id,status,is_active);
CREATE INDEX IF NOT EXISTS idx_discovered_owner_time ON public.discovered_content(user_id,discovery_time DESC);
CREATE INDEX IF NOT EXISTS idx_discovered_hash ON public.discovered_content(content_hash) WHERE content_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_jobs_recovery ON public.processing_jobs(status,heartbeat_at) WHERE status IN ('queued','processing');
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_idempotency ON public.processing_jobs(user_id,idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_queue_due ON public.publishing_queue(status,scheduled_for) WHERE status IN ('scheduled','queued','retrying');
CREATE UNIQUE INDEX IF NOT EXISTS idx_queue_idempotency ON public.publishing_queue(user_id,idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_reconciliation_due ON public.daily_publication_reconciliations(reconciliation_date,status);
CREATE INDEX IF NOT EXISTS idx_job_events_job_time ON public.job_events(job_id,created_at);

-- Private storage: only authenticated owners can access objects under their own first path segment.
INSERT INTO storage.buckets (id,name,public,file_size_limit,allowed_mime_types)
VALUES ('shorts','shorts',false,524288000,ARRAY['video/mp4','video/webm','video/quicktime','image/jpeg','image/png','image/webp'])
ON CONFLICT (id) DO UPDATE SET public=false, file_size_limit=EXCLUDED.file_size_limit, allowed_mime_types=EXCLUDED.allowed_mime_types;
DROP POLICY IF EXISTS "shorts owner read" ON storage.objects;
DROP POLICY IF EXISTS "shorts owner insert" ON storage.objects;
DROP POLICY IF EXISTS "shorts owner update" ON storage.objects;
DROP POLICY IF EXISTS "shorts owner delete" ON storage.objects;
CREATE POLICY "shorts owner read" ON storage.objects FOR SELECT TO authenticated USING (bucket_id='shorts' AND (storage.foldername(name))[1]=auth.uid()::text);
CREATE POLICY "shorts owner insert" ON storage.objects FOR INSERT TO authenticated WITH CHECK (bucket_id='shorts' AND (storage.foldername(name))[1]=auth.uid()::text);
CREATE POLICY "shorts owner update" ON storage.objects FOR UPDATE TO authenticated USING (bucket_id='shorts' AND (storage.foldername(name))[1]=auth.uid()::text) WITH CHECK (bucket_id='shorts' AND (storage.foldername(name))[1]=auth.uid()::text);
CREATE POLICY "shorts owner delete" ON storage.objects FOR DELETE TO authenticated USING (bucket_id='shorts' AND (storage.foldername(name))[1]=auth.uid()::text);

-- Service-role backend access; user access is tenant-scoped. Replace policies idempotently.
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['discovery_runs','autonomous_config','outro_settings','clip_timelines','discovered_moments','daily_publication_reconciliations','job_events'] LOOP
  EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I',t||'_service_all',t);
  EXECUTE format('CREATE POLICY %I ON public.%I FOR ALL TO service_role USING (true) WITH CHECK (true)',t||'_service_all',t);
  EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I',t||'_owner_all',t);
  EXECUTE format('CREATE POLICY %I ON public.%I FOR ALL TO authenticated USING (user_id=auth.uid()) WITH CHECK (user_id=auth.uid())',t||'_owner_all',t);
 END LOOP;
END $$;

-- Protect sensitive records and add tenant policies to existing persistent state tables.
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['content_sources','discovered_content','processing_jobs','generated_clips','publishing_accounts','connected_channels','publishing_queue','publishing_history','platform_analytics','production_schedules','source_permissions','clip_timelines','discovered_moments','autonomous_config','outro_settings'] LOOP
  EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I',t||'_backend_all',t);
  EXECUTE format('CREATE POLICY %I ON public.%I FOR ALL TO service_role USING (true) WITH CHECK (true)',t||'_backend_all',t);
 END LOOP;
END $$;

-- Restrict direct OAuth-token access to server-side service role only; no authenticated-user policy.
DROP POLICY IF EXISTS "Users can manage own publishing accounts" ON public.publishing_accounts;
DROP POLICY IF EXISTS "Users can view own publishing accounts" ON public.publishing_accounts;

COMMIT;
