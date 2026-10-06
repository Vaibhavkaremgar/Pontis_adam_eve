# Production E2E Verification — Adam + Eve + Maya Full Flow

> **Purpose**: Operator checklist for manually verifying ONE real candidate through the complete Adam → Eve → Maya pipeline.
> **Scope**: Read-only verification. Do NOT mutate production data using this document.
> **Source**: Every field, query, and endpoint derived from actual source code — no assumptions.

---

## STAGE 0 — Test Setup

Record these identifiers before starting. Every subsequent stage references them.

| Identifier | Value | Source |
|---|---|---|
| Adam job ID (`job_id`) | ____________ | `job_descriptions.id` |
| Candidate ID (`candidate_id`) | ____________ | `candidates.candidate_id` (external string) |
| Candidate record ID (`candidate_record_id`) | ____________ | `candidates.id` (UUID PK) |
| Candidate email | ____________ | `candidates.email` |
| Candidate agency ID | ____________ | `candidates.agency_id` |
| Recruiter user ID | ____________ | `users.id` |
| Recruiter agency ID | ____________ | `users.agency_id` |
| Qdrant collection (candidates) | `internal_candidate_chunks` | `config.py: INTERNAL_CANDIDATE_COLLECTION_NAME` |
| Qdrant collection (jobs) | `job_chunks` | `config.py: JOB_COLLECTION_NAME` |
| Eve base URL | ____________ | `EVE_BASE_URL` env var |
| Maya/interview base URL | `https://interview.pontis.one` | `config.py: INTERVIEW_APP_URL` |

---

## STAGE 1 — Adam Job Creation / Posting

**Source files**: `app/models/entities.py` (`JobEntity`), `app/services/linkedin_fallback_service.py`

### Checks

- [ ] Job row exists in `job_descriptions` table
- [ ] `job_descriptions.job_status` = `active`
- [ ] `job_descriptions.is_active` = `true`
- [ ] `job_descriptions.description` and/or `job_descriptions.requirements` are non-empty (needed for embedding)
- [ ] `job_descriptions.agency_id` matches recruiter's agency
- [ ] LinkedIn fallback application URL is built by `build_eve_application_url()` in `linkedin_fallback_service.py`
- [ ] Application URL = `{EVE_BASE_URL}/application` — NO `job_id` appended
- [ ] `job_descriptions.structured_data["linkedinPosting"]["applicationUrl"]` = `{EVE_BASE_URL}/application` (if fallback was triggered)

### Read-only SQL

```sql
-- READ ONLY — SAFE FOR PRODUCTION
SELECT
    id,
    title,
    job_status,
    is_active,
    agency_id,
    created_at,
    structured_data->>'linkedinPosting' AS linkedin_posting,
    LENGTH(COALESCE(description,'') || COALESCE(requirements,'')) AS job_text_length
FROM job_descriptions
WHERE id = '<job_id>';
```

### Record

| Field | Value |
|---|---|
| Job ID | |
| Job title | |
| job_status | |
| LinkedIn posting status | |
| Application URL | |
| Job text length (chars) | |

---

## STAGE 2 — Eve Candidate Onboarding

**Architecture**: Candidate opens `https://eve.pontis.one/application`. Eve writes the candidate record directly into the shared PostgreSQL database. Adam and Eve share the same `DATABASE_URL`.

**Source files**: `app/models/entities.py` (`CandidateProfileEntity`)

The candidate is NOT tied to any specific job at entry. `candidates.job_id` is populated later when Adam matches the candidate against a job and creates a `candidates` row for that (job, candidate) pair.

### Checks

- [ ] Candidate row exists in `candidates` table
- [ ] `candidates.email` is non-null and valid
- [ ] `candidates.candidate_id` (external string ID) is set
- [ ] `candidates.agency_id` is set (Eve's agency)
- [ ] Profile data sufficient for embedding: at least one of `resume_text`, `parsed_resume_text`, `current_role`, `summary`, `skills`, `work_experience`, `parsed_resume_json` is non-null

### Read-only SQL

```sql
-- READ ONLY — SAFE FOR PRODUCTION
SELECT
    id                      AS candidate_record_id,
    candidate_id,
    name,
    email,
    agency_id,
    current_role,
    embedding_status,
    embedding_version,
    embedding_text_hash,
    embedding_indexed_at,
    last_refreshed_at,
    created_at,
    (resume_text IS NOT NULL)           AS has_resume_text,
    (parsed_resume_text IS NOT NULL)    AS has_parsed_resume_text,
    (summary IS NOT NULL)               AS has_summary,
    (skills IS NOT NULL)                AS has_skills,
    (parsed_resume_json IS NOT NULL)    AS has_parsed_resume_json
FROM candidates
WHERE candidate_id = '<candidate_id>'
ORDER BY created_at DESC
LIMIT 5;
```

### Record

| Field | Value |
|---|---|
| candidate_record_id | |
| candidate_id | |
| email | |
| agency_id | |
| Profile completeness | |
| created_at | |

---

## STAGE 3 — Adam Candidate Refresh / Embedding Detection

**Source files**: `app/services/refresh_scheduler.py`, `app/services/internal_candidate_embedding_service.py`

### Scheduler flow

The scheduler runs a unified loop every **30 seconds** (`_run_loop` in `refresh_scheduler.py`). Each cycle calls:

1. `_run_candidate_refresh_cycle()` — refreshes candidate data for recent jobs
2. `_run_candidate_flywheel_cycle()` — enqueues `candidate_refresh` job queue task
3. `_run_candidate_embedding_detection_cycle()` — calls `enqueue_stale_candidate_embedding_jobs()` which:
   - Queries `candidates` for rows where profile data exists AND embedding is stale
   - Stale = `embedding_status != 'EMBEDDED'` OR `embedding_version != EMBEDDING_VERSION` OR `updated_at > embedding_indexed_at`
   - Enqueues `candidate_embedding_index` job with idempotency key `candidate-embedding:{row.id}:{EMBEDDING_VERSION}:{text_hash}`

### Embedding index function

`index_candidate_embedding()` in `internal_candidate_embedding_service.py`:
1. Loads `CandidateProfileEntity` by `candidate_record_id`
2. Builds text via `build_structured_candidate_text(row)`
3. Computes `sha256` hash of text
4. Skips if `embedding_status == 'EMBEDDED'` AND `embedding_version == EMBEDDING_VERSION` AND `text_hash` matches AND Qdrant point exists
5. Sets `embedding_status = 'PROCESSING'`, calls `embed_many([text])` (model: `all-MiniLM-L6-v2`, vector size: 384)
6. Calls `upsert_internal_candidate_embeddings()` → writes to Qdrant `internal_candidate_chunks`
7. Sets `embedding_status = 'EMBEDDED'`, `embedding_version`, `embedding_text_hash`, `embedding_indexed_at`

### Checks

- [ ] `candidates.embedding_status` = `EMBEDDED`
- [ ] `candidates.embedding_version` = value of `EMBEDDING_VERSION` env var (default: `v2_structured`)
- [ ] `candidates.embedding_text_hash` is non-null (SHA-256 hex)
- [ ] `candidates.embedding_indexed_at` is non-null
- [ ] `candidates.embedding_indexed_at` >= `candidates.updated_at` (not stale)

### Read-only SQL

```sql
-- READ ONLY — SAFE FOR PRODUCTION
SELECT
    id                      AS candidate_record_id,
    candidate_id,
    embedding_status,
    embedding_version,
    embedding_text_hash,
    embedding_indexed_at,
    updated_at,
    CASE
        WHEN embedding_indexed_at IS NULL THEN 'never_indexed'
        WHEN embedding_status != 'EMBEDDED' THEN 'not_embedded'
        WHEN updated_at > embedding_indexed_at THEN 'stale'
        ELSE 'current'
    END AS embedding_freshness
FROM candidates
WHERE id = '<candidate_record_id>';
```

### Record

| Field | Value |
|---|---|
| embedding_status | |
| embedding_version | |
| embedding_text_hash | |
| embedding_indexed_at | |
| embedding_freshness | |


---

## STAGE 4 — Qdrant Candidate Indexing

**Source files**: `app/services/qdrant_service.py`, `app/services/internal_candidate_embedding_service.py`

**Collection**: `internal_candidate_chunks` (default; overridable via `INTERNAL_CANDIDATE_COLLECTION_NAME`)

### Qdrant point structure

Written by `upsert_internal_candidate_embeddings()`. Point ID is a deterministic integer: `sha256("internal-candidate:{candidateRecordId}")[:8]` as big-endian uint64.

**Payload fields** (exact — from source):

| Field | Type | Value |
|---|---|---|
| `candidateId` | string | `candidates.candidate_id` (external string) |
| `candidateRecordId` | string | `candidates.id` (UUID) |
| `agencyId` | string | `candidates.agency_id` |
| `source` | string | `"internal"` |
| `sourceType` | string | `"internal"` |
| `contentType` | string | `"resume"` |
| `embeddingVersion` | string | value of `EMBEDDING_VERSION` env var |
| `textHash` | string | SHA-256 of the candidate text |
| `indexedAt` | string | ISO-8601 UTC timestamp |
| `resumeFingerprint` | string | from `raw_data` if present, else `""` |

**Vector**: 384-dimensional float array (model `all-MiniLM-L6-v2`)

### Checks

- [ ] Collection `internal_candidate_chunks` exists in Qdrant
- [ ] Point with `candidateRecordId = '<candidate_record_id>'` exists
- [ ] Payload `embeddingVersion` matches `EMBEDDING_VERSION` env var
- [ ] Payload `agencyId` matches candidate's agency
- [ ] Vector has 384 dimensions

### Qdrant inspection (Python — read-only)

```python
# READ ONLY — SAFE FOR PRODUCTION
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue

client = QdrantClient(url="<QDRANT_URL>", api_key="<QDRANT_API_KEY>")
results, _ = client.scroll(
    collection_name="internal_candidate_chunks",
    scroll_filter=Filter(must=[
        FieldCondition(key="candidateRecordId", match=MatchValue(value="<candidate_record_id>"))
    ]),
    limit=5,
    with_payload=True,
    with_vectors=True,
)
for pt in results:
    print("point_id:", pt.id)
    print("payload:", pt.payload)
    print("vector_dims:", len(pt.vector) if pt.vector else "N/A")
```

### Record

| Field | Value |
|---|---|
| Collection | `internal_candidate_chunks` |
| Point ID | |
| candidateRecordId in payload | |
| agencyId in payload | |
| embeddingVersion in payload | |
| textHash in payload | |
| indexedAt in payload | |
| Vector dimensions | |

---

## STAGE 5 — Adam Internal Candidate Matching

**Source files**: `app/services/internal_candidate_semantic_service.py`, `app/core/config.py`

### Matching pipeline (exact from `match_internal_candidates_for_job`)

1. Load job from `job_descriptions`
2. Build job text via `build_job_text(job)` — must be ≥ 20 chars
3. Embed job text → 384-dim vector via `get_embedding(job_text)` (model: `all-MiniLM-L6-v2`)
4. Search Qdrant `internal_candidate_chunks` with `metadata_filters={"embeddingVersion": EMBEDDING_VERSION}`, `limit=INTERNAL_CANDIDATE_RETRIEVAL_TOP_K` (default: 100)
5. For each hit: load `CandidateProfileEntity` from PostgreSQL by `candidateRecordId`
6. Drop if `embedding_status != 'EMBEDDED'` or `embedding_version != EMBEDDING_VERSION`
7. Compute scores:
   - `semantic_similarity` = Qdrant cosine score (0–1)
   - `skill_match` = `|job_tokens ∩ candidate_skill_tokens| / |job_tokens|`
   - `experience_match` = `min(candidate_years, required_years) / max(...)` clamped
   - `location_match` = 1.0 if remote/no location, 1.0 if match, 0.0 if mismatch
   - `role_match` = token overlap between job title and candidate current_role
8. `base_score = (0.70 * semantic + 0.20 * skill + 0.10 * experience) / weight_sum`
9. `final_score = base_score * (0.85 + 0.15 * location_match) * (0.90 + 0.10 * role_match)`
10. Apply threshold: `INTERNAL_CANDIDATE_MATCH_THRESHOLD` = **0.60** (from `config.py`, overridable via env)
11. Return qualified candidates sorted by `final_score` descending, up to `INTERNAL_CANDIDATE_MATCH_LIMIT` (default: 50)

### Threshold (verified from source)

```python
# app/core/config.py line:
INTERNAL_CANDIDATE_MATCH_THRESHOLD = float(os.getenv("INTERNAL_CANDIDATE_MATCH_THRESHOLD", "0.60"))
```

### Qdrant 403 diagnosis

If matching fails with `Unexpected Response: 403 (Forbidden)` on `/collections/internal_candidate_chunks/points/search`:

- **Root cause**: `QDRANT_URL` or `QDRANT_API_KEY` is wrong/missing for the Adam service
- **Client init**: `QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY or None)` in `qdrant_service.py`
- **403 means**: The API key is present but incorrect, OR the endpoint requires auth and none was sent
- **Check**: `QDRANT_URL` must point to the correct cluster (Cloud vs Railway-hosted)
- **Check**: `QDRANT_API_KEY` must match the key configured on that cluster
- **Note**: Candidate indexing and job matching use the **same** `QDRANT_URL` / `QDRANT_API_KEY` — there is only one Qdrant client in `qdrant_service.py`
- **Collection used for matching**: `internal_candidate_chunks` (NOT `job_chunks` — `job_chunks` is for the old sourced-candidate flow)
- **`job_chunks`**: Still defined in schema but matching now uses `internal_candidate_chunks` exclusively for Eve candidates

### Checks

- [ ] `QDRANT_URL` is set and reachable
- [ ] `QDRANT_API_KEY` is correct for that Qdrant instance
- [ ] Collection `internal_candidate_chunks` has > 0 points
- [ ] Candidate appears in Qdrant search results for the job
- [ ] `final_score` >= 0.60
- [ ] Candidate appears in `GET /api/candidates?jobId=<job_id>` response

### API endpoint

```
GET /api/candidates?jobId=<job_id>
Authorization: JWT cookie (recruiter session)
```

Response includes `internalCandidates[]` with per-candidate `explanation.semanticScore`, `explanation.skillOverlap`, `explanation.finalScore`.

### Record

| Field | Value |
|---|---|
| semantic_similarity | |
| skill_match | |
| experience_match | |
| location_match | |
| role_match | |
| final_score | |
| threshold | 0.60 |
| Match decision | PASS / FAIL |

---

## STAGE 6 — Recruiter Sees Candidate

**Source files**: `app/api/routes/candidates.py`, `app/services/internal_candidate_semantic_service.py`

### UI state

- Recruiter opens Adam dashboard → selects job → candidate list loads
- Candidate appears in the internal candidates list (NOT as a traditional application)
- `profileAccess` = `LIMITED` until accepted
- `recruiterAction` = `NONE` (no interest yet)
- Score displayed = `fitScoreDisplay` (derived from `fitScore = round(final_score * 5.0, 4)`)

### Checks

- [ ] Candidate visible in recruiter UI for the job
- [ ] Score displayed matches computed `final_score * 5`
- [ ] `recruiterAction` = `NONE`
- [ ] `requestStatus` = null
- [ ] Candidate is NOT shown as a traditional application (no `candidate_applications` row required)

---

## STAGE 7 — Recruiter Clicks Interested

**Source files**: `app/api/routes/candidates.py`, `app/services/candidate_request_service.py`, `app/services/eve_notification_service.py`

### API endpoint

```
POST /api/candidates/<candidate_id>/interest?jobId=<job_id>
Authorization: JWT cookie (recruiter session)
```

No request body. `candidate_id` is the external string ID from `candidates.candidate_id`.

### What happens (exact from `create_interest_request`)

1. Validates job ownership (`job.agency_id == recruiter.agency_id`)
2. Validates candidate exists in `candidates` where `agency_id = recruiter.agency_id`
3. Extracts candidate email from `candidates.email` or `raw_data` — **required**, raises 422 if missing
4. Checks `candidate_feedback` — raises 409 if candidate was previously rejected
5. Checks `candidate_requests` for existing row (idempotent: returns existing if found)
6. Creates `candidate_requests` row (status=`PENDING`)
7. Creates/updates `recruiter_interest_requests` row
8. Calls `upsert_outbound_event()` → creates `adam_eve_outbound_events` row

### candidate_requests row

| Column | Value |
|---|---|
| `id` | new UUID |
| `candidate_id` | external candidate string ID |
| `agency_id` | recruiter's agency UUID |
| `job_id` | job UUID |
| `status` | `PENDING` |
| `created_by` | recruiter user UUID |
| `eve_event_id` | null (set later when Eve responds) |

**Uniqueness**: `UNIQUE(agency_id, job_id, candidate_id)` — one request per (agency, job, candidate)

### recruiter_interest_requests row

| Column | Value |
|---|---|
| `id` | new UUID |
| `candidate_id` | external candidate string ID |
| `job_id` | job UUID |
| `agency_id` | recruiter's agency UUID |
| `recruiter_id` | recruiter user UUID |
| `request_status` | `interested` |
| `candidate_response` | null |

**Uniqueness**: `UNIQUE(candidate_id, job_id, agency_id, recruiter_id)`

### adam_eve_outbound_events row

| Column | Value |
|---|---|
| `id` | new UUID |
| `adam_event_id` | = `candidate_requests.id` (the request UUID) |
| `candidate_id` | `candidates.id` (record UUID, NOT external string) |
| `job_id` | job UUID |
| `agency_id` | agency UUID |
| `recruiter_user_id` | recruiter UUID |
| `notification_type` | `recruiter_interest` |
| `status` | `pending` |
| `attempt_count` | 0 |
| `payload.candidate_email` | candidate email (required for delivery) |

**Idempotency**: `UNIQUE(adam_event_id)` — duplicate calls return existing row

### Read-only SQL

```sql
-- READ ONLY — SAFE FOR PRODUCTION

-- candidate_requests
SELECT id, candidate_id, agency_id, job_id, status, created_by, created_at, updated_at, responded_at, eve_event_id
FROM candidate_requests
WHERE agency_id = '<agency_id>' AND job_id = '<job_id>' AND candidate_id = '<candidate_id>';

-- recruiter_interest_requests
SELECT id, candidate_id, job_id, agency_id, recruiter_id, request_status, candidate_response, candidate_response_at, created_at
FROM recruiter_interest_requests
WHERE candidate_id = '<candidate_id>' AND job_id = '<job_id>' AND agency_id = '<agency_id>';

-- adam_eve_outbound_events
SELECT id, adam_event_id, candidate_id, job_id, agency_id, notification_type, status, attempt_count, last_error, next_retry_at, delivered_at, created_at
FROM adam_eve_outbound_events
WHERE adam_event_id = '<candidate_request_id>';
```

### Record

| Field | Value |
|---|---|
| candidate_requests.id | |
| candidate_requests.status | |
| recruiter_interest_requests.id | |
| adam_eve_outbound_events.id | |
| adam_eve_outbound_events.status | |


---

## STAGE 8 — Adam → Eve Recruiter-Interest Delivery

**Source files**: `app/services/eve_notification_service.py`

### Delivery mechanism

Background thread `pontis-eve-delivery` runs `_worker_loop()` every **10 seconds**. Each cycle calls `deliver_pending_events()` which:

1. Claims up to 10 `pending` events from `adam_eve_outbound_events` using `SELECT ... FOR UPDATE SKIP LOCKED`
2. Increments `attempt_count`, sets `next_retry_at` to 60s ahead (visibility lock)
3. For `notification_type = 'recruiter_interest'`: POSTs to `{EVE_BASE_URL}/api/internal/recruiter-interest`
4. Auth header: `Authorization: Bearer {EVE_INTERNAL_TOKEN}`
5. On 200/201: marks `status = 'delivered'`, sets `delivered_at`
6. On 5xx (retryable): schedules retry with backoff `[10, 30, 120, 600, 1800]` seconds
7. On 401/403/404/422: marks `status = 'failed'` (non-retryable)
8. Max attempts: 5 (length of `_RETRY_DELAYS_SECONDS`)

### Payload sent to Eve

```json
{
  "adam_event_id": "<candidate_requests.id>",
  "candidate_id": "<candidates.id>",
  "candidate_email": "<candidate email>",
  "job_id": "<job_id>",
  "agency_id": "<agency_id>",
  "recruiter_user_id": "<recruiter_user_id>",
  "recruiter_message": null
}
```

### Checks

- [ ] `adam_eve_outbound_events.status` = `delivered`
- [ ] `adam_eve_outbound_events.delivered_at` is non-null
- [ ] `adam_eve_outbound_events.attempt_count` >= 1
- [ ] `adam_eve_outbound_events.last_error` is null
- [ ] Eve received exactly one delivery (409 from Eve = duplicate, treated as success)

### Read-only SQL

```sql
-- READ ONLY — SAFE FOR PRODUCTION
SELECT id, adam_event_id, status, attempt_count, last_error, next_retry_at, delivered_at, created_at, updated_at
FROM adam_eve_outbound_events
WHERE adam_event_id = '<candidate_request_id>'
ORDER BY created_at DESC;
```

### Record

| Field | Value |
|---|---|
| status | |
| attempt_count | |
| delivered_at | |
| last_error | |

---

## STAGE 9 — Eve Candidate Opportunity + Recruiter-Interest Email

**Architecture**: Eve owns its own dashboard and email sending. Adam only delivers the `recruiter_interest` event. Eve's internal handling is outside Adam's codebase.

### Eve dashboard

- [ ] Opportunity appears in candidate's Eve dashboard
- [ ] Shows correct job title / recruiter / agency
- [ ] Request ID matches `candidate_requests.id` (= `adam_event_id` in the delivered payload)
- [ ] Status = pending / awaiting response

### Candidate email (Eve-side)

Eve is responsible for sending the recruiter-interest notification email to the candidate. Adam does NOT send this email — Adam only delivers the event to Eve.

**If Eve has not implemented this email**: `MISSING — Eve recruiter-interest email is not implemented on Eve's side`

To verify: check candidate's inbox for an email from Eve after `adam_eve_outbound_events.delivered_at`.

### Record

| Field | Value |
|---|---|
| Eve dashboard opportunity visible | YES / NO |
| Recruiter-interest email received | YES / NO / MISSING |
| Email timestamp | |
| CTA URL in email | |

---

## STAGE 10 — Candidate Acceptance from Eve Dashboard

**Source files**: `app/api/routes/candidates.py`, `app/services/candidate_response_service.py`

### Path A — Eve calls Adam directly (internal endpoint)

```
POST /api/candidates/<candidate_id>/respond?action=accept&request_id=<candidate_request_id>
Header: X-Internal-Api-Key: <INTERNAL_API_KEY>
```

`candidate_id` here is the **external string** (`candidates.candidate_id`), NOT the UUID.

**Security**: Gated by `X-Internal-Api-Key` header. JWT-authenticated recruiter calls are rejected.

### Path B — Eve calls the internal candidate-response endpoint

```
POST /api/internal/candidate-response
Header: Authorization: Bearer <ADAM_INTERNAL_SERVICE_TOKEN>
Body:
{
  "eve_event_id": "<uuid>",
  "adam_event_id": "<candidate_requests.id>",
  "candidate_id": "<candidates.candidate_id>",
  "job_id": "<job_id>",
  "agency_id": "<agency_id>",
  "response": "interested"
}
```

This calls `respond_to_eve_candidate_response()` which deduplicates on `eve_event_id`.

### State transitions (exact from `respond_to_candidate_request`)

```
PENDING  + accept  → ACCEPTED  (sets responded_at, triggers slot booking artifacts)
PENDING  + decline → DECLINED  (sets responded_at)
ACCEPTED + accept  → idempotent 200 (re-runs _ensure_slot_selection_artifacts safely)
DECLINED + decline → idempotent 200
ACCEPTED + decline → 409
DECLINED + accept  → 409
```

### After acceptance

`_ensure_slot_selection_artifacts()` is called:
1. Loads `CandidateProfileEntity` and `JobEntity`
2. Checks for existing `notification_events` row (email guard)
3. Checks for existing `notification_workflow_tokens` row
4. If no token: calls `upsert_notification_workflow_token()` → creates token
5. Builds booking URL: `https://interview.pontis.one/booking.html?token=<token>`
6. Creates `interview_sessions` row (stage=`recruiter_screen`, suppress_side_effects=True)
7. Upserts `notification_events` row (email guard)
8. Sends booking email to candidate (only if `email_already_sent = False`)
9. Calls `_enqueue_slot_booking_eve_event()` → creates `adam_eve_outbound_events` row for `interview_slot_booking`

### Checks

- [ ] `candidate_requests.status` = `ACCEPTED`
- [ ] `candidate_requests.responded_at` is non-null
- [ ] `recruiter_interest_requests.candidate_response` = `accept`
- [ ] `recruiter_interest_requests.candidate_response_at` is non-null
- [ ] `notification_workflow_tokens` row created
- [ ] `interview_sessions` row created
- [ ] `notification_events` row created (email guard)
- [ ] Booking email sent to candidate

### Read-only SQL

```sql
-- READ ONLY — SAFE FOR PRODUCTION
SELECT id, candidate_id, job_id, agency_id, status, responded_at, eve_event_id, updated_at
FROM candidate_requests
WHERE id = '<candidate_request_id>';

SELECT id, candidate_id, job_id, request_status, candidate_response, candidate_response_at
FROM recruiter_interest_requests
WHERE candidate_id = '<candidate_id>' AND job_id = '<job_id>';
```

### Record

| Field | Value |
|---|---|
| candidate_requests.status | |
| candidate_requests.responded_at | |
| recruiter_interest_requests.candidate_response | |
| HTTP status returned | |

---

## STAGE 11 — Candidate Acceptance from Email (Idempotency Test)

**Source files**: `app/services/candidate_response_service.py`

This stage MUST be tested separately from Stage 10.

### Test scenarios

**Scenario A — Email CTA after dashboard acceptance**
- Candidate already accepted via dashboard (Stage 10)
- Candidate clicks email CTA link
- Expected: same `candidate_requests.id` is used, status remains `ACCEPTED`, no new booking token created
- Expected HTTP: `200` (idempotent)

**Scenario B — Repeated email CTA**
- Candidate clicks email CTA twice
- Expected: second call returns `200` (idempotent, same state)
- No duplicate `notification_workflow_tokens` row
- No second booking email sent (`email_already_sent = True` guard)

**Scenario C — Dashboard acceptance after email acceptance**
- Expected: `200` idempotent

**Scenario D — Decline after accept**
- Expected: `409` — `"Cannot transition from 'ACCEPTED' to 'DECLINED'"`

### Idempotency guards (exact from source)

1. `candidate_requests` UNIQUE constraint on `(agency_id, job_id, candidate_id)` — prevents duplicate requests
2. `respond_to_candidate_request()` checks `row.status == target_status` before transitioning — returns current state if already in target
3. `respond_to_eve_candidate_response()` checks `row.eve_event_id == eve_event_id` — returns immediately if same event
4. `notification_events` UNIQUE on `notification_key` — prevents duplicate email sends
5. `adam_eve_outbound_events` UNIQUE on `adam_event_id` — prevents duplicate Eve notifications
6. `notification_workflow_tokens` UNIQUE on `token` — prevents duplicate tokens

### Checks

- [ ] Only ONE `candidate_requests` row exists for (agency, job, candidate)
- [ ] Only ONE `notification_workflow_tokens` row exists for (job, candidate, token_type=slot_selection)
- [ ] Only ONE `notification_events` row exists for the notification_key
- [ ] Only ONE `adam_eve_outbound_events` row for `interview_slot_booking` event
- [ ] Booking email sent exactly once

### Read-only SQL

```sql
-- READ ONLY — SAFE FOR PRODUCTION

-- Count requests (must be 1)
SELECT COUNT(*) AS request_count
FROM candidate_requests
WHERE agency_id = '<agency_id>' AND job_id = '<job_id>' AND candidate_id = '<candidate_id>';

-- Count booking tokens (must be 1)
SELECT COUNT(*) AS token_count, MAX(created_at) AS latest_created
FROM notification_workflow_tokens
WHERE job_id = '<job_id>' AND candidate_id = '<candidate_id>' AND token_type = 'slot_selection';

-- Count notification events (must be 1)
SELECT COUNT(*) AS event_count
FROM notification_events
WHERE notification_key LIKE 'slot-selection:<job_id>:<candidate_record_id>%';

-- Count slot booking outbound events (must be 1)
SELECT COUNT(*) AS outbound_count
FROM adam_eve_outbound_events
WHERE notification_type = 'interview_slot_booking'
  AND adam_event_id LIKE 'slot-booking:<candidate_request_id>%';
```

---

## STAGE 12 — Slot Booking Artifacts

**Source files**: `app/services/candidate_response_service.py`, `app/services/notification_service.py`, `app/models/entities.py` (`NotificationWorkflowTokenEntity`)

### notification_workflow_tokens row

Created by `upsert_notification_workflow_token()` in `notification_service.py`.

| Column | Value |
|---|---|
| `id` | UUID |
| `source_app` | `ui` |
| `job_id` | job UUID |
| `candidate_id` | external candidate string ID |
| `agency_id` | agency UUID |
| `user_id` | recruiter UUID |
| `token_type` | `slot_selection` |
| `workflow_name` | `slot_selection` |
| `token` | `secrets.token_urlsafe(32)` — 43-char URL-safe string |
| `is_active` | `true` |
| `status` | `active` |
| `expires_at` | `now + 7 days` |

**Uniqueness**: `UNIQUE(token)` on the token column

### Booking URL construction

```python
# notification_service.py
BOOKING_BASE_URL = "https://interview.pontis.one/booking.html"

def _build_booking_link(token: str, *, source_type: str = "ui") -> str:
    params = {"token": token} if token else {}
    query = urlencode(params) if params else ""
    return f"{BOOKING_BASE_URL}?{query}" if query else BOOKING_BASE_URL
```

**Result**: `https://interview.pontis.one/booking.html?token=<notification_workflow_tokens.token>`

**CRITICAL**: This uses `notification_workflow_tokens.token` — NOT `interview_sessions.session_token`. These are completely different tokens.

### interview_sessions row

Created by `create_interview_session()` with `suppress_side_effects=True`.

| Column | Value |
|---|---|
| `job_id` | job UUID |
| `candidate_id` | external candidate string ID |
| `agency_id` | agency UUID |
| `session_token` | separate token (Maya-owned after booking) |
| `status` | `scheduled` |
| `booking_status` | `pending` |
| `stage` | `recruiter_screen` |
| `booking_url` | booking URL (may be set) |

### Checks

- [ ] `notification_workflow_tokens` row exists with `token_type = 'slot_selection'`
- [ ] `notification_workflow_tokens.is_active = true`
- [ ] Booking URL = `https://interview.pontis.one/booking.html?token=<token>`
- [ ] Token is from `notification_workflow_tokens.token`, NOT from `interview_sessions.session_token`
- [ ] `interview_sessions` row exists for (job, candidate)

### Read-only SQL

```sql
-- READ ONLY — SAFE FOR PRODUCTION
SELECT
    id,
    job_id,
    candidate_id,
    agency_id,
    user_id,
    token_type,
    workflow_name,
    token,
    is_active,
    status,
    expires_at,
    created_at,
    'https://interview.pontis.one/booking.html?token=' || token AS booking_url
FROM notification_workflow_tokens
WHERE job_id = '<job_id>' AND candidate_id = '<candidate_id>'
ORDER BY created_at DESC;

SELECT id, job_id, candidate_id, session_token, status, booking_status, stage, booking_url, created_at
FROM interview_sessions
WHERE job_id = '<job_id>' AND candidate_id = '<candidate_id>'
ORDER BY created_at DESC;
```

### Record

| Field | Value |
|---|---|
| notification_workflow_tokens.id | |
| token | |
| token_type | |
| booking_url | |
| interview_sessions.id | |
| interview_sessions.session_token | |

---

## STAGE 13 — Booking Email + Eve Dashboard Consistency

**Source files**: `app/services/candidate_response_service.py`, `app/services/eve_notification_service.py`

### Email (sent by Adam)

Sent in `_ensure_slot_selection_artifacts()` after `NotificationEventRepository.upsert()`.

- Recipient: `candidates.email`
- Subject: `Interview slot selection: <job.title>`
- Body contains: the booking URL (`https://interview.pontis.one/booking.html?token=<token>`)
- Guard: `email_already_sent = existing_notification is not None` — captured BEFORE upsert
- Only sent if `email_already_sent = False`

### Eve notification (interview_slot_booking)

Sent via `_enqueue_slot_booking_eve_event()` → `upsert_outbound_event()`.

- `adam_event_id` = `slot-booking:<candidate_request_id>:<candidate_record_id>`
- `notification_type` = `interview_slot_booking`
- Delivered to: `{EVE_BASE_URL}/api/internal/candidate-notification`
- Payload contains `booking_url` = same booking URL

### Consistency check

Both the email body and the Eve notification payload must contain the **identical** booking URL string.

```sql
-- READ ONLY — SAFE FOR PRODUCTION

-- Get booking URL from notification_events (email guard row)
SELECT body AS email_booking_url, delivery_reference AS token, created_at
FROM notification_events
WHERE notification_key LIKE 'slot-selection:<job_id>%'
  AND candidate_id = '<candidate_record_id>';

-- Get booking URL from adam_eve_outbound_events (Eve notification)
SELECT payload->>'booking_url' AS eve_booking_url, status, delivered_at
FROM adam_eve_outbound_events
WHERE adam_event_id LIKE 'slot-booking:<candidate_request_id>%'
  AND notification_type = 'interview_slot_booking';
```

### Checks

- [ ] `notification_events.body` = `https://interview.pontis.one/booking.html?token=<token>`
- [ ] `adam_eve_outbound_events.payload.booking_url` = same URL (character-for-character)
- [ ] Both URLs use the same token value
- [ ] `adam_eve_outbound_events.status` = `delivered`
- [ ] No duplicate `notification_events` rows for the same `notification_key`
- [ ] No duplicate `adam_eve_outbound_events` rows for the same `adam_event_id`

### Record

| Field | Value |
|---|---|
| Email booking URL | |
| Eve notification booking URL | |
| URLs match | YES / NO |
| Eve notification delivered_at | |


---

## STAGE 14 — Maya Booking

**Architecture**: Maya/interview project owns everything after the booking URL is opened. Adam does NOT control this stage. Maya reads `notification_workflow_tokens` from the shared DB to resolve context.

### What Maya does

1. Candidate opens `https://interview.pontis.one/booking.html?token=<token>`
2. Maya reads `notification_workflow_tokens` by `token` to get `job_id`, `candidate_id`, `agency_id`
3. Candidate sees available slots
4. Candidate books a slot
5. Maya creates/updates `interview_sessions` row with `booking_status = 'confirmed'`, `scheduled_at`, `stage = 'booked'`
6. Maya marks `notification_workflow_tokens.consumed_at` (optional)

**IMPORTANT**: Adam must NOT generate the final interview session token. `interview_sessions.session_token` is Maya-owned.

### Checks

- [ ] Booking URL opens correctly in browser
- [ ] Maya resolves `job_id` and `candidate_id` from the token
- [ ] Candidate can see and select available slots
- [ ] After booking: `interview_sessions.booking_status` = `confirmed` or `booked`
- [ ] `interview_sessions.scheduled_at` is set
- [ ] `interview_sessions.session_token` is set (Maya-generated)

### Read-only SQL

```sql
-- READ ONLY — SAFE FOR PRODUCTION
SELECT
    id                  AS session_id,
    job_id,
    candidate_id,
    agency_id,
    session_token,
    status,
    booking_status,
    stage,
    scheduled_at,
    booked_at,
    booking_url,
    created_at
FROM interview_sessions
WHERE job_id = '<job_id>' AND candidate_id = '<candidate_id>'
ORDER BY created_at DESC
LIMIT 3;
```

### Record

| Field | Value |
|---|---|
| interview_sessions.id | |
| session_token | |
| booking_status | |
| scheduled_at | |
| stage | |

---

## STAGE 15 — Final Interview URL

**Source files**: `app/services/interview_session_service.py`

Maya constructs the final interview URL from `interview_sessions.session_token`:

```python
# interview_session_service.py
def _interview_url(session_token: str) -> str:
    return f"{INTERVIEW_APP_URL}/interview?token={session_token}"
```

**Result**: `https://interview.pontis.one/interview?token=<interview_sessions.session_token>`

**Token distinction** (critical):
- `notification_workflow_tokens.token` → booking URL (`/booking.html?token=...`) — Adam-owned
- `interview_sessions.session_token` → interview URL (`/interview?token=...`) — Maya-owned

### Checks

- [ ] Interview URL = `https://interview.pontis.one/interview?token=<session_token>`
- [ ] `session_token` maps to correct `candidate_id` in `interview_sessions`
- [ ] `session_token` maps to correct `job_id` in `interview_sessions`
- [ ] Token is from `interview_sessions.session_token`, NOT from `notification_workflow_tokens.token`

---

## STAGE 16 — Interview Execution

**Architecture**: Maya owns interview execution entirely. Adam does not participate.

### Checks

- [ ] Candidate opens interview URL
- [ ] Interview starts (Maya sets `interview_sessions.status = 'in_progress'` or similar)
- [ ] Transcript is generated
- [ ] Interview completes
- [ ] Maya writes results to shared DB `interviews` table

---

## STAGE 17 — Shared DB Interview Results

**Source files**: `app/models/entities.py` (`InterviewEntity`, `InterviewSessionEntity`)

Maya writes directly to the shared PostgreSQL database. Adam reads from it.

### interviews table

```sql
-- READ ONLY — SAFE FOR PRODUCTION
SELECT
    id                      AS interview_id,
    job_id,
    candidate_id,
    agency_id,
    status,
    interview_score,
    technical_score,
    communication_score,
    culture_fit_score,
    interview_score_reason,
    ai_summary,
    transcript,
    feedback,
    interviewer_notes,
    video_url,
    created_at,
    updated_at
FROM interviews
WHERE job_id = '<job_id>' AND candidate_id = '<candidate_id>'
ORDER BY created_at DESC
LIMIT 3;
```

### interview_sessions table

```sql
-- READ ONLY — SAFE FOR PRODUCTION
SELECT
    id, job_id, candidate_id, session_token, status, booking_status,
    stage, evaluation_status, evaluation_ready_at, scheduled_at, created_at
FROM interview_sessions
WHERE job_id = '<job_id>' AND candidate_id = '<candidate_id>'
ORDER BY created_at DESC;
```

### Checks

- [ ] `interviews` row exists for (job_id, candidate_id)
- [ ] `interviews.status` = `completed` (or `interview_completed` / `results_ready`)
- [ ] `interviews.transcript` is non-null and non-empty
- [ ] `interviews.interview_score` is non-null
- [ ] `interviews.ai_summary` is non-null
- [ ] `interviews.video_url` is set (if recording was produced)
- [ ] `interviews.candidate_id` matches the test candidate
- [ ] `interviews.job_id` matches the test job

### Record

| Field | Value |
|---|---|
| interviews.id | |
| interviews.status | |
| interviews.interview_score | |
| interviews.technical_score | |
| interviews.communication_score | |
| interviews.culture_fit_score | |
| interviews.video_url | |
| interviews.transcript (first 100 chars) | |

---

## STAGE 18 — Adam Recruiter Results

**Source files**: `app/services/results_service.py`, `app/api/routes/results.py`

### API endpoints

```
GET /api/results/video/<workflowToken>
Authorization: JWT cookie (recruiter session)
```

```
GET /api/recording/<session_token>
Authorization: JWT cookie (recruiter session)
```

`workflowToken` = `notification_workflow_tokens.token` (the booking token, NOT the session token).

### How Adam fetches results (`get_result_by_workflow_token`)

1. Looks up `notification_workflow_tokens` by `token` → gets `job_id`, `candidate_id`
2. Verifies `job.company_id == agency_id` (agency-level auth)
3. Queries `interviews` JOIN `candidates` JOIN `interview_sessions` JOIN `notification_workflow_tokens`
4. Returns transcript, scores, summary, recording path

### Video streaming (`_proxy_recording`)

Adam proxies video from Maya:
```
GET {INTERVIEW_APP_URL}/api/video/{video_url}
Header: X-Internal-API-Key: <INTERVIEW_INTERNAL_SERVICE_TOKEN>
```

### Checks

- [ ] `GET /api/results/video/<workflowToken>` returns 200 with result data
- [ ] Transcript is present in response
- [ ] Scores are present
- [ ] AI summary is present
- [ ] `recording.videoAvailable` = true (if video exists)
- [ ] `recording.sessionToken` is set

---

## STAGE 19 — Recording Authorization

**Source files**: `app/services/results_service.py` (`_result_agency_matches`)

### Authorization logic (exact from source)

```python
def _result_agency_matches(*, db, job_id, candidate_id, agency_id) -> bool:
    # Check 1: candidates.agency_id == agency_id
    profile = CandidateProfileRepository(db).get(job_id=job_id, candidate_id=candidate_id)
    if profile and profile.agency_id == agency_id:
        return True
    # Check 2: interviews.agency_id == agency_id
    interview_row = _fetch_interview_result_row(...)
    if interview_row.get("agency_id") == agency_id:
        return True
    # Check 3: job_descriptions.company_id == agency_id
    job = JobRepository(db).get(job_id)
    return bool(job and job.company_id == agency_id)
```

**Authorization is agency-level only. There is NO recruiter-level ownership.**

### Test cases

**Recruiter A — same agency as job**
- Expected: `200` — recording accessible

**Recruiter B — different recruiter, same agency**
- Expected: `200` — recording accessible (agency-level only, no recruiter ownership)

**Recruiter C — different agency**
- Expected: `403` — `APIError("Forbidden", status_code=403)`

### Checks

- [ ] Recruiter A (same agency): `200`
- [ ] Recruiter B (same agency, different user): `200`
- [ ] Recruiter C (different agency): `403`

---

## STAGE 20 — Final E2E Pass/Fail Table

| # | Stage | Expected | Actual | Pass/Fail | Evidence |
|---|---|---|---|---|---|
| 1 | Adam job exists | `job_descriptions` row with `job_status=active` | | | |
| 2 | LinkedIn fallback uses `/application` with no job_id | URL = `{EVE_BASE_URL}/application` | | | |
| 3 | Candidate enters Eve | `candidates` row created | | | |
| 4 | Candidate in global Eve talent pool | `candidates.agency_id` set, no job_id at entry | | | |
| 5 | Shared DB candidate exists | `candidates` row readable from Adam | | | |
| 6 | Adam detects candidate | `embedding_status` transitions to `PROCESSING` then `EMBEDDED` | | | |
| 7 | Candidate embedding exists | `candidates.embedding_status = EMBEDDED` | | | |
| 8 | Qdrant candidate point exists | Point in `internal_candidate_chunks` with correct payload | | | |
| 9 | Adam matching finds candidate | `final_score >= 0.60`, appears in `/api/candidates` | | | |
| 10 | Recruiter sees candidate | Candidate visible in Adam UI | | | |
| 11 | Recruiter clicks Interested | `POST /api/candidates/<id>/interest` returns 200 | | | |
| 12 | Adam creates correct interest rows | `candidate_requests` PENDING, `recruiter_interest_requests` interested, `adam_eve_outbound_events` pending | | | |
| 13 | Adam → Eve event succeeds | `adam_eve_outbound_events.status = delivered` | | | |
| 14 | Eve dashboard shows opportunity | Opportunity visible in candidate's Eve dashboard | | | |
| 15 | Candidate receives recruiter-interest email | Email in inbox (Eve-side) | | | |
| 16 | Candidate can accept | `POST /api/candidates/<id>/respond?action=accept` returns 200 | | | |
| 17 | Email/dashboard acceptance converges to one request | COUNT(`candidate_requests`) = 1, COUNT(`notification_workflow_tokens`) = 1 | | | |
| 18 | Booking token generated once | `notification_workflow_tokens` row with `token_type=slot_selection` | | | |
| 19 | Same booking URL in email and Eve dashboard | `notification_events.body` == `adam_eve_outbound_events.payload.booking_url` | | | |
| 20 | Maya booking works | Candidate can open booking URL and select slot | | | |
| 21 | Maya creates interview session | `interview_sessions.booking_status = confirmed` | | | |
| 22 | Final interview URL uses session token | URL = `https://interview.pontis.one/interview?token=<session_token>` | | | |
| 23 | Interview completes | `interviews.status = completed`, transcript non-null | | | |
| 24 | Results exist in shared DB | `interviews` row with scores, summary, video_url | | | |
| 25 | Adam displays correct results | `GET /api/results/video/<workflowToken>` returns 200 with data | | | |
| 26 | Recording works for same-agency recruiters | `GET /api/recording/<session_token>` returns 200 | | | |
| 27 | Cross-agency recording access denied | Different agency recruiter gets 403 | | | |

---

## Production E2E Configuration

All environment variables required for the full flow. Derived from `app/core/config.py`.

### Critical (service will not start without these)

| Variable | Required | Purpose |
|---|---|---|
| `DATABASE_URL` | YES | Shared PostgreSQL connection string |
| `JWT_SECRET` | YES | Auth token signing |
| `PUBLIC_APP_URL` | YES | Adam frontend URL |
| `INTERNAL_API_KEY` | YES | Eve → Adam internal endpoint auth (X-Internal-Api-Key header) |

### Qdrant

| Variable | Required | Purpose |
|---|---|---|
| `QDRANT_URL` | YES (production) | Qdrant cluster endpoint |
| `QDRANT_API_KEY` | YES (if Qdrant Cloud) | Qdrant authentication |
| `INTERNAL_CANDIDATE_COLLECTION_NAME` | NO (default: `internal_candidate_chunks`) | Eve candidate collection |
| `JOB_COLLECTION_NAME` | NO (default: `job_chunks`) | Job embedding collection |
| `EMBEDDING_VERSION` | NO (default: `v2_structured`) | Must match across indexing and search |
| `VECTOR_SIZE` | NO (default: `384`) | Must match embedding model output |
| `INTERNAL_CANDIDATE_MATCH_THRESHOLD` | NO (default: `0.60`) | Minimum score to qualify |
| `INTERNAL_CANDIDATE_RETRIEVAL_TOP_K` | NO (default: `100`) | Qdrant search limit |

### Eve integration

| Variable | Required | Purpose |
|---|---|---|
| `EVE_BASE_URL` | YES | Eve service base URL (e.g. `https://eve.pontis.one`) |
| `EVE_INTERNAL_TOKEN` | YES | Bearer token for Adam → Eve delivery |
| `ADAM_INTERNAL_SERVICE_TOKEN` | NO (falls back to `INTERNAL_API_KEY`) | Eve → Adam internal-response endpoint auth |

### Maya / Interview

| Variable | Required | Purpose |
|---|---|---|
| `INTERVIEW_APP_URL` | NO (default: `https://interview.pontis.one`) | Maya base URL for booking/interview URLs |
| `INTERVIEW_INTERNAL_SERVICE_TOKEN` | YES | Adam → Maya video proxy auth (X-Internal-API-Key) |

### Email

| Variable | Required | Purpose |
|---|---|---|
| `RESEND_API_KEY` | YES (if `OUTREACH_PROVIDER=resend`) | Email sending |
| `OUTREACH_FROM_EMAIL` | NO (default: `info@pontis.one`) | From address |
| `ENABLE_REAL_EMAIL_SENDING` | NO (default: `true`) | Set false to suppress emails in staging |

### Scheduler

| Variable | Required | Purpose |
|---|---|---|
| `REFRESH_CRON_ENABLED` | NO (default: `true`) | Enable/disable background scheduler |
| `REFRESH_INTERVAL_MINUTES` | NO (default: `10`) | Scheduler cycle interval |
| `STALE_DAYS` | NO (default: `7`) | Days before candidate embedding is considered stale |

### LinkedIn fallback

| Variable | Required | Purpose |
|---|---|---|
| `LINKEDIN_JOB_POST_MODE` | NO (default: `DRY_RUN`) | `DRY_RUN` or `LIVE` |
| `LINKEDIN_ENABLED` | NO (default: `false`) | Enable LinkedIn automation |

---

## Qdrant Configuration Diagnosis

### Current Qdrant client setup (from `qdrant_service.py`)

```python
_client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY or None)
```

- Single client instance, shared across all operations (indexing + search)
- `QDRANT_URL` = `os.getenv("QDRANT_URL")` — no default, None if unset
- `QDRANT_API_KEY` = `os.getenv("QDRANT_API_KEY")` — no default, None if unset

### Collections used

| Collection | Purpose | Used by |
|---|---|---|
| `internal_candidate_chunks` | Eve candidate semantic index | Indexing + matching |
| `job_chunks` | Job embeddings (legacy sourced flow) | Old sourced-candidate matching only |
| `candidate_chunks` | Sourced candidate chunks | Old sourced-candidate flow |
| `recruiter_preferences` | Recruiter preference vectors | RLHF |
| `recruiter_memory` | Recruiter memory vectors | RLHF |
| `sourced_candidate_memory` | X-Ray sourced candidates | X-Ray sourcing |

**For the Eve flow, only `internal_candidate_chunks` matters.**

### 403 Forbidden diagnosis

The error `Unexpected Response: 403 (Forbidden)` on Qdrant search means:

| Cause | How to verify | Fix |
|---|---|---|
| Wrong `QDRANT_API_KEY` | Check key in Qdrant Cloud dashboard | Set correct key in env |
| `QDRANT_URL` points to wrong cluster | Compare URL with Qdrant Cloud cluster URL | Set correct URL |
| API key missing entirely | `QDRANT_API_KEY` env var is empty | Set the key |
| Railway-hosted Qdrant with no auth configured | Railway Qdrant may not require a key | Set `QDRANT_API_KEY=` (empty) |
| Qdrant Cloud collection-level ACL | Check collection permissions | Grant read/write to API key |

### Verify Qdrant connectivity (read-only)

```python
# READ ONLY — SAFE FOR PRODUCTION
from qdrant_client import QdrantClient

client = QdrantClient(url="<QDRANT_URL>", api_key="<QDRANT_API_KEY>")

# Test 1: list collections
collections = client.get_collections()
print("Collections:", [c.name for c in collections.collections])

# Test 2: count internal_candidate_chunks
info = client.get_collection("internal_candidate_chunks")
print("Points:", info.points_count)

# Test 3: verify a specific candidate point exists
from qdrant_client.models import Filter, FieldCondition, MatchValue
results, _ = client.scroll(
    collection_name="internal_candidate_chunks",
    scroll_filter=Filter(must=[
        FieldCondition(key="candidateRecordId", match=MatchValue(value="<candidate_record_id>"))
    ]),
    limit=1,
    with_payload=True,
    with_vectors=False,
)
print("Point found:", bool(results))
if results:
    print("Payload:", results[0].payload)
```

### Environment variable consistency check

Both candidate indexing (`internal_candidate_embedding_service.py`) and job matching (`internal_candidate_semantic_service.py`) use:
- Same `QDRANT_URL` / `QDRANT_API_KEY` (single client in `qdrant_service.py`)
- Same `EMBEDDING_VERSION` for filtering
- Same `INTERNAL_CANDIDATE_COLLECTION_NAME`

If indexing succeeds but matching returns 403, the issue is the Qdrant client configuration at runtime, not a code bug.

