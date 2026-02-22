# ClipFlow AI — Agent Instructions

## Before Starting Any Task

1. Run `git pull origin main` to sync with the latest changes before touching any file.
2. Only modify files directly related to the current task scope.
3. Never touch files you weren't explicitly asked to change.
4. If a merge conflict exists in any file relevant to the task, resolve it before proceeding — keep the most recent logic and remove all conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`).

## Coding Standards

- All external dependencies (APIs, DB, S3, FFmpeg, etc.) must be injectable interfaces (Protocol classes) — no direct SDK calls inside service logic.
- Every service must follow the retry/DLQ pattern: 3 retries → `failed`, with `error_log` updated on each failure.
- Every service must implement stuck-job recovery for its status if a job has been in that state for more than 30 minutes.
- Use `FOR UPDATE SKIP LOCKED` for all job claim queries.
- No hardcoded credentials anywhere — all secrets via environment variables.

## Testing Standards

- Every new service must have a corresponding test file in `tests/`.
- Tests must use fake/injectable implementations — no real DB, no real API calls, no paid services.
- Run `python -m unittest discover -s tests` and `python -m compileall app` before committing.
- Always show the full `git diff HEAD~1 HEAD` in the summary — never truncate test files.

## Job State Machine

```
pending → downloading → analyzing → approval → [operator approves] → rendering → scheduled → published
                                              → [operator rejects] → rejected
```

Terminal states: `published`, `rejected`, `failed`

## Project Structure

```
app/
  api/         # FastAPI routers and dependencies
  services/    # Core pipeline services (watcher, downloader, analyzer, renderer, publisher)
  db.py        # Database connection (psycopg2, Supabase-compatible)
  utils.py     # Shared utilities (normalize_keyword_triggers, etc.)
  main.py      # FastAPI app entrypoint
migrations/    # SQL migrations (never modify existing ones)
scripts/       # One-off utility scripts
tests/         # Unit tests (no real DB or API calls)
```

## Migrations

- Never modify existing migration files (001, 002, 003, 004).
- New migrations must be additive only and use `IF NOT EXISTS` / `IF EXISTS` for idempotency.
- Always wrap migrations in `BEGIN; ... COMMIT;`.

## PR Standards

- Open a PR after every completed task.
- PR description must include: summary of changes, what was tested, and what comes next.
- Always show the full diff — never use placeholder comments like `# ... full file added`.
