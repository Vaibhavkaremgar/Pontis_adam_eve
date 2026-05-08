# Pontis Platform Maturity Notes

## Architecture
- FastAPI backend with cookie auth, CSRF, Redis-backed queues, Qdrant vector search, and Postgres persistence.
- Candidate ranking stays API-compatible while adding freshness, recruiter-learning, and deliverability controls.
- Admin and diagnostics endpoints live under `/api/admin/*` and reuse the existing backend proxy path.

## Queue Model
- Queue types: `outreach_send`, `outreach_followup`, `embedding_generation`, `candidate_refresh`, `reply_processing`.
- Dead letters are stored in Redis and can be replayed from the admin API.
- Queue health now reports depth, delayed jobs, dead letters, and stuck-processing signals.

## Security
- Startup validates critical config and fails fast on invalid production configuration.
- Admin tooling remains behind authenticated access and uses the same backend proxy channel as the app.
- Outreach tracking and reply webhooks are signed/verified.

## Deliverability
- Outreach now applies suppression checks, spam-risk gating, recruiter quotas, and open tracking.
- Bounce and unsubscribe signals suppress future sends.
- Email bodies can include a tracking pixel through the frontend proxy path.

## Recovery
- Dead-letter replay is the primary recovery mechanism for failed queue jobs.
- Candidate refresh and embedding migration are available through admin APIs.
- Audit events and platform events are retained for post-incident review.

## Onboarding
- Start with `/health/ready`, `/api/admin/diagnostics`, and `/api/admin/queue/deadletters`.
- Use the admin page for queue replay and operational inspection.
- Review `config_diagnostics` output before changing production env vars.
