# Pontis Backend (FastAPI)

Production-ready backend for Next.js hiring flow:
- POST /api/auth/login
- POST /api/hiring/create
- POST /api/jobs/{job_id}/mode
- GET /api/candidates?jobId=...&mode=volume|elite&refresh=true|false
- POST /api/candidates/swipe
- POST /api/candidates/export
- POST /api/voice/refine
- POST /api/outreach
- GET /api/interviews?jobId=...
- GET /health
- GET /api/health
- POST /slack/commands
- POST /slack/interactions
- GET /api/outreach/status?jobId=...

## Run

1. Create/activate virtualenv
2. Install dependencies:
   pip install -r requirements.txt
3. Copy env file:
   cp .env.example .env
4. Start API:
   uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

## Alembic Workflow

Create a new migration from the `backend/` directory:

```bash
alembic revision --autogenerate -m "add job intakes"
```

Follow these conventions:
- Keep revision IDs short. Prefer Alembic's default 12-character hash format.
- Do not introduce date-based revision IDs or custom IDs longer than 16 characters.
- Keep the migration graph linear whenever possible.
- If you create two heads, merge them immediately with `alembic merge`.

Before pushing, run the integrity check:

```bash
python scripts/check_alembic_integrity.py
```

If Alembic reports multiple heads:
1. Run `alembic heads` to list them.
2. Create a merge migration with `alembic merge -m "merge heads" <head1> <head2>`.
3. Re-run `python scripts/check_alembic_integrity.py`.

In CI, the same check runs before `alembic upgrade head` against a temporary PostgreSQL service.

## Redis Queue Cleanup

If stale queue items or dead-letter entries reference deleted jobs, run the cleanup utility once:

```bash
python scripts/cleanup_queue_state.py
```

For local development only, you can clear Redis completely after stopping the app:

```bash
redis-cli FLUSHDB
```

Do not use `FLUSHALL` against shared or production Redis instances.

The backend also runs a one-time queue cleanup during worker startup, so orphaned queue entries are pruned automatically on deploy.

## Environment Variables

Required:
- GROQ_API_KEY
- GROQ_BASE_URL
- GROQ_MODEL (defaults to `llama-3.3-70b-versatile`)
- QDRANT_URL
- QDRANT_API_KEY
- INTERNAL_CANDIDATE_COLLECTION_NAME (defaults to `internal_candidate_chunks`)
- DATABASE_URL
- PDL_ENABLED=false by default; set PDL_API_KEY only when PDL is enabled
- USE_INTERNAL_CANDIDATE_DB=false by default; set to `true` to source from the internal resume corpus instead of PDL
- JWT_SECRET
- REDIS_URL (recommended for multi-worker cache consistency)
- HF_TOKEN (optional; enables HuggingFace-backed model loading when available)
- Optional outreach/ATS keys: SENDGRID_API_KEY / POSTMARK_SERVER_TOKEN / MERGE_API_KEY / MERGE_ACCOUNT_TOKEN
- Slack integration: SLACK_BOT_TOKEN and SLACK_SIGNING_SECRET
- AUTO_RECREATE_SCHEMA=false (recommended; destructive runtime schema changes are disabled)
- Booking/interview plugins: BOOKING_PROVIDER, BOOKING_PROVIDER_URL, INTERVIEW_PROVIDER, INTERVIEW_PROVIDER_URL

## Internal Resume Ingestion

Seed the internal candidate corpus from `backend/resumes/` with:

`python scripts/seed_resumes.py`

The script extracts PDF text, parses structured candidate JSON, writes PostgreSQL rows, and upserts vectors into Qdrant.

## Architecture

app/
- api/routes: thin controllers
- services: business logic (auth, hiring, candidates, voice, outreach, interviews)
- services/refresh_scheduler.py: periodic candidate + embedding refresh loop
- db: SQLAlchemy session + repositories
- models: SQLAlchemy entities
- schemas: request/response contracts
- utils: response wrappers, exceptions, text helpers

## Response Envelope

All endpoints return:
- success: boolean
- data: payload | null
- error: string | null

## Workflow
source -> rank -> approve (swipe) -> learn (weight updates) -> outreach -> export

## Production Hardening
- RLHF stabilization: smoothed updates + decayed feedback influence + per-job normalization
- Scheduler safety: job refresh locks + duplicate-window guard + PDL rate limiting
- Observability: `/health` (DB, PDL, LLM, scheduler) + metrics logs (`candidate_count`, `feedback_count`, `outreach_sent`, `evaluation_metrics_updated`)
- Slack: `/slack/commands` and `/slack/interactions` use signature verification and Slack SDK message posting
- Flywheel: scheduler runs periodic candidate refresh + re-embedding for stale profiles
- Cache layer: in-memory cache for embedding reuse within process lifetime (SQLite cache backend disabled after Postgres migration)
