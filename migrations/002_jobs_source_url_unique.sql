BEGIN;

ALTER TABLE jobs
ADD CONSTRAINT uq_jobs_source_url UNIQUE (source_url);

COMMIT;
