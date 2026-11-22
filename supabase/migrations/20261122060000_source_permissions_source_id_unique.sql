-- Forward-only: source_permissions is upserted with on_conflict="source_id".
-- PostgreSQL accepts that conflict target only when source_id is unique.
-- Adds the constraint only when no unique constraint or index on source_id exists yet.
-- If duplicate source_id rows already exist, this fails loudly so they can be reviewed first.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_index i
    JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY (i.indkey)
    WHERE i.indrelid = 'public.source_permissions'::regclass
      AND i.indisunique
      AND i.indnatts = 1
      AND a.attname = 'source_id'
  ) THEN
    ALTER TABLE public.source_permissions
      ADD CONSTRAINT source_permissions_source_id_key UNIQUE (source_id);
  END IF;
END $$;
