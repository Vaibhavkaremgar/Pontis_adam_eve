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

## Environment Variables

Required:
- OPENAI_API_KEY
- QDRANT_URL
- QDRANT_API_KEY
- DATABASE_URL
- PDL_API_KEY
- JWT_SECRET
- REDIS_URL (recommended for multi-worker cache consistency)
- Optional outreach/ATS keys: SENDGRID_API_KEY / POSTMARK_SERVER_TOKEN / MERGE_API_KEY / MERGE_ACCOUNT_TOKEN
- Slack integration: SLACK_BOT_TOKEN and SLACK_SIGNING_SECRET
- AUTO_RECREATE_SCHEMA=false (recommended; destructive runtime schema changes are disabled)
- Booking/interview plugins: BOOKING_PROVIDER, BOOKING_PROVIDER_URL, INTERVIEW_PROVIDER, INTERVIEW_PROVIDER_URL

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
- Observability: `/health` (DB, PDL, OpenAI, scheduler) + metrics logs (`candidate_count`, `feedback_count`, `outreach_sent`, `evaluation_metrics_updated`)
- Slack: `/slack/commands` and `/slack/interactions` use signature verification and Slack SDK message posting
- Flywheel: scheduler runs periodic candidate refresh + re-embedding for stale profiles
- Cache layer: in-memory cache for embedding reuse within process lifetime (SQLite cache backend disabled after Postgres migration)
