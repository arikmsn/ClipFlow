BEGIN;

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Enums
CREATE TYPE job_status AS ENUM (
  'pending',
  'downloading',
  'analyzing',
  'approval',
  'rendering',
  'scheduled',
  'published',
  'rejected',
  'failed'
);

CREATE TYPE social_post_status AS ENUM (
  'queued',
  'scheduled',
  'publishing',
  'published',
  'failed',
  'retry_wait',
  'dead_letter'
);

CREATE TYPE social_platform AS ENUM (
  'tiktok',
  'instagram_reels',
  'youtube_shorts'
);

CREATE TYPE social_account_status AS ENUM (
  'active',
  'paused',
  'restricted',
  'banned'
);

CREATE TYPE warming_status AS ENUM (
  'week1',
  'week2',
  'week3',
  'warmed',
  'manual_hold'
);

CREATE TYPE proxy_type AS ENUM (
  'residential_dedicated',
  'residential_rotating',
  'datacenter'
);

CREATE TYPE proxy_status AS ENUM (
  'active',
  'degraded',
  'paused',
  'retired'
);

CREATE TYPE device_profile_status AS ENUM (
  'active',
  'quarantined',
  'retired'
);

-- Core channel registry (referenced by jobs + social_accounts)
CREATE TABLE channels (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  niche TEXT,
  language_code VARCHAR(10) NOT NULL DEFAULT 'en',
  timezone TEXT NOT NULL DEFAULT 'UTC',
  source_platform TEXT NOT NULL DEFAULT 'youtube',
  source_channel_id TEXT,
  source_channel_url TEXT,
  keyword_triggers JSONB NOT NULL DEFAULT '[]'::jsonb,
  min_video_length_seconds INTEGER NOT NULL DEFAULT 600,
  view_velocity_threshold INTEGER NOT NULL DEFAULT 10000,
  polling_interval_minutes INTEGER NOT NULL DEFAULT 10,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT uq_channels_source_channel_id UNIQUE (source_platform, source_channel_id),
  CONSTRAINT uq_channels_source_channel_url UNIQUE (source_channel_url),
  CONSTRAINT chk_channels_min_video_length CHECK (min_video_length_seconds > 0),
  CONSTRAINT chk_channels_view_velocity CHECK (view_velocity_threshold >= 0),
  CONSTRAINT chk_channels_polling_interval CHECK (polling_interval_minutes BETWEEN 1 AND 60)
);

CREATE INDEX idx_channels_active ON channels (active);
CREATE INDEX idx_channels_niche ON channels (niche);
CREATE INDEX idx_channels_polling_interval ON channels (polling_interval_minutes);
CREATE INDEX idx_channels_keyword_triggers_gin ON channels USING GIN (keyword_triggers);

-- Proxy inventory
CREATE TABLE proxies (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  provider TEXT NOT NULL,
  name TEXT NOT NULL,
  proxy_type proxy_type NOT NULL,
  endpoint_host TEXT NOT NULL,
  endpoint_port INTEGER NOT NULL,
  auth_username_ref TEXT NOT NULL,
  auth_password_ref TEXT NOT NULL,
  country_code CHAR(2),
  region TEXT,
  sticky_session_supported BOOLEAN NOT NULL DEFAULT FALSE,
  status proxy_status NOT NULL DEFAULT 'active',
  last_healthcheck_at TIMESTAMPTZ,
  last_healthcheck_ok BOOLEAN,
  failure_count_24h INTEGER NOT NULL DEFAULT 0,
  cooldown_until TIMESTAMPTZ,
  notes TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT uq_proxies_endpoint UNIQUE (provider, endpoint_host, endpoint_port),
  CONSTRAINT chk_proxies_endpoint_port CHECK (endpoint_port BETWEEN 1 AND 65535),
  CONSTRAINT chk_proxies_failure_count CHECK (failure_count_24h >= 0)
);

CREATE INDEX idx_proxies_status ON proxies (status);
CREATE INDEX idx_proxies_provider_status ON proxies (provider, status);
CREATE INDEX idx_proxies_country_status ON proxies (country_code, status);
CREATE INDEX idx_proxies_cooldown_until ON proxies (cooldown_until);
CREATE INDEX idx_proxies_healthcheck ON proxies (last_healthcheck_at DESC);

-- Device/fingerprint profile inventory
CREATE TABLE device_profiles (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  provider TEXT NOT NULL,
  external_profile_id TEXT NOT NULL,
  name TEXT NOT NULL,
  fingerprint_hash TEXT,
  user_agent TEXT,
  platform_os TEXT,
  timezone TEXT,
  locale TEXT,
  webgl_vendor TEXT,
  status device_profile_status NOT NULL DEFAULT 'active',
  last_used_at TIMESTAMPTZ,
  risk_score NUMERIC(5,2) NOT NULL DEFAULT 0.00,
  notes TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT uq_device_profiles_provider_external UNIQUE (provider, external_profile_id),
  CONSTRAINT chk_device_profiles_risk_score CHECK (risk_score >= 0 AND risk_score <= 100)
);

CREATE INDEX idx_device_profiles_status ON device_profiles (status);
CREATE INDEX idx_device_profiles_last_used ON device_profiles (last_used_at DESC);
CREATE INDEX idx_device_profiles_risk_status ON device_profiles (risk_score DESC, status);
CREATE INDEX idx_device_profiles_provider ON device_profiles (provider);

-- Social account registry
CREATE TABLE social_accounts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  channel_id UUID NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
  platform social_platform NOT NULL,
  handle TEXT NOT NULL,
  platform_account_id TEXT,
  status social_account_status NOT NULL DEFAULT 'active',
  warming_status warming_status NOT NULL DEFAULT 'week1',
  warming_started_at TIMESTAMPTZ,
  posting_cap_per_day INTEGER NOT NULL DEFAULT 1,
  proxy_id UUID REFERENCES proxies(id) ON DELETE SET NULL,
  device_profile_id UUID REFERENCES device_profiles(id) ON DELETE SET NULL,
  credential_ref TEXT NOT NULL,
  credential_version INTEGER NOT NULL DEFAULT 1,
  token_expires_at TIMESTAMPTZ,
  last_auth_check_at TIMESTAMPTZ,
  last_post_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT uq_social_accounts_platform_handle UNIQUE (platform, handle),
  CONSTRAINT uq_social_accounts_platform_account UNIQUE (platform, platform_account_id),
  CONSTRAINT chk_social_accounts_posting_cap CHECK (posting_cap_per_day >= 0),
  CONSTRAINT chk_social_accounts_credential_version CHECK (credential_version > 0)
);

CREATE UNIQUE INDEX uq_social_accounts_proxy_id
  ON social_accounts (proxy_id)
  WHERE proxy_id IS NOT NULL;

CREATE UNIQUE INDEX uq_social_accounts_device_profile_id
  ON social_accounts (device_profile_id)
  WHERE device_profile_id IS NOT NULL;

CREATE INDEX idx_social_accounts_channel_platform ON social_accounts (channel_id, platform);
CREATE INDEX idx_social_accounts_warming_status ON social_accounts (warming_status, status);
CREATE INDEX idx_social_accounts_proxy ON social_accounts (proxy_id);
CREATE INDEX idx_social_accounts_status ON social_accounts (status);

-- Pipeline jobs
CREATE TABLE jobs (
  job_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_url TEXT NOT NULL,
  channel_id UUID NOT NULL REFERENCES channels(id) ON DELETE RESTRICT,
  status job_status NOT NULL DEFAULT 'pending',
  viral_score DOUBLE PRECISION,
  clip_manifest JSONB,
  render_path TEXT,
  published_urls JSONB NOT NULL DEFAULT '[]'::jsonb,
  error_log TEXT,
  retry_count INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT chk_jobs_retry_count CHECK (retry_count >= 0)
);

CREATE INDEX idx_jobs_status ON jobs (status);
CREATE INDEX idx_jobs_channel_id ON jobs (channel_id);
CREATE INDEX idx_jobs_created_at ON jobs (created_at DESC);
CREATE INDEX idx_jobs_viral_score ON jobs (viral_score DESC);
CREATE INDEX idx_jobs_clip_manifest_gin ON jobs USING GIN (clip_manifest);

-- Per-platform post records
CREATE TABLE social_posts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  job_id UUID NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
  platform social_platform NOT NULL,
  account_id UUID NOT NULL REFERENCES social_accounts(id) ON DELETE RESTRICT,
  status social_post_status NOT NULL DEFAULT 'queued',
  scheduled_for TIMESTAMPTZ,
  scheduled_for_actual TIMESTAMPTZ,
  published_at TIMESTAMPTZ,
  platform_post_id TEXT,
  post_url TEXT,
  caption_text TEXT,
  utm_url TEXT,
  provider TEXT NOT NULL DEFAULT 'ayrshare',
  provider_request_id TEXT,
  provider_response JSONB,
  attempt_count INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  first_attempt_at TIMESTAMPTZ,
  last_attempt_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT uq_social_posts_job_platform_account UNIQUE (job_id, platform, account_id),
  CONSTRAINT chk_social_posts_attempt_count CHECK (attempt_count >= 0)
);

CREATE UNIQUE INDEX uq_social_posts_provider_request_id
  ON social_posts (provider, provider_request_id)
  WHERE provider_request_id IS NOT NULL;

CREATE INDEX idx_social_posts_job_id ON social_posts (job_id);
CREATE INDEX idx_social_posts_status_scheduled ON social_posts (status, scheduled_for_actual);
CREATE INDEX idx_social_posts_account_status ON social_posts (account_id, status);
CREATE INDEX idx_social_posts_platform_published_at ON social_posts (platform, published_at DESC);
CREATE INDEX idx_social_posts_failed_retries ON social_posts (status, attempt_count, last_attempt_at);
CREATE INDEX idx_social_posts_provider_response_gin ON social_posts USING GIN (provider_response);

COMMIT;
