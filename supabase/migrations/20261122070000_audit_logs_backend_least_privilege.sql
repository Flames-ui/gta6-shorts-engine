-- Least-privilege grants for the backend's audit log writes (audit_logs).
--
-- The backend writes and reads audit_logs with the service_role key. That role bypasses RLS policies
-- but still needs table privileges. The audit trail is append-only from the backend, so service_role
-- gets SELECT and INSERT only: no UPDATE, DELETE or TRUNCATE.
--
-- RLS stays enabled. anon and PUBLIC get nothing. authenticated keeps the existing "view own audit
-- logs" read policy and loses every write privilege. Idempotent; no data change.

ALTER TABLE public.audit_logs ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.audit_logs FROM PUBLIC, anon;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON TABLE public.audit_logs FROM authenticated;

GRANT SELECT, INSERT ON TABLE public.audit_logs TO service_role;
GRANT SELECT ON TABLE public.audit_logs TO authenticated;
