BEGIN;

ALTER TABLE jobs
ADD COLUMN IF NOT EXISTS render_path TEXT;

CREATE INDEX IF NOT EXISTS idx_jobs_render_path ON jobs (render_path);

COMMIT;
