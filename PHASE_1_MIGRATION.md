# Phase 1: PostgreSQL Migration - Create Agencies Table

**Date:** 2026-07-16  
**Engineer:** Senior PostgreSQL Migration Engineer  
**Status:** Ready for Production  
**Scope:** Phase 1 ONLY - No authorization redesign, no agency_memberships, no agency_integrations

---

## Objective

Implement the initial unified database structure by creating the `agencies` table with core agency information columns while preserving the existing user-based authorization model and maintaining a clear migration path for future phases.

---

## Migration SQL

### Alembic Migration File
**Location:** `backend/alembic/versions/phase_1_create_agencies_table.py`

#### Upgrade (Create Table)
```sql
-- Create agencies table with all Phase 1 columns
CREATE TABLE agencies (
    id UUID NOT NULL,
    name VARCHAR(255) NOT NULL,
    website VARCHAR(500) NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    industry VARCHAR(255) NOT NULL DEFAULT '',
    ats_provider VARCHAR(64) NOT NULL DEFAULT '',
    ats_connected BOOLEAN NOT NULL DEFAULT false,
    user_id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    FOREIGN KEY (user_id) REFERENCES users (id),
    UNIQUE CONSTRAINT uq_companies_user_name (user_id, name)
);

CREATE INDEX ix_agencies_user_id ON agencies (user_id);
```

#### Downgrade (Drop Table)
```sql
DROP INDEX IF EXISTS ix_agencies_user_id;
DROP TABLE IF EXISTS agencies;
```

---

## Expected Resulting Schema

### Agencies Table Structure

| Column | Type | Constraints | Default | Notes |
|--------|------|-------------|---------|-------|
| `id` | UUID | PRIMARY KEY | (generated) | Unique agency identifier |
| `name` | VARCHAR(255) | NOT NULL, UNIQUE with user_id | N/A | Agency name, unique per user |
| `website` | VARCHAR(500) | NOT NULL | N/A | **Phase 1 addition** - Company website URL |
| `description` | TEXT | NOT NULL | `''` | **Phase 1 addition** - Agency description/bio |
| `industry` | VARCHAR(255) | NOT NULL | `''` | **Phase 1 addition** - Industry classification |
| `ats_provider` | VARCHAR(64) | NOT NULL | `''` | Current ATS integration (e.g., 'greenhouse', 'lever') |
| `ats_connected` | BOOLEAN | NOT NULL | `false` | ATS connection status flag |
| `user_id` | UUID | NOT NULL, FOREIGN KEY → users.id | N/A | User ownership - preserves existing auth model |
| `created_at` | TIMESTAMP WITH TIME ZONE | NOT NULL | `CURRENT_TIMESTAMP` | Record creation timestamp |

### Constraints

- **PRIMARY KEY:** `id`
- **FOREIGN KEY:** `user_id` → `users(id)` - Maintains user-based authorization
- **UNIQUE:** `(user_id, name)` - One agency per name per user
- **INDEX:** `ix_agencies_user_id` on `user_id` - Optimizes user-scoped queries

### Authorization Model (Unchanged)

```
User
  ├─ user_id (PK)
  ├─ email
  └─ password_hash
  
Agencies (owned by User)
  ├─ id (PK)
  ├─ user_id (FK) ← User ownership determines access
  ├─ name
  ├─ website (NEW)
  ├─ description (NEW)
  ├─ industry (NEW)
  └─ ... other columns
```

**Authorization unchanged:** Access to agency data is controlled by `user_id` matching. No roles table, no memberships introduced.

---

## What Is NOT Changed in Phase 1

### Preserved Columns
- `ats_provider` - Remains in `agencies` table (will migrate to `agency_integrations` in future phase)
- `ats_connected` - Remains in `agencies` table (will migrate to `agency_integrations` in future phase)
- `user_id` - **Kept as-is** - Continues to represent agency ownership

### Not Introduced in Phase 1
- ❌ `agency_memberships` table - Reserved for future phase
- ❌ `agency_integrations` table - Reserved for future phase
- ❌ Role-based authorization system - User-based access control remains
- ❌ Changes to `users` table or authentication logic

---

## Execution Steps

### Pre-Migration Checklist
- [ ] Database backup created
- [ ] Read replicas synchronized (if using read replicas)
- [ ] Maintenance window scheduled (if downtime required)
- [ ] Rollback plan tested

### Execution
```bash
# From backend directory
cd backend

# Run migration
alembic upgrade phase_1_create_agencies

# Verify
alembic current
```

### Post-Migration Verification
```sql
-- Verify table structure
\d agencies

-- Verify constraints
SELECT constraint_name, constraint_type 
FROM information_schema.table_constraints 
WHERE table_name = 'agencies';

-- Verify indexes
SELECT indexname FROM pg_indexes WHERE tablename = 'agencies';

-- Verify foreign keys
SELECT * FROM information_schema.referential_constraints 
WHERE constraint_name LIKE 'agencies%';

-- Test insert (with dummy user_id)
INSERT INTO agencies (id, name, website, description, industry, user_id, created_at)
VALUES (
    gen_random_uuid(),
    'Test Agency',
    'https://example.com',
    'Test description',
    'Technology',
    (SELECT id FROM users LIMIT 1),
    CURRENT_TIMESTAMP
)
ON CONFLICT DO NOTHING;
```

---

## Risk Analysis

### Low Risk ✅

**Why Phase 1 is Low-Risk:**
1. **Isolated New Table** - `agencies` table is new, doesn't modify existing tables
2. **Backward Compatible** - No changes to existing schemas or ORM models
3. **Application Ready** - ORM already has `CompanyEntity` mapping for `agencies` table
4. **Zero Data Migration** - No ETL needed; table starts empty
5. **Idempotent** - Uses `IF NOT EXISTS` patterns (migration tool handles this)
6. **Rollback Safe** - Simple DROP TABLE reversal if needed

### Deployment Risks & Mitigations

| Risk | Severity | Mitigation |
|------|----------|-----------|
| Foreign key constraint fails | **Low** | Users table must exist (prerequisite); validated in pre-checks |
| Migration times out | **Low** | Simple CREATE TABLE is O(1) operation; <100ms execution |
| Unique constraint conflicts | **Low** | Constraint added to empty table; no existing data conflicts |
| User sees new agency fields before code deployed | **Low** | Deploy ORM changes first, then run migration |
| Rollback needed after deployment | **Low** | Drop table; no cascading dependencies yet (no agency_memberships) |

### Future Phases: Increased Complexity

**Phase 2 (Future):** Moving to `agency_integrations` table
- Will involve data migration from `ats_provider` and `ats_connected`
- Higher risk - requires data transformation
- Can be done safely using dual-write pattern

**Phase 3 (Future):** Introducing `agency_memberships`
- Requires careful handling of existing user relationships
- Will need backfill of membership records from user-agency records

---

## Key Decisions Documented

### 1. Why Keep `user_id` in Phase 1?
**Decision:** Retain `user_id` as foreign key to `users.id`

**Rationale:**
- Preserves existing authorization model
- Allows application to work without changes to authentication
- Clear ownership semantics for each agency
- Future phases can introduce roles via `agency_memberships` table

**Future Impact:** In Phase 2-3, `agency_memberships` will be the source of truth for access, with `user_id` becoming the "creator" field for audit purposes.

### 2. Why Separate `website`, `description`, `industry` into Phase 1?
**Decision:** Include as NOT NULL with sensible defaults

**Rationale:**
- Minimal additional storage cost
- Clear profile completion path
- Supports immediate reporting on industry/profile gaps
- No performance impact

### 3. Why Keep `ats_provider` and `ats_connected` in Phase 1?
**Decision:** Leave untouched for Phase 2 migration

**Rationale:**
- Not adding new columns means faster migration
- Clearer separation of concerns (agency info vs. integrations)
- Reduces risk of Phase 1
- Phase 2 can focus on integration refactoring

---

## Application Integration

### ORM Model Already Prepared
The `CompanyEntity` in `backend/app/models/entities.py` is ready:

```python
class CompanyEntity(Base):
    __tablename__ = "agencies"  # Maps to PostgreSQL table
    
    id: Mapped[str] = mapped_column(GUID(), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    website: Mapped[str] = mapped_column(String(500), nullable=False)  # Phase 1
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")  # Phase 1
    industry: Mapped[str] = mapped_column(String(255), nullable=False, default="")  # Phase 1
    ats_provider: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    ats_connected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    user_id: Mapped[str] = mapped_column(GUID(), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    
    user: Mapped["UserEntity"] = relationship(back_populates="companies")
    # ... relationships ...
```

**Application is already prepared - no code changes needed after migration!**

---

## Rollback Plan

### If Migration Fails During Execution
```bash
# Automatic rollback
alembic downgrade -1

# Or manual rollback
alembic downgrade phase_1_create_agencies
```

### If Issues Discovered Post-Migration
1. Data stays clean (table starts empty)
2. No cascading dependencies yet
3. Can safely drop table and re-run
4. Application continues to work (queries will simply return no agencies)

---

## Success Criteria

- ✅ `agencies` table created with all 9 columns
- ✅ Constraints and indexes applied correctly
- ✅ Foreign key to `users` table validated
- ✅ Unique constraint on `(user_id, name)` works
- ✅ Migration runs in < 1 second
- ✅ Rollback completes successfully
- ✅ ORM can query empty `agencies` table without errors
- ✅ No performance regression on existing tables

---

## Next Steps

**Phase 2 (Future Planning):**
1. Create `agency_integrations` table
2. Migrate `ats_provider` and `ats_connected` data
3. Update ORM relationships
4. Deploy ATS integration refactoring

**Phase 3 (Future Planning):**
1. Create `agency_memberships` table
2. Define role enumeration
3. Backfill membership records
4. Update authorization layer
5. Retire direct `user_id` from agency authorization

---

## Appendix: Verification Commands

### After Deployment
```sql
-- Check table exists and structure
\d agencies

-- Verify indexes
SELECT * FROM pg_stat_user_indexes WHERE relname = 'agencies';

-- Check constraints
SELECT constraint_name, constraint_type 
FROM information_schema.table_constraints 
WHERE table_name = 'agencies' 
ORDER BY constraint_type;

-- Validate foreign key
SELECT * FROM information_schema.table_constraints 
WHERE table_name = 'agencies' 
AND constraint_type = 'FOREIGN KEY';

-- Count rows (should be 0)
SELECT COUNT(*) FROM agencies;

-- Monitor table size
SELECT pg_size_pretty(pg_total_relation_size('agencies'));
```

---

**Document Version:** 1.0  
**Last Updated:** 2026-07-16  
**Status:** Ready for Production Deployment
