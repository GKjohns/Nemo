-- Add viz_spec and chart_image_url columns to nodes table
-- These store LLM-suggested chart specifications and rendered chart image URLs

ALTER TABLE public.nodes
  ADD COLUMN IF NOT EXISTS viz_spec JSONB,
  ADD COLUMN IF NOT EXISTS chart_image_url TEXT;
