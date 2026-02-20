# ClipFlow
Autonomous Content Arbitrage Engine

## Current Scaffold

- `migrations/001_initial_schema.sql`: Initial PostgreSQL DDL with explicit enums, constraints, and indexes for:
  - `channels`
  - `proxies`
  - `device_profiles`
  - `social_accounts`
  - `jobs`
  - `social_posts`
- `app/`: Minimal FastAPI scaffold with a `/health` endpoint.

## Job State Machine

`pending -> downloading -> analyzing -> approval -> rendering -> scheduled -> published`

Terminal states:

- `published` (success)
- `rejected` (operator rejection)
- `failed` (technical failure after retry policy)

## Run API

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Apply Migration

```bash
psql "$DATABASE_URL" -f migrations/001_initial_schema.sql
```
