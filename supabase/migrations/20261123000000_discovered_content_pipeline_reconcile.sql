-- Reconcile discovered_content with the discovery pipeline backend.
-- Production returned PGRST204 for 'availability': migration 20261122080000 had not been applied there,
-- so none of the pipeline columns existed. This migration is forward-only and idempotent: every statement
-- uses IF NOT EXISTS, so it is safe whether or not 20261122080000 has already run.
-- Nothing is dropped or retyped. Existing rows keep their values and new columns take their defaults.
-- No rights, RLS or grant changes.

ALTER TABLE public.discovered_content ADD COLUMN IF NOT EXISTS youtube_video_id text;
ALTER TABLE public.discovered_content ADD COLUMN IF NOT EXISTS channel_title text;
ALTER TABLE public.discovered_content ADD COLUMN IF NOT EXISTS thumbnail_url text;
ALTER TABLE public.discovered_content ADD COLUMN IF NOT EXISTS published_at timestamptz;
ALTER TABLE public.discovered_content ADD COLUMN IF NOT EXISTS availability text;
ALTER TABLE public.discovered_content ADD COLUMN IF NOT EXISTS metadata_status text NOT NULL DEFAULT 'pending';
ALTER TABLE public.discovered_content ADD COLUMN IF NOT EXISTS metadata_error text;
ALTER TABLE public.discovered_content ADD COLUMN IF NOT EXISTS metadata_checked_at timestamptz;
ALTER TABLE public.discovered_content ADD COLUMN IF NOT EXISTS media_access_method text;
ALTER TABLE public.discovered_content ADD COLUMN IF NOT EXISTS media_status text NOT NULL DEFAULT 'awaiting_authorized_media';
ALTER TABLE public.discovered_content ADD COLUMN IF NOT EXISTS media_error text;
ALTER TABLE public.discovered_content ADD COLUMN IF NOT EXISTS media_sha256 text;
ALTER TABLE public.discovered_content ADD COLUMN IF NOT EXISTS media_bytes bigint;
ALTER TABLE public.discovered_content ADD COLUMN IF NOT EXISTS media_probe jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE public.discovered_content ADD COLUMN IF NOT EXISTS media_checked_at timestamptz;
ALTER TABLE public.discovered_content ADD COLUMN IF NOT EXISTS analysis_status text NOT NULL DEFAULT 'not_started';
ALTER TABLE public.discovered_content ADD COLUMN IF NOT EXISTS analysis_reason text;
ALTER TABLE public.discovered_content ADD COLUMN IF NOT EXISTS analysis_candidates jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE public.discovered_content ADD COLUMN IF NOT EXISTS analysis_checked_at timestamptz;
ALTER TABLE public.discovered_content ADD COLUMN IF NOT EXISTS qualifying_moment_count integer NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_discovered_media_status ON public.discovered_content(media_status);
CREATE INDEX IF NOT EXISTS idx_discovered_analysis_status ON public.discovered_content(analysis_status);

-- Ask PostgREST to reload its schema cache so the new columns are visible to the API immediately.
NOTIFY pgrst, 'reload schema';
