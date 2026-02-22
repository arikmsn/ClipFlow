# ClipFlow AI — Project Handoff

## What It Is
An autonomous content arbitrage system that monitors YouTube channels, extracts viral moments, renders short-form vertical videos, and publishes to TikTok/Reels/Shorts.

## GitHub
https://github.com/arikmsn/ClipFlow

## Infrastructure
- **Database:** Supabase (PostgreSQL) — port 6543 (Transaction Pooler), port 5432 (Session Mode, required for FOR UPDATE SKIP LOCKED)
- **Storage:** Cloudflare R2 (S3-compatible) — bucket: `clipflow-sources`
- **API:** FastAPI + uvicorn

## Job State Machine
```
pending → downloading → analyzing → approval → [operator approves] → rendering → scheduled → published
                                              → [operator rejects] → rejected
```
Terminal states: `published`, `rejected`, `failed`

## Services (all complete with tests)
| File | Status | Description |
|------|--------|-------------|
| `app/services/watcher.py` | ✅ | Monitors YouTube, creates pending jobs |
| `app/services/downloader.py` | ✅ | Downloads via yt-dlp, uploads to R2 |
| `app/services/analyzer.py` | ✅ | Deepgram transcription + GPT-4o-mini viral scoring |
| `app/services/renderer.py` | ✅ | FFmpeg + face tracking, renders vertical clip |
| `app/services/publisher.py` | ✅ | Posts via Ayrshare, updates social_posts table |
| `app/api/jobs.py` | ✅ | Approval Dashboard API (FastAPI) |

## Database Migrations
- `001_initial_schema.sql` — all core tables
- `002_jobs_source_url_unique.sql` — idempotency constraint
- `003_jobs_add_source_path.sql` — downloader output
- `004_jobs_add_render_path.sql` — renderer output

## Key Architecture Decisions
- All external dependencies (DB, S3, FFmpeg, Deepgram, GPT) are injectable Protocol interfaces — no direct SDK calls in service logic
- Every service uses `FOR UPDATE SKIP LOCKED` for job claiming (requires port 5432, not 6543)
- Retry pattern: 3 retries → `failed`, with `error_log` updated on each failure
- Stuck-job recovery: any job stuck in intermediate state > 30 minutes is auto-recovered

## Current Blocker
`scripts/run_downloader.py` returns `False` because `FOR UPDATE SKIP LOCKED` doesn't work with Supabase Transaction Pooler (port 6543). Fix: use port 5432 in the script's DATABASE_URL.

## Next Tasks (in order)
1. Fix `run_downloader.py` to use port 5432 and verify it claims + downloads a real job
2. Build `app/worker.py` — infinite loop running all 5 services sequentially with SIGTERM handling
3. Deploy worker to a cloud VM (Railway, Render, or Fly.io)
4. Wire real Deepgram + OpenAI API keys and test the Analyzer
5. Wire real Ayrshare API key and test the Publisher

## Environment Variables Required
```
DATABASE_URL=postgresql://...@...pooler.supabase.com:6543/postgres
YOUTUBE_API_KEY=...
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
S3_BUCKET=clipflow-sources
S3_ENDPOINT_URL=https://....r2.cloudflarestorage.com
DEEPGRAM_API_KEY=...
OPENAI_API_KEY=...
AYRSHARE_API_KEY=...
```

## Coding Standards (see AGENTS.md in repo root)
- All injectable interfaces must be Protocol classes
- Tests use fake implementations — no real API calls
- Run `python -m unittest discover -s tests` before every commit
- Never modify existing migrations (001-004)
- Always show full diff — never truncate test files
