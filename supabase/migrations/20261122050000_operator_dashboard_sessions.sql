-- Migration 007: Operator Dashboard Sessions for Supabase Persistence
CREATE TABLE IF NOT EXISTS public.dashboard_sessions (
    session_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'admin',
    is_admin BOOLEAN NOT NULL DEFAULT true,
    csrf_token TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    ip_address TEXT
);

CREATE INDEX IF NOT EXISTS idx_dashboard_sessions_expires_at ON public.dashboard_sessions(expires_at);

-- RLS: Enable RLS and grant service role full access
ALTER TABLE public.dashboard_sessions ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'dashboard_sessions' AND policyname = 'Service role full access on dashboard_sessions'
    ) THEN
        CREATE POLICY "Service role full access on dashboard_sessions"
            ON public.dashboard_sessions
            FOR ALL
            TO service_role
            USING (true)
            WITH CHECK (true);
    END IF;
END $$;
