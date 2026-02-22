BEGIN;

ALTER TABLE jobs
ADD COLUMN source_path TEXT;

CREATE INDEX idx_jobs_source_path ON jobs (source_path);

COMMIT;
