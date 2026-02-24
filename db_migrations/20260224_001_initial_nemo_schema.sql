-- Nemo Sprint 1 initial schema for Supabase Postgres
-- Apply this in Supabase SQL editor or migration runner.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS public.datasets (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  description TEXT,
  source_type TEXT NOT NULL DEFAULT 'csv',
  connection_info TEXT NOT NULL,
  profile JSONB,
  row_count INTEGER,
  column_count INTEGER,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.sessions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  dataset_id UUID NOT NULL REFERENCES public.datasets(id) ON DELETE CASCADE,
  hypothesis TEXT NOT NULL,
  context TEXT,
  status TEXT NOT NULL DEFAULT 'idle',
  config JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.nodes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id UUID NOT NULL REFERENCES public.sessions(id) ON DELETE CASCADE,
  type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'frontier',
  question TEXT,
  code TEXT,
  result JSONB,
  answer TEXT,
  confidence DOUBLE PRECISION,
  summary TEXT,
  supported_by JSONB,
  depth INTEGER NOT NULL DEFAULT 0,
  priority DOUBLE PRECISION NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.edges (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id UUID NOT NULL REFERENCES public.sessions(id) ON DELETE CASCADE,
  source_id UUID NOT NULL REFERENCES public.nodes(id) ON DELETE CASCADE,
  target_id UUID NOT NULL REFERENCES public.nodes(id) ON DELETE CASCADE,
  type TEXT NOT NULL,
  reasoning TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.events (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  session_id UUID NOT NULL REFERENCES public.sessions(id) ON DELETE CASCADE,
  type TEXT NOT NULL,
  payload JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_nodes_session ON public.nodes(session_id);
CREATE INDEX IF NOT EXISTS idx_edges_session ON public.edges(session_id);
CREATE INDEX IF NOT EXISTS idx_events_session ON public.events(session_id, id);
CREATE INDEX IF NOT EXISTS idx_sessions_dataset ON public.sessions(dataset_id);

CREATE OR REPLACE FUNCTION public.set_sessions_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_sessions_updated_at ON public.sessions;
CREATE TRIGGER trg_sessions_updated_at
BEFORE UPDATE ON public.sessions
FOR EACH ROW
EXECUTE FUNCTION public.set_sessions_updated_at();
