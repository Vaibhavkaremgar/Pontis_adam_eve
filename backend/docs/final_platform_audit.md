# Pontis Final Platform Audit

## Summary

Pontis now exposes a role-aware admin surface, hybrid candidate retrieval, richer AI observability, and additive schema support for user roles. The existing frontend and API shapes remain stable.

## Major Additions

- Role-based auth with `recruiter`, `internal_ops`, and `admin` roles.
- JWT role claims and middleware propagation.
- Admin endpoints gated by least-privilege dependencies.
- Hybrid retrieval scoring with vector, lexical, structured, and recruiter preference signals.
- Retrieval attribution embedded into candidate explanations.
- AI observability metrics for retrieval quality, ranking drift, embedding drift, prompt failures, and queue AI latency.
- Additive `users.role` schema support for legacy databases.
- Backend unit tests for auth/RBAC and hybrid retrieval ranking.

## Operational Notes

- Admin tools:
  - Queue dead-letter inspection and replay
  - Candidate refresh
  - Outreach analytics
  - Audit log inspection
  - Embedding migration controls
- Audit events are recorded for admin replay, refresh, and embedding migration actions.
- Retrieval telemetry now shows up under `metrics.ai_observability`.

## Deployment Notes

- Existing databases will receive the `users.role` column automatically on startup.
- Set `ADMIN_EMAILS` and `OPS_EMAILS` to map specific logins to elevated roles.
- Keep `DATABASE_URL`, `JWT_SECRET`, `PUBLIC_APP_URL`, and `INTERNAL_API_KEY` configured before boot.

## Known Limits

- Hybrid retrieval is implemented inside the current vector-first pipeline rather than as a separate index service.
- E2E browser automation and broader dashboard expansion are still backlog items.
- Historical tokens issued before role claims will continue to authenticate, but they default to recruiter permissions.
