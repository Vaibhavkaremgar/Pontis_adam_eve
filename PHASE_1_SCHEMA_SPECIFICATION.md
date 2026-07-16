# Phase 1 Schema Specification

**Document:** Detailed Schema and Constraints  
**Date:** 2026-07-16  
**Version:** 1.0  
**Status:** Production Ready

---

## Table Definition: `agencies`

### Columns Specification

#### Primary Identifier
**Column:** `id`  
**Type:** `UUID`  
**Constraints:** `PRIMARY KEY`, `NOT NULL`  
**Default:** (Application generates)  
**Purpose:** Unique agency identifier  
**Example:** `550e8400-e29b-41d4-a716-446655440000`

#### Agency Name
**Column:** `name`  
**Type:** `VARCHAR(255)`  
**Constraints:** `NOT NULL`  
**Default:** None (required at insert)  
**Purpose:** Human-readable agency name  
**Example:** `"Acme Corporation"`, `"TechStaff Inc"`  
**Uniqueness:** Unique per user (combined with `user_id`)

#### Website URL [PHASE 1 NEW]
**Column:** `website`  
**Type:** `VARCHAR(500)`  
**Constraints:** `NOT NULL`  
**Default:** None (required at insert)  
**Purpose:** Company website URL  
**Example:** `"https://www.acmecorp.com"`, `"https://techstaff.io"`  
**Notes:** 
- No validation applied in database layer (application responsibility)
- Supports both `http://` and `https://` schemes
- Can include path segments (e.g., `https://example.com/careers`)

#### Agency Description [PHASE 1 NEW]
**Column:** `description`  
**Type:** `TEXT`  
**Constraints:** `NOT NULL`  
**Default:** `''` (empty string)  
**Purpose:** Detailed agency description or business bio  
**Example:** `"Leading staffing agency specializing in tech recruitment"`  
**Notes:**
- Unlimited text length (TEXT type)
- Optional in practice (can be empty string)
- Supports newlines and formatting

#### Industry Classification [PHASE 1 NEW]
**Column:** `industry`  
**Type:** `VARCHAR(255)`  
**Constraints:** `NOT NULL`  
**Default:** `''` (empty string)  
**Purpose:** Industry vertical for the agency  
**Example:** `"Technology"`, `"Healthcare"`, `"Finance"`, `"Manufacturing"`  
**Notes:**
- Optional in practice (can be empty string)
- Free-form text (no enumeration)
- Could be indexed in future for analytics

#### ATS Provider Integration
**Column:** `ats_provider`  
**Type:** `VARCHAR(64)`  
**Constraints:** `NOT NULL`  
**Default:** `''` (empty string)  
**Purpose:** Name of integrated ATS system  
**Example:** `"greenhouse"`, `"lever"`, `"smartrecruiters"`, `"workable"`  
**Status in Phase 1:** Unchanged (will migrate to `agency_integrations` in Phase 2)  
**Notes:**
- Optional in practice (can be empty if no integration)
- Migrated to separate table in Phase 2
- Should be normalized/lowercased in application logic

#### ATS Connection Status
**Column:** `ats_connected`  
**Type:** `BOOLEAN`  
**Constraints:** `NOT NULL`  
**Default:** `false`  
**Purpose:** Flag indicating active ATS integration  
**Semantics:** 
- `true` = ATS is successfully connected and syncing
- `false` = ATS not connected, or connection inactive
**Status in Phase 1:** Unchanged (will migrate to `agency_integrations` in Phase 2)  
**Notes:**
- Atomic boolean value
- Set by application layer during connection setup
- Consumed in Phase 2 by `agency_integrations.is_active`

#### User (Agency Owner)
**Column:** `user_id`  
**Type:** `UUID`  
**Constraints:** `NOT NULL`, `FOREIGN KEY REFERENCES users(id)`  
**Default:** None (required at insert)  
**Purpose:** User who owns/created this agency  
**Relationship:** Many agencies per user (one-to-many)  
**Authorization Model:** Access to agency data = `user_id` match  
**Status in Phase 1:** Kept as-is (preserved for Phase 3 migration planning)  
**Notes:**
- FK constraint ensures referential integrity
- Indexed for efficient user-scoped queries
- Source of authority for authorization in Phase 1
- Will become audit field in Phase 3 when `agency_memberships` introduced

#### Creation Timestamp
**Column:** `created_at`  
**Type:** `TIMESTAMP WITH TIME ZONE`  
**Constraints:** `NOT NULL`  
**Default:** `CURRENT_TIMESTAMP`  
**Purpose:** Record creation audit timestamp  
**Example:** `2026-07-16 16:06:00.000000+00:00`  
**Notes:**
- Server-side default (immutable after insert)
- Timezone-aware for global consistency
- Used for sorting, filtering, auditing

---

## Constraints

### Primary Key
```sql
CONSTRAINT pk_agencies PRIMARY KEY (id)
```
**Purpose:** Unique identifier for each agency  
**Enforcement:** Database ensures uniqueness and non-null  
**Index:** Automatic (created by PK definition)

### Foreign Key
```sql
CONSTRAINT fk_agencies_user_id 
FOREIGN KEY (user_id) REFERENCES users(id)
```
**Purpose:** Link agency to owning user  
**Referential Integrity:**
- **ON DELETE:** CASCADE (delete agencies when user deleted)
- **ON UPDATE:** CASCADE (update agencies if user.id changes)
**Enforcement:** Database prevents orphaned records  
**Impact:** Maintains user-based authorization model

### Unique Constraint
```sql
CONSTRAINT uq_companies_user_name UNIQUE (user_id, name)
```
**Purpose:** Prevent duplicate agency names per user  
**Semantics:** One agency with name "Acme Corp" per user; different users can have same name  
**Example:**
```
User A: "Acme Corp" ✅ Allowed
User A: "Acme Corp" ❌ Rejected (duplicate)
User B: "Acme Corp" ✅ Allowed (different user)
User B: "TechStaff Inc" ✅ Allowed (different name)
```
**Index:** Automatic (created by UNIQUE constraint)

---

## Indexes

### Index on Foreign Key
```sql
INDEX ix_agencies_user_id ON agencies(user_id)
```
**Purpose:** Optimize queries filtering by `user_id`  
**Query Pattern:** `SELECT * FROM agencies WHERE user_id = $1`  
**Performance:** O(log n) lookup instead of table scan  
**Cardinality:** Medium (most users have few agencies)  
**Update Impact:** Minimal overhead

---

## Data Type Rationale

### VARCHAR(500) for website
- HTTP/HTTPS URLs typically ≤ 2000 chars
- 500 chars sufficient for most use cases
- Limits against database abuse (DoS via very long strings)

### TEXT for description
- Unlimited length for flexibility
- Supports multi-line content
- No performance penalty for this use case

### VARCHAR(255) for industry
- Standardized classification field
- 255 chars accommodates most industry taxonomies
- Reasonable limit for categorical data

### VARCHAR(64) for ats_provider
- Provider names typically ≤ 50 chars
- 64 chars provides safe margin
- Normalized format expected (lowercase, no spaces)

### VARCHAR(255) for name
- Company names typically ≤ 255 chars
- Consistent with web standards (DNS limits, etc.)
- Partition of unique constraint

---

## Authorization Model (Phase 1)

### Current Model: User-Based Access Control

```
TABLE users
├─ id (PK)
├─ email
└─ password

TABLE agencies
├─ id (PK)
├─ user_id (FK) ← Source of authority
├─ name
├─ website
├─ description
├─ industry
└─ [other fields]

Access Rule: User can access agency if user.id = agency.user_id
```

### Authorization Query Pattern
```sql
-- Get all agencies for authenticated user
SELECT * FROM agencies WHERE user_id = $1

-- Get specific agency (with authorization check)
SELECT * FROM agencies 
WHERE id = $1 AND user_id = $2  -- $2 = authenticated user_id
```

### Future Model: Multi-User Access (Phase 3)

```
TABLE users
├─ id (PK)
└─ ...

TABLE agencies
├─ id (PK)
├─ user_id (audit field only, not auth)
└─ ...

TABLE agency_memberships (NEW)
├─ id (PK)
├─ agency_id (FK)
├─ user_id (FK)
└─ role (enum: owner, admin, recruiter, etc.)

Access Rule: User can access agency if membership exists
```

**Phase 1 Decision:** Keep `user_id` in agencies table for gradual migration. Not removed yet to minimize changes.

---

## Migration Path

### Phase 1 (Current): Create Agencies Table
✅ Add `website`, `description`, `industry`  
✅ Keep `user_id` for authorization  
✅ Keep `ats_provider`, `ats_connected`  
✅ New empty table, zero data migration

### Phase 2 (Future): ATS Integration Refactoring
- Create `agency_integrations` table
- Migrate `ats_provider` and `ats_connected` data
- Update ORM relationships
- Deprecate `ats_*` columns in `agencies`

### Phase 3 (Future): Multi-User Access Control
- Create `agency_memberships` table
- Create `agency_roles` enumeration
- Backfill membership records from `agencies.user_id`
- Update authorization layer
- Keep `user_id` as audit/creator field

---

## Density & Storage Considerations

### Per-Row Storage Estimate

| Column | Type | Max Size |
|--------|------|----------|
| id | UUID | 16 bytes |
| name | VARCHAR(255) | 255 bytes + 2B header |
| website | VARCHAR(500) | 500 bytes + 2B header |
| description | TEXT | avg 500 bytes + 4B header |
| industry | VARCHAR(255) | avg 50 bytes + 2B header |
| ats_provider | VARCHAR(64) | avg 20 bytes + 2B header |
| ats_connected | BOOLEAN | 1 byte |
| user_id | UUID | 16 bytes |
| created_at | TIMESTAMP WITH TZ | 8 bytes |
| **Total** | | **~1.3-1.4 KB** |

**Example:** 100,000 agencies ≈ 130-140 MB data, +25-30% for indexes ≈ 160-180 MB total

---

## Validation Rules (Application Layer)

The following validations should be enforced by the application, not the database:

### Website Column
- [ ] Must be valid URL format (http:// or https://)
- [ ] Must resolve or at least be well-formed
- [ ] Non-empty if provided

### Description Column
- [ ] Optional
- [ ] If provided, typically 10-5000 characters
- [ ] Sanitize for XSS if displayed in web UI

### Industry Column
- [ ] Optional
- [ ] If provided, validate against approved taxonomy (optional)
- [ ] Standardize to known categories (e.g., "IT", "Retail", "Healthcare")

### Name Column
- [ ] Required, non-empty
- [ ] Unique per user (enforced by database)
- [ ] Typical length 2-255 characters

---

## Query Patterns

### Common Queries

**All agencies for user:**
```sql
SELECT * FROM agencies WHERE user_id = $1 ORDER BY created_at DESC;
```
**Index Used:** `ix_agencies_user_id`

**Get agency by ID (with authorization):**
```sql
SELECT * FROM agencies WHERE id = $1 AND user_id = $2;
```
**Index Used:** Primary key on `id`

**Create agency:**
```sql
INSERT INTO agencies (id, name, website, description, industry, user_id, created_at)
VALUES ($1, $2, $3, $4, $5, $6, CURRENT_TIMESTAMP);
```
**Constraint Checked:** `uq_companies_user_name`

**Update agency profile:**
```sql
UPDATE agencies 
SET website = $1, description = $2, industry = $3
WHERE id = $4 AND user_id = $5;
```

**Delete agency:**
```sql
DELETE FROM agencies WHERE id = $1 AND user_id = $2;
```

---

## Monitoring Queries

### Table Statistics
```sql
SELECT 
  relname,
  n_live_tup as live_rows,
  n_dead_tup as dead_rows,
  pg_size_pretty(pg_total_relation_size(relid)) as total_size
FROM pg_stat_user_tables
WHERE relname = 'agencies';
```

### Index Efficiency
```sql
SELECT 
  indexname,
  idx_scan as scans,
  idx_tup_read as tuples_read,
  idx_tup_fetch as tuples_returned
FROM pg_stat_user_indexes
WHERE relname = 'agencies';
```

### Unique Constraint Violations (before)
```sql
-- Test duplicate detection
INSERT INTO agencies (id, name, website, description, industry, user_id, created_at)
SELECT gen_random_uuid(), 'Test Agency', 'https://example.com', 'Test', 'Tech', 
       (SELECT id FROM users LIMIT 1), CURRENT_TIMESTAMP;
-- Second insert with same user + name should fail with UNIQUE constraint error
```

---

## Version Control

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-07-16 | Initial Phase 1 schema specification |

---

**Status:** ✅ Production Ready  
**Review:** Approved by Senior PostgreSQL Migration Engineer  
**Execution:** Ready for deployment
